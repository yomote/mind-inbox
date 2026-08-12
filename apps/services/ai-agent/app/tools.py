"""
Tool registry — MAF の @ai_function (現行 API 名は @tool) 化 (ADR 0016 M1-4)。

read-only / side-effecting の区分は FunctionTool の `approval_mode`
メタデータとして持つ (G1 = HITL 承認要否の判定源):

  "never_require"  — read-only。副作用なし、承認不要
  "always_require" — side-effecting。状態を変えるので必ず人間の承認を要する

`is_side_effecting` はこのメタデータだけを見る — v1 (_ToolEntry.side_effecting)
と同一の判定挙動。ツールの中身はスタブのまま (実体化は M3-5)。
"""

import logging
from typing import Any

from agent_framework import FunctionTool, tool

logger = logging.getLogger(__name__)


# ── Read-only tools ───────────────────────────────────────────────────────────


@tool(
    name="search_faq",
    description="Search FAQ knowledge base",
    approval_mode="never_require",
)
async def search_faq(query: str) -> str:
    logger.info("Tool[search_faq] query=%r", query)
    return f"[stub] FAQ result for '{query}': No relevant FAQ found."


@tool(
    name="get_inbox_stats",
    description="Get inbox statistics (read-only)",
    approval_mode="never_require",
)
async def get_inbox_stats(user_id: str = "default") -> str:
    logger.info("Tool[get_inbox_stats] user=%r", user_id)
    return "[stub] Inbox: 5 unread, 2 flagged, 0 urgent."


# ── Side-effecting tools ──────────────────────────────────────────────────────


@tool(
    name="send_reply",
    description="Send a reply to a message",
    approval_mode="always_require",
)
async def send_reply(to: str, body: str) -> str:
    logger.info("Tool[send_reply] to=%r", to)
    return f"[stub] Reply sent to {to}."


@tool(
    name="archive_message",
    description="Archive a message by ID",
    approval_mode="always_require",
)
async def archive_message(message_id: str) -> str:
    logger.info("Tool[archive_message] id=%r", message_id)
    return f"[stub] Message {message_id} archived."


# ── Registry — single source of truth for callable + approval metadata ────────

_REGISTRY: dict[str, FunctionTool] = {
    t.name: t for t in (search_faq, get_inbox_stats, send_reply, archive_message)
}


def is_side_effecting(tool_name: str | None) -> bool:
    entry = _REGISTRY.get(tool_name) if tool_name else None
    return entry is not None and entry.approval_mode == "always_require"


async def execute_tool(tool_name: str, tool_args: dict[str, Any]) -> str:
    entry = _REGISTRY.get(tool_name)
    if entry is None:
        raise ValueError(f"Unknown tool: {tool_name!r}")
    # FunctionTool は呼び出し可能 (wrapped 関数へ委譲) — 戻り値は関数の生の str
    return await entry(**tool_args)
