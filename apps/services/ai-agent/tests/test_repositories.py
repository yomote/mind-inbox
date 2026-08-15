"""[L1] InMemory repository の "状態を跨いだ" 不変条件を pin する。

InMemory* は dev/test stub であり、本番は SQL/Cosmos に置き換わる予定 (#7)。
本番 repository が実装された時点でこの test 群は **意味を失う** ので、
本番 repository test の追加と同時に削除する想定。

ここで test しないこと:
- 単発の get/save (= dict 操作の wrapper) — 本番 repository 実装時の対象
- 並行アクセスの一貫性 — InMemory は単一プロセス前提

fixture 置き換え (M1-5 / #82): SK 依存除去に伴い、履歴 fixture を SK ChatHistory
から app.history.ChatHistory (MAF Message ベース / 追記 API は同形) へ差し替えた。
検証意図は不変。
"""

import asyncio

from app.history import ChatHistory

from app.schemas import ApprovalRecord, Plan


class TestInMemorySessionRepository:
    async def test_save_overwrites(self, session_repo):
        h1 = ChatHistory()
        h2 = ChatHistory()
        await session_repo.save("s1", h1)
        await session_repo.save("s1", h2)

        assert await session_repo.get("s1") is h2

    async def test_delete_removes_entry(self, session_repo):
        history = ChatHistory()
        await session_repo.save("s1", history)
        await session_repo.delete("s1")

        assert await session_repo.get("s1") is None

    async def test_delete_nonexistent_is_noop(self, session_repo):
        await session_repo.delete("nonexistent")  # should not raise


class TestInMemoryApprovalRepository:
    async def test_save_overwrites_on_same_id(self, approval_repo):
        record = ApprovalRecord(
            session_id="s1",
            plan=Plan(tool_name="send_reply", is_side_effecting=True),
        )
        await approval_repo.save(record)
        record.status = "approved"
        await approval_repo.save(record)

        fetched = await approval_repo.get(record.id)
        assert fetched.status == "approved"

    async def test_ids_are_unique(self, approval_repo):
        r1 = ApprovalRecord(session_id="s1", plan=Plan())
        r2 = ApprovalRecord(session_id="s2", plan=Plan())
        assert r1.id != r2.id


class TestInMemoryApprovalClaim:
    """`claim` は pending を**一度しか渡さない** (PR #430 Codex P1)。

    無いと何が静かに通るか: in-memory 構成だけ「2 本目にも pending を渡す」実装に
    なり、ローカル・テストでは二重実行が再現しないまま Cosmos 構成でだけ起きる。
    構成で守りの強さを変えないことをここで固定する。

    **注意 (このテストが証明していないこと)**: in-memory の `claim` は現状
    await を挟まないので、単一イベントループでは lock が無くても直列に走る =
    「lock を消す」変異ではこのテストは落ちない。lock は**臨界区間に await が
    増えたときに守る**ためのもので、ここで固定しているのは呼び出し側から見た
    振る舞い (勝者は 1 本 / 呼び出し側のレコードは変わらない / pending 以外は None)。
    実際の競合を判定に使っているのは Cosmos 側の ETag テスト
    (`test_cosmos_repositories.py`) と workflow の同時再開テスト。
    """

    def _pending(self) -> ApprovalRecord:
        return ApprovalRecord(
            session_id="s1",
            plan=Plan(tool_name="send_reply", is_side_effecting=True),
            checkpoint_id="cp-1",
        )

    async def test_単体_同時に走らせても獲得できるのは一本だけ(self, approval_repo):
        record = self._pending()
        await approval_repo.save(record)

        results = await asyncio.gather(
            approval_repo.claim(record.id, "approved", "2026-08-15T02:00:00+00:00"),
            approval_repo.claim(record.id, "rejected", "2026-08-15T02:00:01+00:00"),
        )

        assert sum(r is not None for r in results) == 1
        assert (await approval_repo.get(record.id)).status in ("approved", "rejected")

    async def test_単体_claim_は呼び出し側のレコードを書き換えない(self, approval_repo):
        """呼び出し側が持っている `record` が黙って変わらないこと。

        無いと何が静かに通るか: in-memory は同じインスタンスを返すので、破壊的に
        更新すると「保存していないのに状態が進んでいる」= Cosmos 構成と挙動が割れる。
        構成差はテストが緑のまま本番だけ壊れる形で出る。
        """
        record = self._pending()
        await approval_repo.save(record)

        await approval_repo.claim(record.id, "approved", "2026-08-15T02:00:00+00:00")

        assert record.status == "pending"
        assert (await approval_repo.get(record.id)).status == "approved"

    async def test_単体_処理済みや未知_id_の_claim_は_None(self, approval_repo):
        record = self._pending()
        await approval_repo.save(record)
        await approval_repo.claim(record.id, "approved", "2026-08-15T02:00:00+00:00")

        assert (
            await approval_repo.claim(
                record.id, "rejected", "2026-08-15T02:00:01+00:00"
            )
            is None
        )
        assert (
            await approval_repo.claim(
                "no-such-id", "approved", "2026-08-15T02:00:02+00:00"
            )
            is None
        )
