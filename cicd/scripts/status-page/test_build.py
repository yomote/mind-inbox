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
url = args[1] if len(args) > 1 else ""
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


def _run(tmp_path: pathlib.Path, defs: dict | None = None) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(STUB)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)

    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    if defs is not None:
        # 性質のテストは **その時点の watchers.json に依存させない**。
        # 定義から 1 行消しただけでテストが落ちるのは、守りたい性質がずれている印。
        dpath = tmp_path / "watchers.json"
        dpath.write_text(json.dumps(defs, ensure_ascii=False))
        env["STATUS_PAGE_WATCHERS"] = str(dpath)
    out = tmp_path / "out"
    subprocess.run([sys.executable, str(BUILD), str(out)], env=env, check=True,
                   capture_output=True, text=True)
    return (out / "index.html").read_text()


def test_L1_痕跡が取れたら未検証にしない(tmp_path):
    html = _run(tmp_path)
    assert "取得できませんでした" not in html, (
        "gh がスカラー文字列をクォート無しで返す件で、痕跡があるのに未検証になっている"
    )


def test_L1_取得できたものは緑になる(tmp_path):
    html = _run(tmp_path)
    assert "🟢" in html


def test_L1_痕跡を残さない自動化は判定不能のまま残る(tmp_path):
    """「異常時しか喋らない」ものを緑に見せないこと。

    無いと何が静かに通るか:
        沈黙を「異常なし」と表示してしまう。2026-08-10 に無人の仕組みが 4 本とも
        止まっていたのに気づけなかった原因そのもの。
    """
    html = _run(tmp_path, defs={
        "workflows": [],
        "routines": [{
            "name": "異常時しか喋らない見張り",
            "what": "赤いときだけ Issue を立てる",
            "trace": {"kind": "issue_label", "label": "nonexistent-label"},
            "expect_hours": 2,
            "trace_only_on_anomaly": True,
        }],
    })
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
