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

if "/runs" in url:
    emit([{"c": "success", "s": "completed",
           "t": "2099-01-01T00:00:00Z", "u": "https://example.test/run"}])
elif "needs-human" in url or "pulls" in url:
    emit([])
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


def test_L1_定義ファイルが読める():
    defs = json.loads((pathlib.Path(__file__).with_name("watchers.json")).read_text())
    assert defs["workflows"]
    # routines は ADR 0035 D1 / 0037 で 0 本が目標状態 — 空を許す (キー自体は
    # build.py が defs["routines"] を直接読むので必須)
    assert isinstance(defs["routines"], list)
    for w in defs["workflows"]:
        assert w.get("id", "").endswith(".yml"), w


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
elif "needs-human" in url or "pulls" in url:
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
