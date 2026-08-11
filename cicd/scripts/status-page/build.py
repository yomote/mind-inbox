#!/usr/bin/env python3
"""状況ページ (gh-pages /status/) を GitHub の実データから組み立てる。

使い方:
    python3 cicd/scripts/status-page/build.py <out_dir>
      要: `gh` が認証済み (Actions では GH_TOKEN を渡す)

なぜあるか (2026-08-10 の PO 指摘):
    「今何が動いていて何が問題か」がぱっと分かる場所が無かった。セッションで作る
    HTML は作った瞬間に古くなる静的スナップショットで、手で書く表は腐る。
    **状態を持たず、毎回 GitHub の実データから作り直す**ことで両方を避ける。

規律:
    - **取れなかったものを「異常なし」と書かない** (inspect-env.sh / ops-inspect と同じ)。
      取得に失敗したら「(未検証: 理由)」として出す
    - 痕跡を残さない自動化は 🟢 とも 🔴 とも書かない。**❓ 判定不能**として、
      なぜ判定できないかを並べる。ここが空欄でないこと自体が改善の入口
"""

import html
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
# 既定は隣の watchers.json。テストは環境変数で自前の定義に差し替える —
# **性質を守るテストが、その時点の定義ファイルの中身に依存しないようにするため**
# (cd-watchdog を廃止したら「痕跡を残さない自動化」のテストが道連れで落ちた / 2026-08-10)。
DEFS = (
    pathlib.Path(os.environ.get("STATUS_PAGE_WATCHERS") or "")
    if os.environ.get("STATUS_PAGE_WATCHERS")
    else pathlib.Path(__file__).with_name("watchers.json")
)

OK, BAD, WARN, UNKNOWN = "ok", "bad", "warn", "unknown"


def gh(*args: str) -> object | None:
    """gh api を叩く。失敗したら None (呼び出し側が「未検証」として扱う)。"""
    try:
        out = subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=60, check=True
        )
        return json.loads(out.stdout)
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
        FileNotFoundError,
    ) as e:
        print(f"WARN: gh {' '.join(args)} 失敗: {e}", file=sys.stderr)
        return None


def parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def ago(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    if h < 1:
        return f"{int(h * 60)} 分前"
    if h < 48:
        return f"{int(h)} 時間前"
    return f"{int(h / 24)} 日前"


def overdue(dt: datetime | None, expect_hours: int | None) -> bool:
    if dt is None or expect_hours is None:
        return False
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600 > expect_hours


# --- 収集 ---------------------------------------------------------------


def workflow_state(w: dict) -> dict:
    runs = gh(
        "api",
        f"repos/{{owner}}/{{repo}}/actions/workflows/{w['id']}/runs?per_page=5",
        "--jq",
        "[.workflow_runs[] | {c: .conclusion, s: .status, t: .created_at, u: .html_url}]",
    )
    row = {**w, "detail": "", "url": ""}
    if runs is None:
        return {
            **row,
            "state": UNKNOWN,
            "when": "—",
            "detail": "(未検証: run 履歴を取得できませんでした)",
        }
    done = [r for r in runs if r["s"] == "completed"]
    if not done:
        return {
            **row,
            "state": UNKNOWN,
            "when": "—",
            "detail": "(未検証: 完了した run がまだありません)",
        }
    latest = done[0]
    when = parse(latest["t"])
    state = OK if latest["c"] == "success" else BAD
    detail = ""
    if state == BAD:
        streak = 0
        for r in done:
            if r["c"] == "success":
                break
            streak += 1
        detail = f"直近 {streak} 回連続で失敗" if streak > 1 else "直近の実行が失敗"
    elif overdue(when, w.get("expect_hours")):
        state, detail = (
            WARN,
            f"予定より遅れています (期待 {w['expect_hours']} 時間以内)",
        )
    return {
        **row,
        "state": state,
        "when": ago(when),
        "detail": detail,
        "url": latest["u"],
    }


def load_ux_observations() -> list[dict] | None:
    """UX 観測データブランチ (ADR 0041) の checkout から全観測を読む。

    checkout の場所は環境変数 UX_DATA_DIR (workflow が data/ux-observations を
    fetch して渡す)。未設定・ディレクトリ無しは **None = 取得できなかった**
    (「観測ゼロ」とは別物 — 取れなかったを異常なしに見せない)。
    """
    root = os.environ.get("UX_DATA_DIR")
    if not root:
        return None
    root_path = pathlib.Path(root)
    if not root_path.is_dir():
        return None
    observations: list[dict] = []
    for sub in ("probes", "evals"):
        subdir = root_path / sub
        if not subdir.is_dir():
            continue
        for f in sorted(subdir.glob("*.jsonl")):
            for line in f.read_text(errors="ignore").splitlines():
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    observations.append(obj)
    return observations


def trace_time(trace: dict) -> tuple[datetime | None, str, bool]:
    """痕跡の最終時刻を返す。(時刻, 説明, 取得できたか)

    `gh --jq` はスカラー文字列を **クォート無し** で出す (jq -r と同じ) ため、
    `max` の結果をそのまま json.loads すると必ず失敗し、痕跡があるのに
    「取得できませんでした」になる (初回 run で実際にそうなった)。
    かならず**オブジェクトで包んで** JSON として受け取ること。
    """
    kind = trace.get("kind")
    if kind == "data_branch":
        # UX 観測データブランチの kind 別の最終追記時刻 (ADR 0041 D6)。
        # kind で絞るので「機械計測だけ動いて LLM 採点が止まった」も検出できる
        # (Issue コメント時代の既知の穴 — ADR 0037 Negative Consequences)
        record_kind = trace.get("record_kind", "?")
        where = f"データブランチの `{record_kind}`"
        observations = load_ux_observations()
        if observations is None:
            return None, where, False
        times = [
            parse(o.get("recordedAt"))
            for o in observations
            if o.get("kind") == record_kind and o.get("recordedAt")
        ]
        times = [t for t in times if t is not None]
        return (max(times) if times else None), where, True
    if kind == "issue_comment":
        n = trace["issue"]
        got = gh(
            "api",
            f"repos/{{owner}}/{{repo}}/issues/{n}/comments?per_page=100",
            "--jq",
            "{t: ([.[].created_at] | max)}",
        )
        where = f"Issue #{n} のコメント"
    elif kind == "issue_label":
        got = gh(
            "api",
            f"repos/{{owner}}/{{repo}}/issues?state=all&per_page=20"
            f"&labels={trace['label']}",
            "--jq",
            "{t: ([.[].updated_at] | max)}",
        )
        where = f"`{trace['label']}` ラベルの Issue"
    elif kind == "issue_title":
        got = gh(
            "api",
            "repos/{owner}/{repo}/issues?state=all&per_page=100",
            "--jq",
            f'{{t: ([.[] | select(.title | contains("{trace["query"]}")) | .created_at] | max)}}',
        )
        where = f"タイトルに `{trace['query']}` を含む Issue"
    else:
        return None, "", False
    if not isinstance(got, dict):
        return None, where, False
    # t が null = 取得はできたが痕跡が 1 件も無い (= 沈黙。取得失敗とは別物)
    return parse(got.get("t")), where, True


def routine_state(r: dict) -> dict:
    row = {**r, "url": ""}
    trace = r.get("trace", {})
    if trace.get("kind") == "unknown" or r.get("trace_only_on_anomaly"):
        return {
            **row,
            "state": UNKNOWN,
            "when": "—",
            "detail": r.get("note", "痕跡を残さないので判定できません"),
        }
    when, where, fetched = trace_time(trace)
    if not fetched:
        return {
            **row,
            "state": UNKNOWN,
            "when": "—",
            "detail": f"(未検証: {where} を取得できませんでした)",
        }
    if when is None:
        return {
            **row,
            "state": BAD,
            "when": "—",
            "detail": f"{where} に痕跡が 1 件もありません",
        }
    if overdue(when, r.get("expect_hours")):
        return {
            **row,
            "state": BAD,
            "when": ago(when),
            "detail": f"{where} が {r['expect_hours']} 時間以上 更新されていません",
        }
    return {**row, "state": OK, "when": ago(when), "detail": where}


def pending() -> dict:
    needs = gh(
        "api",
        "repos/{owner}/{repo}/issues?state=open&labels=needs-human&per_page=50",
        "--jq",
        "[.[] | {n: .number, t: .title}]",
    )
    prs = gh(
        "api",
        "repos/{owner}/{repo}/pulls?state=open&per_page=50",
        "--jq",
        "[.[] | {n: .number, t: .title, d: .created_at}]",
    )
    proposed = []
    for f in sorted((REPO_ROOT / "docs" / "adr").glob("0*.md")):
        head = f.read_text(errors="ignore")[:1200]
        if "- Status: Proposed" in head:
            title = head.splitlines()[0].lstrip("# ").strip()
            proposed.append({"file": f.name, "title": title})
    return {"needs_human": needs, "prs": prs, "proposed": proposed}


# --- UX トレンド (ADR 0041 D6) ------------------------------------------

TREND_DAYS = 14
VERDICT_MARK = {"green": "🟢", "yellow": "🟡", "red": "🔴"}


def _trend_points(observations: list[dict], now: datetime) -> list[dict]:
    """ux-eval-mech の観測から描画用の点列を作る (純粋関数)。

    欠測 (avg/max が null) は None のまま残す — 0 に丸めるとトレンドが
    実際より良く見える (ux_eval.py の measure と同じ規律)。
    """
    cutoff = now - timedelta(days=TREND_DAYS)
    points = []
    for o in observations:
        if o.get("kind") != "ux-eval-mech":
            continue
        t = parse(o.get("recordedAt") or o.get("evaluatedAt"))
        if t is None or t < cutoff:
            continue
        metrics = o.get("metrics") if isinstance(o.get("metrics"), dict) else {}
        latency = (
            metrics.get("latency") if isinstance(metrics.get("latency"), dict) else {}
        )
        visible = latency.get("sendToReplyVisibleMs")
        visible = visible if isinstance(visible, dict) else {}
        warnings = (
            metrics.get("warnings") if isinstance(metrics.get("warnings"), dict) else {}
        )
        thresholds = (
            metrics.get("thresholds")
            if isinstance(metrics.get("thresholds"), dict)
            else {}
        )
        # 数値以外は欠測扱いで捨てる — 蓄積は第三者も書けた歴史があるため、
        # 型を偽装した 1 行で描画が TypeError にならないよう防御する (PR #260 P1)
        points.append(
            {
                "t": t,
                "avg": _num(visible.get("avgMs")),
                "max": _num(visible.get("maxMs")),
                "warn": sum(
                    v
                    for v in warnings.values()
                    if isinstance(v, int) and not isinstance(v, bool)
                ),
                "threshold": _num(thresholds.get("warnReplyVisibleMs")),
            }
        )
    points.sort(key=lambda p: p["t"])
    return points


def _num(value) -> float | None:
    """int/float 以外 (bool 含む) は欠測 (None) に落とす。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _nice_step(ymax_ms: float) -> int:
    """3〜6 本のグリッドになる秒刻みを選ぶ。"""
    for sec in (1, 2, 5, 10, 20, 30, 60):
        if ymax_ms / (sec * 1000) <= 6:
            return sec * 1000
    return 120000


def trend_svg(points: list[dict], threshold: float | None) -> str:
    """send→表示 avg/max の時系列を 1 枚のインライン SVG にする。

    目的は「悪化してるか一目で分かる」— 上がるほど悪い折れ線 + 警告閾値の
    ガイド線 + 警告が出た朝の赤リング。色はページのトークン (--chart-avg /
    --chart-max) を参照するのでダークモードでも読める。
    """
    w, h = 640, 230
    left, right, top, bottom = 48, 64, 14, 30
    plot_w, plot_h = w - left - right, h - top - bottom

    ys = [p[k] for p in points for k in ("avg", "max") if p[k] is not None]
    ymax = max([*ys, threshold or 0, 1000]) * 1.12
    step = _nice_step(ymax)
    ymax = ((ymax // step) + 1) * step

    t0 = points[0]["t"]
    t1 = points[-1]["t"]
    span = (t1 - t0).total_seconds() or 1.0

    def x(t: datetime) -> float:
        return (
            left + plot_w * ((t - t0).total_seconds() / span)
            if len(points) > 1
            else left + plot_w / 2
        )

    def y(v: float) -> float:
        return top + plot_h * (1 - v / ymax)

    parts = [
        f'<svg viewBox="0 0 {w} {h}" role="img" '
        'aria-label="send から応答表示までのレイテンシ推移" '
        'style="width:100%;height:auto;display:block">'
    ]

    # グリッド (控えめ) + 秒ラベル
    v = 0
    while v <= ymax:
        parts.append(
            f'<line x1="{left}" y1="{y(v):.1f}" x2="{w - right}" y2="{y(v):.1f}" '
            'stroke="var(--line)" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{left - 6}" y="{y(v) + 4:.1f}" text-anchor="end" '
            f'font-size="10" fill="var(--ink-muted)">{int(v / 1000)}s</text>'
        )
        v += step

    # 警告閾値のガイド (状態色 = amber。系列色には使わない)
    if threshold and threshold < ymax:
        parts.append(
            f'<line x1="{left}" y1="{y(threshold):.1f}" x2="{w - right}" '
            f'y2="{y(threshold):.1f}" stroke="var(--amber)" stroke-width="1.5" '
            'stroke-dasharray="5 4"/>'
        )
        parts.append(
            f'<text x="{w - right + 6}" y="{y(threshold) + 4:.1f}" font-size="10" '
            f'fill="var(--amber)">警告 {int(threshold / 1000)}s</text>'
        )

    # 系列 (折れ線 + マーカー)。max が上、avg が下に来るのが普通なのでこの順で描く
    for key, token in (("max", "var(--chart-max)"), ("avg", "var(--chart-avg)")):
        pts = [(x(p["t"]), y(p[key])) for p in points if p[key] is not None]
        if len(pts) > 1:
            path = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
            parts.append(
                f'<polyline points="{path}" fill="none" stroke="{token}" '
                'stroke-width="2" stroke-linejoin="round"/>'
            )
        for (px, py), p in zip(pts, [p for p in points if p[key] is not None]):
            title = f"{p['t'].astimezone(JST):%m-%d} {key} {int(p[key])}ms" + (
                f" / warn {p['warn']}" if p["warn"] else ""
            )
            parts.append(
                f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="{token}" '
                f'stroke="var(--surface)" stroke-width="2">'
                f"<title>{html.escape(title)}</title></circle>"
            )
        if pts:
            # 直接ラベル: 色チップ + 文字はインク色 (色だけに識別を負わせない)
            lx, ly = pts[-1]
            parts.append(
                f'<rect x="{lx + 8:.1f}" y="{ly - 5:.1f}" width="10" height="3" '
                f'rx="1.5" fill="{token}"/>'
            )
            parts.append(
                f'<text x="{lx + 22:.1f}" y="{ly + 1:.1f}" font-size="11" '
                f'fill="var(--ink-muted)">{key}</text>'
            )

    # 警告が出た朝: max の点に赤リング (色 + 形の二重符号)
    for p in points:
        if p["warn"] and p["max"] is not None:
            parts.append(
                f'<circle cx="{x(p["t"]):.1f}" cy="{y(p["max"]):.1f}" r="8" '
                'fill="none" stroke="var(--red)" stroke-width="2">'
                f"<title>警告 {p['warn']} 件</title></circle>"
            )

    # x 軸: 両端の日付だけ (点の日付はホバーの title にある)
    parts.append(
        f'<text x="{left}" y="{h - 8}" font-size="10" fill="var(--ink-muted)">'
        f"{points[0]['t'].astimezone(JST):%m-%d}</text>"
    )
    if len(points) > 1:
        parts.append(
            f'<text x="{w - right}" y="{h - 8}" text-anchor="end" font-size="10" '
            f'fill="var(--ink-muted)">{points[-1]["t"].astimezone(JST):%m-%d}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def ux_trend_section() -> str:
    """UX トレンド節の HTML を作る。データが取れない時は未検証と明示する。"""
    observations = load_ux_observations()
    if observations is None:
        return (
            '<p class="sub">(未検証: データブランチ <code>data/ux-observations</code> を'
            "取得できませんでした — トレンドを描けません)</p>"
        )

    now = datetime.now(timezone.utc)
    points = _trend_points(observations, now)
    cutoff = now - timedelta(days=TREND_DAYS)
    judges = sorted(
        (
            o
            for o in observations
            if o.get("kind") == "ux-judge-score"
            and (parse(o.get("recordedAt")) or cutoff) >= cutoff
        ),
        key=lambda o: o.get("recordedAt") or "",
    )

    out = []
    if not points:
        out.append(
            f'<p class="sub">直近 {TREND_DAYS} 日間の機械計測 (ux-eval-mech) が'
            "まだ積まれていません</p>"
        )
    else:
        out.append(
            '<p class="legend"><span class="chip avg"></span>send→表示 avg'
            '　<span class="chip max"></span>同 max'
            '　<span class="chip warnline"></span>警告閾値 — 上がるほど悪い</p>'
        )
        out.append(
            trend_svg(
                points,
                next(
                    (p["threshold"] for p in reversed(points) if p["threshold"]), None
                ),
            )
        )

    if judges:
        marks = " ・ ".join(
            f"{(parse(j.get('recordedAt')) or now).astimezone(JST):%m-%d} "
            f"{j.get('total', '?')}/{j.get('max', '?')} "
            f"{VERDICT_MARK.get(j.get('verdict'), '❓')}"
            for j in judges[-7:]
        )
        out.append(
            f'<p class="sub">LLM 採点 (ux-judge-score): {html.escape(marks, quote=False)}</p>'
        )
    else:
        out.append(
            '<p class="sub">LLM 採点 (ux-judge-score) は直近 '
            f"{TREND_DAYS} 日間にありません</p>"
        )

    if points:
        rows_html = "".join(
            f"<tr><td>{p['t'].astimezone(JST):%m-%d}</td>"
            f"<td>{int(p['avg']) if p['avg'] is not None else '欠測'}</td>"
            f"<td>{int(p['max']) if p['max'] is not None else '欠測'}</td>"
            f"<td>{p['warn']}</td></tr>"
            for p in points
        )
        out.append(
            "<details><summary>データ表 (ms)</summary>"
            '<div class="tablewrap"><table>'
            "<tr><td>日付</td><td>avg</td><td>max</td><td>警告</td></tr>"
            f"{rows_html}</table></div></details>"
        )
    return "\n".join(out)


# --- 描画 ---------------------------------------------------------------

BADGE = {
    OK: ("🟢", "動いている"),
    BAD: ("🔴", "止まっている"),
    WARN: ("🟡", "遅れている"),
    UNKNOWN: ("❓", "判定できない"),
}


def rows(items: list[dict]) -> str:
    out = []
    for it in items:
        mark, _ = BADGE[it["state"]]
        name = html.escape(it["name"])
        if it.get("url"):
            name = f'<a href="{it["url"]}">{name}</a>'
        detail = html.escape(it.get("detail") or "")
        what = html.escape(it.get("what") or "")
        out.append(
            f'<tr class="{it["state"]}"><td class="mark">{mark}</td>'
            f'<td><div class="nm">{name}</div><div class="sub">{what}</div></td>'
            f'<td class="when">{html.escape(it["when"])}</td>'
            f'<td class="detail">{detail}</td></tr>'
        )
    return "\n".join(out)


def build(out_dir: pathlib.Path) -> int:
    defs = json.loads(DEFS.read_text())
    wf = [workflow_state(w) for w in defs["workflows"]]
    rt = [routine_state(r) for r in defs["routines"]] + [
        routine_state(t) for t in defs.get("traces", [])
    ]
    pend = pending()

    broken = [x for x in wf + rt if x["state"] == BAD]
    unknown = [x for x in wf + rt if x["state"] == UNKNOWN]
    # 取得に失敗した行は「判定不能 (設計上)」とは別物。取れなかったことを
    # 「異常なし」に見せないため、見出しで最優先に出す。
    failed = [x for x in wf + rt if x.get("detail", "").startswith("(未検証")]
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    if failed:
        headline = (
            f"<strong>GitHub から状態を取得できませんでした ({len(failed)} 件)。</strong>"
            "このページの緑は当てになりません — 生成そのものが失敗しています"
        )
        head_class = "bad"
    elif broken:
        headline = f"いま壊れているものが <strong>{len(broken)} 件</strong>あります"
        head_class = "bad"
    elif unknown:
        headline = f"壊れているものはありません。ただし <strong>{len(unknown)} 件</strong>は生死を判定できていません"
        head_class = "warn"
    else:
        headline = "全部動いています"
        head_class = "ok"

    def li_pending() -> str:
        # 想定外の形が返っても**ページごと落とさない**。落ちると gh-pages が
        # 更新されず、古い緑が残り続けて赤を見落とす。
        parts = []
        if not isinstance(pend["needs_human"], list):
            parts.append(
                "<li>(未検証: needs-human の Issue を取得できませんでした)</li>"
            )
        else:
            for i in pend["needs_human"]:
                parts.append(
                    f'<li><a href="https://github.com/yomote/mind-inbox/issues/{i["n"]}">'
                    f"#{i['n']}</a> {html.escape(i['t'])}</li>"
                )
        for a in pend["proposed"]:
            parts.append(f"<li>ADR 未裁定: {html.escape(a['title'])}</li>")
        if not isinstance(pend["prs"], list):
            parts.append("<li>(未検証: open PR を取得できませんでした)</li>")
        else:
            for p in pend["prs"]:
                parts.append(
                    f'<li>開いたままの PR: <a href="https://github.com/yomote/mind-inbox/pull/{p["n"]}">'
                    f"#{p['n']}</a> {html.escape(p['t'])}</li>"
                )
        return "\n".join(parts) or "<li>ありません</li>"

    page = TEMPLATE.format(
        now=now,
        headline=headline,
        head_class=head_class,
        broken_rows=rows(broken)
        or '<tr><td colspan="4" class="none">ありません</td></tr>',
        wf_rows=rows(wf),
        rt_rows=rows(rt),
        pending=li_pending(),
        ux_trend=ux_trend_section(),
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(page)
    print(
        f"built: {out_dir / 'index.html'} / 赤 {len(broken)} 件 / 判定不能 {len(unknown)} 件"
    )
    return 0


TEMPLATE = """<!doctype html>
<html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mind Inbox — いまの状況</title>
<style>
:root {{ --bg:#f4f5f3; --surface:#fff; --ink:#26323b; --ink-strong:#131e27; --ink-muted:#5e6c75;
  --line:#dce1de; --accent:#2e6e60; --accent-soft:#e2efe9; --amber:#a1751f; --amber-soft:#f5ecd9;
  --red:#a5403a; --red-soft:#f7e7e5; --chart-avg:#00815c; --chart-max:#7a52c9; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#14191c; --surface:#1c2328; --ink:#ccd6d3;
  --ink-strong:#eef4f1; --ink-muted:#8b9a96; --line:#313c3e; --accent:#5cae9b; --accent-soft:#20312c;
  --amber:#cfa14e; --amber-soft:#322f1f; --red:#d4837c; --red-soft:#31211f;
  --chart-avg:#1c9b76; --chart-max:#9b7ae8; }} }}
body {{ background:var(--bg); color:var(--ink); margin:0; font-size:16px; line-height:1.75;
  font-family:"Hiragino Kaku Gothic ProN","Hiragino Sans","Yu Gothic Medium",Meiryo,sans-serif; }}
.wrap {{ max-width:900px; margin:0 auto; padding:1.5rem 1rem 5rem; }}
h1 {{ font-family:"Hiragino Mincho ProN","Yu Mincho",serif; font-size:1.5rem; color:var(--ink-strong); margin:0 0 .3rem; }}
h2 {{ font-family:"Hiragino Mincho ProN","Yu Mincho",serif; font-size:1.15rem; color:var(--ink-strong);
  margin:2.2rem 0 .6rem; }}
.stamp {{ font-size:.8rem; color:var(--ink-muted); }}
.headline {{ border-left:4px solid var(--accent); background:var(--accent-soft); padding:.9rem 1.1rem;
  border-radius:0 6px 6px 0; margin:1.2rem 0; font-size:1.05rem; }}
.headline.bad {{ border-left-color:var(--red); background:var(--red-soft); }}
.headline.warn {{ border-left-color:var(--amber); background:var(--amber-soft); }}
.tablewrap {{ overflow-x:auto; background:var(--surface); border:1px solid var(--line); border-radius:8px; }}
table {{ border-collapse:collapse; width:100%; font-size:.9rem; }}
td {{ border-top:1px solid var(--line); padding:.6rem .7rem; vertical-align:top; }}
tr:first-child td {{ border-top:none; }}
.mark {{ width:1.6rem; font-size:1.05rem; }}
.nm {{ color:var(--ink-strong); }}
.nm a {{ color:var(--ink-strong); }}
.sub {{ font-size:.8rem; color:var(--ink-muted); }}
.when {{ white-space:nowrap; color:var(--ink-muted); font-size:.85rem; }}
.detail {{ font-size:.85rem; }}
tr.bad .detail {{ color:var(--red); }}
tr.warn .detail {{ color:var(--amber); }}
tr.unknown .detail {{ color:var(--ink-muted); }}
.none {{ color:var(--ink-muted); text-align:center; }}
.trend {{ background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:.8rem 1rem; }}
.legend {{ font-size:.8rem; color:var(--ink-muted); margin:.2rem 0 .6rem; }}
.chip {{ display:inline-block; width:14px; height:4px; border-radius:2px; vertical-align:middle; margin-right:.35rem; }}
.chip.avg {{ background:var(--chart-avg); }} .chip.max {{ background:var(--chart-max); }}
.chip.warnline {{ background:var(--amber); }}
details {{ margin-top:.6rem; font-size:.85rem; }} summary {{ cursor:pointer; color:var(--ink-muted); }}
ul {{ padding-left:1.2rem; }} li {{ margin:.3rem 0; font-size:.92rem; }}
footer {{ margin-top:3rem; font-size:.8rem; color:var(--ink-muted); border-top:1px solid var(--line); padding-top:1rem; }}
a {{ color:var(--accent); }}
</style></head><body><div class="wrap">

<h1>Mind Inbox — いまの状況</h1>
<div class="stamp">{now} 時点。GitHub の実データから毎回作り直しています（手で書いている欄はありません）。</div>

<div class="headline {head_class}">{headline}</div>

<h2>いま壊れているもの</h2>
<div class="tablewrap"><table>{broken_rows}</table></div>

<h2>自動で動くもの (CI)</h2>
<div class="tablewrap"><table>{wf_rows}</table></div>

<h2>無人で回るもの (Routine)</h2>
<div class="tablewrap"><table>{rt_rows}</table></div>

<h2>UX トレンド (直近 2 週間)</h2>
<div class="trend">
<div class="sub">毎朝の実環境プローブ (相談 4 往復) の send→応答表示レイテンシ。<strong>上がるほど悪い</strong>。
蓄積は <code>data/ux-observations</code> ブランチ (ADR 0041)。</div>
{ux_trend}
</div>

<h2>あなたの番</h2>
<ul>{pending}</ul>

<footer>
<p><strong>❓ は「動いていない」ではなく「生きているか確かめる方法が無い」です。</strong>
動いたときに痕跡を残さない自動化は、沈黙と正常が同じ見え方になります。
❓ を消すには、その自動化に「動いたら痕跡を 1 つ残す」を足します。</p>
<p>定義: <code>cicd/scripts/status-page/watchers.json</code> ／
組み立て: <code>cicd/scripts/status-page/build.py</code> ／
運用: <a href="https://github.com/yomote/mind-inbox/blob/main/docs/runbooks/status-page.md">docs/runbooks/status-page.md</a></p>
</footer>
</div></body></html>
"""


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(build(pathlib.Path(sys.argv[1])))
