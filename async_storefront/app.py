from __future__ import annotations

import contextlib
from io import BytesIO
import warnings

from telegram import Update
from telegram.request import HTTPXRequest
from telegram.ext import Application, CommandHandler, ContextTypes
try:
    from telegram.warnings import PTBUserWarning
except Exception:  # pragma: no cover - depends on PTB minor version
    PTBUserWarning = UserWarning

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional at import time
    load_dotenv = None

from .agent import AgentService
from .config import Settings
from .db import AsyncDatabase
from .handlers import register_handlers
from .panel import PanelClient
from .provisioning import ProvisioningService
from .qr import QRService

if load_dotenv:
    load_dotenv()

warnings.filterwarnings(
    "ignore",
    message=r"If 'per_message=False', 'CallbackQueryHandler' will not be tracked for every message\..*",
    category=PTBUserWarning,
)


async def post_init(app: Application) -> None:
    settings: Settings = app.bot_data["settings"]
    db = AsyncDatabase(settings.db_path)
    await db.connect()
    await db.init_schema()
    panel = PanelClient(
        db,
        pool_size=settings.panel_pool_size,
        timeout_seconds=settings.panel_timeout_seconds,
    )
    agents = AgentService(db)
    qr = QRService(settings.qr_workers)
    app.bot_data.update(
        db=db,
        panel=panel,
        agents=agents,
        provisioning=ProvisioningService(db, panel, agents),
        qr=qr,
        backup_dir=settings.backup_dir,
        backup_chat_id=0,
    )
    # Warm the renamable menu-button routing index so custom labels route
    # correctly even before the first keyboard is rebuilt.
    from .handlers import resolve_nav_labels

    with contextlib.suppress(Exception):
        await resolve_nav_labels(db)


async def post_shutdown(app: Application) -> None:
    qr: QRService | None = app.bot_data.get("qr")
    if qr:
        qr.close()
    panel: PanelClient | None = app.bot_data.get("panel")
    if panel:
        await panel.close()
    panel2: PanelClient | None = app.bot_data.get("panel2")
    if panel2:
        await panel2.close()
    db: AsyncDatabase | None = app.bot_data.get("db")
    if db:
        await db.close()


async def health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: AsyncDatabase = context.application.bot_data["db"]
    users = await db.fetchone("SELECT COUNT(*) AS c FROM users")
    await update.effective_message.reply_text(f"ok users={int(users['c'] if users else 0)}")


async def panel_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    panel: PanelClient = context.application.bot_data["panel"]
    inbounds = await panel.list_inbounds()
    await update.effective_message.reply_text(f"panel ok, inbounds={len(inbounds)}")


async def qr_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payload = " ".join(context.args).strip()
    if not payload:
        await update.effective_message.reply_text("Usage: /qr <payload>")
        return
    qr: QRService = context.application.bot_data["qr"]
    png = await qr.png(payload)
    await update.effective_message.reply_photo(photo=BytesIO(png), caption="QR generated off the event loop")


def build_application(settings: Settings) -> Application:
    request = HTTPXRequest(
        proxy=settings.proxy_url or None,
        connection_pool_size=settings.telegram_pool_size,
        connect_timeout=20,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=20,
    )
    updates_request = HTTPXRequest(
        proxy=settings.proxy_url or None,
        connection_pool_size=max(8, settings.telegram_pool_size // 4),
        connect_timeout=20,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=20,
    )
    app = (
        Application.builder()
        .token(settings.bot_token)
        .request(request)
        .get_updates_request(updates_request)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .concurrent_updates(256)
        .build()
    )
    app.bot_data["settings"] = settings
    register_handlers(app)
    app.add_handler(CommandHandler("health", health))
    app.add_handler(CommandHandler("panel_ping", panel_ping))
    app.add_handler(CommandHandler("qr", qr_command))
    return app


def main() -> None:
    settings = Settings.from_env()
    app = build_application(settings)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
