"""[単体] subagent の置き去り差分の検出 (Issue #392 案 B)。

無いと何が静かに通るか:
    - **親自身の未コミット差分で subagent をブロックする** — subagent には直しようが
      なく、分配そのものが壊れる (Issue #392 が最重要と名指しした事故の B 版)
    - 控えが無いときに「subagent が作った差分」と偽って報告する — 精度を偽ると
      次からその文言が信用されなくなる
    - **並列起動で先に停止した subagent の置き去りを見落とす** — 後発の控えに
      先行 subagent の差分が写り込むと、その差分が「起動前からあったもの」に化ける
    - `stop_hook_active` を見落として無限に差し戻す
    - `git status` が失敗したときに「未コミット差分なし」と答える
      (取れなかったものを「異常なし」と書く — このリポジトリ最優先の禁止事項)
"""

import json
from pathlib import Path

from subagent_dirty_guard import (
    fingerprint,
    format_reason,
    handle,
    load_entries,
    parse_porcelain,
    select_paths_to_report,
    snapshot_path,
    take_baseline,
)

HOOK_DIR = Path(__file__).resolve().parent


def test_単体_porcelain_を解釈する() -> None:
    text = " M apps/bff/src/a.ts\n?? new.txt\nR  old.md -> new.md\nA  docs/x.md\n"
    assert parse_porcelain(text) == {"apps/bff/src/a.ts", "new.txt", "new.md", "docs/x.md"}


def test_単体_控えがあれば_subagent_が増やした分だけ咎める() -> None:
    before = {"parent-dirty.ts": "sha256:aaa"}
    now = {"parent-dirty.ts": "sha256:aaa", "subagent-made.ts": "sha256:bbb"}
    assert select_paths_to_report(now, before) == ["subagent-made.ts"]
    # 親の差分だけが残っている場合は 1 件も咎めない
    assert select_paths_to_report(before, before) == []


def test_単体_dirty_済みファイルへの追記も咎める() -> None:
    # 親が既に触っている foo.py を subagent がさらに編集して未コミットで終わる形。
    # path 集合は起動前後で同じなので、指紋を見ないと差集合が空になって素通しする。
    before = {"foo.py": "sha256:aaa"}
    now = {"foo.py": "sha256:bbb"}
    assert select_paths_to_report(now, before) == ["foo.py"]


def test_単体_指紋を持たない旧形式の控えでは内容変化を判定しない() -> None:
    # 判定できないものを「変わっていない」と読み替えない。path の増分だけを見る。
    before = {"foo.py": None}
    assert select_paths_to_report({"foo.py": "sha256:bbb"}, before) == []
    assert select_paths_to_report({"foo.py": "sha256:bbb", "new.ts": "sha256:c"}, before) == [
        "new.ts"
    ]


def test_単体_控えが無いときは全体を出す() -> None:
    assert select_paths_to_report({"a.ts": "sha256:a", "b.ts": "sha256:b"}, None) == [
        "a.ts",
        "b.ts",
    ]


def test_単体_指紋は内容が変われば変わり_変わらなければ同じ(tmp_path) -> None:
    target = tmp_path / "a.ts"
    target.write_text("x", encoding="utf-8")
    first = fingerprint(str(tmp_path), "a.ts")
    assert first == fingerprint(str(tmp_path), "a.ts"), "同じ内容で指紋が揺れると毎回誤検知する"
    target.write_text("y", encoding="utf-8")
    assert fingerprint(str(tmp_path), "a.ts") != first
    assert fingerprint(str(tmp_path), "無い.ts") == "missing"


def test_単体_未追跡ディレクトリは中身が増えれば指紋が変わる(tmp_path) -> None:
    # `?? dir/` は中にファイルが増えても porcelain の行は 1 本のまま。
    # ディレクトリを 1 個の path として扱うだけだと、中身の追加を見逃す。
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "a.ts").write_text("x", encoding="utf-8")
    before = fingerprint(str(tmp_path), "d")
    (tmp_path / "d" / "b.ts").write_text("y", encoding="utf-8")
    assert fingerprint(str(tmp_path), "d") != before


def test_単体_文言が根拠の強さを言い分ける() -> None:
    assert "あなたが作った差分です" in format_reason(["a.ts"], "diff")
    reason_all = format_reason(["a.ts"], "all")
    assert "混ざっている可能性" in reason_all
    assert "あなたが作った差分です" not in reason_all
    reason_concurrent = format_reason(["a.ts"], "diff-concurrent")
    assert "並行して動いている" in reason_concurrent
    assert "あなたが作った差分です" not in reason_concurrent


def test_単体_控えは_cwd_が違えば使わない(tmp_path: Path) -> None:
    # worktree 分離された subagent で、親の作業ツリーの控えを流用しないこと
    entries = [{"cwd": "/a", "prints": {"x.ts": "sha256:a"}}]
    assert take_baseline(entries, "/a")[0] == {"x.ts": "sha256:a"}
    baseline, _, mode = take_baseline(entries, "/b")
    assert baseline is None
    assert mode == "all"


def test_単体_控えの列は旧形式も空ファイルも読める(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"cwd": "/a", "paths": ["x.ts"]}), encoding="utf-8")
    assert take_baseline(load_entries(legacy), "/a")[0] == {"x.ts": None}
    broken = tmp_path / "broken.json"
    broken.write_text("これは JSON ではない", encoding="utf-8")
    assert load_entries(broken) == []
    assert load_entries(tmp_path / "無い.json") == []


def test_単体_並列起動では最古の控えを基準にして見落とさない() -> None:
    # release-gate は subagent を 4 個並列で起こす。後発の控えを基準にすると、
    # 先行 subagent が作った a1.ts が「起動前からあったもの」に化けて見落とされる。
    p, a1 = "sha256:p", "sha256:a1"
    entries = [
        {"cwd": "/r", "prints": {"parent.ts": p}},  # agent1 起動時
        {"cwd": "/r", "prints": {"parent.ts": p, "a1.ts": a1}},  # agent2 起動時 (a1 が写る)
    ]
    baseline, remaining, mode = take_baseline(entries, "/r")
    assert baseline == {"parent.ts": p}, "最古ではない控えを基準にすると置き去りを見落とす"
    assert mode == "diff-concurrent", "並行起動であることを文言に出せなくなる"
    now = {"parent.ts": p, "a1.ts": a1, "a2.ts": "sha256:a2"}
    assert select_paths_to_report(now, baseline) == ["a1.ts", "a2.ts"]
    # 取り出した控えは列から落ちる (同じ控えを 2 回使わない)
    assert remaining == [{"cwd": "/r", "prints": {"parent.ts": p, "a1.ts": a1}}]
    baseline2, remaining2, mode2 = take_baseline(remaining, "/r")
    assert baseline2 == {"parent.ts": p, "a1.ts": a1}
    assert (remaining2, mode2) == ([], "diff")


def test_単体_控えの置き場はセッションごとに分かれる() -> None:
    assert snapshot_path("aaa") != snapshot_path("bbb")
    assert snapshot_path("../../etc/passwd").name.startswith("claude-hooks-dirty-")


def test_単体_未コミット差分があればブロックする(monkeypatch, tmp_path: Path) -> None:
    import subagent_dirty_guard as guard

    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: ({"a.ts": "sha256:a"}, None))
    monkeypatch.setattr(guard, "snapshot_path", lambda sid: tmp_path / "none.json")
    result = handle({"hook_event_name": "SubagentStop", "cwd": "/repo", "session_id": "s"})
    assert result["decision"] == "block"
    assert "a.ts" in result["reason"]


def test_単体_差分が無ければ何も返さない(monkeypatch, tmp_path: Path) -> None:
    import subagent_dirty_guard as guard

    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: ({}, None))
    monkeypatch.setattr(guard, "snapshot_path", lambda sid: tmp_path / "none.json")
    assert handle({"hook_event_name": "SubagentStop", "cwd": "/repo", "session_id": "s"}) is None


def test_単体_stop_hook_active_のときは再ブロックしない(monkeypatch, tmp_path: Path) -> None:
    import subagent_dirty_guard as guard

    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: ({"a.ts": "sha256:a"}, None))
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

    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: ({}, "git status が失敗した"))
    result = handle({"hook_event_name": "SubagentStop", "cwd": "/repo", "session_id": "s"})
    assert result is not None
    assert "未検証" in result["systemMessage"]


def test_単体_cwd_が無ければ検査していないと言う() -> None:
    result = handle({"hook_event_name": "SubagentStop"})
    assert "未検証" in result["systemMessage"]


def test_単体_PreToolUse_は_Agent_のときだけ控えを取る(monkeypatch, tmp_path: Path) -> None:
    import subagent_dirty_guard as guard

    target = tmp_path / "snap.json"
    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: ({"a.ts": "sha256:a"}, None))
    monkeypatch.setattr(guard, "snapshot_path", lambda sid: target)

    handle({"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": "/repo"})
    assert not target.exists()

    handle({"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": "/repo"})
    assert json.loads(target.read_text(encoding="utf-8")) == [
        {"cwd": "/repo", "prints": {"a.ts": "sha256:a"}}
    ]

    # 2 個目の起動は上書きではなく積む (上書きすると先行 subagent の基準が消える)
    handle({"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": "/repo"})
    assert len(json.loads(target.read_text(encoding="utf-8"))) == 2


def test_単体_停止のたびに控えを_1_件ずつ消費する(monkeypatch, tmp_path: Path) -> None:
    import subagent_dirty_guard as guard

    target = tmp_path / "snap.json"
    target.write_text(
        json.dumps(
            [
                {"cwd": "/repo", "prints": {}},
                {"cwd": "/repo", "prints": {"a.ts": "sha256:a"}},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(guard, "git_dirty_paths", lambda cwd: ({"a.ts": "sha256:a", "b.ts": "sha256:b"}, None))
    monkeypatch.setattr(guard, "snapshot_path", lambda sid: target)

    event = {"hook_event_name": "SubagentStop", "cwd": "/repo", "session_id": "s"}
    first = handle(event)
    assert "a.ts" in first["reason"] and "b.ts" in first["reason"]
    assert json.loads(target.read_text(encoding="utf-8")) == [
        {"cwd": "/repo", "prints": {"a.ts": "sha256:a"}}
    ]

    second = handle(event)
    assert "b.ts" in second["reason"]
    assert "a.ts" not in second["reason"], "消費済みの控えを使い回している"
    assert not target.exists(), "列が空になったら控えは残さない"


def test_単体_並列に控えを積んでも_1_件も失われない(tmp_path: Path) -> None:
    """排他が無いと、同時に走る PreToolUse が同じ旧列を読んで後勝ちで書き、控えが消える。

    hook は**別プロセス**として起動するので、スレッドではなく実プロセスで確かめる。
    さらに「読んでから書くまで」に待ちを差し込む — 実運用の窓は数ミリ秒で、
    そのままだと**排他を外しても偶然すり抜けて緑になる** (ミューテーション試験で確認済み)。
    待ちを入れると、排他が無い実装では全プロセスが同じ空の列を読むので必ず落ちる。
    """
    import subprocess
    import sys

    target = tmp_path / "snap.json"
    runner = tmp_path / "runner.py"
    runner.write_text(
        "\n".join(
            [
                "import sys, time, pathlib",
                f"sys.path.insert(0, {str(HOOK_DIR)!r})",
                "import subagent_dirty_guard as guard",
                f"guard.snapshot_path = lambda sid: pathlib.Path({str(target)!r})",
                "guard.git_dirty_paths = lambda cwd: ({sys.argv[1]: 'sha256:x'}, None)",
                "_load = guard.load_entries",
                "def slow(path):",
                "    entries = _load(path)",
                "    time.sleep(0.05)  # 読んでから書くまでの窓を実測より広げる",
                "    return entries",
                "guard.load_entries = slow",
                "guard.handle({'hook_event_name': 'PreToolUse', 'tool_name': 'Agent',",
                "              'cwd': '/repo', 'session_id': 's'})",
            ]
        ),
        encoding="utf-8",
    )
    procs = [
        subprocess.Popen([sys.executable, str(runner), f"f{i}.ts"])  # noqa: S603
        for i in range(12)
    ]
    for proc in procs:
        assert proc.wait(timeout=60) == 0

    entries = json.loads(target.read_text(encoding="utf-8"))
    assert len(entries) == 12, (
        f"控えが {12 - len(entries)} 件消えた — 並列 hook の read-modify-write が競合している"
    )
