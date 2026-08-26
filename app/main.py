"""VoiceHub AI Gateway — FastAPI 入口 + 后台轮询 + 管理台路由。"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .admin.auth_routes import router as auth_router
from .admin.bootstrap import ensure_admin
from .admin.routes_dashboard import router as dashboard_router
from .admin.routes_providers import router as providers_router
from .config import settings
from .db import init_db
from .providers import seed_default_providers
from .db import GatewaySession
from .workers.poll_pending import run_poll_loop

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
log = logging.getLogger("ai-gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    ensure_admin()
    # 预置供应商（如果库为空）
    session = GatewaySession()
    try:
        seed_default_providers(session)
    finally:
        session.close()
    task = asyncio.create_task(run_poll_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="VoiceHub AI Gateway", version="0.2.0", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(providers_router)


@app.get("/health")
async def health() -> dict:
    from .providers import get_default_provider
    from .db import GatewaySession
    session = GatewaySession()
    try:
        default_provider = get_default_provider(session)
    finally:
        session.close()
    return {
        "status": "ok",
        "voicehub_configured": bool(settings.voicehub_api_key and settings.voicehub_api_base_url),
        "llm_configured": bool(settings.llm_api_key) or default_provider is not None,
        "admin_secret_configured": bool((settings.admin_secret or "").strip()),
    }


@app.exception_handler(404)
async def not_found(request: Request, exc):
    from fastapi.responses import RedirectResponse
    if request.url.path.startswith("/admin"):
        return RedirectResponse(url="/admin/")
    return JSONResponse({"detail": "not found"}, status_code=404)