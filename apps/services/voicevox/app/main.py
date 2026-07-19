import logging

import os

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from .schemas import AudioQueryRequest, SynthesizeRequest
from . import voicevox_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="voicevox-wrapper", version="0.1.0")

# CORS: 匿名 mock デモ（SWA の静的フロント）がブラウザから直接 /synthesize を叩けるようにする。
# 既定は全許可（個人デモ向け）。CORS_ALLOW_ORIGINS を "," 区切りで渡せば絞れる。
_cors_origins = [o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    engine_ok = False
    try:
        await voicevox_client.get_version()
        engine_ok = True
    except Exception as e:
        logger.warning("VOICEVOX Engine unreachable: %s", e)

    return {"status": "ok", "engine_reachable": engine_ok}


@app.get("/speakers")
async def speakers() -> list[dict]:
    try:
        return await voicevox_client.get_speakers()
    except httpx.HTTPStatusError as e:
        logger.error("speakers fetch failed: %s", e)
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except httpx.RequestError as e:
        logger.error("speakers request error: %s", e)
        raise HTTPException(status_code=503, detail="VOICEVOX Engine unreachable")


@app.post("/audio-query")
async def audio_query(req: AudioQueryRequest) -> dict:
    try:
        return await voicevox_client.audio_query(req.text, req.speaker)
    except httpx.HTTPStatusError as e:
        logger.error("audio_query failed: %s", e)
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except httpx.RequestError as e:
        logger.error("audio_query request error: %s", e)
        raise HTTPException(status_code=503, detail="VOICEVOX Engine unreachable")


@app.post("/synthesize")
async def synthesize(req: SynthesizeRequest) -> Response:
    try:
        query = await voicevox_client.audio_query(req.text, req.speaker)

        if req.speed_scale is not None:
            query["speedScale"] = req.speed_scale
        if req.pitch_scale is not None:
            query["pitchScale"] = req.pitch_scale
        if req.intonation_scale is not None:
            query["intonationScale"] = req.intonation_scale
        if req.volume_scale is not None:
            query["volumeScale"] = req.volume_scale

        audio = await voicevox_client.synthesis(query, req.speaker)
        return Response(content=audio, media_type="audio/wav")

    except httpx.HTTPStatusError as e:
        logger.error("synthesize failed: %s", e)
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except httpx.RequestError as e:
        logger.error("synthesize request error: %s", e)
        raise HTTPException(status_code=503, detail="VOICEVOX Engine unreachable")
