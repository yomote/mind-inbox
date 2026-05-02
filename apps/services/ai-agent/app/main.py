"""
FastAPI entrypoint for the AI Agent.

Endpoints:
  POST /chat     — 会話ターン
  POST /organize — セッション履歴を OrganizedResult に変換
  POST /plan     — OrganizedResult から ActionPlan を生成
  POST /approve  — 承認待ちツール呼び出しの実行 / キャンセル
  GET  /health   — ヘルスチェック
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from .config import get_settings
from .kernel import get_kernel
from .organizer import organize
from .planner import generate_plan
from .repositories import InMemoryApprovalRepository, InMemorySessionRepository
from .schemas import (
    ApproveRequest,
    ApproveResponse,
    ChatRequest,
    ChatResponse,
    HealthResponse,
    OrganizeRequest,
    OrganizeResponse,
    PlanRequest,
    PlanResponse,
)
from .workflow import resume_after_approval, run_workflow

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper()))
logger = logging.getLogger(__name__)

# PoC: モジュールレベルのシングルトン
# TODO(PoC): マルチレプリカ環境では Redis に差し替える
_session_repo = InMemorySessionRepository()
_approval_repo = InMemoryApprovalRepository()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s", settings.app_name)
    get_kernel()  # 起動時にカーネルをロードして初回リクエストのレイテンシを下げる
    yield
    logger.info("Shutting down %s", settings.app_name)


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    try:
        return await run_workflow(
            req.session_id,
            req.message,
            _session_repo,
            _approval_repo,
            get_kernel(),
        )
    except Exception as exc:
        logger.error("POST /chat error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/organize", response_model=OrganizeResponse)
async def organize_endpoint(req: OrganizeRequest) -> OrganizeResponse:
    try:
        return await organize(req.session_id, _session_repo, get_kernel())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("POST /organize error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/plan", response_model=PlanResponse)
async def plan_endpoint(req: PlanRequest) -> PlanResponse:
    try:
        return await generate_plan(req, get_kernel())
    except Exception as exc:
        logger.error("POST /plan error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/approve", response_model=ApproveResponse)
async def approve(req: ApproveRequest) -> ApproveResponse:
    try:
        reply = await resume_after_approval(
            req.approval_request_id,
            req.approved,
            _session_repo,
            _approval_repo,
            get_kernel(),
        )
        return ApproveResponse(reply=reply)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("POST /approve error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
