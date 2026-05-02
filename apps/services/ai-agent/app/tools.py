"""
Tool registry.

ReadOnlyPlugin  — side-effect free, no approval required.
SideEffectPlugin — mutates state, always requires human approval.
"""

import logging
from typing import Any, Awaitable, Callable, NamedTuple

from semantic_kernel.functions import kernel_function

logger = logging.getLogger(__name__)


class _ToolEntry(NamedTuple):
    fn: Callable[..., Awaitable[str]]
    side_effecting: bool


# ── Read-only tools ───────────────────────────────────────────────────────────

class ReadOnlyPlugin:
    @kernel_function(name="search_faq", description="Search FAQ knowledge base")
    async def search_faq(self, query: str) -> str:
        logger.info("Tool[search_faq] query=%r", query)
        return f"[stub] FAQ result for '{query}': No relevant FAQ found."

    @kernel_function(name="get_inbox_stats", description="Get inbox statistics (read-only)")
    async def get_inbox_stats(self, user_id: str = "default") -> str:
        logger.info("Tool[get_inbox_stats] user=%r", user_id)
        return "[stub] Inbox: 5 unread, 2 flagged, 0 urgent."


# ── Side-effecting tools ──────────────────────────────────────────────────────

class SideEffectPlugin:
    @kernel_function(name="send_reply", description="Send a reply to a message")
    async def send_reply(self, to: str, body: str) -> str:
        logger.info("Tool[send_reply] to=%r", to)
        return f"[stub] Reply sent to {to}."

    @kernel_function(name="archive_message", description="Archive a message by ID")
    async def archive_message(self, message_id: str) -> str:
        logger.info("Tool[archive_message] id=%r", message_id)
        return f"[stub] Message {message_id} archived."


# ── Registry — single source of truth for callable + side-effect flag ─────────

READONLY_PLUGIN = ReadOnlyPlugin()
SIDEEFFECT_PLUGIN = SideEffectPlugin()

_REGISTRY: dict[str, _ToolEntry] = {
    "search_faq":      _ToolEntry(fn=READONLY_PLUGIN.search_faq,       side_effecting=False),
    "get_inbox_stats": _ToolEntry(fn=READONLY_PLUGIN.get_inbox_stats,   side_effecting=False),
    "send_reply":      _ToolEntry(fn=SIDEEFFECT_PLUGIN.send_reply,      side_effecting=True),
    "archive_message": _ToolEntry(fn=SIDEEFFECT_PLUGIN.archive_message, side_effecting=True),
}


def is_side_effecting(tool_name: str) -> bool:
    entry = _REGISTRY.get(tool_name)
    return entry is not None and entry.side_effecting


async def execute_tool(tool_name: str, tool_args: dict[str, Any]) -> str:
    entry = _REGISTRY.get(tool_name)
    if entry is None:
        raise ValueError(f"Unknown tool: {tool_name!r}")
    return await entry.fn(**tool_args)
