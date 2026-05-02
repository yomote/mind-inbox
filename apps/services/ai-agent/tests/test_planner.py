import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.planner import generate_plan
from app.schemas import PlanRequest, PlanResponse


def _make_kernel(response_text: str) -> MagicMock:
    mock_result = MagicMock()
    mock_result.__str__ = lambda self: response_text

    mock_svc = MagicMock()
    mock_svc.get_chat_message_content = AsyncMock(return_value=mock_result)

    kernel = MagicMock()
    kernel.get_service = MagicMock(return_value=mock_svc)
    return kernel


@pytest.fixture
def basic_request() -> PlanRequest:
    return PlanRequest(
        summary="仕事のストレスを感じている",
        emotions=["疲労", "不安"],
        priorities=["休息", "相談"],
    )


class TestGeneratePlan:
    async def test_valid_json_response(self, basic_request):
        payload = {
            "title": "48時間アクションプラン",
            "steps": ["今日は早く帰る", "信頼できる人に話す", "明日の予定を整理する"],
        }
        kernel = _make_kernel(json.dumps(payload))

        result = await generate_plan(basic_request, kernel)

        assert isinstance(result, PlanResponse)
        assert result.title == "48時間アクションプラン"
        assert len(result.steps) == 3
        assert result.steps[0] == "今日は早く帰る"

    async def test_json_inside_markdown_fence(self, basic_request):
        payload = {"title": "フェンスプラン", "steps": ["ステップ1"]}
        fenced = f"```json\n{json.dumps(payload)}\n```"
        kernel = _make_kernel(fenced)

        result = await generate_plan(basic_request, kernel)

        assert result.title == "フェンスプラン"

    async def test_malformed_json_returns_fallback(self, basic_request):
        kernel = _make_kernel("not valid json")

        result = await generate_plan(basic_request, kernel)

        assert result.title == "アクションプラン"
        assert len(result.steps) == 1

    async def test_partial_json_uses_defaults(self, basic_request):
        kernel = _make_kernel(json.dumps({"title": "タイトルのみ"}))

        result = await generate_plan(basic_request, kernel)

        assert result.title == "タイトルのみ"
        assert result.steps == []

    async def test_empty_emotions_and_priorities(self):
        req = PlanRequest(summary="要約のみ", emotions=[], priorities=[])
        payload = {"title": "シンプルプラン", "steps": ["ステップ"]}
        kernel = _make_kernel(json.dumps(payload))

        result = await generate_plan(req, kernel)

        assert result.title == "シンプルプラン"
