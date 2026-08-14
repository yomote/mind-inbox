"""
/chat・/approve のオーケストレーション — Microsoft Agent Framework (MAF) graph Workflow。

v1 の自前 7 状態 FSM を MAF の graph Workflow に写し (ADR 0016 / M1-3)、
さらに **ツール選択・実行・承認を MAF ネイティブの function calling に載せた** (#320)。

グラフ (エッジ配送は MAF の型付きメッセージルーティング):

    receive → converse → finish

- **ツールは chat client の `options["tools"]` に載る**。どのツールを呼ぶか・引数は
  何かを決めるのは LLM の function calling で、実行も MAF の invocation loop が行う。
  自前の分類プロンプト (CLASSIFY) と自前のツール実行 (EXECUTE_TOOL) は廃止した。
  結果、**ツールを使わない通常ターンの LLM 往復は 2 回 (分類 + 応答) から 1 回**になる。
- **HITL 承認は `@tool(approval_mode="always_require")` が起点**。MAF は副作用ツールの
  呼び出しを実行せず `function_approval_request` として返す。converse executor が
  それを MAF 標準の request-response 機構 (`ctx.request_info` + `@response_handler`)
  に載せ替えて中断する。中断時点の全状態は MAF checkpoint が保持する。
- **approvalRequestId は checkpoint 参照への写像** (v1 から不変): FastAPI 境界の薄い
  アダプタ (`run_workflow` / `resume_after_approval`) が request_id → checkpoint_id の
  対応を `ApprovalRecord` に記録し、API 契約 (requiresApproval / approvalRequestId) を
  不変に保つ。/approve は checkpoint から `workflow.run(responses=..., checkpoint_id=...)`
  で再開し、承認は `to_function_approval_response` として会話に差し戻される。
- **ストリーミング**: converse executor がトークンを intermediate output として
  yield し、アダプタが ChatStreamDelta へ写す (契約は #120 / ADR 0024 のまま)。
"""

# NOTE: `from __future__ import annotations` を使わない — MAF の @handler /
# @response_handler はデコレート時に実型の annotation を検査するため、
# PEP 563 の文字列 annotation では登録に失敗する。

import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Never, Optional, Union

from agent_framework import (
    BaseChatClient,
    CheckpointStorage,
    Content,
    Executor,
    InMemoryCheckpointStorage,
    Message,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    WorkflowEvent,
    handler,
    response_handler,
)
from agent_framework import ChatResponse as MafChatResponse
from agent_framework_azure_cosmos import CosmosCheckpointStorage
from pydantic import BaseModel

from .agents import chat, chat_stream, get_chat_client
from .config import get_settings
from .history import ChatHistory
from .observability import fingerprint, new_ref
from .prompts import CHAT_SYSTEM_PROMPT
from .repositories import ApprovalRepository, SessionRepository
from .schemas import (
    ApprovalRecord,
    ChatResponse,
    ChatStreamDelta,
    ChatStreamDone,
    Plan,
)
from .tools import (
    ToolContext,
    exposed_tools,
    report_identity_arg_attempts,
    use_tool_context,
)

logger = logging.getLogger(__name__)

_WORKFLOW_NAME = "mind-inbox-chat-turn"

_REJECTION_REPLY = "操作はキャンセルされました。他にご用件はありますか？"

# MAF が「引数がツールのスキーマに合わなかった」ときに function_result へ入れる定型文
# (agent_framework._tools._auto_invoke_function)。実行そのものが失敗した場合の
# "Error: Function failed." とはここで区別する (#320 / 段④)。
# **上流がこの文言を変えたら区別が静かに壊れる** ので、MAF の実ループを通す
# tests/test_workflow_tools.py の引数エラーのテストが赤になるようにしてある。
_MAF_ARGUMENT_ERROR_PREFIX = "Error: Argument parsing failed."


# checkpoint に入り得るアプリの pydantic 型 (edge を流れるメッセージ + HITL payload)。
# CosmosCheckpointStorage の復元は許可リスト式 (JSON + pickle ハイブリッド) なので、
# ここに登録が無い型は **保存は通るのに復元 (= /approve の再開) だけが落ちる**。
# executor 間のメッセージ型を増やしたら必ずここにも足すこと
# (test_workflow_checkpoint_storage.py が fake ストア経由で encode → decode を通して pin する)。
#
# **MAF の Content / Message 型はここに要らない** (実測 / #320): 許可リストの判定は
# pickle の**トップレベルの型**に対して行われ、許可された型の内部に入れ子になった
# オブジェクトは一緒に運ばれる。加えて `ApprovalRequest.pending_messages` は
# `Message.to_dict()` で dict に落として持つので、checkpoint の payload に MAF の
# クラスが現れること自体が無い。
_APP_CHECKPOINT_TYPES = [
    "app.schemas:ChatResponse",
    "app.schemas:Plan",
    "app.workflow:ApprovalDecision",
    "app.workflow:ApprovalRequest",
    "app.workflow:ChatTurn",
    "app.workflow:FinalReply",
]


def _cosmos_enabled() -> bool:
    return bool(get_settings().cosmos_endpoint)


# checkpoint storage は構成で 2 系統 (#188):
#
# - **Cosmos (COSMOS_ENDPOINT あり)**: MAF 公式 CosmosCheckpointStorage の共有ストア。
#   プロセス再起動を跨いで /approve が再開できる (この Issue の本体)。掃除は
#   **コンテナ TTL (bicep 宣言) が主経路 + 解決時 delete が即時分** — 下の registry は
#   使わない (プロセス内参照に依存すると再起動で再開できず、永続化の意味が無い)。
# - **in-memory (未設定)**: run ごとに新品を作る。プロセス共有の 1 個にすると、承認と
#   無関係な全ターンの checkpoint が削除経路なしに溜まり続ける (PR #243 レビュー指摘 /
#   1 GiB レプリカでメモリを圧迫)。承認待ちで中断した run の storage だけを registry に
#   保持し、/approve の解決で解放する (in-memory には TTL が無いための掃除機構)。
def _new_checkpoint_storage() -> CheckpointStorage:
    if _cosmos_enabled():
        settings = get_settings()
        from . import cosmos

        # container_client を渡す = create_*_if_not_exists を踏まない
        # (data plane ロールに作成権限は無い。器は bicep が宣言する)。
        # storage 自体は薄い wrapper なので run ごとに作ってよい —
        # 「共有」の実体は cosmos.get_container が返す共有コンテナ。
        return CosmosCheckpointStorage(
            container_client=cosmos.get_container(
                settings.cosmos_checkpoints_container
            ),
            database_name=settings.cosmos_database,
            container_name=settings.cosmos_checkpoints_container,
            allowed_checkpoint_types=_APP_CHECKPOINT_TYPES,
        )
    return InMemoryCheckpointStorage()


_pending_run_storages: dict[str, CheckpointStorage] = {}


def get_pending_checkpoint_storage(approval_id: str) -> Optional[CheckpointStorage]:
    """承認待ち run の checkpoint storage (解決済み・不明 ID は None)。

    in-memory 構成専用の掃除機構。Cosmos 構成では registry を使わないので常に None。
    """
    return _pending_run_storages.get(approval_id)


# ── Session history helpers (v1 から不変の防御仕様) ───────────────────────────


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
        if msg.role == "system":
            # ツール結果などの system メッセージは判定に関係しないので読み飛ばす
            continue
        return msg.role == "user" and msg.text == message
    return False


async def _append_user_message_once(
    session_id: str,
    message: str,
    history: ChatHistory,
    session_repo: SessionRepository,
) -> None:
    """ユーザー発言を履歴に 1 回だけ積む (#120 / ストリーミング失敗の再試行に対する冪等化)。

    ストリーミング (`run_workflow_stream`) が応答生成中に落ちると、ユーザー発言は
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


# ── LLM helpers ───────────────────────────────────────────────────────────────


def _resolve_client(client: Optional[BaseChatClient]) -> BaseChatClient:
    """chat client の遅延解決 (縮退挙動を SK kernel 時代と同一に保つ)。

    未注入 (None = 本番経路) なら LLM を実際に呼ぶこの時点で初めて構築する。
    資格情報なしでも起動・RECEIVE (履歴保存)・/approve の ID 検証までは動き、
    失敗は LLM 呼び出しで表面化する — SK kernel の「構築は成功し get_service で
    落ちる」と同じ失敗面。テストは fake を注入する。
    """
    return client if client is not None else get_chat_client()


def _contents(response: MafChatResponse) -> list[Content]:
    return [content for msg in response.messages for content in msg.contents]


def _function_calls(response: MafChatResponse) -> list[Content]:
    return [c for c in _contents(response) if c.type == "function_call"]


def _approval_requests(response: MafChatResponse) -> list[Content]:
    """MAF が承認待ちで返した function_approval_request (= user_input_requests)。"""
    return [c for c in _contents(response) if c.type == "function_approval_request"]


def _tool_names_by_call_id(messages: Sequence[Message]) -> dict[str, str]:
    return {
        content.call_id: content.name
        for msg in messages
        for content in msg.contents
        if content.type == "function_call" and content.call_id and content.name
    }


def _record_tool_outcomes(
    history: ChatHistory,
    response: MafChatResponse,
    call_names: Optional[dict[str, str]] = None,
) -> None:
    """MAF が実行したツールの結果 / 失敗を履歴に写す。

    v1 の EXECUTE_TOOL が担っていた「結果は履歴に、詳細はログに」を、MAF の
    function_result content に対して行う。**3 区分に分ける** (#320 段④):

      成功        → `Tool result (name): <戻り値>`
      引数エラー  → `Tool argument error (name): ...` — LLM が作った引数がツールの
                    スキーマに合わなかった。**"Tool error" に埋もれさせない** —
                    ツール定義とモデルの噛み合わせが悪いという別種の事故で、
                    直し方 (description / 引数名) が実行時例外とはまったく違う。
      実行エラー  → `Tool error (name): ...`

    例外文・引数の値は履歴に入れない — 履歴はそのまま LLM へ再送され、最終的に
    ユーザーの画面まで届きうる出口 (上流のエンドポイント名等が漏れる)。詳細は
    サーバのログにだけ残し、ref で突き合わせる (Issue #313)。
    """
    # 承認再開のターンでは function_call は checkpoint 側 (pending_messages) にあり、
    # 応答には function_result しか来ない。呼び出し側が名前の手がかりを渡す。
    names = {**(call_names or {}), **_tool_names_by_call_id(response.messages)}
    for content in _contents(response):
        if content.type != "function_result":
            continue
        name = names.get(content.call_id or "", "?")
        result = content.result
        if content.exception is None:
            history.add_system_message(f"Tool result ({name}): {result}")
            continue
        ref = new_ref()
        if isinstance(result, str) and result.startswith(_MAF_ARGUMENT_ERROR_PREFIX):
            # 引数の値も検証エラー文もユーザー入力由来なので指紋だけ残す
            logger.error(
                "Tool argument validation failed ref=%s tool=%s kind=schema_validation detail=%s",
                ref,
                name,
                fingerprint(str(content.exception)),
            )
            history.add_system_message(
                f"Tool argument error ({name}): "
                f"引数がツールの定義と合いませんでした (ref: {ref})"
            )
            continue
        logger.error(
            "Tool execution failed ref=%s tool=%s kind=execution detail=%s",
            ref,
            name,
            fingerprint(str(content.exception)),
        )
        history.add_system_message(
            f"Tool error ({name}): 実行に失敗しました (ref: {ref})"
        )


# ── Workflow messages (executor 間で流れる型 = エッジ配送のルーティングキー) ──


class ChatTurn(BaseModel):
    """workflow への入力 (start executor が受ける)。"""

    session_id: str
    message: str


class ApprovalRequest(BaseModel):
    """HITL request_info の payload。checkpoint に pending として保存される。

    `pending_messages` は MAF が承認待ちで返したメッセージ列を `Message.to_dict()`
    で dict に落としたもの。**MAF のクラスを直接持たない**のは checkpoint の
    復元許可リスト (`_APP_CHECKPOINT_TYPES`) をアプリの型だけに保つため。
    再開時はここから `Message.from_dict` で戻し、承認応答を付けて会話に差し戻す。
    """

    session_id: str
    plan: Plan
    approval_content_id: str
    pending_messages: list[dict] = []
    citations: list[str] = []


class ApprovalDecision(BaseModel):
    """HITL の応答型 (/approve の approved を写す)。"""

    approved: bool


class FinalReply(BaseModel):
    session_id: str
    reply: str
    citations: list[str] = []


# ── Executors ─────────────────────────────────────────────────────────────────


class ReceiveExecutor(Executor):
    """RECEIVE: セッション確保 + user 発言の冪等追加。"""

    def __init__(self, session_repo: SessionRepository):
        super().__init__(id="receive")
        self._session_repo = session_repo

    @handler
    async def receive(self, turn: ChatTurn, ctx: WorkflowContext[ChatTurn]) -> None:
        logger.info("Workflow[RECEIVE] session=%s", turn.session_id)
        history = await _get_or_create_session(turn.session_id, self._session_repo)
        await _append_user_message_once(
            turn.session_id, turn.message, history, self._session_repo
        )
        await ctx.send_message(turn)


class ConverseExecutor(Executor):
    """CONVERSE: ツール付きで LLM を 1 回呼び、承認が要れば中断する。

    v1 の CLASSIFY / RETRIEVE / PLAN / EXECUTE_TOOL / RESPOND がここに畳まれた。
    ツールを使わない通常ターンは **LLM 往復 1 回**で終わる (v1 は分類 + 応答で 2 回)。
    """

    def __init__(
        self,
        session_repo: SessionRepository,
        client: Optional[BaseChatClient],
        *,
        stream: bool,
    ):
        super().__init__(id="converse")
        self._session_repo = session_repo
        self._client = client
        self._stream = stream

    async def _call_llm(
        self,
        session_id: str,
        messages: Sequence[Message],
        ctx: WorkflowContext,
        *,
        stream: bool,
        updates: list,
    ) -> tuple[MafChatResponse, ToolContext]:
        """ツールを載せて LLM を 1 回呼ぶ。stream 時はテキスト差分を intermediate output へ。

        `updates` は呼び出し側が持つ受け皿。**途中で落ちてもそこまでの update が
        残る**ようにしてある — ツールは既に実行済みかもしれず、その事実を握り潰すと
        「実行されたのに履歴に無い」が生まれる。
        """
        tool_context = ToolContext(session_id=session_id)
        tools = exposed_tools()
        client = _resolve_client(self._client)
        logger.info(
            "Workflow[CONVERSE] session=%s tools=%s%s",
            session_id,
            [t.name for t in tools],
            " streaming" if stream else "",
        )
        # ToolContext は ContextVar。MAF の invocation loop は chat client の内側で
        # 回るので、**呼び出し全体をこのスコープで囲む**のが唯一の渡し方になる
        # (並列に走るツールにも copy_context 経由で同じ context が見える)。
        with use_tool_context(tool_context):
            if not stream:
                return await chat(client, messages, tools=tools), tool_context
            async for update in chat_stream(client, messages, tools=tools):
                updates.append(update)
                if update.text:
                    await ctx.yield_output(update.text)
            return MafChatResponse.from_updates(updates), tool_context

    async def _flush_partial_tool_outcomes(
        self, session_id: str, updates: list
    ) -> None:
        """LLM 呼び出しが途中で落ちたとき、そこまでに実行されたツールの結果を履歴へ残す。

        無いと: ストリーミングが応答生成中に落ちたターンで、**ツールは実行済みなのに
        履歴には何も残らない**。フロントは非ストリーミングへ自動フォールバックするので、
        同じツールがもう一度呼ばれうる (副作用ツールなら二重実行)。
        """
        if not updates:
            return
        history = await _get_or_create_session(session_id, self._session_repo)
        _record_tool_outcomes(history, MafChatResponse.from_updates(updates))
        await self._session_repo.save(session_id, history)

    async def _finish_turn(
        self,
        session_id: str,
        response: MafChatResponse,
        tool_context: ToolContext,
        ctx: WorkflowContext,
        call_names: Optional[dict[str, str]] = None,
    ) -> None:
        """ツール結果と assistant 応答を履歴へ積み、FinalReply を送る。"""
        history = await _get_or_create_session(session_id, self._session_repo)
        _record_tool_outcomes(history, response, call_names)
        reply = response.text
        history.add_assistant_message(reply)
        await self._session_repo.save(session_id, history)
        await ctx.send_message(
            FinalReply(
                session_id=session_id,
                reply=reply,
                citations=list(tool_context.citations),
            )
        )

    @handler
    async def converse(
        self, turn: ChatTurn, ctx: WorkflowContext[FinalReply, str]
    ) -> None:
        history = await _get_or_create_session(turn.session_id, self._session_repo)
        updates: list = []
        try:
            response, tool_context = await self._call_llm(
                turn.session_id,
                list(history.messages),
                ctx,
                stream=self._stream,
                updates=updates,
            )
        except Exception:
            await self._flush_partial_tool_outcomes(turn.session_id, updates)
            raise
        report_identity_arg_attempts(_function_calls(response))

        pending = _approval_requests(response)
        if pending:
            request = pending[0]
            call = request.function_call
            logger.info(
                "Workflow[APPROVAL_IF_NEEDED] tool=%s", call.name if call else None
            )
            if len(pending) > 1:
                # 1 ターンに複数の副作用ツールが並ぶのは今の題材では起こらないが、
                # 黙って 1 本目だけ承認して残りを捨てると「承認したつもりのない実行」に
                # なりうる。起きたことが分かるようにログに残す (#321 で題材を決めるまでの暫定)。
                logger.warning(
                    "Workflow[APPROVAL_IF_NEEDED] 承認要求が %d 件届いた — 先頭のみを扱う",
                    len(pending),
                )
            await ctx.request_info(
                ApprovalRequest(
                    session_id=turn.session_id,
                    plan=Plan(
                        tool_name=call.name if call else None,
                        tool_args=(call.parse_arguments() or {}) if call else {},
                        is_side_effecting=True,
                    ),
                    approval_content_id=request.id or "",
                    pending_messages=[m.to_dict() for m in response.messages],
                    citations=list(tool_context.citations),
                ),
                ApprovalDecision,
                request_id=str(uuid.uuid4()),
            )
            return

        await self._finish_turn(turn.session_id, response, tool_context, ctx)

    @response_handler
    async def on_approval_decision(
        self,
        request: ApprovalRequest,
        decision: ApprovalDecision,
        ctx: WorkflowContext[FinalReply, str],
    ) -> None:
        if not decision.approved:
            # 却下は LLM を呼ばずに固定文言で閉じる (v1 と同一の文面・往復 0 回)。
            logger.info("Workflow[APPROVAL_IF_NEEDED] rejected")
            history = await _get_or_create_session(
                request.session_id, self._session_repo
            )
            history.add_assistant_message(_REJECTION_REPLY)
            await self._session_repo.save(request.session_id, history)
            await ctx.send_message(
                FinalReply(session_id=request.session_id, reply=_REJECTION_REPLY)
            )
            return

        logger.info(
            "Workflow[APPROVAL_IF_NEEDED] approved tool=%s", request.plan.tool_name
        )
        history = await _get_or_create_session(request.session_id, self._session_repo)
        pending = [Message.from_dict(m) for m in request.pending_messages]
        approval_content = _find_approval_request(pending, request.approval_content_id)
        if approval_content is None:
            # 承認レコードは pending なのに、再開に要る承認要求が checkpoint から
            # 復元できない = 再開できない。握り潰すと「承認したのに何も起きない」
            raise RuntimeError(
                f"Approval content not found in checkpoint: {request.approval_content_id!r}"
            )
        messages = [
            *history.messages,
            *pending,
            Message(
                role="user",
                contents=[
                    approval_content.to_function_approval_response(approved=True)
                ],
            ),
        ]
        # 再開は常に非ストリーミング (/approve は SSE ではない)
        response, tool_context = await self._call_llm(
            request.session_id, messages, ctx, stream=False, updates=[]
        )
        tool_context.citations.extend(request.citations)
        await self._finish_turn(
            request.session_id,
            response,
            tool_context,
            ctx,
            _tool_names_by_call_id(pending),
        )


def _find_approval_request(
    messages: Sequence[Message], content_id: str
) -> Optional[Content]:
    for msg in messages:
        for content in msg.contents:
            if content.type == "function_approval_request" and content.id == content_id:
                return content
    return None


class FinishExecutor(Executor):
    """終端: FinalReply を API 契約の ChatResponse として workflow output に出す。"""

    def __init__(self):
        super().__init__(id="finish")

    @handler
    async def finish(
        self, msg: FinalReply, ctx: WorkflowContext[Never, ChatResponse]
    ) -> None:
        await ctx.yield_output(ChatResponse(reply=msg.reply, citations=msg.citations))


def _build_chat_workflow(
    session_repo: SessionRepository,
    client: Optional[BaseChatClient],
    *,
    stream: bool,
    checkpoint_storage: CheckpointStorage,
) -> Workflow:
    """1 ターン分の chat workflow を組む。

    stream フラグは converse executor の LLM 呼び出し方 (一括/逐次) だけを変え、
    グラフ構造 (= checkpoint の graph signature) は同一に保つ — /chat で中断した
    checkpoint を /approve (非 stream) で再開できるのはこのため。
    """
    receive = ReceiveExecutor(session_repo)
    converse = ConverseExecutor(session_repo, client, stream=stream)
    finish = FinishExecutor()
    return (
        WorkflowBuilder(
            name=_WORKFLOW_NAME,
            start_executor=receive,
            checkpoint_storage=checkpoint_storage,
            output_from=[finish],
            intermediate_output_from=[converse],
        )
        .add_edge(receive, converse)
        .add_edge(converse, finish)
        .build()
    )


# ── FastAPI 境界の薄いアダプタ (API 契約は v1 と不変) ─────────────────────────


async def _find_checkpoint_id(
    storage: CheckpointStorage, request_id: str
) -> Optional[str]:
    """pending の approval request を保持する checkpoint を探す (写像の実体)。"""
    checkpoints = await storage.list_checkpoints(workflow_name=_WORKFLOW_NAME)
    matching = [
        cp for cp in checkpoints if request_id in cp.pending_request_info_events
    ]
    if not matching:
        logger.warning("No checkpoint found for approval request %s", request_id)
        return None
    return max(matching, key=lambda cp: cp.timestamp).checkpoint_id


async def _record_approval_request(
    event: WorkflowEvent,
    approval_repo: ApprovalRepository,
    storage: CheckpointStorage,
) -> ChatResponse:
    """MAF の request_info event を API 契約 (requiresApproval 応答) へ写す。"""
    request: ApprovalRequest = event.data
    record = ApprovalRecord(
        id=event.request_id,
        session_id=request.session_id,
        plan=request.plan,
        checkpoint_id=await _find_checkpoint_id(storage, event.request_id),
    )
    await approval_repo.save(record)
    if record.checkpoint_id is not None and not _cosmos_enabled():
        # in-memory 構成のみ: 承認待ちの run だけ storage を生かしておく
        # (/approve の解決で解放)。Cosmos 構成は共有ストアなので registry 不要
        _pending_run_storages[record.id] = storage
    return ChatResponse(
        reply=f"「{request.plan.tool_name}」を実行するには承認が必要です。実行してよろしいですか？",
        requires_approval=True,
        approval_request_id=record.id,
        citations=request.citations,
    )


async def run_workflow(
    session_id: str,
    message: str,
    session_repo: SessionRepository,
    approval_repo: ApprovalRepository,
    client: Optional[BaseChatClient] = None,
) -> ChatResponse:
    storage = _new_checkpoint_storage()
    workflow = _build_chat_workflow(
        session_repo, client, stream=False, checkpoint_storage=storage
    )
    result = await workflow.run(ChatTurn(session_id=session_id, message=message))

    requests = result.get_request_info_events()
    if requests:
        return await _record_approval_request(requests[0], approval_repo, storage)

    # 承認なしで完了した run の checkpoint: in-memory 構成は storage ごとここで
    # 破棄される。Cosmos 構成はコンテナ TTL (1 時間) が掃除する
    outputs = result.get_outputs()
    if not outputs:
        raise RuntimeError("Chat workflow completed without producing a response")
    return outputs[-1]


async def run_workflow_stream(
    session_id: str,
    message: str,
    session_repo: SessionRepository,
    approval_repo: ApprovalRepository,
    client: Optional[BaseChatClient] = None,
) -> AsyncIterator[Union[ChatStreamDelta, ChatStreamDone]]:
    """run_workflow のストリーミング版 (#120 / ADR 0024)。

    応答トークン (intermediate output) を ChatStreamDelta として逐次 yield し、
    完了時に従来 /chat と同一形の ChatResponse を ChatStreamDone で返す。
    承認が要るターンは、モデルがツール呼び出しの前にテキストを出さない限り
    逐次配信するものが無いので done のみを返す。
    """
    storage = _new_checkpoint_storage()
    workflow = _build_chat_workflow(
        session_repo, client, stream=True, checkpoint_storage=storage
    )

    request_event: Optional[WorkflowEvent] = None
    final_response: Optional[ChatResponse] = None
    async for event in workflow.run(
        ChatTurn(session_id=session_id, message=message), stream=True
    ):
        if event.type == "intermediate" and isinstance(event.data, str):
            yield ChatStreamDelta(text=event.data)
        elif event.type == "request_info":
            # checkpoint は superstep 境界で書かれるため、ここでは控えるだけに
            # して write 完了後 (ループを抜けた後) に写像を記録する
            request_event = event
        elif event.type == "output" and isinstance(event.data, ChatResponse):
            final_response = event.data

    if request_event is not None:
        yield ChatStreamDone(
            response=await _record_approval_request(
                request_event, approval_repo, storage
            )
        )
        return

    if final_response is None:
        raise RuntimeError("Chat workflow completed without producing a response")
    yield ChatStreamDone(response=final_response)


async def resume_after_approval(
    approval_id: str,
    approved: bool,
    session_repo: SessionRepository,
    approval_repo: ApprovalRepository,
    client: Optional[BaseChatClient] = None,
) -> str:
    record = await approval_repo.get(approval_id)
    if not record:
        raise ValueError(f"Approval not found: {approval_id!r}")
    if record.status != "pending":
        raise ValueError(f"Approval already processed: {record.status!r}")
    if not record.checkpoint_id:
        raise ValueError(f"Approval checkpoint not found: {approval_id!r}")

    if _cosmos_enabled():
        # 共有ストア構成: checkpoint は Cosmos にある (プロセス再起動を跨げる)。
        # TTL 失効等で文書が消えていたら、未知 ID と同じ 404 系に写す
        storage = _new_checkpoint_storage()
        try:
            await storage.load(record.checkpoint_id)
        except Exception as exc:
            raise ValueError(f"Approval checkpoint not found: {approval_id!r}") from exc
    else:
        # in-memory 構成: 解決に入る時点で registry から解放する (成功・失敗
        # どちらでも再試行は status チェックで弾かれるため、保持し続ける理由がない)
        storage = _pending_run_storages.pop(approval_id, None)
        if storage is None:
            raise ValueError(f"Approval checkpoint not found: {approval_id!r}")

    record.status = "approved" if approved else "rejected"
    await approval_repo.save(record)

    workflow = _build_chat_workflow(
        session_repo, client, stream=False, checkpoint_storage=storage
    )
    result = await workflow.run(
        responses={approval_id: ApprovalDecision(approved=approved)},
        checkpoint_id=record.checkpoint_id,
    )
    outputs = result.get_outputs()
    if not outputs:
        raise RuntimeError("Chat workflow resume completed without a response")

    if _cosmos_enabled():
        # 解決時 delete (#188): 解決済みの pending checkpoint は TTL を待たずに消す。
        # 再開中に書かれた後続 checkpoint と、中断前の祖先はコンテナ TTL が掃除する
        await storage.delete(record.checkpoint_id)

    return outputs[-1].reply
