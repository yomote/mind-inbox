#!/usr/bin/env python3
"""subagent が未コミットの差分を作業ツリーに置き去りにしたまま終わるのを止める hook。

これが無いと何が静かに通るか:
    subagent は親と作業ツリーを共有する (2026-08-13 実測: SubagentStop の `cwd` が
    親と同一で、subagent の書き込みが親の作業ツリーにそのまま出た)。subagent が
    commit / push せずに終わると、**親からは「作業が終わった」としか見えない**まま
    差分だけが残る。次に親が別の作業を始めると、そのコミットに他人の差分が紛れるか、
    誰のものか分からない差分として捨てられる。実際に 2026-08-13 に起きている。
    GitHub 側には何の痕跡も残らないので CI では検出できない。

2 つのイベントで動く (settings.json で両方に登録している):
    PreToolUse (Agent/Task) — subagent を起こす直前の dirty な path を控える
    SubagentStop            — 今の dirty と突き合わせ、**subagent が増やした分**だけ咎める

控えが取れている場合と取れていない場合を**必ず区別して出す**。控えが無いときに
作業ツリー全体を subagent のせいにすると、親自身の未コミット差分で subagent を
止めることになる (subagent には直しようがない)。

無限ループ対策: `stop_hook_active` が true のときはブロックしない (実測で存在を確認)。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hook_io  # noqa: E402

_GIT_TIMEOUT_SEC = 10


def snapshot_path(session_id: str) -> Path:
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_")[:64] or "unknown"
    return Path(tempfile.gettempdir()) / f"claude-hooks-dirty-{safe}.json"


def parse_porcelain(text: str) -> set[str]:
    """`git status --porcelain` の出力から path 集合を作る。

    rename (`R  old -> new`) は new 側を採る。`-z` を使わないので path に改行を含む
    ファイルは取りこぼす — 実運用で出ない形なので許容し、ここに明記しておく。
    """
    paths: set[str] = set()
    for raw in text.splitlines():
        if len(raw) < 4:
            continue
        entry = raw[3:]
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        entry = entry.strip().strip('"')
        if entry:
            paths.add(entry)
    return paths


def git_dirty_paths(cwd: str) -> tuple[set[str], str | None]:
    """(dirty な path 集合, 失敗理由)。失敗理由が非 None なら**検査していない**。"""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SEC,
            check=False,  # check=False にした分、非ゼロ終了は下で明示的に理由へ変換する
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return set(), f"git status を実行できなかった ({exc!r})"
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return set(), f"git status が失敗した ({detail[0] if detail else proc.returncode})"
    return parse_porcelain(proc.stdout), None


def select_paths_to_report(
    now: set[str], snapshot: set[str] | None
) -> tuple[list[str], str]:
    """咎める path と、その根拠の強さ。

    戻り値の 2 つ目は "diff" (subagent 起動前の控えと突き合わせた = subagent が
    増やした分) か "all" (控えが無いので作業ツリー全体 = 親の分が混ざりうる)。
    **呼ぶ側はこれを文言に出すこと** — 精度を偽らないため。
    """
    if snapshot is None:
        return sorted(now), "all"
    return sorted(now - snapshot), "diff"


def load_snapshot(path: Path, cwd: str) -> set[str] | None:
    """控えを読む。cwd が一致しないものは使わない (worktree 分離された subagent 対策)。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # 控えが無い / 壊れているのは異常ではない (hook 導入直後・別経路で起きた subagent)。
        # 見えなくなるもの: 「控えの取得に失敗した」と「そもそも控えが無い」の区別。
        # どちらも fallback ("all") に落ちて文言に出るので、黙って精度を偽ることはない。
        return None
    if not isinstance(data, dict) or data.get("cwd") != cwd:
        return None
    paths = data.get("paths")
    if not isinstance(paths, list):
        return None
    return {p for p in paths if isinstance(p, str)}


def format_reason(paths: list[str], mode: str) -> str:
    shown = paths[:20]
    listed = "\n".join(f"  {p}" for p in shown)
    if len(paths) > len(shown):
        listed += f"\n  ... 他 {len(paths) - len(shown)} 件"
    if mode == "diff":
        basis = "これは subagent の起動前後の差分なので、あなたが作った差分です。"
    else:
        basis = (
            "起動前の控えが取れていないため、これは**作業ツリー全体**の未コミット差分です。"
            "あなたが作っていないものが混ざっている可能性があります "
            "(その場合はそう述べて先に進んでください)。"
        )
    return (
        f"未コミットの変更が {len(paths)} 件、作業ツリーに残ったまま終わろうとしています。\n"
        f"{listed}\n"
        f"{basis}\n"
        "subagent は親と作業ツリーを共有します。ここで置き去りにすると、"
        "この差分の始末は親セッションに回り、誰の変更か分からないまま捨てられます。\n"
        "commit と push まで完遂してから終了してください。"
        "意図的に残すなら、何をなぜ残したかを最終回答に書いてください。"
    )


def handle(event: dict[str, Any]) -> dict[str, Any] | None:
    hook_event = event.get("hook_event_name")
    cwd = event.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return hook_io.passthrough("cwd が渡らず、未コミット差分を検査していない")
    session_id = str(event.get("session_id") or "unknown")

    if hook_event == "PreToolUse":
        return _record_snapshot(event, cwd, session_id)
    if hook_event == "SubagentStop":
        return _check(event, cwd, session_id)
    return None


def _record_snapshot(event: dict[str, Any], cwd: str, session_id: str) -> dict[str, Any] | None:
    if event.get("tool_name") not in ("Agent", "Task"):
        return None
    paths, failure = git_dirty_paths(cwd)
    if failure is not None:
        # 控えが取れなくても subagent の起動は止めない。取れなかった事実は
        # SubagentStop 側が "all" モードとして文言に出す。
        return None
    try:
        snapshot_path(session_id).write_text(
            json.dumps({"cwd": cwd, "paths": sorted(paths)}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        # 書けなくても起動は止めない。結果は "all" モードに落ちて文言に出る。
        return None
    return None


def _check(event: dict[str, Any], cwd: str, session_id: str) -> dict[str, Any] | None:
    if event.get("stop_hook_active"):
        # 一度差し戻した後の再停止。ここで再びブロックすると無限ループになる。
        return None

    now, failure = git_dirty_paths(cwd)
    if failure is not None:
        return hook_io.passthrough(f"{failure} ため、未コミット差分を検査していない")

    snapshot = load_snapshot(snapshot_path(session_id), cwd)
    paths, mode = select_paths_to_report(now, snapshot)
    if not paths:
        return None
    return hook_io.block(format_reason(paths, mode))


if __name__ == "__main__":
    raise SystemExit(hook_io.run(handle))
