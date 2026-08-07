"""[L2] FastAPI endpoints の service-level test。

パターン: httpx.AsyncClient(transport=ASGITransport(app=app)) で
HTTP レイヤをバイパスし FastAPI を in-process に叩く。

モック方針:
- /chat /approve : workflow 全体を monkeypatch (workflow 内部分岐は L1 の領域)
- /organize /extract /plan: get_chat_client() を mock して各関数本体を動かす
- /health        : 何もモックしない (FastAPI 自体の wiring 確認)

ここで test しないこと:
- workflow の状態遷移 (CLASSIFY / RETRIEVE / EXECUTE_TOOL の分岐) — L1 (issue #2)
- LLM 出力品質 — prompt engineering の領域
- BFF 側の tRPC 挙動 — それは BFF L2
- 実 Azure 環境疎通 — それは L4 smoke
"""

import json

import pytest
from httpx import ASGITransport, AsyncClient
from semantic_kernel.contents import ChatHistory

from app import main as app_main
from app.main import app, get_approval_repo, get_session_repo
from app.schemas import ChatResponse


@pytest.fixture(autouse=True)
def override_repos(session_repo, approval_repo):
    """FastAPI Depends を test 毎の fresh repo で上書きする。"""
    app.dependency_overrides[get_session_repo] = lambda: session_repo
    app.dependency_overrides[get_approval_repo] = lambda: approval_repo
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ---- /health ----------------------------------------------------------------


class TestHealth:
    async def test_l2_health_returns_ok(self, client):
        # 無いと: FastAPI app の起動 / lifespan / route 登録が壊れた退行が静かに通る
        res = await client.get("/health")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}


# ---- /chat ------------------------------------------------------------------


class TestChat:
    async def test_l2_chat_pass_through_workflow_response(self, client, monkeypatch):
        # 無いと: workflow が返した requires_approval / approval_request_id / citations を
        # endpoint が pass-through せず欠落させる退行が静かに通る
        async def fake_run_workflow(session_id, message, sr, ar, k):
            return ChatResponse(
                reply="整理しましょう",
                requires_approval=True,
                approval_request_id="appr-1",
                citations=["doc-a"],
            )

        monkeypatch.setattr(app_main, "run_workflow", fake_run_workflow)

        res = await client.post("/chat", json={"session_id": "s1", "message": "テスト"})
        assert res.status_code == 200
        assert res.json() == {
            "reply": "整理しましょう",
            "requires_approval": True,
            "approval_request_id": "appr-1",
            "citations": ["doc-a"],
        }

    async def test_l2_chat_returns_500_on_workflow_exception(self, client, monkeypatch):
        # 無いと: workflow 例外を握りつぶして 200 を返す退行が静かに通る (caller が成功と誤認)
        async def boom(*args, **kwargs):
            raise RuntimeError("workflow boom")

        monkeypatch.setattr(app_main, "run_workflow", boom)

        res = await client.post("/chat", json={"session_id": "s1", "message": "テスト"})
        assert res.status_code == 500


# ---- /organize --------------------------------------------------------------


class TestOrganize:
    async def test_l2_organize_returns_200_with_existing_session(
        self, client, monkeypatch, make_client, session_repo
    ):
        # 無いと: organize() の戻り値を FastAPI が JSON で正しく返さない退行が静かに通る
        history = ChatHistory()
        history.add_user_message("仕事が辛い")
        await session_repo.save("s1", history)

        client_mock = make_client(
            json.dumps(
                {
                    "summary": "仕事のストレス",
                    "emotions": ["疲労"],
                    "priorities": ["休息"],
                }
            )
        )
        monkeypatch.setattr(app_main, "get_chat_client", lambda: client_mock)

        res = await client.post("/organize", json={"session_id": "s1"})
        assert res.status_code == 200
        assert res.json() == {
            "summary": "仕事のストレス",
            "emotions": ["疲労"],
            "priorities": ["休息"],
        }

    async def test_l2_organize_returns_404_when_session_not_found(
        self, client, monkeypatch, make_client
    ):
        # 無いと: ValueError → HTTPException(404) マッピングが切れて 500 を返す退行が静かに通る
        client_mock = make_client("{}")
        monkeypatch.setattr(app_main, "get_chat_client", lambda: client_mock)

        res = await client.post("/organize", json={"session_id": "nonexistent"})
        assert res.status_code == 404
        assert "Session not found" in res.json()["detail"]


# ---- /extract ---------------------------------------------------------------


class TestExtract:
    async def test_l2_extract_returns_200_and_camelcase_body(
        self, client, monkeypatch, make_client, session_repo
    ):
        # 無いと: extract() の戻り値を FastAPI が domain.ts の型 (camelCase) で返さない退行が
        #         静かに通り、BFF (Phase B) の deserialize が壊れる
        history = ChatHistory()
        history.add_user_message("転職しようか迷ってて")
        await session_repo.save("s1", history)

        client_mock = make_client(
            json.dumps(
                {
                    "mentions": [
                        {
                            "statement": "転職すべきか迷っている",
                            "excerpt": "転職しようか迷ってて",
                            "affect": {
                                "label": "不安",
                                "valence": "negative",
                                "intensity": 0.6,
                            },
                            "theme": "仕事・キャリア",
                            "tags": ["転職"],
                            "grouping": {
                                "existingProblemId": None,
                                "newProblemTitle": "転職の迷い",
                                "confidence": 0.8,
                            },
                        }
                    ]
                }
            )
        )
        monkeypatch.setattr(app_main, "get_chat_client", lambda: client_mock)

        res = await client.post(
            "/extract", json={"sessionId": "s1", "existingProblems": []}
        )
        assert res.status_code == 200
        body = res.json()
        # by_alias 直列化 (domain.ts 一致) を pin
        assert body["sessionId"] == "s1"
        assert body["newProblemCount"] == 1
        assert body["updatedProblemCount"] == 0
        item = body["items"][0]
        assert item["grouping"]["kind"] == "new"
        assert item["grouping"]["problemTitle"] == "転職の迷い"
        assert item["mention"]["proposedTheme"] == "仕事・キャリア"
        assert item["mention"]["problemId"] == item["grouping"]["problemId"]

    async def test_l2_extract_returns_404_when_session_not_found(
        self, client, monkeypatch, make_client
    ):
        # 無いと: ValueError → HTTPException(404) マッピングが切れて 500 を返す退行が静かに通る
        client_mock = make_client(json.dumps({"mentions": []}))
        monkeypatch.setattr(app_main, "get_chat_client", lambda: client_mock)

        res = await client.post("/extract", json={"sessionId": "nonexistent"})
        assert res.status_code == 404
        assert "Session not found" in res.json()["detail"]


# ---- /plan ------------------------------------------------------------------


class TestPlan:
    async def test_l2_plan_returns_200_with_valid_input(
        self, client, monkeypatch, make_client
    ):
        # 無いと: generate_plan() の戻り値を FastAPI が JSON で正しく返さない退行が静かに通る
        client_mock = make_client(
            json.dumps({"title": "プラン", "steps": ["step1", "step2"]})
        )
        monkeypatch.setattr(app_main, "get_chat_client", lambda: client_mock)

        res = await client.post(
            "/plan",
            json={
                "summary": "仕事のストレス",
                "emotions": ["疲労"],
                "priorities": ["休息"],
            },
        )
        assert res.status_code == 200
        assert res.json() == {"title": "プラン", "steps": ["step1", "step2"]}

    async def test_l2_plan_returns_422_on_missing_required_field(self, client):
        # 無いと: PlanRequest pydantic validation が外れて malformed input が pipeline を流れる退行が静かに通る
        res = await client.post("/plan", json={})
        assert res.status_code == 422


# ---- /approve ---------------------------------------------------------------


class TestApprove:
    @pytest.mark.parametrize(
        "approved,expected",
        [
            (True, "実行しました"),
            (False, "キャンセルしました"),
        ],
    )
    async def test_l2_approve_passes_through_resume_response(
        self, client, monkeypatch, approved, expected
    ):
        # 無いと: approved boolean の意味反転 / endpoint の reply field 欠落が静かに通る
        async def fake_resume(approval_id, _approved, sr, ar, k):
            return expected

        monkeypatch.setattr(app_main, "resume_after_approval", fake_resume)

        res = await client.post(
            "/approve",
            json={"approval_request_id": "appr-1", "approved": approved},
        )
        assert res.status_code == 200
        assert res.json() == {"reply": expected}

    async def test_l2_approve_returns_404_when_approval_not_found(
        self, client, monkeypatch
    ):
        # 無いと: ValueError → HTTPException(404) マッピングが切れて 500 を返す退行が静かに通る
        async def boom(*args, **kwargs):
            raise ValueError("Approval not found: appr-x")

        monkeypatch.setattr(app_main, "resume_after_approval", boom)

        res = await client.post(
            "/approve",
            json={"approval_request_id": "appr-x", "approved": True},
        )
        assert res.status_code == 404
