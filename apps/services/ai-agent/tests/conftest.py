"""共通 fixture (strategy.md §1.3「mock 一元化」原則の運用)。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from azure.cosmos.exceptions import (
    CosmosAccessConditionFailedError,
    CosmosResourceNotFoundError,
)

from app.repositories import InMemoryApprovalRepository, InMemorySessionRepository


@pytest.fixture
def session_repo() -> InMemorySessionRepository:
    return InMemorySessionRepository()


@pytest.fixture
def approval_repo() -> InMemoryApprovalRepository:
    return InMemoryApprovalRepository()


class FakeCosmosContainer:
    """azure.cosmos.aio ContainerProxy の最小 fake (#188)。

    Cosmos リポジトリと MAF CosmosCheckpointStorage が使う操作
    (read_item / upsert_item / delete_item / query_items) だけを、
    dict をストアにして再現する。TTL は再現しない (それは実環境の検証 =
    ADR 0030 動作検証 4 の領域)。
    """

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}
        # 書き込みのたびに変わる ETag。**固定値にしない** — 固定だと
        # 条件付き更新 (replace_item + MatchConditions) が常に成功してしまい、
        # 「ETag 条件を外した」変異をテストが検出できなくなる (PR #430 Codex P1)
        self._etag_seq = 0

    def _next_etag(self) -> str:
        self._etag_seq += 1
        return f"fake-etag-{self._etag_seq}"

    async def read_item(self, item: str, partition_key: str) -> dict:
        if item not in self.items:
            raise CosmosResourceNotFoundError(
                status_code=404, message=f"{item} not found"
            )
        return dict(self.items[item])

    async def upsert_item(self, body: dict) -> dict:
        stored = {**body, "_rid": "fake-rid", "_etag": self._next_etag(), "_ts": 0}
        self.items[body["id"]] = stored
        return dict(stored)

    async def replace_item(
        self,
        item: str,
        body: dict,
        etag: str | None = None,
        match_condition=None,
        **kwargs,
    ) -> dict:
        """条件付き置換 (ETag 一致時のみ書く) を再現する。

        承認の pending → 処理済み遷移がこれに乗っている。`etag` を渡さない実装に
        戻すと、同時リクエストが両方成功する = 二重実行が復活する。
        """
        if item not in self.items:
            raise CosmosResourceNotFoundError(
                status_code=404, message=f"{item} not found"
            )
        if etag is not None and self.items[item].get("_etag") != etag:
            raise CosmosAccessConditionFailedError(
                status_code=412, message=f"etag mismatch for {item}"
            )
        stored = {**body, "_rid": "fake-rid", "_etag": self._next_etag(), "_ts": 0}
        self.items[item] = stored
        return dict(stored)

    async def delete_item(self, item: str, partition_key: str) -> None:
        if item not in self.items:
            raise CosmosResourceNotFoundError(
                status_code=404, message=f"{item} not found"
            )
        del self.items[item]

    def query_items(self, query: str, parameters=None, partition_key=None, **kwargs):
        """checkpoint storage が使う 2 種のクエリ (checkpoint_id / workflow_name 絞り込み) を再現。"""
        params = {p["name"]: p["value"] for p in (parameters or [])}

        async def _iter():
            docs = list(self.items.values())
            if "@checkpoint_id" in params:
                docs = [
                    d
                    for d in docs
                    if d.get("checkpoint_id") == params["@checkpoint_id"]
                ]
            if "@workflow_name" in params:
                docs = [
                    d
                    for d in docs
                    if d.get("workflow_name") == params["@workflow_name"]
                ]
            docs.sort(key=lambda d: d.get("timestamp") or "")
            for d in docs:
                yield dict(d)

        return _iter()


@pytest.fixture
def fake_cosmos_container() -> FakeCosmosContainer:
    return FakeCosmosContainer()


@pytest.fixture
def cosmos_mode(monkeypatch, fake_cosmos_container):
    """COSMOS_ENDPOINT がある構成 (= Cosmos 実装が選ばれる) を fake で再現する。

    実 CosmosClient は作らない: app.cosmos.get_container を fake 容器に差し替える。
    """
    from app import cosmos
    from app.config import get_settings

    monkeypatch.setenv("COSMOS_ENDPOINT", "https://fake-cosmos.example:443/")
    get_settings.cache_clear()
    monkeypatch.setattr(cosmos, "get_container", lambda name: fake_cosmos_container)
    yield fake_cosmos_container
    get_settings.cache_clear()


@pytest.fixture
def tools_enabled(monkeypatch):
    """registry の全ツールを LLM に見せる構成 (`LLM_EXPOSED_TOOLS="*"`)。

    **既定は空 = 1 本も見せない** (#82 design-gate の PO 裁定 2: 題材がスタブのままなので
    #321 の裁定まで実運用の会話に出さない)。ツールが絡む挙動を見るテストはこの
    fixture で明示的に開ける — 「既定でどうなるか」と「開けたらどうなるか」を
    テストの側で取り違えないようにするため。
    """
    from app.config import get_settings

    monkeypatch.setenv("LLM_EXPOSED_TOOLS", "*")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def make_client():
    """MAF chat client mock の factory。get_response が任意のテキストを返すように構成する。

    使い方:
        def test_x(make_client):
            client = make_client('{"summary": "..."}')
    """

    def _factory(response_text: str) -> MagicMock:
        mock_response = MagicMock()
        mock_response.text = response_text
        client = MagicMock()
        client.get_response = AsyncMock(return_value=mock_response)
        return client

    return _factory
