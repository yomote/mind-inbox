"""
セッション履歴を OrganizedResult（要約・感情・優先事項）に変換する。

workflow.py の FSM を経由せず、単発の structured LLM 呼び出しで完結する。
"""
from __future__ import annotations

import json
import logging

from semantic_kernel import Kernel
from semantic_kernel.contents import ChatHistory

from .kernel import get_execution_settings
from .repositories import SessionRepository
from .schemas import OrganizeResponse

logger = logging.getLogger(__name__)

_ORGANIZE_PROMPT = """\
以下の会話を分析し、JSON 形式のみで回答してください。マークダウン記法は使わないでください。

会話:
{conversation}

回答形式:
{{
  "summary": "現在の状況の要約（2〜3文）",
  "emotions": ["感情1", "感情2"],
  "priorities": ["優先事項1", "優先事項2"]
}}

感情と優先事項はそれぞれ 3 件以内にしてください。
"""


def _format_history(history: ChatHistory) -> str:
    """ChatHistory をテキストに整形する。system メッセージは除外する。"""
    lines = []
    for msg in history.messages:
        role_name = getattr(msg.role, "value", str(msg.role)).lower()
        if "system" in role_name:
            continue
        label = "ユーザー" if "user" in role_name else "AI"
        lines.append(f"{label}: {msg.content}")
    return "\n".join(lines)


async def organize(
    session_id: str,
    session_repo: SessionRepository,
    kernel: Kernel,
) -> OrganizeResponse:
    history = await session_repo.get(session_id)
    if history is None:
        raise ValueError(f"Session not found: {session_id!r}")

    conversation = _format_history(history)
    prompt = _ORGANIZE_PROMPT.format(conversation=conversation)

    call_history = ChatHistory()
    call_history.add_user_message(prompt)

    svc = kernel.get_service("chat")
    result = await svc.get_chat_message_content(
        chat_history=call_history, settings=get_execution_settings()
    )

    raw = str(result).strip()
    parts = raw.split("```")
    if len(parts) >= 3:
        raw = parts[1].removeprefix("json").strip()

    try:
        data = json.loads(raw)
        return OrganizeResponse(
            summary=data.get("summary", ""),
            emotions=data.get("emotions", []),
            priorities=data.get("priorities", []),
        )
    except json.JSONDecodeError:
        logger.warning("Organize JSON parse failed: %r", raw)
        return OrganizeResponse(
            summary="整理中にエラーが発生しました。",
            emotions=[],
            priorities=[],
        )
