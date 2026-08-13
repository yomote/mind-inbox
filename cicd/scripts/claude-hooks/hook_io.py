#!/usr/bin/env python3
"""Claude Code hook の入出力と「壊れても止めない、ただし黙らない」共通土台。

これが無いと何が静かに通るか:
    hook が例外で落ちたとき、Claude Code は非ゼロ終了を「非ブロック」として扱うので
    **検査が丸ごと素通しになる**。しかもセッションからは「検査が走って何も出なかった」
    のと区別が付かない。つまり **guard の死が緑色の顔をして通る**。
    ここで例外を捕まえて `systemMessage` で「素通しした/理由」を必ず出すことで、
    「検査が通った」と「検査が走らなかった」を区別できるようにしている。

hook の出力仕様 (2026-08-13 に Claude Code 2.1.229 で実測。docs ではなく実測が正):
    PreToolUse  — {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                   "permissionDecision": "deny", "permissionDecisionReason": str}}
                  → ツールは**実行されない**。reason はエージェントに届く
    PostToolUse — {"decision": "block", "reason": str}
                  → ツールは既に実行済み (**取り消されない**)。reason がエージェントに届く
    SubagentStop— {"decision": "block", "reason": str}
                  → subagent は停止せず続行する
    いずれも終了コードは 0。stdout に JSON 1 個。
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable

# エージェントに届く文言の先頭に付ける印。セッションのログで hook 由来だと分かるようにする。
PREFIX = "[hook]"


def deny_pre_tool_use(reason: str) -> dict[str, Any]:
    """PreToolUse: ツールの実行そのものを止める。"""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"{PREFIX} {reason}",
        }
    }


def block(reason: str) -> dict[str, Any]:
    """PostToolUse / SubagentStop: 実行後にエージェントへ差し戻す。

    **PostToolUse では書き込みは取り消されない** (実測)。「止めた」ではなく
    「直させる」機構であることを、呼ぶ側の文言でも明示すること。
    """
    return {"decision": "block", "reason": f"{PREFIX} {reason}"}


def passthrough(note: str | None = None) -> dict[str, Any] | None:
    """素通し。検査できなかったときは note を必ず付ける (黙って素通ししない)。"""
    if note is None:
        return None
    return {"systemMessage": f"{PREFIX} 未検証のまま素通し: {note}"}


def run(handler: Callable[[dict[str, Any]], dict[str, Any] | None]) -> int:
    """stdin の JSON を handler に渡し、戻り値を stdout に出す。

    handler が何を投げても**セッションは止めない** (常に終了コード 0)。ただし
    素通しした事実は `systemMessage` で出す — ここを握り潰すと guard の死が見えなくなる。
    """
    try:
        raw = sys.stdin.read()
    except Exception as exc:  # noqa: BLE001 — stdin が読めない事故も素通し扱いにするが黙らない
        _emit(passthrough(f"stdin を読めなかった ({exc!r})"))
        return 0

    try:
        event = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        _emit(passthrough(f"stdin の JSON を解釈できなかった ({exc!r})"))
        return 0

    if not isinstance(event, dict):
        _emit(passthrough(f"stdin の JSON が dict ではなかった ({type(event).__name__})"))
        return 0

    try:
        result = handler(event)
    except Exception:  # noqa: BLE001 — handler のバグでセッションを止めない
        detail = traceback.format_exc(limit=3).strip().replace("\n", " / ")
        _emit(passthrough(f"検査中に例外が出た ({detail})"))
        return 0

    _emit(result)
    return 0


def _emit(payload: dict[str, Any] | None) -> None:
    if payload is None:
        return
    print(json.dumps(payload, ensure_ascii=False))


def is_subagent(event: dict[str, Any]) -> bool:
    """親セッションか subagent かの判定 (2026-08-13 実測)。

    `session_id` は親と subagent で**同一**なので使えない。subagent 由来の
    イベントにだけ `agent_id` キーが入る (親には**キー自体が無い**)。
    """
    return "agent_id" in event
