"""[L2] FastAPI endpoints の service-level test。

パターン: httpx.AsyncClient(transport=ASGITransport(app=app)) で
HTTP レイヤをバイパスし FastAPI を in-process に叩く。

モック方針:
- /chat /approve : workflow 全体を monkeypatch (workflow 内部分岐は L1 の領域)
- /organize /plan: get_kernel() を mock して organize()/generate_plan() 本体を動かす
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
from app.main import _approval_repo, _session_repo, app
from app.schemas import ChatResponse


@pytest.fixture(autouse=True)
def reset_repos():
    """各 test の前後で module-level singleton repo の state をクリア。"""
    _session_repo._store.clear()
    _approval_repo._store.clear()
    yield
    _session_repo._store.clear()
    _approval_repo._store.clear()


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
        self, client, monkeypatch, make_kernel
    ):
        # 無いと: organize() の戻り値を FastAPI が JSON で正しく返さない退行が静かに通る
        history = ChatHistory()
        history.add_user_message("仕事が辛い")
        await _session_repo.save("s1", history)

        kernel = make_kernel(
            json.dumps(
                {
                    "summary": "仕事のストレス",
                    "emotions": ["疲労"],
                    "priorities": ["休息"],
                }
            )
        )
        monkeypatch.setattr(app_main, "get_kernel", lambda: kernel)

        res = await client.post("/organize", json={"session_id": "s1"})
        assert res.status_code == 200
        assert res.json() == {
            "summary": "仕事のストレス",
            "emotions": ["疲労"],
            "priorities": ["休息"],
        }

    async def test_l2_organize_returns_404_when_session_not_found(
        self, client, monkeypatch, make_kernel
    ):
        # 無いと: ValueError → HTTPException(404) マッピングが切れて 500 を返す退行が静かに通る
        kernel = make_kernel("{}")
        monkeypatch.setattr(app_main, "get_kernel", lambda: kernel)

        res = await client.post("/organize", json={"session_id": "nonexistent"})
        assert res.status_code == 404
        assert "Session not found" in res.json()["detail"]


# ---- /plan ------------------------------------------------------------------


class TestPlan:
    async def test_l2_plan_returns_200_with_valid_input(
        self, client, monkeypatch, make_kernel
    ):
        # 無いと: generate_plan() の戻り値を FastAPI が JSON で正しく返さない退行が静かに通る
        kernel = make_kernel(
            json.dumps({"title": "プラン", "steps": ["step1", "step2"]})
        )
        monkeypatch.setattr(app_main, "get_kernel", lambda: kernel)

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
