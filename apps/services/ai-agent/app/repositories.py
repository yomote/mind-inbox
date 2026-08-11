"""
Repository パターン。

実装は 2 系統 (#188 / ADR 0030):
  InMemory*  — COSMOS_ENDPOINT 未設定時 (ローカル / テストの既定)。再起動で消える
  Cosmos*    — COSMOS_ENDPOINT 設定時。TTL 付きコンテナ (bicep が宣言) に永続化

Protocol の戻り値型 (ChatHistory / ApprovalRecord) は実装で変えない —
直列化は Cosmos 実装の内側に閉じ込める (#81 コメント: 型を変えると既存テストが全滅)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from azure.cosmos.exceptions import CosmosResourceNotFoundError

from .config import get_settings
from .history import ChatHistory
from .schemas import ApprovalRecord

if TYPE_CHECKING:
    from azure.cosmos.aio import ContainerProxy


class SessionRepository(Protocol):
    async def get(self, session_id: str) -> ChatHistory | None: ...
    async def save(self, session_id: str, history: ChatHistory) -> None: ...
    async def delete(self, session_id: str) -> None: ...


class ApprovalRepository(Protocol):
    async def get(self, approval_id: str) -> ApprovalRecord | None: ...
    async def save(self, record: ApprovalRecord) -> None: ...


class InMemorySessionRepository:
    """COSMOS_ENDPOINT 未設定時の既定。再起動でセッションが消える (ローカル開発用)。"""

    def __init__(self) -> None:
        self._store: dict[str, ChatHistory] = {}

    async def get(self, session_id: str) -> ChatHistory | None:
        return self._store.get(session_id)

    async def save(self, session_id: str, history: ChatHistory) -> None:
        self._store[session_id] = history

    async def delete(self, session_id: str) -> None:
        self._store.pop(session_id, None)


class InMemoryApprovalRepository:
    """COSMOS_ENDPOINT 未設定時の既定。再起動で承認レコードが消える (ローカル開発用)。"""

    def __init__(self) -> None:
        self._store: dict[str, object] = {}

    async def get(self, approval_id: str) -> ApprovalRecord | None:
        return self._store.get(approval_id)  # type: ignore[return-value]

    async def save(self, record: ApprovalRecord) -> None:
        self._store[record.id] = record


class CosmosSessionRepository:
    """会話セッション (ChatHistory) の Cosmos 永続化 (#188)。

    直列化は app.history.ChatHistory の serialize() / deserialize() (MAF Message
    ベース。SK 形式の既存文書も読める後方互換つき — M1-5 の SK 除去) を使い、
    この実装の内側に閉じる。文書は {id: session_id, history: <JSON 文字列>}。
    寿命はコンテナ TTL (7 日 / bicep 宣言) — _ts 起点なので save のたびに延びる。
    """

    def __init__(self, container: ContainerProxy) -> None:
        self._container = container

    async def get(self, session_id: str) -> ChatHistory | None:
        try:
            doc = await self._container.read_item(
                item=session_id, partition_key=session_id
            )
        except CosmosResourceNotFoundError:
            return None
        return ChatHistory.deserialize(doc["history"])

    async def save(self, session_id: str, history: ChatHistory) -> None:
        await self._container.upsert_item(
            body={"id": session_id, "history": history.serialize()}
        )

    async def delete(self, session_id: str) -> None:
        try:
            await self._container.delete_item(item=session_id, partition_key=session_id)
        except CosmosResourceNotFoundError:
            pass


class CosmosApprovalRepository:
    """承認レコードの Cosmos 永続化 (#188)。

    ApprovalRecord は pydantic なので model_dump / model_validate で往復する
    (Cosmos のシステムプロパティは pydantic の extra=ignore が読み飛ばす)。
    寿命はコンテナ TTL (1 時間 / bicep 宣言) = 承認 1 往復の目安。
    """

    def __init__(self, container: ContainerProxy) -> None:
        self._container = container

    async def get(self, approval_id: str) -> ApprovalRecord | None:
        try:
            doc = await self._container.read_item(
                item=approval_id, partition_key=approval_id
            )
        except CosmosResourceNotFoundError:
            return None
        return ApprovalRecord.model_validate(doc)

    async def save(self, record: ApprovalRecord) -> None:
        await self._container.upsert_item(body=record.model_dump(mode="json"))


def create_session_repository() -> SessionRepository:
    """COSMOS_ENDPOINT の有無で実装を選ぶ (stub fallback / ADR 0030 D7 の流儀)。"""
    settings = get_settings()
    if settings.cosmos_endpoint:
        from . import cosmos

        return CosmosSessionRepository(
            cosmos.get_container(settings.cosmos_sessions_container)
        )
    return InMemorySessionRepository()


def create_approval_repository() -> ApprovalRepository:
    """COSMOS_ENDPOINT の有無で実装を選ぶ (stub fallback / ADR 0030 D7 の流儀)。"""
    settings = get_settings()
    if settings.cosmos_endpoint:
        from . import cosmos

        return CosmosApprovalRepository(
            cosmos.get_container(settings.cosmos_approvals_container)
        )
    return InMemoryApprovalRepository()
