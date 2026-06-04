from __future__ import annotations

from datetime import datetime
from pathlib import Path

from telegram import InputFile
from telegram.ext import ContextTypes

from .db import AsyncDatabase


async def create_safe_backup(db: AsyncDatabase, backup_dir: Path) -> Path:
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return await db.create_native_backup(Path(backup_dir) / f"botok-{stamp}.db")


async def backup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    db: AsyncDatabase = context.application.bot_data["db"]
    if await db.get_setting("backup_send_to_telegram", "0") != "1":
        return
    backup_chat_id = int(context.application.bot_data.get("backup_chat_id") or 0)
    backup_dir: Path = context.application.bot_data["backup_dir"]
    if not backup_chat_id:
        return
    backup_path = await create_safe_backup(db, backup_dir)
    with backup_path.open("rb") as handle:
        await context.bot.send_document(
            chat_id=backup_chat_id,
            document=InputFile(handle, filename=backup_path.name),
            caption=f"Safe SQLite backup: {backup_path.name}",
        )
