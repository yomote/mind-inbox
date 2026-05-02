"""
Repository パターン。

PoC では in-memory 実装を使用する。
差し替えポイント:
  InMemorySessionRepository  → Redis
  InMemoryApprovalRepository → Redis（TTL 付き）
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from semantic_kernel.contents import ChatHistory

if TYPE_CHECKING:
    from .schemas import ApprovalRecord


class SessionRepository(Protocol):
    async def get(self, session_id: str) -> ChatHistory | None: ...
    async def save(self, session_id: str, history: ChatHistory) -> None: ...
    async def delete(self, session_id: str) -> None: ...


class ApprovalRepository(Protocol):
    async def get(self, approval_id: str) -> ApprovalRecord | None: ...
    async def save(self, record: ApprovalRecord) -> None: ...


class InMemorySessionRepository:
    """TODO(PoC): 再起動でセッションが消える。本番では Redis に差し替える。"""

    def __init__(self) -> None:
        self._store: dict[str, ChatHistory] = {}

    async def get(self, session_id: str) -> ChatHistory | None:
        return self._store.get(session_id)

    async def save(self, session_id: str, history: ChatHistory) -> None:
        self._store[session_id] = history

    async def delete(self, session_id: str) -> None:
        self._store.pop(session_id, None)


class InMemoryApprovalRepository:
    """TODO(PoC): 再起動で承認レコードが消える。本番では Redis（TTL 付き）に差し替える。"""

    def __init__(self) -> None:
        self._store: dict[str, object] = {}

    async def get(self, approval_id: str) -> ApprovalRecord | None:
        return self._store.get(approval_id)  # type: ignore[return-value]

    async def save(self, record: ApprovalRecord) -> None:
        self._store[record.id] = record
