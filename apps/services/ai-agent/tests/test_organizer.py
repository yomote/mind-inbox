import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from semantic_kernel.contents import ChatHistory

from app.organizer import organize
from app.repositories import InMemorySessionRepository
from app.schemas import OrganizeResponse


def _make_kernel(response_text: str) -> MagicMock:
    """Return a Kernel mock whose chat service returns response_text."""
    mock_result = MagicMock()
    mock_result.__str__ = lambda self: response_text

    mock_svc = MagicMock()
    mock_svc.get_chat_message_content = AsyncMock(return_value=mock_result)

    kernel = MagicMock()
    kernel.get_service = MagicMock(return_value=mock_svc)
    return kernel


@pytest.fixture
def session_repo() -> InMemorySessionRepository:
    return InMemorySessionRepository()


class TestOrganize:
    async def test_valid_json_response(self, session_repo):
        history = ChatHistory()
        history.add_user_message("仕事が辛い")
        history.add_assistant_message("どんなところが辛いですか？")
        await session_repo.save("s1", history)

        payload = {
            "summary": "仕事のストレスを感じている",
            "emotions": ["疲労", "不安"],
            "priorities": ["休息", "相談"],
        }
        kernel = _make_kernel(json.dumps(payload))

        result = await organize("s1", session_repo, kernel)

        assert isinstance(result, OrganizeResponse)
        assert result.summary == "仕事のストレスを感じている"
        assert result.emotions == ["疲労", "不安"]
        assert result.priorities == ["休息", "相談"]

    async def test_json_inside_markdown_fence(self, session_repo):
        history = ChatHistory()
        history.add_user_message("テスト")
        await session_repo.save("s1", history)

        payload = {"summary": "テスト要約", "emotions": ["平静"], "priorities": ["確認"]}
        fenced = f"```json\n{json.dumps(payload)}\n```"
        kernel = _make_kernel(fenced)

        result = await organize("s1", session_repo, kernel)

        assert result.summary == "テスト要約"

    async def test_malformed_json_returns_fallback(self, session_repo):
        history = ChatHistory()
        history.add_user_message("テスト")
        await session_repo.save("s1", history)

        kernel = _make_kernel("not valid json at all")

        result = await organize("s1", session_repo, kernel)

        assert "エラー" in result.summary
        assert result.emotions == []
        assert result.priorities == []

    async def test_missing_session_raises_value_error(self, session_repo):
        kernel = _make_kernel("{}")

        with pytest.raises(ValueError, match="Session not found"):
            await organize("nonexistent", session_repo, kernel)

    async def test_partial_json_uses_defaults(self, session_repo):
        history = ChatHistory()
        history.add_user_message("テスト")
        await session_repo.save("s1", history)

        kernel = _make_kernel(json.dumps({"summary": "部分的な要約"}))

        result = await organize("s1", session_repo, kernel)

        assert result.summary == "部分的な要約"
        assert result.emotions == []
        assert result.priorities == []
