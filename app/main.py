"""VoiceHub AI Gateway — FastAPI 入口 + 后台轮询 + 管理台路由。"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .admin.auth_routes import router as auth_router
from .admin.bootstrap import ensure_admin
from .admin.routes_dashboard import router as dashboard_router
from .admin.routes_logs import router as logs_router
from .admin.routes_providers import router as providers_router
from .admin.routes_queue import router as queue_router
from .admin.routes_risk import router as risk_router
from .admin.routes_roster import router as roster_router
from .admin.routes_rules import router as rules_router
from .admin.routes_settings import router as settings_router
from .admin.routes_spotcheck import router as spotcheck_router
from .config import settings
from .db import GatewaySession, init_db
from .providers import seed_default_providers
from .workers.archive import run_archive_loop
from .workers.cleanup import run_cleanup_loop
from .workers.poll_pending import run_poll_loop
from .workers.spotcheck import run_spotcheck_loop

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
log = logging.getLogger("ai-gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    ensure_admin()
    session = GatewaySession()
    try:
        seed_default_providers(session)
    finally:
        session.close()
    task = asyncio.create_task(run_poll_loop())
    spotcheck_task = asyncio.create_task(run_spotcheck_loop())
    archive_task = asyncio.create_task(run_archive_loop())
    cleanup_task = asyncio.create_task(run_cleanup_loop())
    try:
        yield
    finally:
        for t in (task, spotcheck_task, archive_task, cleanup_task):
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass


app = FastAPI(title="VoiceHub AI Gateway", version="0.2.0", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(providers_router)
app.include_router(queue_router)
app.include_router(logs_router)
app.include_router(rules_router)
app.include_router(settings_router)
app.include_router(roster_router)
app.include_router(spotcheck_router)
app.include_router(risk_router)


@app.get("/health")
async def health() -> dict:
    from .providers import get_default_provider
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