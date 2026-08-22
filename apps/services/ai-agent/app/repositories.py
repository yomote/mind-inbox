"""
Repository パターン。

実装は 2 系統 (#188 / ADR 0030):
  InMemory*  — COSMOS_ENDPOINT 未設定時 (ローカル / テストの既定)。再起動で消える
  Cosmos*    — COSMOS_ENDPOINT 設定時。TTL 付きコンテナ (bicep が宣言) に永続化

Protocol の戻り値型 (ChatHistory / ApprovalRecord) は実装で変えない —
直列化は Cosmos 実装の内側に閉じ込める (#81 コメント: 型を変えると既存テストが全滅)。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

from azure.core import MatchConditions
from azure.cosmos.exceptions import (
    CosmosAccessConditionFailedError,
    CosmosResourceNotFoundError,
)

from .config import get_settings
from .history import ChatHistory
from .schemas import ApprovalDecision, ApprovalRecord

if TYPE_CHECKING:
    from azure.cosmos.aio import ContainerProxy


class SessionRepository(Protocol):
    async def get(self, session_id: str) -> ChatHistory | None: ...
    async def save(self, session_id: str, history: ChatHistory) -> None: ...
    async def delete(self, session_id: str) -> None: ...


class ApprovalRepository(Protocol):
    async def get(self, approval_id: str) -> ApprovalRecord | None: ...
    async def save(self, record: ApprovalRecord) -> None: ...

    async def claim(
        self, approval_id: str, status: ApprovalDecision, processed_at: str
    ) -> ApprovalRecord | None:
        """`pending` → `status` の遷移を**原子的に**獲得する。

        獲得できたら更新後のレコード、**取れなかったら None** (= 既に誰かが
        解決した / レコードが無い)。呼び出し側は None を「二重送信」として
        409 に写す (`workflow.resume_after_approval`)。

        無いと何が静かに通るか (PR #430 Codex P1): 「read して status を見てから
        save する」だけでは、同じ承認 ID がほぼ同時に 2 本届いたとき**両方が
        pending を読む**。両方が checkpoint 再開へ進み、**副作用が二重に実行される**
        (承認 UI が守っているはずの「1 回だけ実行する」が崩れる)。409 はその後に
        返る二重送信を説明するだけで、実行そのものは止められない。
        """
        ...


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
        # `claim` の排他。単一プロセス・単一イベントループなので lock で足りる
        # (Cosmos 実装の ETag 条件付き更新と同じ「pending は 1 度しか取れない」を
        # ここでも成立させる — 構成によって二重実行の可否が変わらないようにする)。
        self._claim_lock = asyncio.Lock()

    async def get(self, approval_id: str) -> ApprovalRecord | None:
        return self._store.get(approval_id)  # type: ignore[return-value]

    async def save(self, record: ApprovalRecord) -> None:
        self._store[record.id] = record

    async def claim(
        self, approval_id: str, status: ApprovalDecision, processed_at: str
    ) -> ApprovalRecord | None:
        async with self._claim_lock:
            record: ApprovalRecord | None = self._store.get(approval_id)  # type: ignore[assignment]
            if record is None or record.status != "pending":
                return None
            # **元のオブジェクトを書き換えない** (model_copy): in-memory は呼び出し側と
            # 同じインスタンスを返しているので、破壊的に書くと「保存していないのに
            # 呼び出し側の record が変わっている」= Cosmos 構成と挙動が割れる。
            claimed = record.model_copy(
                update={"status": status, "processed_at": processed_at}
            )
            self._store[approval_id] = claimed
            return claimed


class CosmosSessionRepository:
    """会話セッション (ChatHistory) の Cosmos 永続化 (#188)。

    直列化は app.history.ChatHistory の serialize() / deserialize() (MAF Message
    ベース) を使い、この実装の内側に閉じる。
    文書は {id: session_id, history: <JSON 文字列>}。
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

    async def claim(
        self, approval_id: str, status: ApprovalDecision, processed_at: str
    ) -> ApprovalRecord | None:
        """ETag 条件付き置換で `pending` からの遷移を 1 本だけ通す (PR #430 Codex P1)。

        `save` (= 条件なしの `upsert_item`) では、同時に届いた 2 本が**両方 pending を
        読んで両方 upsert に成功する**ため、二重実行を止められない。ここは読んだ
        文書の `_etag` を条件に付け、負けた側 (412) を None で返す。

        `pending` 以外を読んだ時点でも None (= もう誰かが取った)。この 2 つを
        呼び出し側で区別する必要は無い — どちらも「この呼び出しは実行してはいけない」。
        """
        try:
            doc = await self._container.read_item(
                item=approval_id, partition_key=approval_id
            )
        except CosmosResourceNotFoundError:
            return None
        if doc.get("status") != "pending":
            return None

        claimed = ApprovalRecord.model_validate(doc).model_copy(
            update={"status": status, "processed_at": processed_at}
        )
        body = claimed.model_dump(mode="json")
        try:
            await self._container.replace_item(
                item=approval_id,
                body=body,
                etag=doc["_etag"],
                match_condition=MatchConditions.IfNotModified,
            )
        except CosmosAccessConditionFailedError:
            # 412 = 読んでから置換するまでの間に他のリクエストが遷移させた。
            # **握り潰していない**: 呼び出し側は None を 409 (二重送信) として返す
            return None
        return claimed


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
