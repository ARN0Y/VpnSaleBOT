from __future__ import annotations

import os
import asyncio
import contextlib
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from async_storefront.db import AsyncDatabase
from async_storefront.panel import PanelClient
from async_storefront.util import resolve_proxy_url

from . import api

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional at import time
    load_dotenv = None

from .auth import AuthConfig, install_auth
from .backup import backup_scheduler
from .event_worker import event_worker
from .routers import agent_requests, broadcast, dashboard, events, files, orders, settings, subscriptions, topups, users

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
if load_dotenv:
    load_dotenv(PROJECT_ROOT / ".env")


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    db = AsyncDatabase(app.state.db_path)
    await db.connect()
    await db.init_schema()
    await db.ensure_admin_runtime_schema()
    app.state.db = db
    app.state.panel = PanelClient(
        db,
        pool_size=16,
        timeout_seconds=getattr(app.state, "panel_timeout_seconds", 45.0),
    )
    app.state.backup_dir.mkdir(parents=True, exist_ok=True)
    app.state.backup_lock = asyncio.Lock()
    app.state.backup_task = asyncio.create_task(backup_scheduler(app))
    app.state.event_task = asyncio.create_task(event_worker(app))
    try:
        yield
    finally:
        event_task = getattr(app.state, "event_task", None)
        if event_task is not None:
            event_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await event_task
        backup_task = getattr(app.state, "backup_task", None)
        if backup_task is not None:
            backup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await backup_task
        panel = getattr(app.state, "panel", None)
        if panel is not None:
            await panel.close()
        state_db = getattr(app.state, "db", None)
        if state_db is not None:
            await state_db.close()


SPA_DIST = PROJECT_ROOT / "admin_ui" / "dist"


def _mount_spa(app: FastAPI) -> None:
    """Serve the built React SPA from admin_ui/dist if it exists.

    The build is produced on any machine with Node (``npm run build`` inside
    admin_ui/) and the resulting dist/ folder is deployed alongside the app —
    so the server itself does not need Node. If dist/ is absent, this is a
    no-op and the legacy Jinja panel remains the only UI.
    """
    if not SPA_DIST.exists():
        return
    assets_dir = SPA_DIST / "assets"
    if assets_dir.exists():
        app.mount("/admin/app/assets", StaticFiles(directory=str(assets_dir)), name="spa-assets")

    index_file = SPA_DIST / "index.html"
    dist_root = SPA_DIST.resolve()

    @app.get("/admin/app")
    async def spa_root() -> FileResponse:
        return FileResponse(index_file)

    @app.get("/admin/app/{spa_path:path}")
    async def spa_catch_all(spa_path: str) -> FileResponse:
        # Serve real files (favicon, etc.) when present, else the SPA shell so
        # client-side routing works on deep links/refreshes. Resolve and confine
        # to dist/ so a crafted path like ../../etc/passwd cannot escape.
        if spa_path:
            candidate = (SPA_DIST / spa_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(dist_root):
                return FileResponse(candidate)
        return FileResponse(index_file)


def create_app() -> FastAPI:
    app = FastAPI(title="ElsaVPN Admin", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    install_auth(app, AuthConfig.from_env())
    app.state.db_path = Path(os.getenv("BOT_DB_PATH", "elsavpn.db")).resolve()
    app.state.bot_token = os.getenv("BOT_TOKEN", "").strip()
    app.state.proxy_url = resolve_proxy_url()
    app.state.panel_timeout_seconds = _float_env("ADMIN_PANEL_TIMEOUT_SECONDS", 45.0)
    app.state.env_path = PROJECT_ROOT / ".env"
    app.state.backup_dir = Path(os.getenv("BOT_BACKUP_DIR", str(PROJECT_ROOT / "backup"))).resolve()
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse("/admin", status_code=303)

    # New React/shadcn dashboard (served as static once built).
    _mount_spa(app)

    # Single entry point: /admin serves the UI the admin chose in settings
    # (modern SPA or classic Jinja). Registered before the dashboard router so
    # it takes precedence for GET /admin. Switching is instant (no restart).
    @app.get("/admin")
    async def admin_home(request: Request):
        mode = "modern"
        try:
            mode = (await request.app.state.db.get_setting("ui_mode", "modern") or "modern").strip().lower()
        except Exception:
            pass
        if mode != "classic" and SPA_DIST.exists():
            return RedirectResponse("/admin/app", status_code=307)
        return await dashboard.overview(request)

    app.include_router(api.router)
    app.include_router(dashboard.router)
    app.include_router(users.router)
    app.include_router(topups.router)
    app.include_router(agent_requests.router)
    app.include_router(broadcast.router)
    app.include_router(events.router)
    app.include_router(orders.router)
    app.include_router(subscriptions.router)
    app.include_router(settings.router)
    app.include_router(files.router)
    return app


app = create_app()


def main() -> None:
    access_log = os.getenv("ADMIN_ACCESS_LOG", "0").strip().lower() in {"1", "true", "yes", "on"}
    uvicorn.run(
        "admin_panel.main:app",
        host=os.getenv("ADMIN_HOST", "127.0.0.1"),
        port=int(os.getenv("ADMIN_PORT", "8080")),
        reload=False,
        access_log=access_log,
        log_level=os.getenv("ADMIN_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
