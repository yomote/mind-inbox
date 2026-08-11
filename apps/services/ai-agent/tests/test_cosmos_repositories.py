"""[L1] Cosmos repository — 直列化を実装の内側に閉じ込める契約を pin する (#188)。

無いと何が静かに通るか:
- ChatHistory の serialize/restore の往復が壊れ、再起動を跨いだセッションが壊れて
  返る退行 (Protocol の戻り値型が dict 等に化けても、動的型の呼び出し側は
  一見動いてしまう — #81 コメントの「既存テスト 21 件」が守る境界の実装側)
- 未存在 id の get が None ではなく例外になり、/chat の新規セッション作成が 500 になる退行
- COSMOS_ENDPOINT の有無で実装を選ぶ stub fallback (ADR 0030 D7 の流儀) の退行

ここで test しないこと:
- 実 Cosmos への I/O・TTL 失効の実測 (ADR 0030 動作検証 4 = live 検証の領域)
- InMemory 実装 (test_repositories.py が pin 済み)
"""

from semantic_kernel.contents import ChatHistory

from app.repositories import (
    CosmosApprovalRepository,
    CosmosSessionRepository,
    InMemoryApprovalRepository,
    InMemorySessionRepository,
    create_approval_repository,
    create_session_repository,
)
from app.schemas import ApprovalRecord, Plan


class TestCosmosSessionRepository:
    async def test_l1_roundtrip_returns_chat_history(self, fake_cosmos_container):
        repo = CosmosSessionRepository(fake_cosmos_container)
        history = ChatHistory()
        history.add_system_message("system prompt")
        history.add_user_message("こんにちは")
        history.add_assistant_message("どうしました？")

        await repo.save("s1", history)
        restored = await repo.get("s1")

        # Protocol の戻り値型は ChatHistory のまま (直列化は実装の内側)
        assert isinstance(restored, ChatHistory)
        assert [m.role.value for m in restored.messages] == [
            "system",
            "user",
            "assistant",
        ]
        assert [str(m.content) for m in restored.messages] == [
            "system prompt",
            "こんにちは",
            "どうしました？",
        ]

    async def test_l1_get_missing_returns_none(self, fake_cosmos_container):
        repo = CosmosSessionRepository(fake_cosmos_container)
        assert await repo.get("nonexistent") is None

    async def test_l1_save_overwrites(self, fake_cosmos_container):
        repo = CosmosSessionRepository(fake_cosmos_container)
        h1 = ChatHistory()
        h1.add_user_message("first")
        h2 = ChatHistory()
        h2.add_user_message("second")

        await repo.save("s1", h1)
        await repo.save("s1", h2)
        restored = await repo.get("s1")

        assert [str(m.content) for m in restored.messages] == ["second"]

    async def test_l1_delete_removes_entry(self, fake_cosmos_container):
        repo = CosmosSessionRepository(fake_cosmos_container)
        await repo.save("s1", ChatHistory())
        await repo.delete("s1")

        assert await repo.get("s1") is None

    async def test_l1_delete_nonexistent_is_noop(self, fake_cosmos_container):
        repo = CosmosSessionRepository(fake_cosmos_container)
        await repo.delete("nonexistent")  # should not raise


class TestCosmosApprovalRepository:
    async def test_l1_roundtrip_preserves_record(self, fake_cosmos_container):
        repo = CosmosApprovalRepository(fake_cosmos_container)
        record = ApprovalRecord(
            session_id="s1",
            plan=Plan(
                tool_name="send_reply",
                tool_args={"to": "a@example.com"},
                is_side_effecting=True,
            ),
            rag_context="ctx",
            checkpoint_id="cp-1",
        )

        await repo.save(record)
        fetched = await repo.get(record.id)

        # Cosmos のシステムプロパティ (_rid 等) が混ざらず、同値のレコードが返る
        assert isinstance(fetched, ApprovalRecord)
        assert fetched == record

    async def test_l1_get_missing_returns_none(self, fake_cosmos_container):
        repo = CosmosApprovalRepository(fake_cosmos_container)
        assert await repo.get("nonexistent") is None

    async def test_l1_save_overwrites_on_same_id(self, fake_cosmos_container):
        repo = CosmosApprovalRepository(fake_cosmos_container)
        record = ApprovalRecord(
            session_id="s1",
            plan=Plan(tool_name="send_reply", is_side_effecting=True),
        )
        await repo.save(record)
        record.status = "approved"
        await repo.save(record)

        fetched = await repo.get(record.id)
        assert fetched.status == "approved"


class TestRepositoryFactories:
    """COSMOS_ENDPOINT の有無による実装選択 (stub fallback / ADR 0030 D7 の流儀)。"""

    def test_l1_defaults_to_inmemory_without_endpoint(self, monkeypatch):
        from app.config import get_settings

        monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
        get_settings.cache_clear()
        try:
            assert isinstance(create_session_repository(), InMemorySessionRepository)
            assert isinstance(create_approval_repository(), InMemoryApprovalRepository)
        finally:
            get_settings.cache_clear()

    def test_l1_uses_cosmos_when_endpoint_set(self, cosmos_mode):
        assert isinstance(create_session_repository(), CosmosSessionRepository)
        assert isinstance(create_approval_repository(), CosmosApprovalRepository)
