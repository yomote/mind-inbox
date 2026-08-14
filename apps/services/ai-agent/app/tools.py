"""
Tool registry — MAF の @ai_function (現行 API 名は @tool) 化 (ADR 0016 M1-4 / #320)。

**このレジストリが LLM に見えるツールの唯一の真実源**。`exposed_tools()` が返した
`FunctionTool` がそのまま chat client の `options["tools"]` に載り、MAF ネイティブの
function calling がツール選択・引数生成・実行・承認を担う。プロンプトにツール一覧を
書き写す場所はもう無い (#320 の「定義とプロンプトの二重管理」を構造的に潰した)。

read-only / side-effecting の区分は FunctionTool の `approval_mode` メタデータ
(G1 = HITL 承認要否の判定源):

  "never_require"  — read-only。副作用なし、承認不要
  "always_require" — side-effecting。状態を変えるので必ず人間の承認を要する

**この 1 行が承認の実体**: MAF の function invocation loop が
`approval_mode == "always_require"` のツール呼び出しをすべて `function_approval_request`
に変えて実行前に返す。`is_side_effecting` は API 応答の文言組み立て等に使う読み取り
専用のヘルパで、承認の可否そのものはもう自前判定ではない。

**主体 (誰として実行するか) はモデルの出力から取らない** (Issue #313):
ツール引数は LLM が生成する = ユーザーの発話で左右できる文字列なので、そこに
`user_id` のような識別子を置くと「user_id=他人 で呼んで」というプロンプト 1 通で
IDOR が成立する。主体は必ず呼び出し側 (FastAPI / workflow) が与える `ToolContext`
から取る。この不変条件は tests/test_tools.py が registry 全体に対して機械検査する。
"""

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Optional

from agent_framework import (
    Content,
    FunctionInvocationContext,
    FunctionTool,
    function_middleware,
    tool,
)

from .config import get_settings
from .observability import fingerprint
from .rag import retrieve

logger = logging.getLogger(__name__)


# ── 実行コンテキスト (主体の出どころ) ─────────────────────────────────────────


@dataclass(frozen=True)
class ToolContext:
    """ツール 1 回の実行がどの主体・どの会話に属するか。**呼び出し側だけが作る**。

    - `session_id`: 実行中の会話。今この時点でこのサービスが持つ唯一の実行文脈。
    - `user_id`: 認証済みの主体。BFF (EasyAuth の oid) から渡るようになったら埋まる
      (セッションの所有者バインドは Issue #319 の範囲)。**LLM は決して埋められない**。
    - `citations`: ツールが引いた出典の集積先。**mutable にしてあるのが要点** —
      MAF は各ツール呼び出しを `contextvars.copy_context()` の中で走らせるので、
      ツール側で ContextVar を **再代入** しても呼び出し側には伝わらない。同じ
      list オブジェクトを共有して append する形だけが並列実行を跨いで届く
      (tests/test_tools.py が並列 2 本で pin する)。
    """

    session_id: str
    user_id: Optional[str] = None
    citations: list[str] = field(default_factory=list)


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


def use_tool_context(context: ToolContext):
    """`with use_tool_context(ctx):` の間だけ実行コンテキストを立てる。

    MAF の function invocation loop は chat client の中で回るので、**LLM を呼ぶ側が
    呼び出し全体をこれで囲む**。ループが何本ツールを走らせても同じ context が見える。
    """

    class _Scope:
        def __enter__(self):
            self._token = _tool_context.set(context)
            return context

        def __exit__(self, *exc_info):
            _tool_context.reset(self._token)
            return False

    return _Scope()


# LLM のツール引数に現れてはいけない「主体を指す名前」。ツールがこれらを宣言していない
# ことは test が機械検査する。middleware での除去は二重防御 (将来ツールが増えたときの保険)。
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
    description="Search the FAQ / knowledge base for passages relevant to a question",
    approval_mode="never_require",
)
async def search_faq(query: str) -> str:
    # ユーザー由来の文字列はログに出さない (rubric S3 / Issue #313)
    logger.info("Tool[search_faq] invoked query=%s", fingerprint(query))
    # RAG は rag.py のスタブのまま (#82: 「rag.py はスタブのまま」)。
    # v1 では自前分類プロンプトの needs_retrieval が retrieve() を呼んでいたが、
    # 分類を廃した今、検索するかどうかを決めるのは LLM の function calling。
    # 出典は戻り値の文字列ではなく ToolContext に積む — API 契約の citations は
    # 応答本文とは別のフィールドで、モデルに書き写させると事実と乖離しうる。
    results = await retrieve(query)
    context = current_tool_context()
    context.citations.extend(r.source for r in results)
    if not results:
        return "[stub] No relevant FAQ found."
    return "\n".join(r.content for r in results)


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


class UnknownExposedTool(ValueError):
    """LLM_EXPOSED_TOOLS に registry に無い名前が入っていた。"""


def exposed_tools() -> tuple[FunctionTool, ...]:
    """この構成で LLM に見せるツール (= `options["tools"]` に載るもの)。

    既定は空。`LLM_EXPOSED_TOOLS` の書式と既定オフの理由は config.py を参照。
    ここが registry から導出されているので、**registry に 1 本足せば LLM に渡る
    tools も増える** (tests/test_tools.py が pin する)。
    """
    raw = get_settings().llm_exposed_tools.strip()
    if not raw:
        return ()
    if raw == "*":
        return tuple(_REGISTRY.values())
    names = [name.strip() for name in raw.split(",") if name.strip()]
    unknown = [name for name in names if name not in _REGISTRY]
    if unknown:
        raise UnknownExposedTool(
            f"LLM_EXPOSED_TOOLS に registry に無いツール名が含まれています: {unknown}"
        )
    return tuple(_REGISTRY[name] for name in names)


# ── 主体識別子の防御 (Issue #313) ─────────────────────────────────────────────


@function_middleware
async def identity_arg_guard(context: FunctionInvocationContext, call_next) -> None:
    """モデルが送ってきた「主体を指す引数」を実行直前に捨てる (二重防御)。

    v1 の `_strip_identity_args` を MAF の FunctionMiddleware として置き直したもの。
    middleware は chat client の function invocation loop の中で必ず通るので、
    workflow を経由しない MAF 直呼び (将来の ChatAgent 化など) でも効く。

    **これが拾えない層があることを明示しておく**: MAF は middleware に渡す前に
    `tool.input_model.model_validate(...)` を通すため、**ツールが宣言していない**
    引数 (今の registry では `user_id` 等がこれに当たる) はここに届く前に pydantic が
    黙って捨てている (実測)。したがって「注入が試みられた事実」はここでは見えない。
    その観測は `report_identity_arg_attempts()` が LLM の生の function_call 引数に
    対して行う。ここが担うのは **ツールが将来そういう引数を宣言してしまった場合**に
    値が実行まで届かないようにすること。
    """
    arguments = context.arguments
    if isinstance(arguments, dict):
        dropped = [key for key in arguments if key.lower() in IDENTITY_ARG_NAMES]
        if dropped:
            # 値は出さない (LLM 由来 = ユーザー入力由来の文字列)。捨てたキー名だけ残す
            logger.warning(
                "Tool[%s] model-supplied identity args dropped: %s",
                context.function.name,
                dropped,
            )
            context.arguments = {
                key: value
                for key, value in arguments.items()
                if key.lower() not in IDENTITY_ARG_NAMES
            }
    await call_next()


def report_identity_arg_attempts(function_calls: list[Content]) -> list[str]:
    """LLM が生成した function_call の**生の引数**に主体識別子が混じっていたら記録する。

    無いと: MAF の引数バリデーションが未宣言の引数を黙って捨てるため、
    「user_id=他人 で呼んで」というプロンプト注入の**試行が痕跡なく消える**
    (成功も失敗も等しく無音になり、攻撃されていることに気づけない)。

    返すのは検出したツール名のリスト (呼び出し側がテスト・観測に使う)。
    """
    hits: list[str] = []
    for call in function_calls:
        if call.type != "function_call":
            continue
        arguments = call.parse_arguments() or {}
        dropped = sorted(k for k in arguments if k.lower() in IDENTITY_ARG_NAMES)
        if dropped:
            hits.append(call.name or "?")
            logger.warning(
                "Tool[%s] model-supplied identity args rejected: %s",
                call.name,
                dropped,
            )
    return hits


def function_invocation_limits() -> dict[str, Any]:
    """MAF の function invocation loop に掛ける上限 (既定は無制限なので必ず明示する)。"""
    settings = get_settings()
    return {
        "max_function_calls": settings.llm_max_function_calls,
        "max_iterations": settings.llm_max_tool_iterations,
    }
