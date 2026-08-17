"""[L1] planner の入出力契約と例外パスを pin する。

ここで test しないこと:
- LLM のプラン品質 (steps の妥当性、実行可能性) — prompt engineering の領域
- prompt 文面の正しさ — 仕様変更時に false positive を量産する
- HTTP / FastAPI 経由の通し挙動 — それは L2 (issue #3)
"""

import json
import logging

import pytest

from app.planner import PlanParseError, generate_plan
from app.schemas import PlanRequest, PlanResponse


@pytest.fixture
def basic_request() -> PlanRequest:
    return PlanRequest(
        summary="仕事のストレスを感じている",
        emotions=["疲労", "不安"],
        priorities=["休息", "相談"],
    )


class TestGeneratePlan:
    async def test_l1_maps_client_response_to_plan_schema(
        self, basic_request, make_client
    ):
        # Kernel が返した JSON の各フィールドが PlanResponse に正しく mapping されることを pin する。
        # 無いと: schema フィールドのリネーム/型変更が静かに通り、BFF 側で deserialize が壊れる
        payload = {
            "title": "48時間アクションプラン",
            "steps": ["今日は早く帰る", "信頼できる人に話す", "明日の予定を整理する"],
        }
        client_mock = make_client(json.dumps(payload))

        result = await generate_plan(basic_request, client_mock)

        assert result == PlanResponse(
            title="48時間アクションプラン",
            steps=["今日は早く帰る", "信頼できる人に話す", "明日の予定を整理する"],
        )

    async def test_json_inside_markdown_fence(self, basic_request, make_client):
        payload = {"title": "フェンスプラン", "steps": ["ステップ1"]}
        fenced = f"```json\n{json.dumps(payload)}\n```"
        client_mock = make_client(fenced)

        result = await generate_plan(basic_request, client_mock)

        assert result.title == "フェンスプラン"

    async def test_単体_パース失敗は固定文言に落とさず失敗として上げる(
        self, basic_request, make_client
    ):
        # LLM の応答を解釈できなかったら**失敗として上げる** (#485)。
        # 無いと: 握り潰し (固定文言の PlanResponse を返す) が復活しても緑のまま通り、
        #         偽のプラン「具体的なステップを考えてみましょう」がユーザーに
        #         正常応答として届く (#183 で /extract に対して塞いだのと同種の穴)。
        client_mock = make_client("not valid json")

        with pytest.raises(PlanParseError):
            await generate_plan(basic_request, client_mock)

    async def test_単体_パース失敗のログにLLM応答本文を出さない(
        self, basic_request, make_client, caplog
    ):
        # 無いと: Problem の要約から生成された行動プラン (機微テキスト) が
        # ERROR ログ経由で Log Analytics に流れ続ける (Issue #313 / rubric S3)。
        # デバッグ可能性は ref + 指紋で残すので、ここは「本文が出ない」だけを見る。
        secret = "妻との関係を整理するため、まず弁護士に相談する"
        client_mock = make_client(f"not json: {secret}")

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(PlanParseError) as excinfo:
                await generate_plan(basic_request, client_mock)

        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert secret not in logged
        assert secret not in str(excinfo.value)
        # 追える形は残っている: 同じ ref がログとクライアント向けメッセージの両方にある
        ref = str(excinfo.value).split("ref: ")[1].rstrip(")")
        assert ref in logged

    async def test_partial_json_uses_defaults(self, basic_request, make_client):
        client_mock = make_client(json.dumps({"title": "タイトルのみ"}))

        result = await generate_plan(basic_request, client_mock)

        assert result.title == "タイトルのみ"
        assert result.steps == []
