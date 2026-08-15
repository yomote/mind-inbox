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
    MiddlewareTermination,
    function_middleware,
    tool,
)
from agent_framework.exceptions import UserInputRequiredException

from .config import get_settings
from .observability import fingerprint, new_ref, redact_framework_tool_logs
from .rag import retrieve

logger = logging.getLogger(__name__)

# **import 時に張る** (#417 P2): ツールが走る経路は必ずこのモジュールを import する
# ので、本番の入口 (main.py) を待たずにここで掛けておけば、workflow 経由でも
# 将来の MAF 直呼びでも取りこぼしが無い。冪等なので何度 import されても 1 枚。
redact_framework_tool_logs()


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
    - `choices`: `offer_choices` が積んだ選択肢 (#432-b)。citations と同じ理由で
      mutable。**このターンで 1 度だけ埋まる** — 空でないことが「すでに提示済み」の
      判定そのものになっている (`offer_choices` を参照)。
    - `executions`: **MAF が実際に実行した**ツール呼び出しの記録 (`tool_boundary`
      middleware が積む)。citations と同じ理由で mutable。用途は
      `ToolExecution` の docstring を参照。
    """

    session_id: str
    user_id: Optional[str] = None
    citations: list[str] = field(default_factory=list)
    choices: list[str] = field(default_factory=list)
    executions: list["ToolExecution"] = field(default_factory=list)


@dataclass(frozen=True)
class ToolExecution:
    """ツールが 1 回実行された事実 (`tool_boundary` middleware が記録する)。

    **なぜ MAF の応答 (function_result) だけでは足りないか** (#417 P1 / 実測):
    MAF の function invocation loop は、そのターンで**新しい承認要求が出た瞬間に
    return する**ため、`_prepend_function_call_messages` を通らない。結果、
    「承認済みツールを実行 → モデルが別の副作用ツールを要求」というターンでは、
    **実行済みツールの function_result が応答から丸ごと落ちる**。応答だけを見て
    履歴を書くと「実行されたのに履歴に無い」= 次のターンで同じ副作用ツールが
    もう一度呼ばれる、が静かに成立する。

    そこで**ツール境界で直接**記録する。`call_id` で応答側の function_result と
    突き合わせられるので、両方に現れるものは二重に履歴へ積まない (workflow 側)。

    `error_ref` が入っているものは実行が例外で失敗したもの。例外文そのものは
    どこにも持たない — 出せるのは ref だけ (Issue #313)。
    """

    call_id: str
    name: str
    result: Optional[str] = None
    error_ref: Optional[str] = None


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


# ── 対話ツール (副作用なし / #432-b) ──────────────────────────────────────────
#
# **選択肢は「会話の分岐」であって「実行の可否」ではない** (#432 design-gate /
# PO 裁定 2026-08-15 の 2)。承認 (`always_require`) の中断 + checkpoint 機構は
# あえて再利用しない — 選択肢を出したターンはそのまま完結し、サーバに待ち状態を
# 残さない。ユーザーの選択は**次の発話**として普通に届く (完了型)。
#
# 再利用すると、承認が抱えている問題群 (TTL 失効 / 404 の多義性 / 却下の送り忘れ /
# in-memory の checkpoint 滞留) を、**それが 1 つも要らない機能**に丸ごと持ち込む
# ことになる。「選択肢を無視して自由に書く」は正常な操作なので、そのたびにサーバへ
# 却下相当を送る形にしてはいけない。

# 1 ターンに出せる選択肢の上限 (docs/frontend/ui_specs/dialogue-session.mdx §5.10)。
# 読み上げと画面の両方で「選ぶ」より「読む」が重くならない範囲に縛る。
MAX_CHOICES = 3
# 選択肢 1 つの最大文字数。**タップした文言がそのまま次の発話として送られる**ので、
# 切り詰めは「ユーザーが送っていない中途半端な発話」を作る = 絶対にしない。
# 超えたら提示そのものを断り、モデルに短く作り直させる。
MAX_CHOICE_LENGTH = 40

# **そのターンが完了して初めて意味を持つ**ツール (#432-b / PR #448 Codex P2)。
#
# `offer_choices` の成果は応答 payload (`ChatResponse.choices`) にしか現れず、副作用は
# 何も無い。**ターンが途中で落ちたときに実行を履歴へ残してはいけない**:
#
#   1. ストリーミングが done の前に切れる → `_flush_partial_tool_outcomes` が
#      「N 件の選択肢を提示しました」を履歴に積む。**payload は捨てられている**
#   2. フロントは非ストリーミング `/chat` へフォールバックして同じ発話を送り直す
#   3. モデルは履歴を読んで「もう提示した」と判断し、ツールを呼び直さない
#   4. 結果: 本文は「近いものを選んでください」なのに、画面にボタンが 1 つも無い
#
# 副作用ツール (#417 P1) が「実行したのに履歴に無い」を恐れるのとちょうど逆で、
# こちらは**実行していないことにするのが安全側**。判定をここに集約しておかないと、
# 将来 payload だけのツールを足した人が同じ穴を踏む。
TURN_LOCAL_TOOLS = frozenset({"offer_choices"})


@tool(
    name="offer_choices",
    description=(
        "Offer the user up to 3 short, tappable choices to pick from when an open "
        "question is unlikely to move the conversation forward. Each choice is sent "
        "verbatim as the user's next message if tapped. The user can always ignore "
        "them and answer freely, so never use this to force a decision."
    ),
    approval_mode="never_require",
)
async def offer_choices(options: list[str]) -> str:
    """選択肢を応答に添える (副作用なし / 承認不要)。

    戻り値は**モデルへの指示**であって画面に出るものではない。実際に画面へ届くのは
    `ToolContext.choices` に積んだ側で、これが `ChatResponse.choices` になる。

    **不正な引数は例外にせず、断りの文字列を返す**。例外にすると `tool_boundary` が
    ref だけの `ToolExecutionFailed` に一般化するので、モデルには「失敗した」しか
    見えず直しようがない (同じ引数で呼び直す)。断りの文で条件を伝えれば作り直せる。
    """
    ctx = current_tool_context()
    # 選択肢の文言は会話内容から作られる = 相談の中身なので**本文はログに出さない**
    # (件数だけ / Issue #313)。
    logger.info(
        "Tool[offer_choices] invoked session=%s count=%d", ctx.session_id, len(options)
    )

    if ctx.choices:
        # 同じターンで 2 回呼ばれた。2 回目を足すと上限 (MAX_CHOICES) を超えた帯が
        # 画面に出る。**先に提示したものを黙って置き換えない** — モデルが 1 回目の
        # 結果を前提に本文を書いている可能性があるため
        logger.warning("Tool[offer_choices] このターンではすでに提示済み — 追加しない")
        return (
            "このターンではすでに選択肢を提示しています。"
            "追加はできないので、本文の作成に進んでください。"
        )

    normalized: list[str] = []
    for option in options:
        text = option.strip()
        if not text or text in normalized:
            continue
        normalized.append(text)

    if not normalized:
        return "選択肢が空でした。提示していません。選択肢なしで応答してください。"
    if len(normalized) > MAX_CHOICES:
        return (
            f"選択肢は {MAX_CHOICES} 件までです ({len(normalized)} 件受け取りました)。"
            f"提示していません。{MAX_CHOICES} 件までに絞って呼び直してください。"
        )
    too_long = [text for text in normalized if len(text) > MAX_CHOICE_LENGTH]
    if too_long:
        # 文言は出さない (相談内容)。長さだけで何を直せばよいか伝わる
        return (
            f"選択肢は 1 つ {MAX_CHOICE_LENGTH} 文字までです "
            f"({len(too_long)} 件が超過)。提示していません。短くして呼び直してください。"
        )

    ctx.choices.extend(normalized)
    return (
        f"{len(normalized)} 件の選択肢を提示しました。"
        "本文では選択肢を列挙せず、選んでも自由に書いてもよいことが伝わる短い一文にしてください。"
    )


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
    t.name: t
    for t in (search_faq, get_inbox_stats, offer_choices, send_reply, archive_message)
}


def is_side_effecting(tool_name: str | None) -> bool:
    entry = _REGISTRY.get(tool_name) if tool_name else None
    return entry is not None and entry.approval_mode == "always_require"


class UnknownExposedTool(ValueError):
    """LLM_EXPOSED_TOOLS に registry に無い名前が入っていた。"""


def exposed_tools(exclude: frozenset[str] = frozenset()) -> tuple[FunctionTool, ...]:
    """この構成で LLM に見せるツール (= `options["tools"]` に載るもの)。

    既定は空。`LLM_EXPOSED_TOOLS` の書式と既定オフの理由は config.py を参照。
    ここが registry から導出されているので、**registry に 1 本足せば LLM に渡る
    tools も増える** (tests/test_tools.py が pin する)。

    `exclude` は「この呼び出しでは成果を運べないツール」を落とすためのもの
    (PR #448 Codex P2)。**運べないなら呼ばせない**のが要点 — 呼ばせてから結果を
    捨てると、モデルは提示したつもりで本文を書き、画面には何も出ない。今の用途は
    `/approve` の再開経路 (応答型が reply しか運べない / workflow.py を参照)。
    """
    raw = get_settings().llm_exposed_tools.strip()
    if not raw:
        return ()
    if raw == "*":
        selected: tuple[FunctionTool, ...] = tuple(_REGISTRY.values())
    else:
        names = [name.strip() for name in raw.split(",") if name.strip()]
        unknown = [name for name in names if name not in _REGISTRY]
        if unknown:
            raise UnknownExposedTool(
                f"LLM_EXPOSED_TOOLS に registry に無いツール名が含まれています: {unknown}"
            )
        selected = tuple(_REGISTRY[name] for name in names)
    if not exclude:
        return selected
    return tuple(t for t in selected if t.name not in exclude)


# ── ツール境界 (実行の記録 + 例外の一般化) ───────────────────────────────────

# 一般化した例外文の目印。workflow 側がこれを見て「ref は境界で採番済み」と分かる。
_TOOL_ERROR_REF_PREFIX = "ToolExecutionFailed ref="


class ToolExecutionFailed(RuntimeError):
    """ツール本体の例外を **ref だけに置き換えた**もの (原文は外に出さない)。"""


def tool_error_ref(exception_text: Optional[str]) -> Optional[str]:
    """MAF の function_result に載った例外文から、境界で採番した ref を取り出す。

    `tool_boundary` が一般化していない失敗 (MAF 内部が作る "Function not found" 等)
    では None を返す — 呼び出し側はそこで新しい ref を採番してログに残す。
    """
    if not exception_text or _TOOL_ERROR_REF_PREFIX not in exception_text:
        return None
    return exception_text.split(_TOOL_ERROR_REF_PREFIX, 1)[1].split()[0] or None


@function_middleware
async def tool_boundary(context: FunctionInvocationContext, call_next) -> None:
    """ツール境界で (1) 実行を記録し (2) 例外を ref だけに一般化する (#417 P2 / Issue #418)。

    **(2) が無いと何が静かに通るか**: MAF は本体が投げた例外を function_result に
    落とす前に `agent_framework._tools` ロガーへ `Exception: %r` でそのまま出す
    (実測 / WARNING レベル)。ツールが上流の URL・外部応答・相談内容を含む例外を
    投げれば、こちらの `_record_tool_outcomes` が指紋化しても**その前に原文が
    Log Analytics へ流れている**。境界で `ToolExecutionFailed(ref)` に差し替えれば、
    フレームワークが出せるのは ref だけになる。

    `raise ... from None` にしているのは、`__cause__` として原文の例外を繋ぐと
    traceback 整形 (`exc_info=True` を使う第三者コード) の最終行に原文が復活する
    ため。**原文はこのプロセスのログにも残さず、指紋だけを残す**。

    **(1)** は #417 P1: 承認要求で打ち切られたターンでは実行済みツールの
    function_result が応答から落ちる (`ToolExecution` の docstring)。

    MAF が制御フローに使う例外 (`MiddlewareTermination` /
    `UserInputRequiredException`) は握らずそのまま通す — ここで一般化すると
    「承認待ち」「middleware による打ち切り」が「ツールが失敗した」に化ける。
    """
    name = context.function.name
    call_id = str(context.metadata.get("call_id") or "")
    # 実行コンテキストは呼び出し側 (workflow) が立てる。MAF を直に叩く経路では
    # 立っていないことがあり、そこでは記録だけを諦める (例外の一般化は常に効く)。
    execution_log = getattr(_tool_context.get(), "executions", None)
    try:
        await call_next()
    except (MiddlewareTermination, UserInputRequiredException):
        raise
    except Exception as exc:
        ref = new_ref()
        logger.error(
            "Tool execution failed ref=%s tool=%s kind=execution detail=%s",
            ref,
            name,
            fingerprint(str(exc)),
        )
        if execution_log is not None:
            execution_log.append(
                ToolExecution(call_id=call_id, name=name, error_ref=ref)
            )
        raise ToolExecutionFailed(f"{_TOOL_ERROR_REF_PREFIX}{ref}") from None
    if execution_log is not None:
        # MAF が function_result を作るときと**同じ正規化**を通す (`Content` の
        # 生リストや非文字列がそのまま履歴に出ないように)。応答経由で記録された
        # 場合と履歴の 1 行が一致する形にしておく
        normalized = Content.from_function_result(call_id, result=context.result)
        execution_log.append(
            ToolExecution(call_id=call_id, name=name, result=str(normalized.result))
        )


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
