"""
FastAPI entrypoint for the AI Agent.

Endpoints:
  POST /chat        — 会話ターン
  POST /chat/stream — 会話ターン (SSE ストリーミング / #120, ADR 0024)
  POST /extract  — セッション全文を Mention[] に抽出 + 既存 Problem へグルーピング (ADR 0007)
  POST /plan     — Problem の文脈から ActionPlan を生成
  POST /approve  — 承認待ちツール呼び出しの実行 / キャンセル
  GET  /health   — ヘルスチェック
"""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from .agents import get_chat_client
from .config import get_settings
from .extractor import ExtractionParseError, ExtractionUnavailable, extract
from .observability import (
    client_detail,
    exception_frames,
    exception_kind,
    new_ref,
)
from .planner import generate_plan
from .repositories import (
    ApprovalRepository,
    SessionRepository,
    create_approval_repository,
    create_session_repository,
)
from .schemas import (
    ApprovalConflictResponse,
    ApproveRequest,
    ApproveResponse,
    ChatRequest,
    ChatResponse,
    ChatStreamError,
    ExtractionResult,
    ExtractRequest,
    HealthResponse,
    PlanRequest,
    PlanResponse,
)
from .workflow import (
    ApprovalAlreadyProcessedError,
    resume_after_approval,
    run_workflow,
    run_workflow_stream,
)

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper()))
logger = logging.getLogger(__name__)

# モジュールレベルのシングルトン。実装は COSMOS_ENDPOINT の有無で選ばれる
# (#188 / ADR 0030: あれば Cosmos 永続化、無ければ従来どおり in-memory)
_session_repo: SessionRepository = create_session_repository()
_approval_repo: ApprovalRepository = create_approval_repository()


def get_session_repo() -> SessionRepository:
    """FastAPI Depends provider。test では app.dependency_overrides で差し替える。"""
    return _session_repo


def get_approval_repo() -> ApprovalRepository:
    """FastAPI Depends provider。test では app.dependency_overrides で差し替える。"""
    return _approval_repo


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s", settings.app_name)
    try:
        # MAF chat client を起動時にロードして初回リクエストのレイテンシを下げる
        get_chat_client()
    except Exception as exc:
        # 資格情報なしでも起動は継続する (stub fallback の流儀)。LLM 呼び出し時に失敗する
        logger.error("Chat client setup failed — LLM calls will fail: %s", exc)
    yield
    logger.info("Shutting down %s", settings.app_name)


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


def _fail(endpoint: str, exc: Exception) -> HTTPException:
    """例外をクライアントに返してよい形へ落とす (Issue #313 / rubric S3)。

    `str(exc)` をそのまま返すと、Azure OpenAI SDK の例外文 (エンドポイント URL /
    デプロイ名 / api-version / request id、コンテンツフィルタが引用したプロンプト断片)
    が BFF の素通しでブラウザの devtools まで届く。**外に出すのは一般化した文言 + ref
    だけ**にし、詳細 (traceback 込み) は同じ ref を持つサーバのログにだけ残す。

    タイムアウトは 504 に分けて写す — 「上流が遅くて諦めた」と「こちらが壊れた」は
    運用上まったく別の事象で、まとめて 500 にすると切り分けができなくなる。

    ログ側も `exc_info=True` は使わない — traceback の最終行が例外メッセージそのもの
    (= コンテンツフィルタの引用や検証に落ちた値) なので、サーバのログが機微データの
    出口として残ってしまう。フレームだけを `exception_frames` で残す (PR #324 P1)。
    """
    ref = new_ref()
    logger.error(
        "%s failed ref=%s kind=%s at=%s",
        endpoint,
        ref,
        exception_kind(exc),
        exception_frames(exc),
    )
    if isinstance(exc, TimeoutError):
        return HTTPException(
            status_code=504,
            detail=client_detail(ref, "処理が時間内に完了しませんでした"),
        )
    return HTTPException(status_code=500, detail=client_detail(ref))


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
        # chat client は workflow が LLM を呼ぶ時点で遅延解決する (資格情報なし
        # でも起動し、失敗は LLM 呼び出しで表面化する縮退挙動を保つ)
        return await run_workflow(
            req.session_id,
            req.message,
            session_repo,
            approval_repo,
        )
    except Exception as exc:
        raise _fail("POST /chat", exc) from exc


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
            ):
                yield f"data: {event.model_dump_json()}\n\n"
        except Exception as exc:
            # SSE は 200 を返した後なので HTTP エラーにできない。error イベントで伝え、
            # クライアント (BFF/フロント) が非ストリーミングへフォールバックする。
            # data 行はブラウザまで素通しされる (BFF はバイト列を転送するだけ) ので、
            # 例外文ではなく一般化した文言 + ref を載せる (Issue #313)。
            ref = new_ref()
            logger.error(
                "POST /chat/stream failed ref=%s kind=%s at=%s",
                ref,
                exception_kind(exc),
                exception_frames(exc),
            )
            error = ChatStreamError(message=client_detail(ref))
            yield f"data: {error.model_dump_json()}\n\n"

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
        # 例外文は extractor が作った一般化済みの文言 + ref (壊れた応答本文は含まない)。
        logger.error("POST /extract parse error: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise _fail("POST /extract", exc) from exc


@app.post("/plan", response_model=PlanResponse)
async def plan_endpoint(req: PlanRequest) -> PlanResponse:
    try:
        return await generate_plan(req, get_chat_client())
    except Exception as exc:
        raise _fail("POST /plan", exc) from exc


@app.post(
    "/approve",
    response_model=ApproveResponse,
    responses={409: {"model": ApprovalConflictResponse}},
)
async def approve(
    req: ApproveRequest,
    session_repo: SessionRepository = Depends(get_session_repo),
    approval_repo: ApprovalRepository = Depends(get_approval_repo),
):
    try:
        reply = await resume_after_approval(
            req.approval_request_id,
            req.approved,
            session_repo,
            approval_repo,
        )
        return ApproveResponse(reply=reply)
    except ApprovalAlreadyProcessedError as exc:
        # 二重送信 (#82 / PO 裁定 2026-08-15 B 案)。**404 に混ぜない** — 混ぜると
        # 「レコードが消えた」のか「もう解決済み」なのかをクライアントが判定できず、
        # **却下済み (= 確実に未実行) すら案内できない**。
        # なお `status` は受け付けた決定であって実行の完了ではない (PR #430 Codex P1)。
        #
        # response_model (ApproveResponse) を通さずに JSONResponse を返しているので、
        # **この body の形は OpenAPI の宣言 (responses=) と自動では一致しない**。
        # 一致は `ApprovalConflictResponse` を組み立てて dump することで担保する
        # (フィールド名を変えれば型エラー、schema を変えれば contract-check が落ちる)。
        return JSONResponse(
            status_code=409,
            content=ApprovalConflictResponse(
                detail=str(exc),
                # pending がここに来ることはない (呼び出し元が pending 以外でだけ
                # 投げる) が、来たら pydantic の Literal が実行時に弾く = 500 になる。
                # 「未処理を処理済みとして返す」より落ちる方を選ぶ
                status=exc.status,
                processed_at=exc.processed_at,
            ).model_dump(),
        )
    except ValueError as exc:
        # このサービス自身が投げる「承認レコードがもう無い」(未知 ID / TTL 失効 /
        # checkpoint 消失)。上流の情報を含まない。**処理済みはここに来ない** —
        # 来ていた頃の問題は上の except に書いた
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise _fail("POST /approve", exc) from exc
