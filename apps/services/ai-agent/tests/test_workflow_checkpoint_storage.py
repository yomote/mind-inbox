"""[L1] checkpoint storage の選択と、共有ストア (Cosmos) 構成の掃除設計を pin する (#188)。

無いと何が静かに通るか:
- COSMOS_ENDPOINT があっても checkpoint だけ in-memory のまま残り、
  「セッションは残るのに承認は再起動で消える」中途半端な永続化が無言で通る
- アプリの pydantic 型を allowed_checkpoint_types へ登録し忘れ、Cosmos からの
  checkpoint 復元 (= /approve の再開) だけが本番で落ちる退行
  (このテストは fake ストア経由で実際に encode → decode を通すので登録漏れで落ちる)
- 共有ストア構成の /approve がプロセス内 registry に依存してしまい、
  再起動後 (= registry が空) の再開が壊れる退行
- 解決済み承認の checkpoint が削除されず TTL まで残る退行
  (掃除の設計: container TTL が主経路 + 解決時 delete が即時分)

ここで test しないこと:
- InMemory 構成の承認 roundtrip / registry 解放 (test_workflow_approval.py が pin 済み)
- MAF CosmosCheckpointStorage の内部・実 Cosmos の TTL (フレームワーク / live 検証の領域)
"""

import pytest
from agent_framework import InMemoryCheckpointStorage
from agent_framework_azure_cosmos import CosmosCheckpointStorage

from app.workflow import (
    _APP_CHECKPOINT_TYPES,
    _new_checkpoint_storage,
    _pending_run_storages,
    resume_after_approval,
    run_workflow,
)
from tests.test_workflow_approval import (
    APPROVAL_CLASSIFICATION,
    FakeKernel,
    RoutedChatService,
)


class TestCheckpointStorageSelection:
    def test_l1_defaults_to_inmemory_without_endpoint(self, monkeypatch):
        from app.config import get_settings

        monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
        get_settings.cache_clear()
        try:
            assert isinstance(_new_checkpoint_storage(), InMemoryCheckpointStorage)
        finally:
            get_settings.cache_clear()

    def test_l1_uses_cosmos_storage_with_app_types_when_endpoint_set(self, cosmos_mode):
        storage = _new_checkpoint_storage()

        assert isinstance(storage, CosmosCheckpointStorage)
        # 登録漏れ = 本番の /approve 復元だけが落ちる。ここで配線を pin する
        assert storage._allowed_types >= frozenset(_APP_CHECKPOINT_TYPES)


class TestCosmosModeApprovalLifecycle:
    async def test_l1_resume_does_not_depend_on_process_registry(
        self, cosmos_mode, session_repo, approval_repo
    ):
        """共有ストア構成では registry を経由しない = 再起動を跨いでも再開できる形。"""
        kernel = FakeKernel(RoutedChatService(APPROVAL_CLASSIFICATION))

        res = await run_workflow(
            "s-cosmos", "返信して", session_repo, approval_repo, kernel
        )

        assert res.requires_approval is True
        # 共有ストア構成はプロセス内 registry に何も残さない
        assert res.approval_request_id not in _pending_run_storages

        # registry が空のまま (= プロセス再起動相当) でも、共有ストアの
        # checkpoint から再開できる
        reply = await resume_after_approval(
            res.approval_request_id, True, session_repo, approval_repo, kernel
        )
        assert reply == "対応しました。"

        record = await approval_repo.get(res.approval_request_id)
        assert record.status == "approved"

    async def test_l1_resolve_deletes_pending_checkpoint(
        self, cosmos_mode, session_repo, approval_repo
    ):
        kernel = FakeKernel(RoutedChatService(APPROVAL_CLASSIFICATION))

        res = await run_workflow(
            "s-cosmos-del", "返信して", session_repo, approval_repo, kernel
        )
        record = await approval_repo.get(res.approval_request_id)
        assert any(
            d.get("checkpoint_id") == record.checkpoint_id
            for d in cosmos_mode.items.values()
        )

        await resume_after_approval(
            res.approval_request_id, True, session_repo, approval_repo, kernel
        )

        # 解決時 delete: 解決済みの pending checkpoint は TTL を待たずに消える
        assert not any(
            d.get("checkpoint_id") == record.checkpoint_id
            for d in cosmos_mode.items.values()
        )

    async def test_l1_resume_missing_checkpoint_raises_not_found(
        self, cosmos_mode, session_repo, approval_repo
    ):
        """TTL 失効相当 (checkpoint 文書だけ消えた) は 404 系 (ValueError) に写る。"""
        kernel = FakeKernel(RoutedChatService(APPROVAL_CLASSIFICATION))

        res = await run_workflow(
            "s-cosmos-ttl", "返信して", session_repo, approval_repo, kernel
        )
        record = await approval_repo.get(res.approval_request_id)
        # checkpoint 文書だけ TTL で消えた状況を再現
        for doc_id, doc in list(cosmos_mode.items.items()):
            if doc.get("checkpoint_id") == record.checkpoint_id:
                del cosmos_mode.items[doc_id]

        with pytest.raises(ValueError, match="Approval checkpoint not found"):
            await resume_after_approval(
                res.approval_request_id, True, session_repo, approval_repo, kernel
            )
