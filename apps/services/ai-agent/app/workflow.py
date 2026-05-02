"""
Workflow engine using Semantic Kernel.

State transitions:
  RECEIVE → CLASSIFY → RETRIEVE_IF_NEEDED → PLAN
       → APPROVAL_IF_NEEDED  (side-effecting tool: pause, return to caller)
       → EXECUTE_TOOL → RESPOND
"""
from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Optional

from semantic_kernel import Kernel
from semantic_kernel.contents import ChatHistory

from .kernel import get_execution_settings
from .rag import retrieve
from .repositories import ApprovalRepository, SessionRepository
from .schemas import ApprovalRecord, ChatResponse, Plan
from .tools import execute_tool, is_side_effecting

logger = logging.getLogger(__name__)

CHAT_SYSTEM_PROMPT = """\
あなたは「Mind Inbox」の対話 AI です。
ユーザーが頭の中のモヤモヤや悩みを言語化できるよう、
共感的かつ具体的な問いかけで対話を深めてください。

応答ルール:
- 返答は 3 文以内に収める
- 評価・アドバイスはせず、まず気持ちに寄り添う
- 具体的なエピソードや感情を引き出す問いかけを 1 つ含める
- ユーザーと同じ言語（原則日本語）で答える
"""


class WorkflowState(str, Enum):
    RECEIVE = "RECEIVE"
    CLASSIFY = "CLASSIFY"
    RETRIEVE_IF_NEEDED = "RETRIEVE_IF_NEEDED"
    PLAN = "PLAN"
    APPROVAL_IF_NEEDED = "APPROVAL_IF_NEEDED"
    EXECUTE_TOOL = "EXECUTE_TOOL"
    RESPOND = "RESPOND"


async def _get_or_create_session(
    session_id: str,
    session_repo: SessionRepository,
) -> ChatHistory:
    history = await session_repo.get(session_id)
    if history is None:
        history = ChatHistory()
        history.add_system_message(CHAT_SYSTEM_PROMPT)
        await session_repo.save(session_id, history)
    return history


async def _classify(message: str, kernel: Kernel) -> dict:
    """LLM でメッセージを分類し、必要なツール・RAG 検索を判定する。"""
    prompt = f"""Analyze the user message and respond with JSON only. No markdown.

User message: "{message}"

Available tools:
- search_faq(query: str)           — read-only: search FAQ
- get_inbox_stats(user_id: str)    — read-only: get inbox stats
- send_reply(to: str, body: str)   — SIDE-EFFECTING: send a reply
- archive_message(message_id: str) — SIDE-EFFECTING: archive a message

Respond with this exact JSON structure:
{{
  "needs_retrieval": <true|false>,
  "needs_tool": <true|false>,
  "tool_name": <"tool_name" or null>,
  "tool_args": <dict or {{}}>
}}"""

    classification_chat = ChatHistory()
    classification_chat.add_user_message(prompt)
    svc = kernel.get_service("chat")
    result = await svc.get_chat_message_content(
        chat_history=classification_chat, settings=get_execution_settings()
    )

    llm_response = str(result).strip()
    parts = llm_response.split("```")
    if len(parts) >= 3:
        llm_response = parts[1].removeprefix("json").strip()

    try:
        return json.loads(llm_response)
    except json.JSONDecodeError:
        logger.warning("Classification JSON parse failed: %r", llm_response)
        return {
            "needs_retrieval": False,
            "needs_tool": False,
            "tool_name": None,
            "tool_args": {},
        }


async def _respond(
    history: ChatHistory,
    kernel: Kernel,
    rag_context: str = "",
) -> str:
    """最終的なアシスタント返答を生成する。"""
    if rag_context:
        call_history = ChatHistory()
        for msg in history.messages:
            call_history.messages.append(msg)
        call_history.add_system_message(f"Relevant context:\n{rag_context}")
    else:
        call_history = history

    svc = kernel.get_service("chat")
    result = await svc.get_chat_message_content(
        chat_history=call_history, settings=get_execution_settings()
    )
    return str(result)


async def run_workflow(
    session_id: str,
    message: str,
    session_repo: SessionRepository,
    approval_repo: ApprovalRepository,
    kernel: Kernel,
) -> ChatResponse:
    logger.info("Workflow[RECEIVE] session=%s", session_id)
    history = await _get_or_create_session(session_id, session_repo)
    history.add_user_message(message)
    await session_repo.save(session_id, history)

    logger.info("Workflow[CLASSIFY]")
    classification = await _classify(message, kernel)

    rag_context = ""
    citations: list[str] = []

    if classification.get("needs_retrieval"):
        logger.info("Workflow[RETRIEVE_IF_NEEDED]")
        results = await retrieve(message)
        rag_context = "\n".join(r.content for r in results)
        citations = [r.source for r in results]

    logger.info("Workflow[PLAN]")
    tool_name: Optional[str] = classification.get("tool_name")
    tool_args: dict = classification.get("tool_args") or {}
    needs_tool = bool(classification.get("needs_tool") and tool_name)

    if needs_tool and is_side_effecting(tool_name):
        logger.info("Workflow[APPROVAL_IF_NEEDED] tool=%s", tool_name)
        record = ApprovalRecord(
            session_id=session_id,
            plan=Plan(
                needs_retrieval=bool(classification.get("needs_retrieval")),
                tool_name=tool_name,
                tool_args=tool_args,
                is_side_effecting=True,
            ),
            rag_context=rag_context,
        )
        await approval_repo.save(record)
        return ChatResponse(
            reply=f"「{tool_name}」を実行するには承認が必要です。実行してよろしいですか？",
            requires_approval=True,
            approval_request_id=record.id,
            citations=citations,
        )

    if needs_tool:
        logger.info("Workflow[EXECUTE_TOOL] tool=%s", tool_name)
        try:
            tool_result = await execute_tool(tool_name, tool_args)
            history.add_system_message(f"Tool result ({tool_name}): {tool_result}")
        except Exception as exc:
            logger.error("Tool execution failed: %s", exc)
            history.add_system_message(f"Tool error: {exc}")
        await session_repo.save(session_id, history)

    logger.info("Workflow[RESPOND]")
    reply = await _respond(history, kernel, rag_context)
    history.add_assistant_message(reply)
    await session_repo.save(session_id, history)
    return ChatResponse(reply=reply, citations=citations)


async def resume_after_approval(
    approval_id: str,
    approved: bool,
    session_repo: SessionRepository,
    approval_repo: ApprovalRepository,
    kernel: Kernel,
) -> str:
    record = await approval_repo.get(approval_id)
    if not record:
        raise ValueError(f"Approval not found: {approval_id!r}")
    if record.status != "pending":
        raise ValueError(f"Approval already processed: {record.status!r}")

    record.status = "approved" if approved else "rejected"
    await approval_repo.save(record)

    history = await _get_or_create_session(record.session_id, session_repo)

    if not approved:
        reply = "操作はキャンセルされました。他にご用件はありますか？"
        history.add_assistant_message(reply)
        await session_repo.save(record.session_id, history)
        return reply

    plan = record.plan
    logger.info("Workflow[EXECUTE_TOOL] post-approval tool=%s", plan.tool_name)
    try:
        tool_result = await execute_tool(plan.tool_name, plan.tool_args)
        history.add_system_message(f"Tool result ({plan.tool_name}): {tool_result}")
    except Exception as exc:
        logger.error("Post-approval tool execution failed: %s", exc)
        history.add_system_message(f"Tool error: {exc}")
    await session_repo.save(record.session_id, history)

    logger.info("Workflow[RESPOND] post-approval")
    reply = await _respond(history, kernel, record.rag_context)
    history.add_assistant_message(reply)
    await session_repo.save(record.session_id, history)
    return reply
