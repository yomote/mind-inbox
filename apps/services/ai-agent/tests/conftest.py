"""共通 fixture (strategy.md §1.3「mock 一元化」原則の運用)。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories import InMemoryApprovalRepository, InMemorySessionRepository


@pytest.fixture
def session_repo() -> InMemorySessionRepository:
    return InMemorySessionRepository()


@pytest.fixture
def approval_repo() -> InMemoryApprovalRepository:
    return InMemoryApprovalRepository()


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
