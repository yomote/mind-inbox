"""
/chat・/approve のオーケストレーション — Microsoft Agent Framework (MAF) graph Workflow。

v1 の自前 7 状態 FSM (RECEIVE→CLASSIFY→RETRIEVE_IF_NEEDED→PLAN→APPROVAL_IF_NEEDED
→EXECUTE_TOOL→RESPOND) を MAF の graph Workflow に写した (ADR 0016 / M1-3)。

グラフ (エッジ配送は MAF の型付きメッセージルーティング):

    receive → classify → retrieve → plan ─┬→ execute_tool → respond → finish
                                          └────────────────────────→ finish
                                            (却下時: FinalReply 直行)

- **HITL 承認**: plan executor が MAF 標準の request-response 機構
  (`ctx.request_info` + `@response_handler`) で中断する。中断時点の全状態は
  MAF checkpoint (superstep 境界で自動保存) が保持する。
- **approvalRequestId は checkpoint 参照への写像**: FastAPI 境界の薄いアダプタ
  (`run_workflow` / `resume_after_approval`) が request_id → checkpoint_id の
  対応を `ApprovalRecord` に記録し、API 契約 (requiresApproval /
  approvalRequestId) を v1 と不変に保つ。/approve は checkpoint から
  `workflow.run(responses=..., checkpoint_id=...)` で再開する。
- **ストリーミング**: respond executor がトークンを intermediate output として
  yield し、アダプタが ChatStreamDelta へ写す (契約は #120 / ADR 0024 のまま)。
"""

# NOTE: `from __future__ import annotations` を使わない — MAF の @handler /
# @response_handler はデコレート時に実型の annotation を検査するため、
# PEP 563 の文字列 annotation では登録に失敗する。

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Never, Optional, Union

from agent_framework import (
    BaseChatClient,
    CheckpointStorage,
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
from agent_framework_azure_cosmos import CosmosCheckpointStorage
from pydantic import BaseModel

from .agents import chat, chat_stream, complete, get_chat_client
from .config import get_settings
from .history import ChatHistory
from .observability import exception_kind, fingerprint, new_ref
from .prompts import CHAT_SYSTEM_PROMPT
from .rag import retrieve
from .repositories import ApprovalRepository, SessionRepository
from .schemas import (
    ApprovalRecord,
    ChatResponse,
    ChatStreamDelta,
    ChatStreamDone,
    Plan,
)
from .tools import ToolContext, execute_tool, is_side_effecting

logger = logging.getLogger(__name__)

_WORKFLOW_NAME = "mind-inbox-chat-turn"

_REJECTION_REPLY = "操作はキャンセルされました。他にご用件はありますか？"


# checkpoint に入り得るアプリの pydantic 型 (edge を流れるメッセージ + HITL payload)。
# CosmosCheckpointStorage の復元は許可リスト式 (JSON + pickle ハイブリッド) なので、
# ここに登録が無い型は **保存は通るのに復元 (= /approve の再開) だけが落ちる**。
# executor 間のメッセージ型を増やしたら必ずここにも足すこと
# (test_workflow_checkpoint_storage.py が fake ストアで encode → decode を通して pin する)。
_APP_CHECKPOINT_TYPES = [
    "app.schemas:ChatResponse",
    "app.schemas:Plan",
    "app.workflow:ApprovalDecision",
    "app.workflow:ApprovalRequest",
    "app.workflow:ChatTurn",
    "app.workflow:ClassifiedTurn",
    "app.workflow:FinalReply",
    "app.workflow:PlannedTurn",
    "app.workflow:RespondRequest",
    "app.workflow:ToolInvocation",
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


# ── LLM helpers (v1 から不変: 分類プロンプト / 壊れた JSON → 安全側 no-op) ────


def _resolve_client(client: Optional[BaseChatClient]) -> BaseChatClient:
    """chat client の遅延解決 (縮退挙動を SK kernel 時代と同一に保つ)。

    未注入 (None = 本番経路) なら LLM を実際に呼ぶこの時点で初めて構築する。
    資格情報なしでも起動・RECEIVE (履歴保存)・/approve の ID 検証までは動き、
    失敗は LLM 呼び出し (CLASSIFY / RESPOND) で表面化する — SK kernel の
    「構築は成功し get_service で落ちる」と同じ失敗面。テストは fake を注入する。
    """
    return client if client is not None else get_chat_client()


async def _classify(message: str, client: Optional[BaseChatClient]) -> dict:
    """LLM でメッセージを分類し、必要なツール・RAG 検索を判定する。"""
    prompt = f"""Analyze the user message and respond with JSON only. No markdown.

User message: "{message}"

Available tools:
- search_faq(query: str)           — read-only: search FAQ
- get_inbox_stats()                — read-only: inbox stats for the current user
                                     (no arguments — the server decides the user)
- send_reply(to: str, body: str)   — SIDE-EFFECTING: send a reply
- archive_message(message_id: str) — SIDE-EFFECTING: archive a message

Never put a user, account, or session identifier in tool_args — the server supplies
the acting user; identifier arguments are rejected.

Respond with this exact JSON structure:
{{
  "needs_retrieval": <true|false>,
  "needs_tool": <true|false>,
  "tool_name": <"tool_name" or null>,
  "tool_args": <dict or {{}}>
}}"""

    llm_response = (await complete(_resolve_client(client), prompt)).strip()
    parts = llm_response.split("```")
    if len(parts) >= 3:
        llm_response = parts[1].removeprefix("json").strip()

    try:
        return json.loads(llm_response)
    except json.JSONDecodeError:
        # LLM の生出力にはユーザーの発話が写り込む。本文ではなく指紋だけ残す
        # (同じ壊れ方の再発は指紋の一致で追える / Issue #313)
        logger.warning(
            "Classification JSON parse failed: %s", fingerprint(llm_response)
        )
        return {
            "needs_retrieval": False,
            "needs_tool": False,
            "tool_name": None,
            "tool_args": {},
        }


def _build_call_messages(history: ChatHistory, rag_context: str) -> list[Message]:
    """RAG コンテキストがある場合だけ system メッセージを足した呼び出し用メッセージ列を作る。"""
    if not rag_context:
        return list(history.messages)
    return [
        *history.messages,
        Message(role="system", contents=[f"Relevant context:\n{rag_context}"]),
    ]


async def _respond(
    history: ChatHistory,
    client: Optional[BaseChatClient],
    rag_context: str = "",
) -> str:
    """最終的なアシスタント返答を生成する。"""
    return await chat(
        _resolve_client(client), _build_call_messages(history, rag_context)
    )


async def _respond_stream(
    history: ChatHistory,
    client: Optional[BaseChatClient],
    rag_context: str = "",
) -> AsyncIterator[str]:
    """_respond のストリーミング版。トークン (チャンク) 文字列を逐次 yield する。"""
    async for token in chat_stream(
        _resolve_client(client), _build_call_messages(history, rag_context)
    ):
        yield token


# ── Workflow messages (executor 間で流れる型 = エッジ配送のルーティングキー) ──


class ChatTurn(BaseModel):
    """workflow への入力 (start executor が受ける)。"""

    session_id: str
    message: str


class ClassifiedTurn(BaseModel):
    session_id: str
    message: str
    needs_retrieval: bool = False
    needs_tool: bool = False
    tool_name: Optional[str] = None
    tool_args: dict = {}


class PlannedTurn(BaseModel):
    session_id: str
    needs_retrieval: bool = False
    needs_tool: bool = False
    tool_name: Optional[str] = None
    tool_args: dict = {}
    rag_context: str = ""
    citations: list[str] = []


class ApprovalRequest(BaseModel):
    """HITL request_info の payload。checkpoint に pending として保存される。"""

    session_id: str
    plan: Plan
    rag_context: str = ""
    citations: list[str] = []


class ApprovalDecision(BaseModel):
    """HITL の応答型 (/approve の approved を写す)。"""

    approved: bool


class ToolInvocation(BaseModel):
    session_id: str
    tool_name: Optional[str] = None
    tool_args: dict = {}
    rag_context: str = ""
    citations: list[str] = []


class RespondRequest(BaseModel):
    session_id: str
    rag_context: str = ""
    citations: list[str] = []


class FinalReply(BaseModel):
    session_id: str
    reply: str
    citations: list[str] = []


# ── Executors (旧 FSM の各状態を 1 executor に写す) ───────────────────────────


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


class ClassifyExecutor(Executor):
    """CLASSIFY: LLM でツール・RAG 要否を判定。"""

    def __init__(self, client: Optional[BaseChatClient]):
        super().__init__(id="classify")
        self._client = client

    @handler
    async def classify(
        self, turn: ChatTurn, ctx: WorkflowContext[ClassifiedTurn]
    ) -> None:
        logger.info("Workflow[CLASSIFY]")
        classification = await _classify(turn.message, self._client)
        tool_name = classification.get("tool_name")
        await ctx.send_message(
            ClassifiedTurn(
                session_id=turn.session_id,
                message=turn.message,
                needs_retrieval=bool(classification.get("needs_retrieval")),
                needs_tool=bool(classification.get("needs_tool") and tool_name),
                tool_name=tool_name,
                tool_args=classification.get("tool_args") or {},
            )
        )


class RetrieveExecutor(Executor):
    """RETRIEVE_IF_NEEDED: 必要なら RAG 検索してコンテキストを付与。"""

    def __init__(self):
        super().__init__(id="retrieve")

    @handler
    async def retrieve_if_needed(
        self, turn: ClassifiedTurn, ctx: WorkflowContext[PlannedTurn]
    ) -> None:
        rag_context = ""
        citations: list[str] = []
        if turn.needs_retrieval:
            logger.info("Workflow[RETRIEVE_IF_NEEDED]")
            results = await retrieve(turn.message)
            rag_context = "\n".join(r.content for r in results)
            citations = [r.source for r in results]
        await ctx.send_message(
            PlannedTurn(
                session_id=turn.session_id,
                needs_retrieval=turn.needs_retrieval,
                needs_tool=turn.needs_tool,
                tool_name=turn.tool_name,
                tool_args=turn.tool_args,
                rag_context=rag_context,
                citations=citations,
            )
        )


class PlanExecutor(Executor):
    """PLAN + APPROVAL_IF_NEEDED: 副作用ツールは MAF の request-response で中断する。

    `ctx.request_info` が pending request として checkpoint に残り、workflow は
    IDLE_WITH_PENDING_REQUESTS で停止する。応答 (`ApprovalDecision`) は /approve
    経由で `workflow.run(responses=...)` により `on_approval_decision` へ届く。
    """

    def __init__(self, session_repo: SessionRepository):
        super().__init__(id="plan")
        self._session_repo = session_repo

    @handler
    async def plan(
        self, turn: PlannedTurn, ctx: WorkflowContext[ToolInvocation]
    ) -> None:
        logger.info("Workflow[PLAN]")
        if turn.needs_tool and is_side_effecting(turn.tool_name):
            logger.info("Workflow[APPROVAL_IF_NEEDED] tool=%s", turn.tool_name)
            await ctx.request_info(
                ApprovalRequest(
                    session_id=turn.session_id,
                    plan=Plan(
                        needs_retrieval=turn.needs_retrieval,
                        tool_name=turn.tool_name,
                        tool_args=turn.tool_args,
                        is_side_effecting=True,
                    ),
                    rag_context=turn.rag_context,
                    citations=turn.citations,
                ),
                ApprovalDecision,
                request_id=str(uuid.uuid4()),
            )
            return
        await ctx.send_message(
            ToolInvocation(
                session_id=turn.session_id,
                tool_name=turn.tool_name if turn.needs_tool else None,
                tool_args=turn.tool_args,
                rag_context=turn.rag_context,
                citations=turn.citations,
            )
        )

    @response_handler
    async def on_approval_decision(
        self,
        request: ApprovalRequest,
        decision: ApprovalDecision,
        ctx: WorkflowContext[ToolInvocation | FinalReply],
    ) -> None:
        if not decision.approved:
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
        await ctx.send_message(
            ToolInvocation(
                session_id=request.session_id,
                tool_name=request.plan.tool_name,
                tool_args=request.plan.tool_args,
                rag_context=request.rag_context,
                citations=request.citations,
            )
        )


class ExecuteToolExecutor(Executor):
    """EXECUTE_TOOL: ツール実行 (無ければ素通し)。結果/失敗は履歴に残す。"""

    def __init__(self, session_repo: SessionRepository):
        super().__init__(id="execute_tool")
        self._session_repo = session_repo

    @handler
    async def execute_tool_if_needed(
        self, invocation: ToolInvocation, ctx: WorkflowContext[RespondRequest]
    ) -> None:
        if invocation.tool_name:
            logger.info("Workflow[EXECUTE_TOOL] tool=%s", invocation.tool_name)
            history = await _get_or_create_session(
                invocation.session_id, self._session_repo
            )
            try:
                tool_result = await execute_tool(
                    invocation.tool_name,
                    invocation.tool_args,
                    # 主体はモデル出力ではなく実行コンテキストから (Issue #313)
                    ToolContext(session_id=invocation.session_id),
                )
                history.add_system_message(
                    f"Tool result ({invocation.tool_name}): {tool_result}"
                )
            except Exception as exc:
                # 例外文は履歴に入れない — 履歴はそのまま LLM へ再送され、最終的に
                # ユーザーの画面まで届きうる出口 (上流のエンドポイント名等が漏れる)。
                # 詳細はサーバのログにだけ残し、ref で突き合わせる (Issue #313)。
                ref = new_ref()
                logger.error(
                    "Tool execution failed ref=%s tool=%s kind=%s",
                    ref,
                    invocation.tool_name,
                    exception_kind(exc),
                    exc_info=True,
                )
                history.add_system_message(
                    f"Tool error ({invocation.tool_name}): "
                    f"実行に失敗しました (ref: {ref})"
                )
            await self._session_repo.save(invocation.session_id, history)
        await ctx.send_message(
            RespondRequest(
                session_id=invocation.session_id,
                rag_context=invocation.rag_context,
                citations=invocation.citations,
            )
        )


class RespondExecutor(Executor):
    """RESPOND: 最終応答を生成。stream 時はトークンを intermediate output で流す。"""

    def __init__(
        self,
        session_repo: SessionRepository,
        client: Optional[BaseChatClient],
        stream: bool,
    ):
        super().__init__(id="respond")
        self._session_repo = session_repo
        self._client = client
        self._stream = stream

    @handler
    async def respond(
        self, req: RespondRequest, ctx: WorkflowContext[FinalReply, str]
    ) -> None:
        logger.info("Workflow[RESPOND]%s", " streaming" if self._stream else "")
        history = await _get_or_create_session(req.session_id, self._session_repo)
        if self._stream:
            parts: list[str] = []
            async for token in _respond_stream(history, self._client, req.rag_context):
                parts.append(token)
                await ctx.yield_output(token)
            reply = "".join(parts)
        else:
            reply = await _respond(history, self._client, req.rag_context)
        history.add_assistant_message(reply)
        await self._session_repo.save(req.session_id, history)
        await ctx.send_message(
            FinalReply(session_id=req.session_id, reply=reply, citations=req.citations)
        )


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

    stream フラグは respond executor の LLM 呼び出し方 (一括/逐次) だけを変え、
    グラフ構造 (= checkpoint の graph signature) は同一に保つ — /chat で中断した
    checkpoint を /approve (非 stream) で再開できるのはこのため。
    """
    receive = ReceiveExecutor(session_repo)
    classify = ClassifyExecutor(client)
    retrieve_exec = RetrieveExecutor()
    plan = PlanExecutor(session_repo)
    execute = ExecuteToolExecutor(session_repo)
    respond = RespondExecutor(session_repo, client, stream=stream)
    finish = FinishExecutor()
    return (
        WorkflowBuilder(
            name=_WORKFLOW_NAME,
            start_executor=receive,
            checkpoint_storage=checkpoint_storage,
            output_from=[finish],
            intermediate_output_from=[respond],
        )
        .add_edge(receive, classify)
        .add_edge(classify, retrieve_exec)
        .add_edge(retrieve_exec, plan)
        .add_edge(plan, execute)
        .add_edge(plan, finish)  # 却下時: FinalReply が finish へ直行
        .add_edge(execute, respond)
        .add_edge(respond, finish)
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
        rag_context=request.rag_context,
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

    RESPOND のトークン (intermediate output) を ChatStreamDelta として逐次 yield
    し、完了時に従来 /chat と同一形の ChatResponse を ChatStreamDone で返す。
    承認が要るターンは逐次配信するものが無いので done のみを返す。
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
