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
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Union

from semantic_kernel import Kernel
from semantic_kernel.contents import ChatHistory

from .kernel import get_execution_settings
from .rag import retrieve
from .repositories import ApprovalRepository, SessionRepository
from .schemas import (
    ApprovalRecord,
    ChatResponse,
    ChatStreamDelta,
    ChatStreamDone,
    Plan,
)
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


def _is_retry_of_last_user_turn(history: ChatHistory, message: str) -> bool:
    """直近の履歴末尾が「同一内容の user 発言」で終わっているか。

    そうなるのは **アシスタント応答を返せずにターンが落ちた直後だけ** — 正常な
    ターンは必ず assistant メッセージで終わるため、同じ文面を後から送り直しても
    間に assistant が挟まる。つまりこの条件は「失敗したターンの再試行」を意味する。
    """
    for msg in reversed(history.messages):
        if msg.role.value == "system":
            # ツール結果などの system メッセージは判定に関係しないので読み飛ばす
            continue
        return msg.role.value == "user" and str(msg.content) == message
    return False


async def _append_user_message_once(
    session_id: str,
    message: str,
    history: ChatHistory,
    session_repo: SessionRepository,
) -> None:
    """ユーザー発言を履歴に 1 回だけ積む (#120 / ストリーミング失敗の再試行に対する冪等化)。

    ストリーミング (`run_workflow_stream`) が RESPOND 中に落ちると、ユーザー発言は
    保存済みなのに assistant 応答が無い状態で終わる。フロントはそれを検知して同じ
    sessionId / message で非ストリーミング `/chat` に自動フォールバックするため、
    素朴に add_user_message すると「assistant を挟まない同一 user ターンの重複」が
    履歴に残る。Mind Inbox の核は累積履歴 (後続ターンの文脈解釈にも効く) なので、
    再試行と判定できる場合は積み直さない。
    """
    if _is_retry_of_last_user_turn(history, message):
        logger.info(
            "Workflow[RECEIVE] session=%s — 直前の失敗ターンの再試行と判定、user メッセージの重複追加を抑止",
            session_id,
        )
        return
    history.add_user_message(message)
    await session_repo.save(session_id, history)


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


def _build_call_history(history: ChatHistory, rag_context: str) -> ChatHistory:
    """RAG コンテキストがある場合だけ system メッセージを足した呼び出し用履歴を作る。"""
    if not rag_context:
        return history
    call_history = ChatHistory()
    for msg in history.messages:
        call_history.messages.append(msg)
    call_history.add_system_message(f"Relevant context:\n{rag_context}")
    return call_history


async def _respond(
    history: ChatHistory,
    kernel: Kernel,
    rag_context: str = "",
) -> str:
    """最終的なアシスタント返答を生成する。"""
    call_history = _build_call_history(history, rag_context)

    svc = kernel.get_service("chat")
    result = await svc.get_chat_message_content(
        chat_history=call_history, settings=get_execution_settings()
    )
    return str(result)


async def _respond_stream(
    history: ChatHistory,
    kernel: Kernel,
    rag_context: str = "",
) -> AsyncIterator[str]:
    """_respond のストリーミング版。トークン (チャンク) 文字列を逐次 yield する。"""
    call_history = _build_call_history(history, rag_context)

    svc = kernel.get_service("chat")
    async for chunk in svc.get_streaming_chat_message_content(
        chat_history=call_history, settings=get_execution_settings()
    ):
        text = str(chunk) if chunk is not None else ""
        if text:
            yield text


@dataclass
class _TurnPlan:
    """RESPOND 直前までの共通前段 (RECEIVE〜EXECUTE_TOOL) の結果。

    run_workflow (一括) と run_workflow_stream (SSE) が同じ状態遷移を共有する
    ための内部表現。approval が入っている場合は RESPOND せずそれを返す。
    """

    history: ChatHistory
    rag_context: str = ""
    citations: list[str] = field(default_factory=list)
    approval: Optional[ChatResponse] = None


async def _prepare_respond(
    session_id: str,
    message: str,
    session_repo: SessionRepository,
    approval_repo: ApprovalRepository,
    kernel: Kernel,
) -> _TurnPlan:
    """RECEIVE → CLASSIFY → RETRIEVE_IF_NEEDED → PLAN → APPROVAL_IF_NEEDED → EXECUTE_TOOL。"""
    logger.info("Workflow[RECEIVE] session=%s", session_id)
    history = await _get_or_create_session(session_id, session_repo)
    await _append_user_message_once(session_id, message, history, session_repo)

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
        return _TurnPlan(
            history=history,
            rag_context=rag_context,
            citations=citations,
            approval=ChatResponse(
                reply=f"「{tool_name}」を実行するには承認が必要です。実行してよろしいですか？",
                requires_approval=True,
                approval_request_id=record.id,
                citations=citations,
            ),
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

    return _TurnPlan(history=history, rag_context=rag_context, citations=citations)


async def run_workflow(
    session_id: str,
    message: str,
    session_repo: SessionRepository,
    approval_repo: ApprovalRepository,
    kernel: Kernel,
) -> ChatResponse:
    plan = await _prepare_respond(
        session_id, message, session_repo, approval_repo, kernel
    )
    if plan.approval is not None:
        return plan.approval

    logger.info("Workflow[RESPOND]")
    reply = await _respond(plan.history, kernel, plan.rag_context)
    plan.history.add_assistant_message(reply)
    await session_repo.save(session_id, plan.history)
    return ChatResponse(reply=reply, citations=plan.citations)


async def run_workflow_stream(
    session_id: str,
    message: str,
    session_repo: SessionRepository,
    approval_repo: ApprovalRepository,
    kernel: Kernel,
) -> AsyncIterator[Union[ChatStreamDelta, ChatStreamDone]]:
    """run_workflow のストリーミング版 (#120 / ADR 0024)。

    RESPOND だけを LLM ストリーミングで逐次 yield し、完了時に従来 /chat と
    同一形の ChatResponse を ChatStreamDone で返す。承認が要るターンは
    逐次配信するものが無いので done のみを返す。履歴保存は一括版と同じ
    タイミング (全文確定後) で行う。
    """
    plan = await _prepare_respond(
        session_id, message, session_repo, approval_repo, kernel
    )
    if plan.approval is not None:
        yield ChatStreamDone(response=plan.approval)
        return

    logger.info("Workflow[RESPOND] streaming")
    parts: list[str] = []
    async for token in _respond_stream(plan.history, kernel, plan.rag_context):
        parts.append(token)
        yield ChatStreamDelta(text=token)

    reply = "".join(parts)
    plan.history.add_assistant_message(reply)
    await session_repo.save(session_id, plan.history)
    yield ChatStreamDone(response=ChatResponse(reply=reply, citations=plan.citations))


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
