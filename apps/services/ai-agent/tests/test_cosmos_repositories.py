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
- SK ChatHistory.serialize 形式 (items/content_type) の読み取り。M1-5 の移行期に
  限った後方互換で、その形式の文書は TTL 7 日の経過で消滅済みのため #502 で削除した
  (守る仕様そのものが無くなったので、テストも一緒に落とした)

fixture 置き換え (M1-5 / #82): SK 依存除去に伴い ChatHistory を SK 型から
app.history.ChatHistory (MAF Message ベース) へ差し替えた。メッセージの
role は素の str、テキストは .text になったが、roundtrip の検証意図は不変。
"""

from app.history import ChatHistory
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
        assert [m.role for m in restored.messages] == [
            "system",
            "user",
            "assistant",
        ]
        assert [m.text for m in restored.messages] == [
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

        assert [m.text for m in restored.messages] == ["second"]

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


class TestCosmosApprovalClaim:
    """`pending` → 処理済みの遷移を **1 本だけ通す** 条件付き更新 (PR #430 Codex P1)。

    無いと何が静かに通るか: 条件なしの upsert に戻すと、同じ承認 ID がほぼ同時に
    2 本届いたとき**両方が pending を読んで両方が書き込みに成功**し、両方が
    checkpoint 再開へ進む = **副作用 (メール送信など) が二重に実行される**。
    409 は「2 本目が来た」ことを説明するだけで、実行そのものは止められない。
    画面上は普通に成功して見えるので、二重送信された側の証跡が出るまで気づけない。
    """

    def _pending(self) -> ApprovalRecord:
        return ApprovalRecord(
            session_id="s1",
            plan=Plan(tool_name="send_reply", is_side_effecting=True),
            checkpoint_id="cp-1",
        )

    async def test_単体_claim_は_pending_を処理済みに進めて更新後を返す(
        self, fake_cosmos_container
    ):
        repo = CosmosApprovalRepository(fake_cosmos_container)
        record = self._pending()
        await repo.save(record)

        claimed = await repo.claim(record.id, "approved", "2026-08-15T02:00:00+00:00")

        assert claimed is not None
        assert claimed.status == "approved"
        assert claimed.processed_at == "2026-08-15T02:00:00+00:00"
        # 保存側にも反映されていること (返り値だけ作って書いていない、を弾く)
        assert (await repo.get(record.id)).status == "approved"

    async def test_単体_二本目の_claim_は_None(self, fake_cosmos_container):
        repo = CosmosApprovalRepository(fake_cosmos_container)
        record = self._pending()
        await repo.save(record)

        first = await repo.claim(record.id, "approved", "2026-08-15T02:00:00+00:00")
        second = await repo.claim(record.id, "rejected", "2026-08-15T02:00:01+00:00")

        assert first is not None
        assert second is None
        # 負けた側の決定で**上書きされない** (approved が rejected に化けない)
        assert (await repo.get(record.id)).status == "approved"

    async def test_単体_読んでから置換するまでに他が書いたら_claim_は_None(
        self, fake_cosmos_container
    ):
        """ETag 条件が本当に効いているか (= 同時 2 本のうち片方が負けるか)。

        無いと何が静かに通るか: `etag` を渡さない実装に戻しても、直列に呼ぶ限りは
        status チェックで弾けるので上のテストは通ってしまう。**読んだあとに他者が
        書いた**場合だけが条件付き更新の存在意義なので、その窓をここで再現する。
        """
        repo = CosmosApprovalRepository(fake_cosmos_container)
        record = self._pending()
        await repo.save(record)

        original_read = fake_cosmos_container.read_item

        async def read_then_let_another_win(item, partition_key):
            doc = await original_read(item=item, partition_key=partition_key)
            # 読んだ直後に「もう 1 本」が同じ文書を書き換えた状況 (ETag が変わる)。
            # status は pending のままにして、**ETag だけが判定材料**になるようにする
            if fake_cosmos_container.items[item].get("status") == "pending":
                await fake_cosmos_container.upsert_item(
                    body={**fake_cosmos_container.items[item]}
                )
            return doc

        fake_cosmos_container.read_item = read_then_let_another_win

        claimed = await repo.claim(record.id, "approved", "2026-08-15T02:00:00+00:00")

        assert claimed is None
        # 負けた側は 1 文字も書いていない
        assert (await repo.get(record.id)).status == "pending"


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
