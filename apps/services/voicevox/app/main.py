import logging

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

from .schemas import AudioQueryRequest, SynthesizeRequest
from . import voicevox_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="voicevox-wrapper", version="0.1.0")


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
