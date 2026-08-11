"""プロダクトの現在地 — PO が「できているもの / 進行中 / 次」を見て指させる場所。

なぜあるか (2026-08-11 の PO 要望 / Issue #280):
    状況ページは「自動化の生死」は見えるが、プロダクトがどこまで進んだかは
    見えなかった。「いつでも開けば、できているもの / 進行中 / 次にやることが
    見えて、指させる場所」をページ冒頭に足す。

規律 (ページ全体と同じ):
    - **状態を持たない** — milestone / deploy run / PR / Issue という GitHub の
      実データから毎回組み立て直す。手で書く欄はゼロ
    - **取れなかったものを「異常なし」と書かない** — 取得に失敗した欄は
      「(未検証: 理由)」として出す。分類できなかった PR も黙って片方に混ぜない
"""

import html
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
WEEKDAY_JA = "月火水木金土日"

# 「プロダクト」= アプリ本体を触る変更。それ以外は開発体制 (=「工場」) の変更
PRODUCT_PREFIX = "apps/"

# 「次の候補」の並べ替え指示の宛先 (このセクションを導入した Issue)
PLAN_ISSUE = 280

GATE_MARK = {"success": "🟢", "failure": "🔴", "error": "🔴", "pending": "🟡"}


def _fmt_jst(iso: str | None) -> str:
    if not iso:
        return "—"
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(JST)
    return dt.strftime("%m-%d %H:%M")


def _fmt_due(iso: str | None) -> str:
    if not iso:
        return ""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(JST)
    return f"{dt.strftime('%m-%d')} ({WEEKDAY_JA[dt.weekday()]}) まで"


# --- 判定 (純関数。テストはここを直接叩く) -------------------------------


def parse_steps(steps: object) -> dict | None:
    """run の steps 一覧 → {"deployed", "attempted", "torn_down"}。

    deployed  = デプロイ処理が **success で完走**した (= dev にこれが載る)
    attempted = **デプロイ経路に入った** (marker が success)。marker より後の
                どこで落ちても True になるので、「試みたが失敗した」を取りこぼさない。
                marker の無い過去の run 向けに DEPLOY_STEP が走った (success /
                failure) 場合も True にする (後方互換のフォールバック)
    torn_down = 撤収 (down) が完走した
    """
    if not isinstance(steps, list):
        return None
    by_name: dict = {}
    for s in steps:
        # 同名ステップが複数あるときは最初の 1 つ (deploy.yml は同名を持たない)
        if isinstance(s, dict) and s.get("n") not in by_name:
            by_name[s.get("n")] = s.get("c")
    deploy_ran = by_name.get(DEPLOY_STEP) in ("success", "failure")
    return {
        "deployed": by_name.get(DEPLOY_STEP) == "success",
        "attempted": by_name.get(MARKER_STEP) == "success" or deploy_ran,
        "torn_down": by_name.get(TEARDOWN_STEP) == "success",
    }


def pick_milestone(milestones: object) -> dict | None:
    """open milestone から「期限が直近」の 1 件を選ぶ。期限なしは期限ありの後ろ。"""
    if not isinstance(milestones, list):
        return None
    ok = [m for m in milestones if isinstance(m, dict) and m.get("n") is not None]
    with_due = [m for m in ok if m.get("due")]
    if with_due:
        return min(with_due, key=lambda m: m["due"])
    return ok[0] if ok else None


# deploy.yml のステップ名 (実デプロイ / 撤収 / 経路突入の痕跡)。
# **名前を変えたらここも追随すること** (deploy.yml 側にもその旨のコメントがある)。
DEPLOY_STEP = "Provision + deploy (up)"
TEARDOWN_STEP = "Tear down (cleanup-env)"
# guard を通過して「デプロイ経路に入った」ことだけを記録する no-op ステップ。
# Azure login や IMAGE_TAG 解決で落ちると DEPLOY_STEP は skipped になるため、
# DEPLOY_STEP だけでは「試みたが失敗した」と「そもそも走らなかった」が区別できない
# (PR #281 Codex P2-b)。marker は失敗し得る処理より**前**に置いてある。
MARKER_STEP = "デプロイ経路に入った (marker)"
# 実デプロイの痕跡を steps で確認する run の上限 (1 run = 1 API 呼び出し)。
# 自動デプロイ未解禁などで skip が延々続く場合に API を叩き続けないための箍。
# 「今 dev に載っている commit」を探す成功 run 側 (ANCHOR) と、「直近のデプロイが
# 通ったか」を見る push run 側 (OK) で別枠にしてある — 同じ枠にすると、赤が
# 連続したときに ok の判定だけで予算を使い切り、載っている commit を見失う
# (実データで deploy が 13 連敗していた 2026-08-11 がまさにその形)。
MAX_STEP_CHECKS = 6
MAX_OK_CHECKS = 3


def dev_state(runs: object, deploy_issue: int | None = None, run_steps=None) -> dict:
    """deploy.yml の run 履歴から「dev がどの commit の状態か」を判定する。

    runs: 新しい順の [{id, c: conclusion, s: status, t: created_at, sha, e: event, u}]。
    run_steps: run dict → {"deployed", "attempted", "torn_down"} | None (取得失敗)。
      deployed  = デプロイ処理が**完走**した / attempted = デプロイ処理が**走った**
      (失敗も含む)。attempted が無い辞書では deployed で代用する。

    conclusion == success を信用しない理由 (PR #281 Codex P2):
        deploy.yml には **run が success でも実デプロイが走っていない経路** がある —
        push の guard skip (OIDC 未設定 / AUTO_DEPLOY_ENABLED 未設定) と、
        workflow_dispatch の down (撤収)。conclusion をそのまま読むと 2 通りに嘘をつく:
        (a) dev が古いまま / 撤収済みでも「最新の main が反映済み」と出る、
        (b) **デプロイ失敗の次の push が guard skip で緑になると赤警告が消える**
        (直前の失敗と ci-failure Issue が隠れる)。そこで run の jobs API から
        「Provision + deploy (up)」/「Tear down (cleanup-env)」の痕跡を見て、
        **今 dev に載っている commit (deployed)** と
        **直近にデプロイを試みた push の結果 (attempted)** を別々に決める。

    判定の限界 (API から取れる範囲の最善):
      - workflow_dispatch の inputs (up/down) は runs API に出ない → steps で識別する
      - run_steps 未指定 (None) のときは「push = デプロイを試みた / push の成功 run =
        デプロイ完走」と近似し、workflow_dispatch はどちらにも数えない (最小の近似)
      - run が failure でも provision までは完走している (smoke / golden-path で
        落ちた) 場合、dev の実体は更新されている可能性があるが、「実環境で通る
        ところまで確かめられた状態」を dev の状態と定義し、成功 run だけを
        反映済みとみなす (保守側に倒す)
      - 痕跡を確認できないまま予算 (MAX_*_CHECKS) を使い切ったら、
        **緑とも赤とも断定しない** (ok=None / anchor=None = 未検証側に倒す)。
        ただし **赤の保持だけは例外** — 失敗した push があり、それより新しい
        「デプロイできた痕跡」が無いなら ok=False のままにする (下の (3))。
        隠す側 (緑・無表示) には倒さない

    返り値:
      fetched       run 履歴が読めたか (False なら以降のキーは無い)
      ok            **直近にデプロイを試みた push** が緑か (判定できなければ None)
      has_push      完了した push run が 1 本でもあるか (ok=None の理由の区別用)
      last_success  実デプロイが完走した最後の run | None
      down          last_success より新しい撤収 (down) run | None
      verify_failed 実デプロイの痕跡確認 (jobs API) に失敗したか
      behind        last_success より後に積まれた push run の数 (= 未反映マージ数)
      issue         deploy の ci-failure Issue 番号 (open のものがあれば)
    """
    if not isinstance(runs, list):
        return {"fetched": False}
    done = [r for r in runs if isinstance(r, dict) and r.get("s") == "completed"]
    pushes = [r for r in done if r.get("e") == "push"]

    def _default_steps(r: dict) -> dict:
        return {
            "deployed": r.get("e") == "push" and r.get("c") == "success",
            "attempted": r.get("e") == "push",
            "torn_down": False,
        }

    check = run_steps or _default_steps
    cache: dict = {}
    verify_failed = False

    def info_for(r: dict):
        """1 run につき jobs API は 1 回だけ (ok 判定と anchor 探索で共有する)。"""
        nonlocal verify_failed
        key = r.get("id") if r.get("id") is not None else id(r)
        if key not in cache:
            got = check(r)
            if not isinstance(got, dict):
                verify_failed = True
                got = None
            cache[key] = got
        return cache[key]

    # (1) 直近に**デプロイを試みた** push の結果 = ok。guard skip の緑は読み飛ばす —
    # 飛ばさないと、失敗の次の push が skip されただけで赤警告が消える
    ok, ok_run = None, None
    for r in pushes[:MAX_OK_CHECKS]:
        info = info_for(r)
        if info is None:
            break
        if info.get("attempted", info.get("deployed")):
            ok, ok_run = r.get("c") == "success", r
            break

    # (2) 今 dev に載っている commit = 実デプロイが完走した最後の成功 run
    anchor, down = None, None
    checked = 0
    for r in done:
        if r.get("c") != "success":
            continue
        if checked >= MAX_STEP_CHECKS:
            # 打ち切り。「痕跡が見つからない」側に倒す (反映済みとは書かない)
            break
        checked += 1
        info = info_for(r)
        if info is None:
            break
        if info.get("torn_down"):
            down = r
            break
        if info.get("deployed"):
            anchor = r
            break
    # (3) 赤の保持 (PR #281 Codex P2-a)。guard skip の成功 push が予算 (MAX_OK_CHECKS)
    # より多く続くと (1) が届かず ok=None になり、⚠️ と ci-failure Issue が消える。
    # **jobs API を追加で叩かず**、取得済みの run だけで保守側に倒す:
    #   「失敗した push があり、それより新しい『デプロイできた痕跡』が無いなら赤のまま」
    # 痕跡 = ok を決めた成功 run / 実デプロイが完走した run (anchor)。
    if ok is not False:
        failed_push = next((r for r in pushes if r.get("c") == "failure"), None)
        if failed_push is not None:
            good_times = [
                r.get("t") or ""
                for r in (ok_run if ok else None, anchor)
                if isinstance(r, dict)
            ]
            newest_good = max(good_times) if good_times else ""
            # ISO 8601 (Z 固定) は文字列比較で時刻順になる
            if (failed_push.get("t") or "") > newest_good:
                ok = False

    behind = 0
    if anchor is not None:
        # ISO 8601 (Z 固定) は文字列比較で時刻順になる
        behind = sum(
            1
            for r in runs
            if isinstance(r, dict)
            and r.get("e") == "push"
            and (r.get("t") or "") > (anchor.get("t") or "")
        )
    return {
        "fetched": True,
        "ok": ok,
        "has_push": bool(pushes),
        "last_success": anchor,
        "down": down,
        "verify_failed": verify_failed,
        "behind": behind,
        "issue": deploy_issue,
    }


def classify_prs(prs: list) -> tuple[list, list, list]:
    """open PR を「プロダクト (apps/ を触る)」と「工場 (それ以外)」に分ける。

    変更ファイルが取れなかった PR はどちらにも黙って混ぜず、第 3 の
    「未分類」として返す (取得失敗を「工場の変更」と読ませないため)。
    """
    product, factory, unknown = [], [], []
    for p in prs or []:
        files = p.get("files")
        if not isinstance(files, list):
            unknown.append(p)
        elif any(isinstance(f, str) and f.startswith(PRODUCT_PREFIX) for f in files):
            product.append(p)
        else:
            factory.append(p)
    return product, factory, unknown


def gate_mark(got: object) -> str:
    """commit status (context=review-gate) の生データを表示マークにする。"""
    if not isinstance(got, dict):
        return "❓ (未検証)"  # 取得そのものに失敗 — 「まだ無い」とは別物
    return GATE_MARK.get(got.get("s"), "❓ (未評価)")


def next_candidates(issues: object, limit: int = 5) -> list | None:
    """P1 の open Issue から「次の候補」を選ぶ。

    ci 系ラベル (ci-failure 等) が付いたものは障害対応であって
    プロダクトの「次」ではないので除く。作成日昇順 = 古い約束から。
    """
    if not isinstance(issues, list):
        return None
    out = [
        i
        for i in issues
        if isinstance(i, dict)
        and not i.get("pr")
        and not any(
            isinstance(name, str) and name.lower().startswith("ci")
            for name in (i.get("labels") or [])
        )
    ]
    out.sort(key=lambda i: i.get("c") or "")
    return out[:limit]


# --- 収集 ---------------------------------------------------------------


def collect(gh, gh_lines, gh_objects) -> dict:
    """GitHub の実データを取る。個々の取得失敗は None のまま返し、描画側が
    「(未検証: …)」にする — 1 箇所の失敗でページごと落とさない。

    gh: build.gh (単発 JSON)。gh_lines / gh_objects: build の `--paginate` 版
    (1 行 1 値 / 1 行 1 オブジェクト)。`--paginate` + `--jq` は**ページごとに**
    jq を適用するため、`[...]` で包むと 2 ページ目以降が壊れる — 一覧 API は
    必ず paginate 版で取ること (build.gh_objects の docstring 参照)。"""
    milestones = gh(
        "api",
        "repos/{owner}/{repo}/milestones?state=open&per_page=20",
        "--jq",
        "[.[] | {n: .number, t: .title, due: .due_on, u: .html_url}]",
    )
    goal = pick_milestone(milestones)
    goal_items = None
    if goal:
        goal_items = gh(
            "api",
            f"repos/{{owner}}/{{repo}}/issues?milestone={goal['n']}&state=all&per_page=100",
            "--jq",
            "[.[] | {n: .number, t: .title, s: .state, pr: (.pull_request != null)}]",
        )

    runs = gh(
        "api",
        "repos/{owner}/{repo}/actions/workflows/deploy.yml/runs?branch=main&per_page=30",
        "--jq",
        "[.workflow_runs[] | {id: .id, c: .conclusion, s: .status, t: .created_at,"
        " sha: .head_sha, e: .event, u: .html_url}]",
    )

    def _run_steps(run: dict) -> dict | None:
        """run の jobs API から steps を取り、`parse_steps` で痕跡に畳む。

        deploy.yml は run success でも実デプロイが走らない経路 (guard skip / down)
        を持つため、conclusion では判定できない (dev_state の docstring 参照)。
        """
        rid = run.get("id")
        if rid is None:
            return None
        got = gh(
            "api",
            f"repos/{{owner}}/{{repo}}/actions/runs/{rid}/jobs?per_page=30",
            "--jq",
            "{steps: [.jobs[].steps[]? | {n: .name, c: .conclusion}]}",
        )
        if not isinstance(got, dict):
            return None
        return parse_steps(got.get("steps"))

    ci_fail = gh(
        "api",
        "repos/{owner}/{repo}/issues?state=open&labels=ci-failure&per_page=50",
        "--jq",
        "[.[] | {n: .number, t: .title}]",
    )
    deploy_issue = None
    if isinstance(ci_fail, list):
        deploy_issue = next(
            (
                i["n"]
                for i in ci_fail
                if isinstance(i, dict) and "deploy" in (i.get("t") or "")
            ),
            None,
        )

    # 一覧そのものも全ページ取る — 50 件で切ると 51 件目以降が分類以前に
    # 「進行中」から静かに消える (PR #281 Codex P2)
    prs = gh_objects(
        "api",
        "--paginate",
        "repos/{owner}/{repo}/pulls?state=open&base=main&per_page=100",
        "--jq",
        ".[] | {n: .number, t: .title, sha: .head.sha, draft: .draft}",
    )
    if isinstance(prs, list):
        for p in prs:
            # files は 100 件を超えうる (1 ページ目だけ見ると apps/ が 101 件目
            # 以降のとき「工場」に静かに誤分類される — PR #281 Codex P2)。
            # --paginate + 1 行 1 値で全ページを結合する
            p["files"] = gh_lines(
                "api",
                "--paginate",
                f"repos/{{owner}}/{{repo}}/pulls/{p['n']}/files?per_page=100",
                "--jq",
                ".[].filename",
            )
            p["gate"] = gate_mark(
                gh(
                    "api",
                    f"repos/{{owner}}/{{repo}}/commits/{p['sha']}/status",
                    "--jq",
                    '{s: ([.statuses[] | select(.context == "review-gate")'
                    " | .state] | first)}",
                )
            )

    p1 = gh(
        "api",
        "repos/{owner}/{repo}/issues?state=open&labels=P1&per_page=100",
        "--jq",
        "[.[] | {n: .number, t: .title, c: .created_at,"
        " labels: [.labels[].name], pr: (.pull_request != null)}]",
    )

    return {
        "milestones": milestones,
        "goal": goal,
        "goal_items": goal_items,
        "dev": dev_state(runs, deploy_issue, _run_steps),
        "prs": prs,
        "p1": p1,
    }


# --- 描画 ---------------------------------------------------------------

_ISSUE_URL = "https://github.com/yomote/mind-inbox/issues"
_PULL_URL = "https://github.com/yomote/mind-inbox/pull"


def _issue_link(n: int) -> str:
    return f'<a href="{_ISSUE_URL}/{n}">#{n}</a>'


def _pr_link(n: int) -> str:
    return f'<a href="{_PULL_URL}/{n}">#{n}</a>'


def _goal_html(data: dict) -> str:
    if not isinstance(data.get("milestones"), list):
        return "<p>(未検証: milestone を取得できませんでした)</p>"
    goal = data.get("goal")
    if not goal:
        return "<p>未設定 — open な milestone がありません (milestone を切るとここに出ます)</p>"
    title = html.escape(goal.get("t") or "")
    if goal.get("u"):
        title = f'<a href="{goal["u"]}">{title}</a>'
    due = html.escape(_fmt_due(goal.get("due")))
    head = f"<p><strong>{title}</strong>"
    if due:
        head += f' <span class="sub">{due}</span>'
    head += "</p>"
    items = data.get("goal_items")
    if not isinstance(items, list):
        return head + "<p>(未検証: milestone の Issue を取得できませんでした)</p>"
    if not items:
        return head + '<p class="sub">紐づく Issue がまだありません</p>'
    lis = []
    closed = 0
    for i in items:
        done = i.get("s") == "closed"
        closed += done
        link = _pr_link(i["n"]) if i.get("pr") else _issue_link(i["n"])
        lis.append(
            f"<li>{'✅' if done else '⬜'} {link} {html.escape(i.get('t') or '')}</li>"
        )
    return (
        head
        + f'<p class="sub">{closed}/{len(items)} 件消化</p><ul>'
        + "\n".join(lis)
        + "</ul>"
    )


def _dev_html(dev: dict) -> str:
    if not dev.get("fetched"):
        return "<p>(未検証: deploy の run 履歴を取得できませんでした)</p>"
    if dev.get("down") is not None:
        # 撤収 (down) が最後のデプロイより新しい — 「反映済み」と読ませない
        return (
            f'<p class="devwarn">⚠️ <strong>dev は撤収されています</strong>'
            f" ({_fmt_jst(dev['down'].get('t'))} JST の down 実行以降、環境がありません)</p>"
        )
    if dev.get("verify_failed"):
        return "<p>(未検証: 実デプロイの痕跡 (run の jobs) を取得できませんでした)</p>"
    last = dev.get("last_success")
    behind = dev.get("behind", 0)
    issue = dev.get("issue")
    state = None
    if last is not None:
        tail = (
            f" (以降 {behind} 本のマージが未反映)"
            if behind
            else " (最新の main が反映済み)"
        )
        state = (
            f"<p>dev は {_fmt_jst(last['t'])} JST の commit "
            f"<code>{html.escape((last.get('sha') or '')[:7])}</code> の状態{tail}</p>"
        )
    # 「実デプロイの痕跡が無い」= guard skip (自動デプロイ未解禁 / OIDC 未設定) が
    # 続いている。緑とも赤とも書かない
    no_trace = (
        "<p>直近の push に実デプロイの痕跡がありません"
        " (自動デプロイ未解禁 / guard skip の可能性 — dev は古いままかもしれません)</p>"
    )
    if dev.get("ok") is False:
        since = _fmt_jst(last["t"]) + " JST" if last else "—"
        ref = f" — {_issue_link(issue)}" if issue else ""
        warn = (
            f'<p class="devwarn">⚠️ <strong>{since} から更新が届いていない</strong>'
            f" (deploy 赤{ref})</p>"
        )
        return warn + (state or "<p>実デプロイが完走した run が見つかりません</p>")
    if dev.get("ok") is None:
        if not dev.get("has_push"):
            return "<p>完了した deploy (main への push) がまだありません</p>"
        return no_trace + (state or "")
    return state or no_trace


def _pr_items(prs: list, note: str = "") -> str:
    if not prs:
        return '<li class="sub">ありません</li>'
    out = []
    for p in prs:
        draft = " (draft)" if p.get("draft") else ""
        gate = p.get("gate") or "❓ (未検証)"
        out.append(
            f"<li>{gate} {_pr_link(p['n'])} {html.escape(p.get('t') or '')}{draft}{note}</li>"
        )
    return "\n".join(out)


def _wip_html(prs: object, all_prs: object = None) -> str:
    if not isinstance(prs, list):
        return "<p>(未検証: open PR を取得できませんでした)</p>"
    product, factory, unknown = classify_prs(prs)
    cols = (
        '<div class="cols"><div><h4>プロダクト (apps/ を触る)</h4><ul>'
        + _pr_items(product)
        + "</ul></div><div><h4>工場 (開発体制)</h4><ul>"
        + _pr_items(factory)
        + "</ul></div></div>"
    )
    if unknown:
        cols += "<ul>" + _pr_items(unknown, " (未検証: 変更ファイル不明)") + "</ul>"
    # main 向け以外 (リリース PR 等) を黙って落とさない。全 PR 一覧の取得だけが
    # 失敗した場合も「main 向け以外は無い」と読ませない (PR #281 Codex P2)
    if isinstance(all_prs, list):
        seen = {p.get("n") for p in prs}
        others = [p for p in all_prs if p.get("n") not in seen]
        if others:
            links = "、".join(
                f"{_pr_link(p['n'])} {html.escape(p.get('t') or '')}" for p in others
            )
            cols += f'<p class="sub">main 向け以外の open PR: {links}</p>'
    else:
        cols += (
            '<p class="sub">(未検証: 全 open PR の一覧を取得できませんでした —'
            " main 向け以外の PR の有無は不明)</p>"
        )
    return cols


def _your_turn_html(pend: dict) -> str:
    parts = []
    if not isinstance(pend.get("needs_human"), list):
        parts.append("<li>(未検証: needs-human の Issue を取得できませんでした)</li>")
    else:
        for i in pend["needs_human"]:
            parts.append(f"<li>{_issue_link(i['n'])} {html.escape(i['t'])}</li>")
    for a in pend.get("proposed") or []:
        parts.append(f"<li>ADR 未裁定: {html.escape(a['title'])}</li>")
    return "<ul>" + ("\n".join(parts) or '<li class="sub">ありません</li>') + "</ul>"


def _next_html(p1: object) -> str:
    picked = next_candidates(p1)
    if picked is None:
        return "<p>(未検証: P1 の Issue を取得できませんでした)</p>"
    if not picked:
        return '<p class="sub">ありません (P1 ラベルの open Issue が空です)</p>'
    lis = [
        f"<li>{_issue_link(i['n'])} {html.escape(i.get('t') or '')}</li>"
        for i in picked
    ]
    return "<ul>" + "\n".join(lis) + "</ul>"


def render(data: dict, pend: dict) -> str:
    """「プロダクトの現在地」セクションの HTML 断片を返す。"""
    return f"""
<h2>プロダクトの現在地</h2>
<div class="product">
<h3>今週の目標</h3>
{_goal_html(data)}
<h3>いま dev で触れるもの</h3>
{_dev_html(data.get("dev") or {"fetched": False})}
<h3>進行中</h3>
{_wip_html(data.get("prs"), pend.get("prs"))}
<h3>🙋 あなたの番</h3>
{_your_turn_html(pend)}
<h3>次の候補 <span class="note">PM の優先案 — 並べ替えの指示は {_issue_link(PLAN_ISSUE)} へ</span></h3>
{_next_html(data.get("p1"))}
</div>
"""
