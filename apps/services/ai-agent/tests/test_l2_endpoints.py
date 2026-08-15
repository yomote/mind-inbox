"""[L2] FastAPI endpoints の service-level test。

パターン: httpx.AsyncClient(transport=ASGITransport(app=app)) で
HTTP レイヤをバイパスし FastAPI を in-process に叩く。

モック方針:
- /chat /approve : workflow 全体を monkeypatch (workflow 内部分岐は L1 の領域)
- /extract /plan: get_chat_client() を mock して各関数本体を動かす
- /health        : 何もモックしない (FastAPI 自体の wiring 確認)

ここで test しないこと:
- workflow の内部分岐 (ツール選択 / 承認の中断・再開 / 履歴の積み方) —
  L1 (tests/test_workflow_*.py)
- LLM 出力品質 — prompt engineering の領域
- BFF 側の tRPC 挙動 — それは BFF L2
- 実 Azure 環境疎通 — それは L4 smoke

fixture 置き換え (M1-5 / #82): SK 依存除去に伴い、
- 履歴 fixture を SK ChatHistory から app.history.ChatHistory (MAF Message ベース /
  追記 API は同形) へ差し替えた
- /chat /approve 系の workflow fake から第 5 引数 (旧 SK kernel) を外した —
  endpoint はもう kernel を渡さない (chat client は workflow 内で遅延解決)
検証意図 (status code / passthrough payload) は不変。
"""

import json

import pytest
from httpx import ASGITransport, AsyncClient
from app.history import ChatHistory

from app import main as app_main
from app.main import app, get_approval_repo, get_session_repo
from app.schemas import ChatResponse
from app.workflow import ApprovalAlreadyProcessedError


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
        async def fake_run_workflow(session_id, message, sr, ar):
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


# ---- /chat/stream -----------------------------------------------------------


def _parse_sse_events(payload: str) -> list[dict]:
    """SSE テキストから data 行の JSON を順に取り出す (テスト用の簡易パース)。"""
    events = []
    for block in payload.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


class TestChatStream:
    async def test_l2_chat_stream_emits_delta_then_done_as_sse(
        self, client, monkeypatch
    ):
        # 無いと: SSE の枠組み (data 行 / event 順序 / content-type) が壊れても
        # BFF は素通しするだけなので、フロントの逐次表示が全滅する退行が静かに通る
        async def fake_stream(session_id, message, sr, ar):
            from app.schemas import ChatStreamDelta, ChatStreamDone

            yield ChatStreamDelta(text="こん")
            yield ChatStreamDelta(text="にちは")
            yield ChatStreamDone(response=ChatResponse(reply="こんにちは"))

        monkeypatch.setattr(app_main, "run_workflow_stream", fake_stream)

        async with client.stream(
            "POST", "/chat/stream", json={"session_id": "s1", "message": "テスト"}
        ) as res:
            assert res.status_code == 200
            assert res.headers["content-type"].startswith("text/event-stream")
            body = ""
            async for chunk in res.aiter_text():
                body += chunk

        events = _parse_sse_events(body)
        assert [e["type"] for e in events] == ["delta", "delta", "done"]
        assert (
            "".join(e["text"] for e in events if e["type"] == "delta") == "こんにちは"
        )
        assert events[-1]["response"]["reply"] == "こんにちは"
        assert events[-1]["response"]["requires_approval"] is False

    async def test_l2_chat_stream_emits_error_event_on_exception(
        self, client, monkeypatch
    ):
        # 無いと: ストリーム途中の例外が接続切断だけで終わり、フロントが
        # フォールバックの判断材料 (error イベント) を得られない退行が静かに通る
        async def broken_stream(session_id, message, sr, ar):
            from app.schemas import ChatStreamDelta

            yield ChatStreamDelta(text="途中")
            raise RuntimeError("LLM connection lost")

        monkeypatch.setattr(app_main, "run_workflow_stream", broken_stream)

        async with client.stream(
            "POST", "/chat/stream", json={"session_id": "s1", "message": "テスト"}
        ) as res:
            body = ""
            async for chunk in res.aiter_text():
                body += chunk

        events = _parse_sse_events(body)
        assert events[-1]["type"] == "error"
        # 追跡できる形は保つ (ref があればサーバのログ行に辿り着ける)
        assert "ref:" in events[-1]["message"]

    async def test_単体_ストリームのエラー本文に上流の例外文を載せない(
        self, client, monkeypatch
    ):
        # 無いと: Azure OpenAI SDK の例外文 (エンドポイント URL / デプロイ名 /
        # api-version / コンテンツフィルタが引用したプロンプト断片) が
        # BFF の素通しでブラウザの devtools まで届く (Issue #313 / rubric S3)
        upstream = (
            "AuthenticationError: https://aoai-dev-mindbox.openai.azure.com/"
            " deployment=gpt-4o api-version=preview 「転職したい」"
        )

        async def broken_stream(session_id, message, sr, ar):
            raise RuntimeError(upstream)
            yield  # pragma: no cover — 非同期ジェネレータにするためだけの行

        monkeypatch.setattr(app_main, "run_workflow_stream", broken_stream)

        async with client.stream(
            "POST", "/chat/stream", json={"session_id": "s1", "message": "転職したい"}
        ) as res:
            body = ""
            async for chunk in res.aiter_text():
                body += chunk

        message = _parse_sse_events(body)[-1]["message"]
        for leaked in ("openai.azure.com", "gpt-4o", "api-version", "転職したい"):
            assert leaked not in message, leaked

    async def test_単体_chat_の_500_応答に上流の例外文を載せない(
        self, client, monkeypatch
    ):
        # 無いと: 同じ流出が非ストリーミング経路 (detail=str(exc)) から起き続ける
        async def boom(*args, **kwargs):
            raise RuntimeError(
                "https://aoai-dev-mindbox.openai.azure.com/ deployment=gpt-4o"
            )

        monkeypatch.setattr(app_main, "run_workflow", boom)

        res = await client.post("/chat", json={"session_id": "s1", "message": "テスト"})
        assert res.status_code == 500
        detail = res.json()["detail"]
        assert "openai.azure.com" not in detail
        assert "gpt-4o" not in detail
        assert "ref:" in detail


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

    async def test_l2_extract_serializes_thinking_map_in_camelcase(
        self, client, monkeypatch, make_client, session_repo
    ):
        # 無いと: 整理マップ (#433) の alias 直列化 (thinkingMap / parentId / problemId) が
        #         snake_case のまま出ても Python 側のテストは全部緑のまま通り、
        #         BFF の zod parse で初めて落ちる (= 右ペインが実環境でだけ空になる)。
        history = ChatHistory()
        history.add_user_message("転職しようか迷ってて")
        await session_repo.save("s1", history)

        client_mock = make_client(
            json.dumps(
                {
                    "mentions": [],
                    "thinkingMap": {
                        "nodes": [
                            {
                                "id": "n1",
                                "kind": "topic",
                                "label": "転職の不安",
                                "status": "confirmed",
                                "parentId": None,
                            },
                            {
                                "id": "n2",
                                "kind": "hypothesis",
                                "label": "失敗が怖い",
                                "status": "tentative",
                                "parentId": "n1",
                            },
                        ]
                    },
                }
            )
        )
        monkeypatch.setattr(app_main, "get_chat_client", lambda: client_mock)

        res = await client.post(
            "/extract", json={"sessionId": "s1", "existingProblems": []}
        )

        assert res.status_code == 200
        nodes = res.json()["thinkingMap"]["nodes"]
        assert nodes[0] == {
            "id": "n1",
            "kind": "topic",
            "label": "転職の不安",
            "status": "confirmed",
            "parentId": None,
            "problemId": None,
        }
        assert nodes[1]["parentId"] == "n1"

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
        async def fake_resume(approval_id, _approved, sr, ar):
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
        # 404 の detail は BFF が「承認レコードがもう無い」を判別する契約
        # (APPROVAL_GONE_DETAIL / contract-check.mjs)。形が変わると判別が静かに外れる
        assert res.json()["detail"].startswith("Approval not found")

    @pytest.mark.parametrize(
        "status,processed_at",
        [
            ("approved", "2026-08-15T02:00:00+00:00"),
            ("rejected", "2026-08-15T02:00:00+00:00"),
            # 時刻を持たない古いレコード (この項目より前に書かれた Cosmos 文書)
            ("approved", None),
        ],
    )
    async def test_単体_二回目の承認は409で現在状態を返す(
        self, client, monkeypatch, status, processed_at
    ):
        """#82 / PO 裁定 2026-08-15 B 案。

        無いと何が静かに通るか: 二重送信が 404 (= レコードが無い) に戻り、
        BFF/フロントは「レコードが消えた」のか「もう解決済み」なのかを区別できない。
        **却下済み (= 確実に未実行) すら案内できなくなる**。status が欠けると
        フロントは結果を言い分けられず、processed_at が欠けると運用が
        「いつ受け付けた二重送信か」を追えない。
        """

        async def boom(*args, **kwargs):
            raise ApprovalAlreadyProcessedError(status, processed_at)

        monkeypatch.setattr(app_main, "resume_after_approval", boom)

        res = await client.post(
            "/approve",
            json={"approval_request_id": "appr-1", "approved": True},
        )

        assert res.status_code == 409
        assert res.json() == {
            "detail": f"Approval already processed: '{status}'",
            "status": status,
            "processed_at": processed_at,
        }

    async def test_単体_409の宣言はopenapiにも出ている(self, client):
        """生成 OpenAPI (docs/api/ai-agent.yaml) に 409 が載ること。

        無いと何が静かに通るか: 実装だけ 409 を返し、生成 docs は 200/404 しか
        宣言しない状態になる (契約書を読んだ実装者が 409 を扱わない)。
        """
        schema = app_main.app.openapi()
        responses = schema["paths"]["/approve"]["post"]["responses"]
        assert "409" in responses
        ref = responses["409"]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("ApprovalConflictResponse")
