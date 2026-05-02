import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)

BASE_URL = settings.voicevox_engine_base_url


async def get_version() -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{BASE_URL}/version", timeout=5.0)
        r.raise_for_status()
        return r.json()


async def get_speakers() -> list[dict]:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{BASE_URL}/speakers", timeout=10.0)
        r.raise_for_status()
        return r.json()


async def audio_query(text: str, speaker: int) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE_URL}/audio_query",
            params={"text": text, "speaker": speaker},
            timeout=30.0,
        )
        r.raise_for_status()
        return r.json()


async def synthesis(query: dict, speaker: int) -> bytes:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE_URL}/synthesis",
            params={"speaker": speaker},
            json=query,
            timeout=60.0,
        )
        r.raise_for_status()
        return r.content
