"""
FastAPI entrypoint for the AI Agent.

Endpoints:
  POST /chat        — 会話ターン
  POST /chat/stream — 会話ターン (SSE ストリーミング / #120, ADR 0024)
  POST /extract  — セッション全文を Mention[] に抽出 + 既存 Problem へグルーピング (ADR 0007)
  POST /organize — セッション履歴を OrganizedResult に変換 (deprecated: Phase C で /extract に置換)
  POST /plan     — OrganizedResult から ActionPlan を生成
  POST /approve  — 承認待ちツール呼び出しの実行 / キャンセル
  GET  /health   — ヘルスチェック
"""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from .agents import get_chat_client
from .config import get_settings
from .extractor import ExtractionParseError, ExtractionUnavailable, extract
from .kernel import get_kernel
from .organizer import organize
from .planner import generate_plan
from .repositories import (
    ApprovalRepository,
    InMemoryApprovalRepository,
    InMemorySessionRepository,
    SessionRepository,
)
from .schemas import (
    ApproveRequest,
    ApproveResponse,
    ChatRequest,
    ChatResponse,
    ChatStreamError,
    ExtractionResult,
    ExtractRequest,
    HealthResponse,
    OrganizeRequest,
    OrganizeResponse,
    PlanRequest,
    PlanResponse,
)
from .workflow import resume_after_approval, run_workflow, run_workflow_stream

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper()))
logger = logging.getLogger(__name__)

# PoC: モジュールレベルのシングルトン (本番動作用)
# TODO(PoC): マルチレプリカ環境では Redis に差し替える
_session_repo: SessionRepository = InMemorySessionRepository()
_approval_repo: ApprovalRepository = InMemoryApprovalRepository()


def get_session_repo() -> SessionRepository:
    """FastAPI Depends provider。test では app.dependency_overrides で差し替える。"""
    return _session_repo


def get_approval_repo() -> ApprovalRepository:
    """FastAPI Depends provider。test では app.dependency_overrides で差し替える。"""
    return _approval_repo


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s", settings.app_name)
    get_kernel()  # 起動時にカーネルをロードして初回リクエストのレイテンシを下げる (M1-3 まで /chat 系が使用)
    try:
        get_chat_client()  # MAF chat client も起動時にロード
    except Exception as exc:
        # 資格情報なしでも起動は継続する (kernel と同じ縮退方針)。LLM 呼び出し時に失敗する
        logger.error("Chat client setup failed — LLM calls will fail: %s", exc)
    yield
    logger.info("Shutting down %s", settings.app_name)


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@app.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    session_repo: SessionRepository = Depends(get_session_repo),
    approval_repo: ApprovalRepository = Depends(get_approval_repo),
) -> ChatResponse:
    try:
        return await run_workflow(
            req.session_id,
            req.message,
            session_repo,
            approval_repo,
            get_kernel(),
        )
    except Exception as exc:
        logger.error("POST /chat error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/chat/stream",
    responses={
        200: {
            "description": (
                "SSE (text/event-stream)。data 行の JSON は "
                "ChatStreamDelta / ChatStreamDone / ChatStreamError のいずれか (#120, ADR 0024)。"
            ),
            "content": {"text/event-stream": {}},
        }
    },
)
async def chat_stream(
    req: ChatRequest,
    session_repo: SessionRepository = Depends(get_session_repo),
    approval_repo: ApprovalRepository = Depends(get_approval_repo),
) -> StreamingResponse:
    """/chat のストリーミング版。契約の真実は schemas.ChatStream* (done は ChatResponse を運ぶ)。"""

    async def event_stream():
        try:
            async for event in run_workflow_stream(
                req.session_id,
                req.message,
                session_repo,
                approval_repo,
                get_kernel(),
            ):
                yield f"data: {event.model_dump_json()}\n\n"
        except Exception as exc:
            # SSE は 200 を返した後なので HTTP エラーにできない。error イベントで伝え、
            # クライアント (BFF/フロント) が非ストリーミングへフォールバックする。
            logger.error("POST /chat/stream error: %s", exc, exc_info=True)
            yield f"data: {ChatStreamError(message=str(exc)).model_dump_json()}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/extract", response_model=ExtractionResult)
async def extract_endpoint(
    req: ExtractRequest,
    session_repo: SessionRepository = Depends(get_session_repo),
) -> ExtractionResult:
    try:
        return await extract(
            req.session_id,
            req.existing_problems,
            session_repo,
            get_chat_client(),
            req.messages,
        )
    except ExtractionUnavailable as exc:
        # 会話が手に入らない。呼び出し側が会話を送れば解消する種類の失敗 (#183)。
        raise HTTPException(status_code=404, detail=str(exc))
    except ExtractionParseError as exc:
        # 「0 件だった」と区別できるようにする。502 = 上流 (LLM) の応答が壊れている。
        logger.error("POST /extract parse error: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        logger.error("POST /extract error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/organize", response_model=OrganizeResponse)
async def organize_endpoint(
    req: OrganizeRequest,
    session_repo: SessionRepository = Depends(get_session_repo),
) -> OrganizeResponse:
    try:
        return await organize(req.session_id, session_repo, get_chat_client())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("POST /organize error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/plan", response_model=PlanResponse)
async def plan_endpoint(req: PlanRequest) -> PlanResponse:
    try:
        return await generate_plan(req, get_chat_client())
    except Exception as exc:
        logger.error("POST /plan error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/approve", response_model=ApproveResponse)
async def approve(
    req: ApproveRequest,
    session_repo: SessionRepository = Depends(get_session_repo),
    approval_repo: ApprovalRepository = Depends(get_approval_repo),
) -> ApproveResponse:
    try:
        reply = await resume_after_approval(
            req.approval_request_id,
            req.approved,
            session_repo,
            approval_repo,
            get_kernel(),
        )
        return ApproveResponse(reply=reply)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("POST /approve error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
