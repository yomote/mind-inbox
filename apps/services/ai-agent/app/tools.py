"""
Tool registry — MAF の @ai_function (現行 API 名は @tool) 化 (ADR 0016 M1-4)。

read-only / side-effecting の区分は FunctionTool の `approval_mode`
メタデータとして持つ (G1 = HITL 承認要否の判定源):

  "never_require"  — read-only。副作用なし、承認不要
  "always_require" — side-effecting。状態を変えるので必ず人間の承認を要する

`is_side_effecting` はこのメタデータだけを見る — v1 (_ToolEntry.side_effecting)
と同一の判定挙動。ツールの中身はスタブのまま (実体化は M3-5)。

**主体 (誰として実行するか) はモデルの出力から取らない** (Issue #313):
ツール引数は LLM が生成する = ユーザーの発話で左右できる文字列なので、そこに
`user_id` のような識別子を置くと「user_id=他人 で呼んで」というプロンプト 1 通で
IDOR が成立する。主体は必ず呼び出し側 (FastAPI / workflow) が与える `ToolContext`
から取る。この不変条件は tests/test_tools.py が registry 全体に対して機械検査する。
"""

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Optional

from agent_framework import FunctionTool, tool

from .observability import fingerprint

logger = logging.getLogger(__name__)


# ── 実行コンテキスト (主体の出どころ) ─────────────────────────────────────────


@dataclass(frozen=True)
class ToolContext:
    """ツール 1 回の実行がどの主体・どの会話に属するか。**呼び出し側だけが作る**。

    - `session_id`: 実行中の会話。今この時点でこのサービスが持つ唯一の実行文脈。
    - `user_id`: 認証済みの主体。BFF (EasyAuth の oid) から渡るようになったら埋まる
      (セッションの所有者バインドは Issue #319 の範囲)。**LLM は決して埋められない**。
    """

    session_id: str
    user_id: Optional[str] = None


class ToolContextUnavailable(RuntimeError):
    """実行コンテキスト無しにツールが呼ばれた = 主体不明のまま実行しかけている。"""


_tool_context: ContextVar[Optional[ToolContext]] = ContextVar(
    "tool_context", default=None
)


def current_tool_context() -> ToolContext:
    """実行中のツールが主体を知るための唯一の入口 (未設定なら実行させない)。"""
    ctx = _tool_context.get()
    if ctx is None:
        raise ToolContextUnavailable(
            "Tool invoked without an execution context — 主体不明のまま実行できない"
        )
    return ctx


# LLM のツール引数に現れてはいけない「主体を指す名前」。ツールがこれらを宣言していない
# ことは test が機械検査する。ここでの除去は二重防御 (将来ツールが増えたときの保険)。
IDENTITY_ARG_NAMES = frozenset(
    {
        "user_id",
        "userid",
        "user",
        "owner_id",
        "principal_id",
        "subject",
        "sub",
        "oid",
        "account_id",
        "tenant_id",
        "session_id",
    }
)


# ── Read-only tools ───────────────────────────────────────────────────────────


@tool(
    name="search_faq",
    description="Search FAQ knowledge base",
    approval_mode="never_require",
)
async def search_faq(query: str) -> str:
    # ユーザー由来の文字列はログに出さない (rubric S3 / Issue #313)
    logger.info("Tool[search_faq] invoked query=%s", fingerprint(query))
    return f"[stub] FAQ result for '{query}': No relevant FAQ found."


@tool(
    name="get_inbox_stats",
    description="Get inbox statistics for the current user (read-only)",
    approval_mode="never_require",
)
async def get_inbox_stats() -> str:
    # 主体は引数ではなく実行コンテキストから取る (モデルは指定できない / Issue #313)
    ctx = current_tool_context()
    logger.info("Tool[get_inbox_stats] invoked session=%s", ctx.session_id)
    return "[stub] Inbox: 5 unread, 2 flagged, 0 urgent."


# ── Side-effecting tools ──────────────────────────────────────────────────────


@tool(
    name="send_reply",
    description="Send a reply to a message",
    approval_mode="always_require",
)
async def send_reply(to: str, body: str) -> str:
    # 宛先も本文も機微 (宛先は PII、本文は相談内容を含みうる) — 指紋だけ残す
    logger.info(
        "Tool[send_reply] invoked to=%s body=%s", fingerprint(to), fingerprint(body)
    )
    return f"[stub] Reply sent to {to}."


@tool(
    name="archive_message",
    description="Archive a message by ID",
    approval_mode="always_require",
)
async def archive_message(message_id: str) -> str:
    logger.info("Tool[archive_message] invoked id=%s", fingerprint(message_id))
    return f"[stub] Message {message_id} archived."


# ── Registry — single source of truth for callable + approval metadata ────────

_REGISTRY: dict[str, FunctionTool] = {
    t.name: t for t in (search_faq, get_inbox_stats, send_reply, archive_message)
}


def is_side_effecting(tool_name: str | None) -> bool:
    entry = _REGISTRY.get(tool_name) if tool_name else None
    return entry is not None and entry.approval_mode == "always_require"


def _strip_identity_args(tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
    """モデルが送ってきた「主体を指す引数」を捨てる (二重防御)。

    ツール側が宣言していない以上、渡しても TypeError になるだけだが、その形だと
    **注入の試行が「ツールが壊れた」に化けて見分けられない**。ここで落として警告に残す。
    """
    dropped = [k for k in tool_args if k.lower() in IDENTITY_ARG_NAMES]
    if dropped:
        # 値は出さない (LLM 由来 = ユーザー入力由来の文字列)。捨てたキー名だけ残す
        logger.warning(
            "Tool[%s] model-supplied identity args dropped: %s", tool_name, dropped
        )
    return {k: v for k, v in tool_args.items() if k.lower() not in IDENTITY_ARG_NAMES}


async def execute_tool(
    tool_name: str, tool_args: dict[str, Any], context: ToolContext
) -> str:
    """ツールを実行する唯一の入口。主体は `context` から来る (tool_args からは来ない)。"""
    entry = _REGISTRY.get(tool_name)
    if entry is None:
        raise ValueError(f"Unknown tool: {tool_name!r}")
    args = _strip_identity_args(tool_name, tool_args)
    token = _tool_context.set(context)
    try:
        # FunctionTool は呼び出し可能 (wrapped 関数へ委譲) — 戻り値は関数の生の str
        return await entry(**args)
    finally:
        _tool_context.reset(token)
