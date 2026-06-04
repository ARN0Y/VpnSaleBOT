from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from async_storefront.db import AsyncDatabase
from async_storefront.panel import PanelClient
from async_storefront.util import now_ts

LOG = logging.getLogger(__name__)
DEFAULT_XUI_BACKUP_TIMEOUT_SECONDS = 180
MIN_XUI_BACKUP_TIMEOUT_SECONDS = 30
MAX_XUI_BACKUP_TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class BackupResult:
    archive_path: Path
    bot_db_path: Path | None
    xui_path: Path | None
    mode: str
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class XuiExportResult:
    path: Path
    mode: str
    endpoint: str
    backup_format: str


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except Exception:
        return default


def normalize_xui_backup_timeout(value: Any, default: int = DEFAULT_XUI_BACKUP_TIMEOUT_SECONDS) -> int:
    parsed = as_int(value, default)
    return min(MAX_XUI_BACKUP_TIMEOUT_SECONDS, max(MIN_XUI_BACKUP_TIMEOUT_SECONDS, parsed))


def _tehran_now_label() -> str:
    return datetime.now(ZoneInfo("Asia/Tehran")).strftime("%Y/%m/%d - %H:%M:%S")


async def send_backup_to_telegram(app, result: BackupResult, *, source: str) -> None:
    db: AsyncDatabase = app.state.db
    token = str(getattr(app.state, "bot_token", "") or "").strip()
    chat_id = str(await db.get_setting("backup_telegram_chat_id", "-1003940678338") or "-1003940678338").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is not configured; cannot send backup to Telegram")
    if not chat_id:
        raise RuntimeError("backup_telegram_chat_id is not configured")

    archive = Path(result.archive_path)
    if not archive.exists():
        raise RuntimeError(f"backup archive does not exist: {archive}")

    source_label = "بکاپ فوری" if source == "manual" else "بکاپ خودکار"
    size_mb = archive.stat().st_size / (1024 * 1024)
    status_label = "کامل" if not result.errors else "دارای هشدار"
    includes = []
    if result.bot_db_path:
        includes.append("دیتابیس ربات")
    if result.xui_path:
        includes.append("دیتابیس x-ui")
    caption = (
        f"🗄 <b>{source_label} تسکو نتورک</b>\n\n"
        f"⏱ زمان تهران: <b>{_tehran_now_label()}</b>\n"
        f"📦 محتوا: <b>{' + '.join(includes) or 'نامشخص'}</b>\n"
        f"🧾 وضعیت: <b>{status_label}</b>\n"
        f"📁 فایل: <code>{archive.name}</code>\n"
        f"⚖️ حجم: <b>{size_mb:.2f} MB</b>"
    )
    if result.errors:
        caption += "\n\n⚠️ هشدار:\n" + "\n".join(str(item)[:300] for item in result.errors[:3])

    proxy = str(getattr(app.state, "proxy_url", "") or "").strip() or None
    async with httpx.AsyncClient(proxy=proxy, timeout=120, trust_env=False) as client:
        with archive.open("rb") as handle:
            response = await client.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={
                    "chat_id": chat_id,
                    "caption": caption,
                    "parse_mode": "HTML",
                    "disable_notification": "false",
                },
                files={"document": (archive.name, handle, "application/zip")},
            )
        response.raise_for_status()


async def _json_bytes(payload: Any) -> bytes:
    text = await asyncio.to_thread(json.dumps, payload, ensure_ascii=False, indent=2)
    return text.encode("utf-8")


async def _write_bytes(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_bytes, data)
    return path


async def export_xui_snapshot(
    panel: PanelClient,
    backup_dir: Path,
    stamp: str,
    *,
    timeout_seconds: int = DEFAULT_XUI_BACKUP_TIMEOUT_SECONDS,
) -> XuiExportResult:
    """Export the richest x-ui backup available.

    Newer/older x-ui forks disagree on the DB backup endpoint. We first try a
    few common raw download routes, then fall back to the complete inbound JSON,
    which still captures all inbounds, clients and traffic stats available to
    the bot.
    """

    timeout_seconds = normalize_xui_backup_timeout(timeout_seconds)
    candidates = (
        "/panel/api/server/getDb",
        "/server/getDb",
        "/xui/server/getDb",
        "/panel/server/getDb",
    )
    for path in candidates:
        try:
            response = await panel.raw_request("GET", path, timeout_seconds=timeout_seconds)
        except Exception:
            continue
        content = response.content or b""
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type.lower():
            try:
                payload = response.json()
                if isinstance(payload, dict) and payload.get("success") is False:
                    continue
            except Exception:
                pass
        if len(content) > 128 and "text/html" not in content_type.lower():
            is_sqlite = content.startswith(b"SQLite format 3")
            looks_raw_db = is_sqlite or "octet-stream" in content_type.lower() or "download" in content_type.lower()
            if not looks_raw_db and "application/json" in content_type.lower():
                continue
            export_format = "sqlite_db" if is_sqlite else "raw_db_unverified"
            mode = "panel_getDb" if path == "/panel/api/server/getDb" else "fallback_getDb"
            saved = await _write_bytes(backup_dir / f"xui-panel-{stamp}.db", content)
            return XuiExportResult(path=saved, mode=mode, endpoint=path, backup_format=export_format)

    inbounds = await panel.list_inbounds_uncached(timeout_seconds=timeout_seconds)
    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "mode": "inbounds_json_fallback",
        "inbounds": inbounds,
    }
    saved = await _write_bytes(backup_dir / f"xui-inbounds-{stamp}.json", await _json_bytes(payload))
    return XuiExportResult(path=saved, mode="inbounds_json_fallback", endpoint="", backup_format="json")


async def create_full_backup(
    *,
    db: AsyncDatabase,
    panel: PanelClient,
    backup_dir: Path,
    include_bot: bool = True,
    include_xui: bool = True,
    xui_timeout_seconds: int = DEFAULT_XUI_BACKUP_TIMEOUT_SECONDS,
) -> BackupResult:
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()
    work_dir = backup_dir / f"work-{stamp}"
    work_dir.mkdir(parents=True, exist_ok=True)

    bot_db_path: Path | None = None
    xui_path: Path | None = None
    xui_export: XuiExportResult | None = None
    errors: list[str] = []
    if include_bot:
        bot_db_path = await db.create_native_backup(work_dir / f"botok-{stamp}.db")
    if include_xui:
        try:
            xui_export = await export_xui_snapshot(panel, work_dir, stamp, timeout_seconds=xui_timeout_seconds)
            xui_path = xui_export.path
        except Exception as exc:
            LOG.exception("x-ui backup export failed")
            errors.append(f"x-ui backup failed: {exc}")
            xui_path = await _write_bytes(
                work_dir / f"xui-error-{stamp}.json",
                await _json_bytes({"exported_at": datetime.now(timezone.utc).isoformat(), "error": str(exc)}),
            )
            xui_export = XuiExportResult(path=xui_path, mode="error", endpoint="", backup_format="json")

    settings_rows = await db.admin_list_settings()
    panel_settings = await db.get_panel_settings()
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "include_bot": include_bot,
        "include_xui": include_xui,
        "bot_db": bot_db_path.name if bot_db_path else None,
        "xui_export": xui_path.name if xui_path else None,
        "xui_export_mode": xui_export.mode if xui_export else None,
        "xui_endpoint": xui_export.endpoint if xui_export else None,
        "xui_backup_format": xui_export.backup_format if xui_export else None,
        "errors": errors,
        "settings_keys": [row["key"] for row in settings_rows],
        "panel_inbound_id": int(panel_settings["inbound_id"] or 0) if panel_settings else 0,
    }
    manifest_path = await _write_bytes(work_dir / "manifest.json", await _json_bytes(manifest))

    archive_path = backup_dir / f"full-backup-{stamp}.zip"

    def _zip() -> None:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for path in (bot_db_path, xui_path, manifest_path):
                if path and path.exists():
                    zf.write(path, arcname=path.name)
        for path in work_dir.iterdir():
            path.unlink(missing_ok=True)
        work_dir.rmdir()

    await asyncio.to_thread(_zip)
    mode = "full" if include_bot and include_xui else "bot" if include_bot else "xui"
    return BackupResult(archive_path=archive_path, bot_db_path=bot_db_path, xui_path=xui_path, mode=mode, errors=tuple(errors))


async def run_scheduled_backup_once(app) -> BackupResult | None:
    db: AsyncDatabase = app.state.db
    enabled = await db.get_setting("backup_enabled", "0")
    if enabled != "1":
        return None
    interval_value = max(1, as_int(await db.get_setting("backup_interval_value", ""), 0))
    interval_unit = (await db.get_setting("backup_interval_unit", "days") or "days").strip().lower()
    if interval_value <= 0:
        interval_value = max(1, as_int(await db.get_setting("backup_interval_days", "7"), 7))
        interval_unit = "days"
    unit_seconds = {
        "minutes": 60,
        "hours": 3600,
        "days": 86400,
        "weeks": 604800,
    }.get(interval_unit, 60)
    last_run = as_int(await db.get_setting("backup_last_run_ts", "0"), 0)
    if last_run and now_ts() - last_run < interval_value * unit_seconds:
        return None
    return await run_backup_now(app, source="scheduled")


async def run_backup_now(app, *, source: str = "manual") -> BackupResult:
    lock = getattr(app.state, "backup_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        app.state.backup_lock = lock
    async with lock:
        return await _run_backup_now_locked(app, source=source)


async def _run_backup_now_locked(app, *, source: str = "manual") -> BackupResult:
    db: AsyncDatabase = app.state.db
    panel: PanelClient = app.state.panel
    backup_dir = Path(getattr(app.state, "backup_dir", Path("backup"))).resolve()
    include_bot = await db.get_setting("backup_include_bot", "1") == "1"
    include_xui = await db.get_setting("backup_include_xui", "1") == "1"
    xui_timeout_seconds = normalize_xui_backup_timeout(
        await db.get_setting("backup_xui_timeout_seconds", str(DEFAULT_XUI_BACKUP_TIMEOUT_SECONDS))
    )
    if not include_bot and not include_xui:
        include_bot = True

    await db.set_setting("backup_last_status", "running")
    result: BackupResult | None = None
    try:
        result = await create_full_backup(
            db=db,
            panel=panel,
            backup_dir=backup_dir,
            include_bot=include_bot,
            include_xui=include_xui,
            xui_timeout_seconds=xui_timeout_seconds,
        )
        await send_backup_to_telegram(app, result, source=source)
    except Exception as exc:
        LOG.exception("backup failed source=%s", source)
        await db.set_setting("backup_last_status", "failed")
        await db.set_setting("backup_last_error", str(exc)[:1000])
        if result is not None:
            with contextlib.suppress(Exception):
                Path(result.archive_path).unlink(missing_ok=True)
        raise
    await db.set_setting("backup_last_status", "partial" if result.errors else "ok")
    await db.set_setting("backup_last_error", "\n".join(result.errors)[:1000])
    await db.set_setting("backup_last_run_ts", str(now_ts()))
    await db.set_setting("backup_last_file", f"sent:{result.archive_path.name}")
    with contextlib.suppress(Exception):
        Path(result.archive_path).unlink(missing_ok=True)
    return result


async def backup_scheduler(app) -> None:
    while True:
        try:
            await run_scheduled_backup_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("scheduled backup loop iteration failed")
        await asyncio.sleep(60)


def list_backup_files(backup_dir: Path, *, limit: int = 30) -> list[dict[str, Any]]:
    backup_dir = Path(backup_dir)
    if not backup_dir.exists():
        return []
    files = sorted((p for p in backup_dir.glob("*.zip") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    result = []
    for path in files[:limit]:
        stat = path.stat()
        result.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "mtime": int(stat.st_mtime),
            }
        )
    return result
