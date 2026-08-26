"""VoiceHub AI Gateway — FastAPI 入口 + 后台轮询。"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import settings
from .workers.poll_pending import run_poll_loop

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
log = logging.getLogger("ai-gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(run_poll_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="VoiceHub AI Gateway", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "voicehub_configured": bool(settings.voicehub_api_key and settings.voicehub_api_base_url),
        "llm_configured": bool(settings.llm_api_key),
    }