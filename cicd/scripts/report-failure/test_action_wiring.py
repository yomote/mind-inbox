"""[単体] composite action (.github/actions/report-failure) の配線 (Issue #507)。

無いと何が静かに通るか:
    **#507 のバグは YAML の中にあった。** notify.py をいくら固めても、action.yml が
    判定をシェルに書き戻したり、パスを間違えて notify.py に到達しなかったりすれば、
    同じ「無言の exit 1」が戻る。しかも composite action の中身は
    `npm run test:fast` でも lint でも一切実行されないので、**壊しても CI は緑**。

    ここでは action.yml の run ブロックを取り出し、runner と同じ
    `bash --noprofile --norc -e -o pipefail` + gh スタブで実際に走らせる。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
ACTION_DIR = REPO_ROOT / ".github" / "actions" / "report-failure"
ACTION_YML = ACTION_DIR / "action.yml"

sys.path.insert(0, str(HERE))
from test_notify import _STUB  # noqa: E402  (gh スタブは 1 つに保つ)


def _run_block() -> str:
    steps = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))["runs"]["steps"]
    assert len(steps) == 1, "step が増えたら、どれが通報の実体かをここで固定し直すこと"
    return steps[0]["run"]


def test_単体_action_の_run_に判定を書き戻していない() -> None:
    """判定が YAML に戻ると、テストできない場所に if が生えて #507 が再発する
    (cicd/CLAUDE.md「判定ロジックをシェルや workflow の中に埋めない」)。"""
    block = _run_block()
    assert "notify.py" in block
    for banned in ("if ", "case ", "$(gh", "`gh", "/dev/null", "|| true"):
        assert banned not in block, f"action.yml の run に {banned!r} が戻っている"
    # 実行行は 1 本だけ (シェルの分岐が増えていない)
    commands = [ln for ln in block.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert len(commands) == 2, commands  # set -uo pipefail + python3 の 2 行


def test_単体_action_の_run_を_runner_と同じシェルで通す(tmp_path) -> None:
    """`$GITHUB_ACTION_PATH/../../../` の解決も含めて、実際に notify.py に届くか。

    ここが折れると通報経路が丸ごと死ぬが、**デプロイは緑のまま**なので気づけない。
    """
    script = tmp_path / "step.sh"
    script.write_text(_run_block(), encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "gh"
    stub.write_text(_STUB.format(python=sys.executable), encoding="utf-8")
    stub.chmod(0o755)
    log = tmp_path / "calls.json"

    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        # runner が composite action に渡す値と同じ形 (workspace 直下にチェックアウト)
        "GITHUB_ACTION_PATH": str(ACTION_DIR),
        "GH_STUB_LOG": str(log),
        "GH_STUB_FAIL": "",
        "GH_STUB_FAIL_TIMES": "0",
        "GH_STUB_ISSUES": json.dumps([{"number": 42, "title": "[ci-failure] deploy が落ちている"}]),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "REPORT_FAILURE_BACKOFF_SECONDS": "0",
        "JOB_STATUS": "success",
        "WORK_RAN": "true",
        "CANCELLED_IS_FAILURE": "true",
        "WHAT": "x",
        "WF": "deploy",
        "RUN_URL": "https://example/run/1",
        "REPO": "yomote/mind-inbox",
        "SHA": "abc",
        "REF": "main",
        "GH_TOKEN": "dummy",
    }
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", str(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "復旧したので #42 を閉じた" in proc.stdout
    calls = json.loads(log.read_text())
    assert ["issue", "close"] in [c[:2] for c in calls]
