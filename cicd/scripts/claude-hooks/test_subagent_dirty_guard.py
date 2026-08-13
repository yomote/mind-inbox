"""[単体] subagent の置き去り差分の検出 (Issue #392 案 B)。

無いと何が静かに通るか:
    - **親自身の未コミット差分で subagent をブロックする** — subagent には直しようが
      なく、分配そのものが壊れる (Issue #392 が最重要と名指しした事故の B 版)
    - 控えが無いときに「subagent が作った差分」と偽って報告する — 精度を偽ると
      次からその文言が信用されなくなる
    - `stop_hook_active` を見落として無限に差し戻す
    - `git status` が失敗したときに「未コミット差分なし」と答える
      (取れなかったものを「異常なし」と書く — このリポジトリ最優先の禁止事項)
"""

import json
from pathlib import Path

from subagent_dirty_guard import (
    format_reason,
    handle,
    load_snapshot,
    parse_porcelain,
    select_paths_to_report,
    snapshot_path,
)


def test_単体_porcelain_を解釈する() -> None:
    text = " M apps/bff/src/a.ts\n?? new.txt\nR  old.md -> new.md\nA  docs/x.md\n"
    assert parse_porcelain(text) == {"apps/bff/src/a.ts", "new.txt", "new.md", "docs/x.md"}


def test_単体_控えがあれば_subagent_が増やした分だけ咎める() -> None:
    before = {"parent-dirty.ts"}
    now = {"parent-dirty.ts", "subagent-made.ts"}
    paths, mode = select_paths_to_report(now, before)
    assert paths == ["subagent-made.ts"]
    assert mode == "diff"
    # 親の差分だけが残っている場合は 1 件も咎めない
    assert select_paths_to_report(before, before) == ([], "diff")


def test_単体_控えが無いときは全体を出すが根拠を_all_と申告する() -> None:
    paths, mode = select_paths_to_report({"a.ts", "b.ts"}, None)
    assert paths == ["a.ts", "b.ts"]
    assert mode == "all"


def test_単体_文言が根拠の強さを言い分ける() -> None:
    assert "あなたが作った差分です" in format_reason(["a.ts"], "diff")
    reason_all = format_reason(["a.ts"], "all")
    assert "混ざっている可能性" in reason_all
    assert "あなたが作った差分です" not in reason_all


def test_単体_控えは_cwd_が違えば使わない(tmp_path: Path) -> None:
    # worktree 分離された subagent で、親の作業ツリーの控えを流用しないこと
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"cwd": "/a", "paths": ["x.ts"]}), encoding="utf-8")
    assert load_snapshot(snap, "/a") == {"x.ts"}
    assert load_snapshot(snap, "/b") is None
    assert load_snapshot(tmp_path / "無い.json", "/a") is None


def test_単体_控えの置き場はセッションごとに分かれる() -> None:
    assert snapshot_path("aaa") != snapshot_path("bbb")
    assert snapshot_path("../../etc/passwd").name.startswith("claude-hooks-dirty-")


def test_単体_未コミット差分があればブロックする(monkeypatch, tmp_path: Path) -> None:
    import subagent_dirty_guard as guard

    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: ({"a.ts"}, None))
    monkeypatch.setattr(guard, "snapshot_path", lambda sid: tmp_path / "none.json")
    result = handle({"hook_event_name": "SubagentStop", "cwd": "/repo", "session_id": "s"})
    assert result["decision"] == "block"
    assert "a.ts" in result["reason"]


def test_単体_差分が無ければ何も返さない(monkeypatch, tmp_path: Path) -> None:
    import subagent_dirty_guard as guard

    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: (set(), None))
    monkeypatch.setattr(guard, "snapshot_path", lambda sid: tmp_path / "none.json")
    assert handle({"hook_event_name": "SubagentStop", "cwd": "/repo", "session_id": "s"}) is None


def test_単体_stop_hook_active_のときは再ブロックしない(monkeypatch, tmp_path: Path) -> None:
    import subagent_dirty_guard as guard

    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: ({"a.ts"}, None))
    monkeypatch.setattr(guard, "snapshot_path", lambda sid: tmp_path / "none.json")
    event = {
        "hook_event_name": "SubagentStop",
        "cwd": "/repo",
        "session_id": "s",
        "stop_hook_active": True,
    }
    assert handle(event) is None


def test_単体_git_が失敗したら異常なしと答えない(monkeypatch) -> None:
    import subagent_dirty_guard as guard

    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: (set(), "git status が失敗した"))
    result = handle({"hook_event_name": "SubagentStop", "cwd": "/repo", "session_id": "s"})
    assert result is not None
    assert "未検証" in result["systemMessage"]


def test_単体_cwd_が無ければ検査していないと言う() -> None:
    result = handle({"hook_event_name": "SubagentStop"})
    assert "未検証" in result["systemMessage"]


def test_単体_PreToolUse_は_Agent_のときだけ控えを取る(monkeypatch, tmp_path: Path) -> None:
    import subagent_dirty_guard as guard

    target = tmp_path / "snap.json"
    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: ({"a.ts"}, None))
    monkeypatch.setattr(guard, "snapshot_path", lambda sid: target)

    handle({"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": "/repo"})
    assert not target.exists()

    handle({"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": "/repo"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"cwd": "/repo", "paths": ["a.ts"]}
