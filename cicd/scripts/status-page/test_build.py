"""[L1] 状況ページのビルダー — 「取れなかった」と「痕跡が無い」を取り違えない。

無いと何が静かに通るか:
    `gh --jq` はスカラー文字列をクォート無しで返す (jq -r と同じ)。これを JSON として
    読もうとすると必ず失敗し、**痕跡があるのに「取得できませんでした」**になる。
    初回 run で実際に 3 行がこれで潰れた。見た目は「❓ が並んでいる」だけなので、
    テストが無いと静かに再発する。
"""

import json
import os
import pathlib
import stat
import subprocess
import sys

BUILD = pathlib.Path(__file__).with_name("build.py")

# gh の出力を模した stub。--jq の結果が
#   - 配列/オブジェクト → JSON として出す
#   - スカラー文字列    → **クォート無し**で出す (ここが本物の gh の挙動)
STUB = r"""#!/usr/bin/env python3
import sys, json
args = sys.argv[1:]
# --paginate が入ると位置引数がずれる (gh api --paginate <url>)
positional = [a for a in args if not a.startswith("-")]
url = positional[1] if len(positional) > 1 else ""
jqf = args[args.index("--jq") + 1].strip() if "--jq" in args else ""

# ここが本物の gh の肝: --jq の結果が配列/オブジェクトなら JSON、
# **スカラー文字列ならクォート無しの生テキスト** で出す (jq -r と同じ)。
# 呼び出し側が `| max` を裸で渡すと json.loads が必ず落ちる。
def emit(value):
    if jqf.startswith(("[", "{")):
        print(json.dumps(value))
    else:
        print(value if isinstance(value, str) else json.dumps(value))

# プロダクトの現在地 (product_status.py / Issue #280) が読む口。
# ここに列挙が無い形が返ると「(未検証: …)」がページに出て、
# test_L1_痕跡が取れたら未検証にしない が落ちる — わざとそうしてある
# (新しい取得口を足したら stub にも足す、を強制する)。
if "/milestones?" in url:
    emit([{"n": 1, "t": "今週: テスト目標", "due": "2099-01-08T00:00:00Z",
           "u": "https://example.test/milestone/1"}])
elif "milestone=" in url:
    emit([{"n": 187, "t": "目標に紐づく Issue", "s": "open", "pr": False}])
elif "labels=ci-failure" in url:
    emit([])
elif "labels=P1" in url:
    emit([{"n": 42, "t": "次の候補になる Issue", "c": "2099-01-01T00:00:00Z",
           "labels": ["P1"], "pr": False}])
elif "/jobs" in url:
    # 実デプロイの痕跡 (deploy.yml の Provision ステップ完走) — /runs より先に
    # 引っ掛けること (URL は .../actions/runs/<id>/jobs で両方に部分一致する)
    emit({"steps": [{"n": "Provision + deploy (up)", "c": "success"}]})
elif "/files" in url:
    # --paginate + 1 行 1 値 (jq .[].filename) — 配列ではなく行で返す
    print("apps/frontend/src/App.tsx")
    print("docs/x.md")
elif "/runs" in url:
    # event=schedule で絞った照会にだけ失敗 run を返す — 「イベント種別を絞らない
    # と、schedule の連続失敗が直後の PR run の成功に隠れる」(#258) を再現する。
    # フィルタ無しの照会 (従来の watcher) は success のまま。
    if "event=schedule" in url:
        emit([{"id": 2, "c": "failure", "s": "completed", "t": "2099-01-01T00:00:00Z",
               "sha": "abc1234def5678", "e": "schedule",
               "u": "https://example.test/sweep-run"}])
    else:
        emit([{"id": 1, "c": "success", "s": "completed", "t": "2099-01-01T00:00:00Z",
               "sha": "abc1234def5678", "e": "push", "u": "https://example.test/run"}])
elif "pulls" in url:
    # 一覧は --paginate で 1 行 1 オブジェクト。付いていなければ 1 ページ目しか
    # 見ていないので、stub 側で落として気づけるようにする
    if "--paginate" not in args:
        print("stub: PR 一覧を --paginate 無しで取っている", file=sys.stderr)
        sys.exit(1)
    if "base=main" in url:
        # 51 件 — 1 ページ (50) で切れていないことを確かめるため
        for i in range(1, 52):
            print(json.dumps({"n": i, "t": f"PR {i}", "sha": f"sha{i}", "draft": False}))
    else:
        # 全 open PR は 52 件 (#52 だけ main 向けではない = 脚注に出る側)
        for i in range(1, 53):
            print(json.dumps({"n": i, "t": f"PR {i}", "d": "2099-01-01T00:00:00Z"}))
elif "needs-human" in url:
    emit([])
elif "labels=debt-review" in url:
    # 週次 AI 負債審査 (labeled_issue_comment) の台帳 Issue 一覧。
    # jq `.[].number` はスカラー行で返る (行が URL に入るので数字であること)
    print("77")
elif "/comments" in url or "labels=" in url or "select(.title" in jqf:
    emit({"t": "2099-01-01T00:00:00Z"} if jqf.startswith("{") else "2099-01-01T00:00:00Z")
else:
    emit("null")
"""


def _run(
    tmp_path: pathlib.Path,
    defs: dict | None = None,
    ux_data: dict[str, list[dict]] | None = None,
    stub: str | None = None,
) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(stub or STUB)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)

    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    env.pop("UX_DATA_DIR", None)  # 外の環境に依存させない
    if defs is not None:
        # 性質のテストは **その時点の watchers.json に依存させない**。
        # 定義から 1 行消しただけでテストが落ちるのは、守りたい性質がずれている印。
        dpath = tmp_path / "watchers.json"
        dpath.write_text(json.dumps(defs, ensure_ascii=False))
        env["STATUS_PAGE_WATCHERS"] = str(dpath)
    if ux_data is not None:
        # データブランチ checkout の模倣 (probes/ evals/ の月別 JSONL — ADR 0041)
        root = tmp_path / "uxdata"
        for sub, items in ux_data.items():
            (root / sub).mkdir(parents=True, exist_ok=True)
            (root / sub / "2099-01.jsonl").write_text(
                "".join(json.dumps(o, ensure_ascii=False) + "\n" for o in items)
            )
        env["UX_DATA_DIR"] = str(root)
    out = tmp_path / "out"
    subprocess.run(
        [sys.executable, str(BUILD), str(out)],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return (out / "index.html").read_text()


def _mech(recorded_at: str, avg: int = 4100, mx: int = 9162, warn: int = 0) -> dict:
    return {
        "kind": "ux-eval-mech",
        "recordedAt": recorded_at,
        "metrics": {
            "latency": {"sendToReplyVisibleMs": {"avgMs": avg, "maxMs": mx}},
            "warnings": {"latency": warn, "functional": 0, "other": 0},
            "thresholds": {"warnReplyVisibleMs": 10000},
        },
    }


FRESH_UX_DATA = {
    "probes": [
        {
            "kind": "ux-probe-record",
            "recordedAt": "2099-01-01T00:00:00Z",
            "probeId": "p1",
            "record": {},
        }
    ],
    "evals": [
        _mech("2099-01-01T01:00:00Z", warn=1),
        {
            "kind": "ux-judge-score",
            "recordedAt": "2099-01-01T02:00:00Z",
            "total": 11,
            "max": 12,
            "verdict": "green",
        },
    ],
}


def test_L1_痕跡が取れたら未検証にしない(tmp_path):
    html = _run(tmp_path, ux_data=FRESH_UX_DATA)
    assert "取得できませんでした" not in html, (
        "gh がスカラー文字列をクォート無しで返す件で、痕跡があるのに未検証になっている"
    )


def test_L1_取得できたものは緑になる(tmp_path):
    html = _run(tmp_path, ux_data=FRESH_UX_DATA)
    assert "🟢" in html


def test_L1_UXデータが取れないときは未検証として出す(tmp_path):
    """データブランチが fetch できなかった run で、トレンドと trace を
    「異常なし」に見せないこと (取れなかった ≠ 観測ゼロ)。"""
    html = _run(tmp_path, ux_data=None)  # UX_DATA_DIR なし = fetch 失敗と同じ
    assert "未検証" in html
    assert "data/ux-observations" in html


def test_L1_UXトレンドはSVGとデータ表になる(tmp_path):
    html = _run(tmp_path, ux_data=FRESH_UX_DATA)
    assert "<svg" in html
    assert "9162" in html  # max がデータ表に出る
    assert "11/12" in html  # LLM 採点の行
    assert "🟢" in html


def test_L1_traceはkindで絞って判定する(tmp_path):
    """機械計測 (ux-eval-mech) だけ動いて LLM 採点 (ux-judge-score) が止まった状態を
    検出できること。

    無いと何が静かに通るか:
        Issue コメント時代の trace は kind を区別せず、機械計測が毎朝積まれる限り
        LLM 採点が何日止まっても緑に見えた (ADR 0037 Negative Consequences)。
        kind フィルタが壊れると同じ穴が静かに戻る。
    """
    html = _run(
        tmp_path,
        defs={
            "workflows": [],
            "routines": [
                {
                    "name": "採点の痕跡",
                    "what": "x",
                    "trace": {"kind": "data_branch", "record_kind": "ux-judge-score"},
                    "expect_hours": 50,
                },
            ],
        },
        ux_data={"evals": [_mech("2099-01-01T01:00:00Z")]},
    )  # 機械計測しか無い
    assert "痕跡が 1 件もありません" in html
    assert "🔴" in html


def test_L1_痕跡を残さない自動化は判定不能のまま残る(tmp_path):
    """「異常時しか喋らない」ものを緑に見せないこと。

    無いと何が静かに通るか:
        沈黙を「異常なし」と表示してしまう。2026-08-10 に無人の仕組みが 4 本とも
        止まっていたのに気づけなかった原因そのもの。
    """
    html = _run(
        tmp_path,
        defs={
            "workflows": [],
            "routines": [
                {
                    "name": "異常時しか喋らない見張り",
                    "what": "赤いときだけ Issue を立てる",
                    "trace": {"kind": "issue_label", "label": "nonexistent-label"},
                    "expect_hours": 2,
                    "trace_only_on_anomaly": True,
                }
            ],
        },
    )
    # 元のテストは watchers.json の note (データ) の文字列を見ていた。
    # それだと定義を 1 行消しただけで落ちる一方、**ロジックが壊れても気づけない**。
    # 見るのは build.py の振る舞い: 痕跡を残さない watcher は緑にせず、既定の説明を出す。
    assert "❓" in html
    assert "痕跡を残さないので判定できません" in html
    assert "🟢" not in html, "痕跡が無いのに緑にしている"


def test_L1_open_PRの一覧は両方とも全ページ取得する(tmp_path):
    """「進行中」の base=main 一覧も、脚注の補完元 (pending) も paginate すること。

    無いと何が静かに通るか:
        open PR が 50 件を超えると、51 件目以降が**分類以前にページから消える**。
        しかも消えたことはページ上に何の痕跡も残さない (「無かった」に化ける)。
        stub は --paginate 無しの取得を失敗させてあるので、退行すると赤くなる。
    """
    html = _run(tmp_path, ux_data=FRESH_UX_DATA)
    assert "/pull/51" in html, "51 件目の PR が「進行中」から消えている"
    assert "/pull/52" in html, (
        "pending 側 (main 向け以外の脚注) が 1 ページ目で切れている"
    )
    assert "取得できませんでした" not in html


def test_L1_eventフィルタ付きwatcherはその種別のrunだけを見る(tmp_path):
    """review-gate は PR・コメントイベントでも起動する。イベント種別を絞らないと
    「schedule sweep (マージ再試行・補償) が毎回失敗していても、直後の PR 再評価
    run の成功で緑かつ期限内」になる (#258 / Codex P2)。

    無いと何が静かに通るか:
        『最悪 30 分でマージ』の下限保証を担う sweep の停止が状況ページで
        緑に見え続ける — 2026-08-11 に PO が滞留 PR を手動発見した状況が、
        監視を付けたのに再発する。stub は event=schedule で絞った照会にだけ
        失敗 run を返すため、build.py がフィルタを付け落とすと未絞りの成功
        run を拾って緑になり、このテストが赤くなる (mutation で検証済み)。
    """
    html = _run(tmp_path, defs={
        "workflows": [{
            "id": "review-gate.yml",
            "name": "マージの門 (review-gate)",
            "what": "sweep の死活",
            "event": "schedule",
            "expect_hours": 2,
        }],
        "routines": [],
    })
    assert "🔴" in html, "schedule run の失敗が PR run の成功に隠れている"
    assert "https://example.test/sweep-run" in html, (
        "リンク先も schedule run のもの (未絞りの run ではない)"
    )


def test_L1_eventフィルタ無しのwatcherは従来どおり全runで判定する(tmp_path):
    """後方互換: フィルタを持たない既存 watcher の判定を変えない。"""
    html = _run(tmp_path, defs={
        "workflows": [{
            "id": "deploy.yml",
            "name": "自動デプロイ",
            "what": "main が動いたら dev に配る",
        }],
        "routines": [],
    })
    assert "🟢" in html


def test_L1_定義ファイルが読める():
    defs = json.loads((pathlib.Path(__file__).with_name("watchers.json")).read_text())
    assert defs["workflows"]
    # routines は ADR 0035 D1 / 0037 で 0 本が目標状態 — 空を許す (キー自体は
    # build.py が defs["routines"] を直接読むので必須)
    assert isinstance(defs["routines"], list)
    for w in defs["workflows"]:
        wid = w.get("id", "")
        # id は `actions/workflows/{id}/runs` にそのまま入る。通るのは 2 形だけ:
        #   - 自前 workflow のファイル名 (`foo.yml`)
        #   - **数値の workflow id** — GitHub が管理する dynamic workflow
        #     (CodeQL default setup / Dependabot) は path 文字列を渡すと 404 で、
        #     数値でしか run 履歴を引けない (2026-08-14 実測 / Issue #408)
        # 無いと何が静かに通るか: `dynamic/github-code-scanning/codeql` のような
        # 「見た目は正しいが API が 404 を返す id」を書いても気づけず、その行は
        # 恒久的に「未検証」のまま表に居座る (赤ではないので見過ごされる)。
        assert wid.endswith(".yml") or wid.isdigit(), w


def test_l1_型を偽装したmech行でもトレンド描画が落ちない() -> None:
    """無いと何が静かに通るか: 蓄積に混入した非数値の avgMs 1 行で trend_svg が
    TypeError になり、status-page 全体の生成が止まり続ける (PR #260 Codex P1)。"""
    import importlib.util
    from datetime import datetime, timezone

    spec = importlib.util.spec_from_file_location("status_page_build", BUILD)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    observations = [
        {
            "kind": "ux-eval-mech",
            "recordedAt": "2026-08-10T08:20:00Z",
            "metrics": {
                "latency": {"sendToReplyVisibleMs": {"avgMs": "4100", "maxMs": None}},
                "warnings": {"latency": True},
                "thresholds": {"warnReplyVisibleMs": "10000"},
            },
        },
        {
            "kind": "ux-eval-mech",
            "recordedAt": "2026-08-11T08:20:00Z",
            "metrics": {
                "latency": {"sendToReplyVisibleMs": {"avgMs": 4100, "maxMs": 9162}},
                "warnings": {"latency": 0},
                "thresholds": {"warnReplyVisibleMs": 10000},
            },
        },
    ]
    points = build._trend_points(observations, now)
    assert points[0]["avg"] is None  # 偽装行は欠測扱い
    assert points[0]["warn"] == 0  # bool は int に数えない
    assert points[1]["avg"] == 4100.0
    svg = build.trend_svg(points, points[1]["threshold"])
    assert svg.startswith("<svg")


# 心拍を「マーカーを含むコメント」だけで数えるかを見るための gh stub。
# コメントは 2 件 — 古い当番レポート (マーカーあり) と、新しい人間のコメント
# (マーカー無し)。マーカーで絞らなければ後者で時刻が進み 🟢 になってしまう。
COMMENT_STUB = r"""#!/usr/bin/env python3
import sys, json
args = sys.argv[1:]
positional = [a for a in args if not a.startswith("-")]
url = positional[1] if len(positional) > 1 else ""
jqf = args[args.index("--jq") + 1].strip() if "--jq" in args else ""

def emit(value):
    if jqf.startswith(("[", "{")):
        print(json.dumps(value))
    else:
        print(value if isinstance(value, str) else json.dumps(value))

if "/comments" in url:
    # --paginate 無しでの取得は「1 ページ目しか見ていない」ので、心拍としては
    # 数えさせない (常設の心拍が 100 件を超えると静かに止まる退行を落とすため)
    if "--paginate" not in args:
        print("stub: --paginate 無しの取得は許さない", file=sys.stderr)
        sys.exit(1)
    marked = "contains(" in jqf
    print("2020-01-01T00:00:00Z")          # 古い当番レポート (マーカーあり)
    if not marked:
        print("2099-01-01T00:00:00Z")      # 新しい人間コメント (マーカー無し)
elif "/runs" in url:
    emit([])
elif "pulls" in url:
    pass  # open PR ゼロ (--paginate なので「行を出さない」が空一覧)
elif "needs-human" in url:
    emit([])
else:
    emit("null")
"""

_TICK_DEFS = {
    "workflows": [],
    "routines": [
        {
            "name": "当番 PM tick",
            "what": "巡回レポートを Issue に残す",
            "trace": {
                "kind": "issue_comment",
                "issue": 254,
                "body_contains": "\U0001f64b あなたの番",
            },
            "expect_hours": 16,
        }
    ],
}


def test_L1_issue_comment_の心拍はマーカー付きコメントだけを数える(tmp_path):
    """人間の新しいコメントで心拍が進まないこと。

    無いと何が静かに通るか:
        Routine が止まっていても、その Issue に人間が一言書けば時刻が進んで
        🟢 に戻る。「沈黙 = 未発火」という監視の前提そのものが崩れ、
        止まった自動化が動いているように見える。
    """
    html = _run(tmp_path, defs=_TICK_DEFS, stub=COMMENT_STUB)
    assert "🔴" in html, "マーカー無しの新しいコメントを心拍として数えている"
    assert "🟢" not in html


def test_L1_issue_comment_は全ページを見る(tmp_path):
    """`--paginate` を付けて取得すること。

    無いと何が静かに通るか:
        `?per_page=100` の 1 ページ目しか見ないため、常設の心拍はコメントが
        100 件を超えた時点で更新が止まり、**発火し続けていても恒久的に 🔴**
        になる (1 日 3 件なら約 34 日)。誤検知はページを見た人の信頼を削る。
    """
    defs = json.loads(json.dumps(_TICK_DEFS))
    defs["routines"][0]["trace"].pop("body_contains")
    html = _run(tmp_path, defs=defs, stub=COMMENT_STUB)
    # stub は --paginate が無いと失敗する (= ❓ 未検証)。取得できていれば
    # 新しい方 (2099) が拾えて 🟢 になる
    assert "🟢" in html, "--paginate を付けずに取得している (1 ページ目だけ)"


# 週次 AI 負債審査 (labeled_issue_comment / #410) 用の gh stub。
# 台帳 Issue #77 のコメントは 2 件:
#   - 古い審査結果 (マーカー **で始まる** / 2020) — これだけが心拍
#   - 新しい自動依頼 (本文中でマーカーを**引用するだけ** / 2099)
# 判定が startswith でなく contains に退行すると、毎週の自動依頼が心拍に化けて
# 新しい方 (2099) を拾い、審査が止まっていても 🟢 になる — その退行でこの stub は
# 「両方のコメント」を返すので、テストが赤くなる (mutation で検証すること)。
DEBT_REVIEW_STUB = r"""#!/usr/bin/env python3
import sys, json
args = sys.argv[1:]
positional = [a for a in args if not a.startswith("-")]
url = positional[1] if len(positional) > 1 else ""
jqf = args[args.index("--jq") + 1].strip() if "--jq" in args else ""

def emit(value):
    if jqf.startswith(("[", "{")):
        print(json.dumps(value))
    else:
        print(value if isinstance(value, str) else json.dumps(value))

if "labels=debt-review" in url:
    if "--paginate" not in args:
        print("stub: --paginate 無しの取得は許さない", file=sys.stderr)
        sys.exit(1)
    print("77")
elif "/comments" in url:
    if "--paginate" not in args:
        print("stub: --paginate 無しの取得は許さない", file=sys.stderr)
        sys.exit(1)
    marked = "startswith(" in jqf
    print("2020-01-01T00:00:00Z")          # 審査結果 (マーカーで始まる・古い)
    if not marked:
        print("2099-01-01T00:00:00Z")      # 自動依頼 (引用のみ・新しい)
elif "/runs" in url:
    emit([])
elif "pulls" in url:
    pass
elif "needs-human" in url:
    emit([])
else:
    emit("null")
"""

# /comments だけ落ちる変種 — 「取れなかった」を「痕跡なし (🔴)」に化けさせない側を見る
DEBT_REVIEW_FAIL_STUB = DEBT_REVIEW_STUB.replace(
    'marked = "startswith(" in jqf',
    'sys.exit(1)\n    marked = False',
)

_DEBT_REVIEW_DEFS = {
    "workflows": [],
    "routines": [],
    "traces": [
        {
            "name": "週次 AI 負債審査の結果",
            "what": "審査結果を台帳 Issue にマーカー付きコメントで残す",
            "trace": {
                "kind": "labeled_issue_comment",
                "label": "debt-review",
                "body_startswith": "🔍 debt-review 審査結果",
            },
            "expect_hours": 216,
        }
    ],
}


def test_L1_labeled_issue_comment_は先頭一致のコメントだけを心拍に数える(tmp_path):
    """依頼コメントによる結果マーカーの引用で心拍が進まないこと。

    無いと何が静かに通るか (#410):
        依頼 (毎週の自動投稿) は「このマーカーで始めて」と本文中で結果マーカーを
        引用する。判定が contains だと依頼自身が審査の痕跡として数えられ、
        **審査が一度も走っていなくても毎週 🟢 が更新され続ける** — 「呼ばれなかった
        週と 0 件の週が外形上同じ」という #410 が潰そうとしている穴の再発。
    """
    html = _run(tmp_path, defs=_DEBT_REVIEW_DEFS, stub=DEBT_REVIEW_STUB)
    assert "🔴" in html, "自動依頼のマーカー引用を心拍として数えている"
    assert "🟢" not in html


def test_L1_labeled_issue_comment_の取得失敗は未検証として出す(tmp_path):
    """無いと何が静かに通るか:
        コメント取得の失敗を「痕跡なし」に畳むと、GitHub API の一時失敗のたびに
        🔴 (審査停止) の誤報が出る。逆に成功に畳むと止まった審査が緑に見える。
        どちらでもなく ❓ 未検証として区別して出すこと。
    """
    html = _run(tmp_path, defs=_DEBT_REVIEW_DEFS, stub=DEBT_REVIEW_FAIL_STUB)
    assert "未検証" in html
    assert "🔴" not in html, "取得失敗を「痕跡なし」として赤にしている"
    assert "🟢" not in html


def test_l1_LLM採点行はシナリオごとに分かれ表示窓は各7件(tmp_path, monkeypatch) -> None:
    """無いと何が静かに通るか (#443 judge A):

    採点行の表示窓が「全体で 7 件」の固定だと、2 シナリオ × 5 朝 (10 件) で窓が
    3.5 朝に縮んで古い朝が黙って落ちるうえ、同じ日付に 🟢/🔴 が並んで
    どちらの台本の赤か読めない。シナリオごとに窓を取れば 5 朝すべてが両方の行に残る。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("status_page_build", BUILD)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)

    root = tmp_path / "uxdata"
    (root / "evals").mkdir(parents=True)
    judges = [
        {
            "kind": "ux-judge-score",
            "scenarioId": sid,
            "recordedAt": f"2099-01-{day:02d}T02:00:00Z",
            "total": total,
            "max": 14,
            "verdict": verdict,
        }
        for day in range(1, 6)
        for sid, total, verdict in (
            ("work-overwhelm-v1", 12, "green"),
            ("hypothesis-pushback-v1", 7, "red"),
        )
    ]
    (root / "evals" / "2099-01.jsonl").write_text(
        "".join(json.dumps(j, ensure_ascii=False) + "\n" for j in judges)
    )
    monkeypatch.setenv("UX_DATA_DIR", str(root))

    section = build.ux_trend_section()
    # シナリオごとに別の採点行が出る (継続線が先)
    baseline_at = section.find("ux-judge-score / <code>work-overwhelm-v1</code>")
    pushback_at = section.find("ux-judge-score / <code>hypothesis-pushback-v1</code>")
    assert baseline_at != -1 and pushback_at != -1
    assert baseline_at < pushback_at
    # 5 朝ぶんの日付が**両方**の行に残る (全体 7 件の窓だと 01-01 が 0 回になる)
    for day in range(1, 6):
        assert section.count(f"01-{day:02d}") == 2, f"01-{day:02d} が両方の行に残っていない"


def test_l1_トレンドの折れ線はシナリオを混ぜない() -> None:
    """無いと何が静かに通るか (#435):

    プローブの台本が 2 本になると、1 本の折れ線に**別々の台本の点**が交互に載る。
    台本が違えば応答の長さも往復の重さも違うので、上下が「遅くなった」なのか
    「別のシナリオの点」なのか誰にも区別できなくなり、#123 M0 から続く継続線
    (`work-overwhelm-v1`) の劣化検知が読めなくなる。
    """
    import importlib.util
    from datetime import datetime, timezone

    spec = importlib.util.spec_from_file_location("status_page_build", BUILD)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    now = datetime(2026, 8, 16, tzinfo=timezone.utc)

    def mech(scenario_id: str | None, day: int, avg: int) -> dict:
        obs = {
            "kind": "ux-eval-mech",
            "recordedAt": f"2026-08-{day:02d}T08:20:00Z",
            "metrics": {
                "latency": {"sendToReplyVisibleMs": {"avgMs": avg, "maxMs": avg + 100}},
                "warnings": {"latency": 0},
                "thresholds": {"warnReplyVisibleMs": 10000},
            },
        }
        if scenario_id is not None:
            obs["scenarioId"] = scenario_id
        return obs

    observations = [
        mech(None, 10, 4000),  # 移行データ (scenarioId なし) = 継続線の一部
        mech("work-overwhelm-v1", 15, 4100),
        mech("hypothesis-pushback-v1", 15, 9000),
    ]

    baseline = build._trend_points(
        observations, now, scenario_id=build.BASELINE_SCENARIO_ID
    )
    # 継続線には旧観測 (scenarioId なし) を含み、別シナリオの 9000ms は入らない
    assert [p["avg"] for p in baseline] == [4000.0, 4100.0]

    everything = build._trend_points(observations, now)
    assert sorted(p["scenario"] for p in everything) == [
        "hypothesis-pushback-v1",
        "work-overwhelm-v1",
        "work-overwhelm-v1",
    ]
