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
  **再開したターンも最初のターンと同じ経路 (`_settle`) を通る** — 承認済みツールの
  実行後にモデルが別の副作用ツールを要求したら、そこで新しい承認要求として立て直す
  (完了扱いにしない / PR #417 P1)。
- **ストリーミング**: converse executor がトークンを intermediate output として
  yield し、アダプタが ChatStreamDelta へ写す (契約は #120 / ADR 0024 のまま)。
"""

# NOTE: `from __future__ import annotations` を使わない — MAF の @handler /
# @response_handler はデコレート時に実型の annotation を検査するため、
# PEP 563 の文字列 annotation では登録に失敗する。

import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timezone
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
from .history import ChatHistory, select_window
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
    TURN_LOCAL_TOOLS,
    ToolContext,
    ToolExecution,
    exposed_tools,
    report_identity_arg_attempts,
    tool_error_ref,
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


class ApprovalAlreadyProcessedError(Exception):
    """同じ承認 ID が **2 回目**に送られた (#82 / PO 裁定 2026-08-15 B 案)。

    `ValueError` (= main.py が 404 に写す「承認レコードがもう無い」) と**別の型**に
    しているのが本体。二重送信とレコード消失を同じ 404 に混ぜていた頃は、
    「もう解決済み」ということすらクライアントに伝わらず、**確実に未実行と言える
    却下済みまで「実行されたか不明」**に落ちていた。型で分けることで main.py が
    409 + 現在状態に写す。

    **status が意味するのは「どちらの決定を受け付けたか」であって実行の完了ではない**
    (PR #430 Codex P1)。遷移は checkpoint 再開の**前**に書くので、`approved` の記録後・
    副作用の実行前にプロセスが落ちれば「approved なのに実行されていない」レコードが
    残る。したがって:

    - `approved` … 実行してよいと受け付けた。**実行された保証はない**
    - `rejected` … 実行しないと受け付けた。この経路でツールは呼ばれない = **未実行と言える**

    `processed_at` は**受け付けた時刻**で、完了の証拠ではない。None は「時刻を持たない
    古いレコード」であって未処理ではない — status を無視して時刻で判定してはいけない。
    """

    def __init__(self, status: str, processed_at: Optional[str]) -> None:
        super().__init__(f"Approval already processed: {status!r}")
        self.status = status
        self.processed_at = processed_at


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


def _window_limits() -> tuple[int, int]:
    """会話履歴の窓の上限 (件数, 文字数) — env で調整可 (#486)。"""
    settings = get_settings()
    return settings.history_window_max_messages, settings.history_window_max_chars


def _log_window(
    source: str, before: Sequence[Message], after: Sequence[Message]
) -> None:
    """窓が何を落としたかを声に出す (#486)。落としていなければ何も言わない。

    **刈ったこと自体は正常** — ここで出すのは「本番のセッションで窓が実際に効いて
    いる」という運用の裏取りと、下の縮退の検出。
    """
    if len(after) == len(before):
        return
    max_messages, max_chars = _window_limits()
    logger.info(
        "Workflow[HISTORY_WINDOW] source=%s kept=%d/%d messages %d/%d chars (limits %d/%d)",
        source,
        len(after),
        len(before),
        sum(len(m.text or "") for m in after),
        sum(len(m.text or "") for m in before),
        max_messages,
        max_chars,
    )
    if sum(1 for m in after if m.role == "user") <= 1:
        # 窓が最新ターンだけまで縮んだ = エージェントが**会話の記憶を失っている**。
        # 上限を system プロンプト長より小さく設定すると起きるが、応答は普通に返る
        # ので画面からは「なんとなく話が噛み合わない」としか見えない。縮退したことは
        # 必ず声に出す (握り潰すと設定ミスが永久に見つからない)
        logger.warning(
            "Workflow[HISTORY_WINDOW] 窓が最新ターンのみまで縮退した source=%s "
            "(HISTORY_WINDOW_MAX_MESSAGES=%d / HISTORY_WINDOW_MAX_CHARS=%d が "
            "system プロンプトに対して小さすぎる可能性)",
            source,
            max_messages,
            max_chars,
        )


def _windowed(history: ChatHistory) -> list[Message]:
    """LLM へ渡す messages (窓の内側だけ / #486)。

    **`_save_session` が保存側でも刈っているのに、渡す側でも掛ける。** 片方だけでは
    ①この変更より前に保存された既存セッション ②env で上限を下げた直後
    ③失敗ターンの再試行 (`_append_user_message_once` が save ごと省く経路) が、
    丸ごと LLM へ飛ぶ = 恒久 500 を踏む経路として残る。
    """
    max_messages, max_chars = _window_limits()
    window = select_window(
        history.messages, max_messages=max_messages, max_chars=max_chars
    )
    _log_window("llm", history.messages, window)
    return window


async def _save_session(
    session_id: str,
    history: ChatHistory,
    session_repo: SessionRepository,
) -> None:
    """窓まで刈ってから保存する (#486)。

    **保存の入口をここ 1 箇所に絞ってある** — `session_repo.save` を直接呼ぶ経路を
    足すと、そのターンだけ刈られないまま文書が育ち、Cosmos の 2MB 上限に当たった
    ときに save が落ちる (会話がそこで永久に進まなくなる)。

    通常のターンでは**保存側が先に刈る**ので、窓が実際に効いた記録が残るのはここ。
    """
    max_messages, max_chars = _window_limits()
    before = list(history.messages)
    history.prune(max_messages=max_messages, max_chars=max_chars)
    _log_window("save", before, history.messages)
    await session_repo.save(session_id, history)


async def _get_or_create_session(
    session_id: str,
    session_repo: SessionRepository,
) -> ChatHistory:
    history = await session_repo.get(session_id)
    if history is None:
        history = ChatHistory()
        history.add_system_message(CHAT_SYSTEM_PROMPT)
        await _save_session(session_id, history, session_repo)
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
    await _save_session(session_id, history, session_repo)


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
    executions: Sequence[ToolExecution] = (),
    drop_tools: frozenset[str] = frozenset(),
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

    `executions` は `tool_boundary` middleware がツール境界で直に記録した実行分。
    **応答に function_result が現れないケースがある**ため必要になる (#417 P1 /
    `ToolExecution` の docstring)。応答にも現れているものは call_id で除いて
    二重に積まない。

    `drop_tools` に挙げたツールの実行は**履歴に残さない**。落ちたターンの部分結果を
    書き戻す経路 (`_flush_partial_tool_outcomes`) だけが使う — 理由と、これを外すと
    何が静かに起きるかは `tools.TURN_LOCAL_TOOLS` の説明を参照。
    """
    # 承認再開のターンでは function_call は checkpoint 側 (pending_messages) にあり、
    # 応答には function_result しか来ない。呼び出し側が名前の手がかりを渡す。
    names = {**(call_names or {}), **_tool_names_by_call_id(response.messages)}
    recorded: set[str] = set()
    for content in _contents(response):
        if content.type != "function_result":
            continue
        recorded.add(content.call_id or "")
        name = names.get(content.call_id or "", "?")
        if name in drop_tools:
            continue
        result = content.result
        if content.exception is None:
            history.add_system_message(f"Tool result ({name}): {result}")
            continue
        if isinstance(result, str) and result.startswith(_MAF_ARGUMENT_ERROR_PREFIX):
            # 引数の値も検証エラー文もユーザー入力由来なので指紋だけ残す
            ref = new_ref()
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
        # 実行の失敗は `tool_boundary` が境界で ref を採番済み (ログもそこで出ている)。
        # 採番されていない = MAF 内部が作った失敗 (未知のツール等) なので、ここで
        # 採番して残す — 拾えなかったものを黙って通さない
        ref = tool_error_ref(str(content.exception))
        if ref is None:
            ref = new_ref()
            logger.error(
                "Tool execution failed ref=%s tool=%s kind=execution detail=%s",
                ref,
                name,
                fingerprint(str(content.exception)),
            )
        history.add_system_message(
            f"Tool error ({name}): 実行に失敗しました (ref: {ref})"
        )

    # 応答から落ちた実行分 (承認要求で打ち切られたターンで起きる / #417 P1)
    for execution in executions:
        if execution.call_id in recorded or execution.name in drop_tools:
            continue
        if execution.error_ref is not None:
            history.add_system_message(
                f"Tool error ({execution.name}): 実行に失敗しました "
                f"(ref: {execution.error_ref})"
            )
            continue
        history.add_system_message(
            f"Tool result ({execution.name}): {execution.result}"
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
    # `offer_choices` が提示した選択肢 (#432-b)。承認要求のターンでは運ばない
    # (`_request_approval` は choices を見ない) — 承認カードと選択肢が同時に出る
    # 画面を作らないため。
    choices: list[str] = []


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
        tool_context: ToolContext,
        exclude_tools: frozenset[str] = frozenset(),
    ) -> MafChatResponse:
        """ツールを載せて LLM を 1 回呼ぶ。stream 時はテキスト差分を intermediate output へ。

        `updates` / `tool_context` は呼び出し側が持つ受け皿。**途中で落ちても
        そこまでの update と実行記録が残る**ようにしてある — ツールは既に実行済み
        かもしれず、その事実を握り潰すと「実行されたのに履歴に無い」が生まれる。
        """
        tools = exposed_tools(exclude_tools)
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
                return await chat(client, messages, tools=tools)
            async for update in chat_stream(client, messages, tools=tools):
                updates.append(update)
                if update.text:
                    await ctx.yield_output(update.text)
            return MafChatResponse.from_updates(updates)

    async def _flush_partial_tool_outcomes(
        self, session_id: str, updates: list, tool_context: ToolContext
    ) -> None:
        """LLM 呼び出しが途中で落ちたとき、そこまでに実行されたツールの結果を履歴へ残す。

        無いと: ストリーミングが応答生成中に落ちたターンで、**ツールは実行済みなのに
        履歴には何も残らない**。フロントは非ストリーミングへ自動フォールバックするので、
        同じツールがもう一度呼ばれうる (副作用ツールなら二重実行)。

        **`TURN_LOCAL_TOOLS` だけは逆に「残さない」** (PR #448 Codex P2)。成果が
        応答 payload にしか無いツールをここで履歴に残すと、フォールバックしたターンで
        モデルが「もう提示した」と読み、payload は捨てられているので**本文だけが
        選択を促して画面にはボタンが無い**状態になる。
        """
        if not updates and not tool_context.executions:
            return
        history = await _get_or_create_session(session_id, self._session_repo)
        _record_tool_outcomes(
            history,
            MafChatResponse.from_updates(updates),
            executions=tool_context.executions,
            drop_tools=TURN_LOCAL_TOOLS,
        )
        await _save_session(session_id, history, self._session_repo)

    async def _settle(
        self,
        session_id: str,
        response: MafChatResponse,
        tool_context: ToolContext,
        ctx: WorkflowContext,
        call_names: Optional[dict[str, str]] = None,
    ) -> None:
        """LLM 呼び出しの結果を「完了」か「承認待ちで中断」のどちらかに落とす。

        **最初のターンと承認再開のターンで同じ経路を通す** (#417 P1)。分けていた
        頃は、再開後にモデルが**別の**副作用ツールを要求しても承認レコードを作らず、
        空の reply でターンを完了させていた — 1 本目の副作用だけ実行済みで、
        2 本目は承認もされず実行もされない状態が黙って残る (Codex 指摘の再現:
        `send_reply` 承認後に `archive_message`)。
        """
        report_identity_arg_attempts(_function_calls(response))
        pending = _approval_requests(response)
        if pending:
            await self._request_approval(
                session_id, response, tool_context, ctx, pending, call_names
            )
            return
        await self._finish_turn(session_id, response, tool_context, ctx, call_names)

    async def _request_approval(
        self,
        session_id: str,
        response: MafChatResponse,
        tool_context: ToolContext,
        ctx: WorkflowContext,
        pending: list[Content],
        call_names: Optional[dict[str, str]] = None,
    ) -> None:
        """承認要求で workflow を中断する (checkpoint に全状態が残る)。"""
        request = pending[0]
        call = request.function_call
        logger.info("Workflow[APPROVAL_IF_NEEDED] tool=%s", call.name if call else None)
        if len(pending) > 1:
            # 1 ターンに複数の副作用ツールが並ぶのは今の題材では起こらないが、
            # 黙って 1 本目だけ承認して残りを捨てると「承認したつもりのない実行」に
            # なりうる。**実測 (#417)**: 残りは再開時に MAF が黙って捨てる = 実行は
            # されない (安全側) が、モデルは「やった」と応答しうる。起きたことが
            # 分かるようにログに残す (#321 で題材を決めるまでの暫定)。
            logger.warning(
                "Workflow[APPROVAL_IF_NEEDED] 承認要求が %d 件届いた — 先頭のみを扱う",
                len(pending),
            )
        # **中断前に実行済みツールの結果を履歴へ**: 承認は再開されないかもしれない
        # (ユーザーが承認画面を閉じる / 承認要求で打ち切られた応答から
        # function_result が落ちる #417 P1)。ここで残さないと「実行されたのに履歴に
        # 無い」= 次のターンで同じツールがもう一度呼ばれる、が静かに成立する
        history = await _get_or_create_session(session_id, self._session_repo)
        _record_tool_outcomes(
            history, response, call_names, executions=tool_context.executions
        )
        await _save_session(session_id, history, self._session_repo)
        await ctx.request_info(
            ApprovalRequest(
                session_id=session_id,
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
        _record_tool_outcomes(
            history, response, call_names, executions=tool_context.executions
        )
        reply = response.text
        history.add_assistant_message(reply)
        await _save_session(session_id, history, self._session_repo)
        await ctx.send_message(
            FinalReply(
                session_id=session_id,
                reply=reply,
                citations=list(tool_context.citations),
                choices=list(tool_context.choices),
            )
        )

    @handler
    async def converse(
        self, turn: ChatTurn, ctx: WorkflowContext[FinalReply, str]
    ) -> None:
        history = await _get_or_create_session(turn.session_id, self._session_repo)
        updates: list = []
        tool_context = ToolContext(session_id=turn.session_id)
        try:
            response = await self._call_llm(
                turn.session_id,
                _windowed(history),
                ctx,
                stream=self._stream,
                updates=updates,
                tool_context=tool_context,
            )
        except Exception:
            await self._flush_partial_tool_outcomes(
                turn.session_id, updates, tool_context
            )
            raise

        await self._settle(turn.session_id, response, tool_context, ctx)

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
            await _save_session(request.session_id, history, self._session_repo)
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
        # **窓は履歴にだけ掛け、`pending` は丸ごと残す** (#486)。pending は中断した
        # ターンの function_call とその承認要求で、ここを削ると承認応答が対を失って
        # 再開そのものが壊れる (窓の目的は「古いターンを落とす」であって、いま
        # 再開しようとしているターンを削ることではない)。
        messages = [
            *_windowed(history),
            *pending,
            Message(
                role="user",
                contents=[
                    approval_content.to_function_approval_response(approved=True)
                ],
            ),
        ]
        # 再開は常に非ストリーミング (/approve は SSE ではない)
        tool_context = ToolContext(session_id=request.session_id)
        updates: list = []
        try:
            response = await self._call_llm(
                request.session_id,
                messages,
                ctx,
                stream=False,
                updates=updates,
                tool_context=tool_context,
                # **この経路では選択肢を呼ばせない** (PR #448 Codex P2)。
                # `/approve` の応答型 (ApproveResponse) は reply しか運べないので、
                # ここで提示された選択肢はクライアントに届かない。呼ばせてから
                # 捨てると、モデルは提示したつもりで「近いものを選んでください」と
                # 書き、画面にはボタンが 1 つも無い応答になる。運べないなら
                # 見せない (`exposed_tools` の exclude)
                exclude_tools=TURN_LOCAL_TOOLS,
            )
        except Exception:
            # `converse` と同じ扱いにする (judge #417)。**再開経路の方が損害が大きい** —
            # ここで落ちた時点で承認済みの副作用ツールは既に実行されているので、
            # 履歴に残さないと /approve が 500 を返したあとユーザーが出し直した
            # ターンで、モデルが同じ副作用ツールを呼び直す (承認済みメールの二重送信)。
            await self._flush_partial_tool_outcomes(
                request.session_id, updates, tool_context
            )
            raise
        tool_context.citations.extend(request.citations)
        # `_settle` を通す = 再開後にモデルが別の副作用ツールを要求したら、
        # **新しい承認要求として立て直す** (完了扱いにしない / #417 P1)
        await self._settle(
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
        await ctx.yield_output(
            ChatResponse(reply=msg.reply, citations=msg.citations, choices=msg.choices)
        )


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
    # **choices は載せない** (#432-b): 承認カードは「承認するまで実行されません」と
    # 言う画面で、そこに「会話の分岐」の選択肢を並べると、押した文言が実行の可否に
    # 効くのか会話に効くのかが読めなくなる。承認要求のターンは承認だけを出す。
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
        # **404 (レコードが無い) と混ぜない** (#82 / PO 裁定 2026-08-15 B 案)。
        # ここに来るのは「同じ承認 ID がもう一度送られた」= 二重送信。404 に混ぜると
        # クライアントは「レコードが消えた」のか「もう解決済み」なのかを判定できず、
        # 却下済み (= 確実に未実行) すら案内できない。
        #
        # **これは早期の見切りであって排他ではない** — 実際に 1 本だけ通す判定は
        # 下の `claim` (原子的な遷移) が持つ。ここだけだと同時リクエストは
        # 両方素通りする (PR #430 Codex P1)。
        raise ApprovalAlreadyProcessedError(record.status, record.processed_at)
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
        # in-memory 構成: ここでは**参照するだけ**で解放しない。pop を排他の代用に
        # すると、同時に届いた 2 本目が「checkpoint が無い」(404) に化けて
        # **二重送信が二重送信として説明されない**。排他の責務は下の claim に
        # 1 本化し、解放は再開が終わってから (finally) 行う。
        storage = _pending_run_storages.get(approval_id)
        if storage is None:
            raise ValueError(f"Approval checkpoint not found: {approval_id!r}")

    # **ここが「1 回だけ実行する」の要** (PR #430 Codex P1)。pending からの遷移を
    # 原子的に獲得し (Cosmos は ETag 条件付き置換 / in-memory は lock)、取れなかった
    # 側は checkpoint を再開せずに 409 で返す。上の status チェックだけでは、
    # 同時に届いた 2 本が**両方 pending を読んで両方が副作用を実行する**。
    #
    # 遷移を resume の**前**に書く順序は従来どおり: 実行中にクラッシュしたときは
    # 「実行されたか分からない」レコードが残るが、二重実行よりそちらを選ぶ。
    # したがって `processed_at` は **受け付けた時刻であって完了の証拠ではない**
    # (この意味は 409 の応答と UI 文言まで一貫させてある)。
    claimed = await approval_repo.claim(
        approval_id,
        "approved" if approved else "rejected",
        datetime.now(timezone.utc).isoformat(),
    )
    if claimed is None:
        current = await approval_repo.get(approval_id)
        if current is None or current.status == "pending":
            # 競合に負けたのに pending のまま / レコードが消えた = 承認レコードは
            # もう当てにできない。**「処理済み」と断定せず** 404 側に倒す
            raise ValueError(f"Approval not found: {approval_id!r}")
        raise ApprovalAlreadyProcessedError(current.status, current.processed_at)
    record = claimed

    try:
        return await _resume_claimed_run(
            approval_id, approved, record, storage, session_repo, approval_repo, client
        )
    finally:
        if not _cosmos_enabled():
            # in-memory には TTL が無いので、解決した run の checkpoint を残さない
            # (PR #243 レビュー指摘)。**解放は claim を獲得した側が、再開を終えてから**
            # — 早く pop すると、同時に届いた 2 本目の checkpoint 参照が先に消えて
            # 409 (二重送信) が 404 (checkpoint が無い) に化ける。成功・失敗どちらでも
            # 解放するので、失敗した run の storage が残り続けることもない。
            _pending_run_storages.pop(approval_id, None)


async def _resume_claimed_run(
    approval_id: str,
    approved: bool,
    record: ApprovalRecord,
    storage: CheckpointStorage,
    session_repo: SessionRepository,
    approval_repo: ApprovalRepository,
    client: Optional[BaseChatClient],
) -> str:
    """claim を獲得した 1 本だけが通る再開処理 (排他の判定は呼び出し側が済ませている)。"""
    workflow = _build_chat_workflow(
        session_repo, client, stream=False, checkpoint_storage=storage
    )
    result = await workflow.run(
        responses={approval_id: ApprovalDecision(approved=approved)},
        checkpoint_id=record.checkpoint_id,
    )

    # 再開したターンでモデルが**別の副作用ツール**を要求した場合 (#417 P1)。
    # 完了 output は無く、代わりに新しい request_info event が立つ。ここで
    # 新しい承認レコード (= 新しい checkpoint への写像) を作らないと、承認も
    # 実行もされないまま空の reply でターンが閉じる。
    #
    # **残る制約**: `/approve` の応答型 (ApproveResponse) は reply しか運べないため、
    # ここで採番した approvalRequestId をクライアントへ渡す口が今は無い
    # (契約変更 = BFF + フロントの追随が要る)。サービス層では承認レコードも
    # checkpoint も正しく立っているので、続きの承認は次の /chat ターンで
    # 立て直される。end-to-end の連鎖は Issue #82 のスレッドで扱う。
    requests = result.get_request_info_events()
    if requests:
        follow_up = await _record_approval_request(requests[0], approval_repo, storage)
        if _cosmos_enabled():
            await storage.delete(record.checkpoint_id)
        return follow_up.reply

    outputs = result.get_outputs()
    if not outputs:
        raise RuntimeError("Chat workflow resume completed without a response")

    if outputs[-1].choices:
        # ここは**到達しないはず** (PR #448 Codex P2): 再開経路では `offer_choices` を
        # LLM に見せていない (`on_approval_decision` の exclude_tools)。それでも
        # 選択肢が載っていたら、除外が効いていない = 「モデルは提示したのに画面には
        # 出ない」が起きている。**黙って落とさず**、除外が壊れたことに気づける形で
        # 残す (件数だけ / 文言は相談内容なので出さない)
        logger.error(
            "Workflow[APPROVAL_IF_NEEDED] 再開ターンに選択肢が %d 件載っている — "
            "除外 (exclude_tools) が効いていない。/approve の応答型では運べない",
            len(outputs[-1].choices),
        )

    if _cosmos_enabled():
        # 解決時 delete (#188): 解決済みの pending checkpoint は TTL を待たずに消す。
        # 再開中に書かれた後続 checkpoint と、中断前の祖先はコンテナ TTL が掃除する
        await storage.delete(record.checkpoint_id)

    return outputs[-1].reply
