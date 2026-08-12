"""Cosmos DB への共有アクセス (ADR 0030 / #188)。

COSMOS_ENDPOINT が未設定なら、このモジュールの関数は一切呼ばれない
(repositories / workflow が in-memory 実装へフォールバックする)。

- 認証は Managed Identity (DefaultAzureCredential) のみ。アカウントキーは
  disableLocalAuth: true で無効化済み (ADR 0030 D3) なので、キーを扱う経路は書かない。
- DB / コンテナは bicep (bootstrap-core.bicep) が宣言する。ここでは
  create_*_if_not_exists を呼ばない — data plane ロール (Cosmos DB Built-in Data
  Contributor) には control plane の作成権限が無く、作成を試みた時点で落ちるため。
  get_database_client / get_container_client はクライアント側の参照だけで I/O しない。
"""

from functools import lru_cache

from azure.cosmos.aio import ContainerProxy, CosmosClient
from azure.identity.aio import DefaultAzureCredential

from .config import get_settings


@lru_cache
def _client() -> CosmosClient:
    settings = get_settings()
    return CosmosClient(
        url=settings.cosmos_endpoint, credential=DefaultAzureCredential()
    )


@lru_cache
def get_container(container_name: str) -> ContainerProxy:
    """指定コンテナの proxy (プロセス内で共有)。"""
    settings = get_settings()
    return (
        _client()
        .get_database_client(settings.cosmos_database)
        .get_container_client(container_name)
    )
