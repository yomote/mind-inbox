"""[単体] hook の配線と、hook スクリプトを「実際にプロセスとして起動した」ときの振る舞い。

無いと何が静かに通るか:
    - `.claude/settings.json` が指す先のファイルが**消えている / 名前が変わっている**のに
      誰も気づかない。hook の死は GitHub 側に何の痕跡も残さないので、
      status ページ (watchers.json) では検出できない。**ここが唯一の検出点**
    - 純関数のテストは通るのに、**プロセスとして起動すると import に失敗する**
      (`hook_io` の解決は cwd に依存する)。この場合 hook は毎回エラーになり、
      Claude Code はそれを非ブロックとして扱うので**検査が丸ごと素通しになる**
    - hook が例外で落ちたときに**何も出さずに終わり**、「検査した」と
      「検査が死んでいる」が区別できなくなる
"""

import json
import subprocess
import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
REPO_ROOT = HOOK_DIR.parents[2]
SETTINGS = REPO_ROOT / ".claude" / "settings.json"

# .claude/settings.json に載っているべき hook (Issue #392 案 A / B / C)。
# ここを増やしたら settings.json 側も同じ PR で足す。
EXPECTED = {
    "PostToolUse": {"swallow_guard.py"},
    "SubagentStop": {"subagent_dirty_guard.py"},
    "PreToolUse": {"subagent_dirty_guard.py", "adr_number_guard.py"},
}


def _commands(settings: dict, event: str) -> list[str]:
    out: list[str] = []
    for matcher in settings.get("hooks", {}).get(event, []):
        for hook in matcher.get("hooks", []):
            if hook.get("type") == "command":
                out.append(hook["command"])
    return out


def _load_settings() -> dict:
    return json.loads(SETTINGS.read_text(encoding="utf-8"))


def test_単体_settings_が指す_hook_の実体が全部存在する() -> None:
    settings = _load_settings()
    checked = 0
    for event in settings.get("hooks", {}):
        for command in _commands(settings, event):
            script = command.split()[0].strip('"')
            assert script.startswith("$CLAUDE_PROJECT_DIR/"), (
                f"{event}: hook は $CLAUDE_PROJECT_DIR 相対で書くこと ({command})"
            )
            path = REPO_ROOT / script[len("$CLAUDE_PROJECT_DIR/") :]
            assert path.is_file(), f"{event}: {path} が存在しない (hook が黙って死ぬ)"
            checked += 1
    assert checked > 0, "hook が 1 つも登録されていない"


def test_単体_案_A_B_C_の_hook_が期待どおりの_event_に登録されている() -> None:
    settings = _load_settings()
    for event, expected_scripts in EXPECTED.items():
        registered = {Path(c.split()[0]).name for c in _commands(settings, event)}
        missing = expected_scripts - registered
        assert not missing, f"{event} に {missing} が登録されていない"


def test_単体_SessionStart_hook_を壊していない() -> None:
    # Issue #392 の「触ってはいけないもの」。hooks キーを触る作業で巻き添えにしない
    settings = _load_settings()
    assert any("session-start.sh" in c for c in _commands(settings, "SessionStart"))


def _run(script: str, payload: dict, cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(HOOK_DIR / script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
        check=False,  # 非ゼロ終了そのものを検査対象にするので check=False
    )
    return proc.returncode, proc.stdout


def test_単体_プロセスとして起動しても_import_に失敗しない(tmp_path: Path) -> None:
    # cwd を hook ディレクトリの外にして起動する (実運用の hook は project root で起動する)
    for script in ("swallow_guard.py", "subagent_dirty_guard.py", "adr_number_guard.py"):
        code, _ = _run(script, {"tool_name": "Bash", "tool_input": {}}, cwd=tmp_path)
        assert code == 0, f"{script} がプロセス起動で失敗した (hook が毎回エラーになる)"


def test_単体_壊れた入力でもセッションを止めず_素通しを申告する(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(HOOK_DIR / "swallow_guard.py")],
        input="これは JSON ではない",
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, "hook の失敗でセッションを止めてはいけない"
    assert "未検証" in json.loads(proc.stdout)["systemMessage"], (
        "素通しを黙ってやると、検査の死が『問題なし』に化ける"
    )


def test_単体_違反入力をプロセス経由で渡すとブロック_JSON_が出る(tmp_path: Path) -> None:
    target = tmp_path / "run.sh"
    target.write_text("npm test || true\n", encoding="utf-8")
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target)},
        "tool_response": {
            "structuredPatch": [{"newStart": 1, "lines": ["+npm test || true"]}]
        },
    }
    code, stdout = _run("swallow_guard.py", payload, cwd=tmp_path)
    assert code == 0
    assert json.loads(stdout)["decision"] == "block"
