"""[L1] organizer の入出力契約と例外パスを pin する。

ここで test しないこと:
- LLM の組織化品質 (要約の妥当性、感情ラベルの適切さ) — prompt engineering の領域
- prompt 文面の正しさ — 仕様変更時に false positive を量産する
- session repository 自体の挙動 — それは test_repositories.py
- HTTP / FastAPI 経由の通し挙動 — それは L2 (issue #3)
"""

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
    async def test_l1_maps_kernel_response_to_response_schema(self, session_repo):
        # Kernel が返した JSON の各フィールドが OrganizeResponse に正しく mapping されることを pin する。
        # 無いと: schema フィールドのリネーム/型変更が静かに通り、BFF 側で deserialize が壊れる
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

        assert result == OrganizeResponse(
            summary="仕事のストレスを感じている",
            emotions=["疲労", "不安"],
            priorities=["休息", "相談"],
        )

    async def test_json_inside_markdown_fence(self, session_repo):
        history = ChatHistory()
        history.add_user_message("テスト")
        await session_repo.save("s1", history)

        payload = {
            "summary": "テスト要約",
            "emotions": ["平静"],
            "priorities": ["確認"],
        }
        fenced = f"```json\n{json.dumps(payload)}\n```"
        kernel = _make_kernel(fenced)

        result = await organize("s1", session_repo, kernel)

        assert result.summary == "テスト要約"

    async def test_malformed_json_returns_fallback(self, session_repo):
        # LLM が JSON でない文字列を返した時、例外を投げず fallback OrganizeResponse を返す契約を pin する。
        # 無いと: parse failure 時に 500 で落ちる退行が静かに通り、user に対して generic error が返る
        history = ChatHistory()
        history.add_user_message("テスト")
        await session_repo.save("s1", history)

        kernel = _make_kernel("not valid json at all")

        result = await organize("s1", session_repo, kernel)

        # fallback の "形" を全体一致で pin。文言を変える時は test も更新する運用。
        assert result == OrganizeResponse(
            summary="整理中にエラーが発生しました。",
            emotions=[],
            priorities=[],
        )

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
