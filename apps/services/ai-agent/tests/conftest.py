"""共通 fixture (strategy.md §1.3「mock 一元化」原則の運用)。"""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def make_kernel():
    """Kernel mock の factory。chat service が任意の文字列を返すように構成する。

    使い方:
        def test_x(make_kernel):
            kernel = make_kernel('{"summary": "..."}')
    """

    def _factory(response_text: str) -> MagicMock:
        mock_result = MagicMock()
        mock_result.__str__ = lambda self: response_text
        mock_svc = MagicMock()
        mock_svc.get_chat_message_content = AsyncMock(return_value=mock_result)
        kernel = MagicMock()
        kernel.get_service = MagicMock(return_value=mock_svc)
        return kernel

    return _factory
