"""[L1] planner の入出力契約と例外パスを pin する。

ここで test しないこと:
- LLM のプラン品質 (steps の妥当性、実行可能性) — prompt engineering の領域
- prompt 文面の正しさ — 仕様変更時に false positive を量産する
- HTTP / FastAPI 経由の通し挙動 — それは L2 (issue #3)
"""

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
    async def test_l1_maps_kernel_response_to_plan_schema(self, basic_request):
        # Kernel が返した JSON の各フィールドが PlanResponse に正しく mapping されることを pin する。
        # 無いと: schema フィールドのリネーム/型変更が静かに通り、BFF 側で deserialize が壊れる
        payload = {
            "title": "48時間アクションプラン",
            "steps": ["今日は早く帰る", "信頼できる人に話す", "明日の予定を整理する"],
        }
        kernel = _make_kernel(json.dumps(payload))

        result = await generate_plan(basic_request, kernel)

        assert result == PlanResponse(
            title="48時間アクションプラン",
            steps=["今日は早く帰る", "信頼できる人に話す", "明日の予定を整理する"],
        )

    async def test_json_inside_markdown_fence(self, basic_request):
        payload = {"title": "フェンスプラン", "steps": ["ステップ1"]}
        fenced = f"```json\n{json.dumps(payload)}\n```"
        kernel = _make_kernel(fenced)

        result = await generate_plan(basic_request, kernel)

        assert result.title == "フェンスプラン"

    async def test_malformed_json_returns_fallback(self, basic_request):
        # LLM が JSON でない文字列を返した時、例外を投げず fallback PlanResponse を返す契約を pin する。
        # 無いと: parse failure 時に 500 で落ちる退行が静かに通り、user に対して generic error が返る
        kernel = _make_kernel("not valid json")

        result = await generate_plan(basic_request, kernel)

        # fallback の "形" を全体一致で pin。文言を変える時は test も更新する運用。
        assert result == PlanResponse(
            title="アクションプラン",
            steps=["具体的なステップを考えてみましょう"],
        )

    async def test_partial_json_uses_defaults(self, basic_request):
        kernel = _make_kernel(json.dumps({"title": "タイトルのみ"}))

        result = await generate_plan(basic_request, kernel)

        assert result.title == "タイトルのみ"
        assert result.steps == []
