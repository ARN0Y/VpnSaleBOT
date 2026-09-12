from __future__ import annotations

import os
import asyncio
import contextlib
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from async_storefront.db import AsyncDatabase
from async_storefront.panel import PanelClient
from async_storefront.config import Runtime, WebRuntime
from async_storefront.settings_source import resolve_proxy_url

from . import api

from .auth import install_auth
from .backup import backup_scheduler
from .credentials import PanelCredentials
from .event_worker import event_worker
from . import files

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent


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
    await app.state.credentials.load(db)
    app.state.bot_token = str(await db.get_setting("bot_token", "") or "").strip()
    app.state.proxy_url = await resolve_proxy_url(db)
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
        app.mount("/admin/assets", StaticFiles(directory=str(assets_dir)), name="spa-assets")

    index_file = SPA_DIST / "index.html"
    dist_root = SPA_DIST.resolve()

    @app.get("/admin")
    async def spa_root() -> FileResponse:
        return FileResponse(index_file)

    @app.get("/admin/{spa_path:path}")
    async def spa_catch_all(spa_path: str) -> FileResponse:
        # Serve real files (fonts, favicon…) when present, else the SPA shell so
        # client-side routing works on deep links/refreshes. Resolve and confine
        # to dist/ so a crafted path like ../../etc/passwd cannot escape.
        if spa_path:
            candidate = (SPA_DIST / spa_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(dist_root):
                return FileResponse(candidate)
            # A request that names a file wants that file. Handing it index.html
            # is a 200 that silently delivers HTML where a font or script was
            # expected — which is exactly how a stale font path went unnoticed.
            if Path(spa_path).suffix:
                raise HTTPException(status_code=404, detail="not found")
        return FileResponse(index_file)


def create_app() -> FastAPI:
    app = FastAPI(title="Panel", docs_url=None, redoc_url=None, lifespan=lifespan)
    runtime = Runtime.load().prepare()
    app.state.runtime = runtime
    app.state.db_path = runtime.db_path
    app.state.backup_dir = runtime.backup_dir
    app.state.panel_timeout_seconds = runtime.panel_timeout_seconds
    # Filled in by the lifespan, once the database is open: the bot token, the
    # proxy and the panel account all live there now, not in a file.
    app.state.credentials = PanelCredentials()
    app.state.bot_token = ""
    app.state.proxy_url = ""
    install_auth(app)

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse("/admin", status_code=303)

    # The JSON API and the Telegram file proxy first, so the SPA's catch-all
    # (mounted at /admin) can never shadow them.
    app.include_router(api.router)
    app.include_router(files.router)

    # The React dashboard IS the panel — there is no second UI to choose between.
    _mount_spa(app)
    return app


app = create_app()


def main() -> None:
    web = WebRuntime.load()
    uvicorn.run(
        "admin_panel.main:app",
        host=web.host,
        port=web.port,
        reload=False,
        access_log=web.access_log,
        log_level=web.log_level,
    )


if __name__ == "__main__":
    main()
