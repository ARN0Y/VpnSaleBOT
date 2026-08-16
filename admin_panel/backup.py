from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
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

from .pg_backup import (
    DEFAULT_BACKUP_DIR as PG_DEFAULT_BACKUP_DIR,
    DEFAULT_CLI as PG_DEFAULT_CLI,
    DEFAULT_COMPOSE_FILE as PG_DEFAULT_COMPOSE_FILE,
    DEFAULT_MAX_AGE_MINUTES as PG_DEFAULT_MAX_AGE_MINUTES,
    DEFAULT_TIMEOUT_SECONDS as PG_DEFAULT_TIMEOUT_SECONDS,
    PgExportResult,
    export_pasarguard_backup,
)

LOG = logging.getLogger(__name__)
DEFAULT_XUI_BACKUP_TIMEOUT_SECONDS = 180
MIN_XUI_BACKUP_TIMEOUT_SECONDS = 30
MAX_XUI_BACKUP_TIMEOUT_SECONDS = 600
# Telegram rejects bot uploads over 50 MB; split a hair below that.
TELEGRAM_MAX_DOCUMENT_BYTES = 49 * 1000 * 1000


@dataclass(frozen=True)
class BackupResult:
    archive_path: Path
    bot_db_path: Path | None
    xui_path: Path | None
    mode: str
    errors: tuple[str, ...] = ()
    # The PasarGuard archive is delivered as its own document, not buried inside
    # the bot archive: `pasarguard restore` takes this file as-is.
    pg_path: Path | None = None
    pg_export: PgExportResult | None = None


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


async def backup_telegram_target(app) -> tuple[str, str]:
    """Token and chat id used for backup delivery.

    ``backup_bot_token`` lets backups go through a dedicated bot into a private
    archive channel, so rotating or losing the storefront bot does not take the
    backups with it. Empty falls back to the storefront bot's own token.
    """
    db: AsyncDatabase = app.state.db
    token = str(await db.get_setting("backup_bot_token", "") or "").strip()
    if not token:
        token = str(getattr(app.state, "bot_token", "") or "").strip()
    chat_id = str(await db.get_setting("backup_telegram_chat_id", "") or "").strip()
    return token, chat_id


def _split_for_telegram(archive: Path, *, chunk_bytes: int = TELEGRAM_MAX_DOCUMENT_BYTES) -> list[Path]:
    """Split an oversized archive into ``name.partNN.zip`` pieces.

    Same convention the PasarGuard CLI uses, so the documented reassembly
    (``cat name.part*.zip > name``) works for these too.
    """
    total = archive.stat().st_size
    if total <= chunk_bytes:
        return [archive]
    parts: list[Path] = []
    with archive.open("rb") as handle:
        index = 1
        while True:
            chunk = handle.read(chunk_bytes)
            if not chunk:
                break
            part = archive.with_name(f"{archive.name}.part{index:02d}.zip")
            part.write_bytes(chunk)
            parts.append(part)
            index += 1
    return parts


async def _send_document(
    client: httpx.AsyncClient, *, token: str, chat_id: str, path: Path, caption: str
) -> None:
    with path.open("rb") as handle:
        response = await client.post(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data={
                "chat_id": chat_id,
                "caption": caption[:1024],
                "parse_mode": "HTML",
                "disable_notification": "false",
            },
            files={"document": (path.name, handle, "application/zip")},
        )
    if response.status_code >= 400:
        # Telegram's JSON description is far more useful than "HTTP 400".
        detail = ""
        with contextlib.suppress(Exception):
            detail = str(response.json().get("description") or "")
        raise RuntimeError(f"Telegram sendDocument failed ({response.status_code}): {detail or response.text[:200]}")


async def _deliver_archive(
    client: httpx.AsyncClient, *, token: str, chat_id: str, archive: Path, caption: str
) -> None:
    """Send one archive, splitting it when Telegram would reject the size."""
    parts = await asyncio.to_thread(_split_for_telegram, archive)
    if len(parts) == 1:
        await _send_document(client, token=token, chat_id=chat_id, path=archive, caption=caption)
        return
    LOG.info("backup %s exceeds Telegram's limit; sending %d parts", archive.name, len(parts))
    try:
        for index, part in enumerate(parts, start=1):
            part_caption = (
                f"{caption}\n\n"
                f"🧩 بخش <b>{index}</b> از <b>{len(parts)}</b>\n"
                f"<code>cat {archive.name}.part*.zip &gt; {archive.name}</code>\n"
                "همه بخش‌ها را دانلود کنید، سپس با دستور بالا فایل را بازسازی کنید."
            )
            await _send_document(client, token=token, chat_id=chat_id, path=part, caption=part_caption)
    finally:
        for part in parts:
            with contextlib.suppress(Exception):
                part.unlink(missing_ok=True)


async def send_backup_to_telegram(app, result: BackupResult, *, source: str) -> bool:
    """Deliver the backup to Telegram. Returns True when delivered, False when
    delivery is not configured (missing token / chat id) — a missing destination
    is NOT a backup failure, so the caller keeps the local archive instead of
    discarding it. Genuine send errors still raise.

    The PasarGuard archive goes as its own document so it can be fed straight to
    ``pasarguard restore`` without unwrapping anything first.
    """
    token, chat_id = await backup_telegram_target(app)
    if not token or not chat_id:
        LOG.warning(
            "backup Telegram delivery skipped: %s not configured",
            "bot token" if not token else "backup_telegram_chat_id",
        )
        return False

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
    if result.pg_path:
        includes.append("پنل PasarGuard")
    caption = (
        f"🗄 <b>{source_label} NavidVPN</b>\n\n"
        f"⏱ زمان تهران: <b>{_tehran_now_label()}</b>\n"
        f"📦 محتوا: <b>{' + '.join(includes) or 'نامشخص'}</b>\n"
        f"🧾 وضعیت: <b>{status_label}</b>\n"
        f"📁 فایل: <code>{archive.name}</code>\n"
        f"⚖️ حجم: <b>{size_mb:.2f} MB</b>"
    )
    if result.errors:
        caption += "\n\n⚠️ هشدار:\n" + "\n".join(str(item)[:200] for item in result.errors[:3])

    proxy = str(getattr(app.state, "proxy_url", "") or "").strip() or None
    async with httpx.AsyncClient(proxy=proxy, timeout=300, trust_env=False) as client:
        await _deliver_archive(client, token=token, chat_id=chat_id, archive=archive, caption=caption)

        if result.pg_path and Path(result.pg_path).exists():
            pg_file = Path(result.pg_path)
            pg = result.pg_export
            pg_size = pg_file.stat().st_size / (1024 * 1024)
            db_mb = (pg.db_bytes / (1024 * 1024)) if pg else 0.0
            pg_caption = (
                "🛡 <b>بکاپ پنل PasarGuard</b>\n\n"
                f"⏱ زمان تهران: <b>{_tehran_now_label()}</b>\n"
                f"🗃 دیتابیس پنل: <b>{db_mb:.1f} MB</b>\n"
                f"⚖️ حجم فایل: <b>{pg_size:.2f} MB</b>\n"
                f"📁 <code>{pg_file.name}</code>\n\n"
                "♻️ <b>بازگردانی:</b> این فایل را روی سرور پنل بگذارید و اجرا کنید:\n"
                f"<code>pasarguard restore {pg_file.name}</code>"
            )
            await _deliver_archive(client, token=token, chat_id=chat_id, archive=pg_file, caption=pg_caption)
    return True


def _prune_local_backups(backup_dir: Path, *, keep: int = 3, pattern: str = "full-backup-*.zip") -> None:
    """Keep only the newest ``keep`` local backup archives so undelivered
    backups don't accumulate and fill the disk."""
    try:
        files = sorted(
            (p for p in Path(backup_dir).glob(pattern) if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for path in files[keep:]:
            with contextlib.suppress(Exception):
                path.unlink(missing_ok=True)
    except Exception:
        LOG.exception("failed to prune local backups in %s", backup_dir)


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
    include_pg: bool = False,
    pg_options: dict[str, Any] | None = None,
    xui_timeout_seconds: int = DEFAULT_XUI_BACKUP_TIMEOUT_SECONDS,
) -> BackupResult:
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()
    work_dir = backup_dir / f"work-{stamp}"
    work_dir.mkdir(parents=True, exist_ok=True)

    bot_db_path: Path | None = None
    xui_path: Path | None = None
    pg_path: Path | None = None
    pg_export: PgExportResult | None = None
    xui_export: XuiExportResult | None = None
    errors: list[str] = []
    if include_bot:
        bot_db_path = await db.create_native_backup(work_dir / f"botok-{stamp}.db")
    if include_pg:
        try:
            pg_export = await export_pasarguard_backup(
                work_dir=backup_dir, stamp=stamp, **(pg_options or {})
            )
            pg_path = pg_export.path
        except Exception as exc:
            # Loud but non-fatal: the bot's own data must still get backed up.
            LOG.exception("PasarGuard backup export failed")
            errors.append(f"PasarGuard backup failed: {exc}")
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
        "include_pg": include_pg,
        "bot_db": bot_db_path.name if bot_db_path else None,
        "xui_export": xui_path.name if xui_path else None,
        "xui_export_mode": xui_export.mode if xui_export else None,
        "xui_endpoint": xui_export.endpoint if xui_export else None,
        "xui_backup_format": xui_export.backup_format if xui_export else None,
        # The PasarGuard archive ships as a separate document; record what it was
        # so a stored manifest still explains where the panel backup went.
        "pasarguard_archive": pg_path.name if pg_path else None,
        "pasarguard_mode": pg_export.mode if pg_export else None,
        "pasarguard_db_bytes": pg_export.db_bytes if pg_export else 0,
        "pasarguard_restorable": bool(pg_export.restorable) if pg_export else False,
        "pasarguard_restore_hint": (
            f"pasarguard restore {pg_path.name}" if pg_path else None
        ),
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

    try:
        await asyncio.to_thread(_zip)
    finally:
        # Always clear the staging directory. It used to be removed only on the
        # success path, so any failure mid-backup left a work-*/ folder holding a
        # full copy of the bot database behind forever.
        await asyncio.to_thread(shutil.rmtree, work_dir, True)
    mode = "full" if include_bot and include_xui else "bot" if include_bot else "xui"
    if include_pg:
        mode = f"{mode}+pg"
    return BackupResult(
        archive_path=archive_path,
        bot_db_path=bot_db_path,
        xui_path=xui_path,
        mode=mode,
        errors=tuple(errors),
        pg_path=pg_path,
        pg_export=pg_export,
    )


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
    include_pg = await db.get_setting("backup_include_pg", "0") == "1"
    xui_timeout_seconds = normalize_xui_backup_timeout(
        await db.get_setting("backup_xui_timeout_seconds", str(DEFAULT_XUI_BACKUP_TIMEOUT_SECONDS))
    )
    if not include_bot and not include_xui:
        include_bot = True
    pg_options = {
        "mode": (await db.get_setting("pg_backup_mode", "auto") or "auto").strip().lower(),
        "compose_file": (await db.get_setting("pg_backup_compose_file", "") or "").strip() or PG_DEFAULT_COMPOSE_FILE,
        "backup_dir": (await db.get_setting("pg_backup_dir", "") or "").strip() or PG_DEFAULT_BACKUP_DIR,
        "cli_path": (await db.get_setting("pg_backup_cli", "") or "").strip() or PG_DEFAULT_CLI,
        "max_age_minutes": as_int(await db.get_setting("pg_backup_max_age_minutes", ""), PG_DEFAULT_MAX_AGE_MINUTES),
        "timeout_seconds": as_int(await db.get_setting("pg_backup_timeout_seconds", ""), PG_DEFAULT_TIMEOUT_SECONDS),
    }

    await db.set_setting("backup_last_status", "running")
    result: BackupResult | None = None
    # Phase 1 — CREATING the archive is the only fatal step.
    try:
        result = await create_full_backup(
            db=db,
            panel=panel,
            backup_dir=backup_dir,
            include_bot=include_bot,
            include_xui=include_xui,
            include_pg=include_pg,
            pg_options=pg_options,
            xui_timeout_seconds=xui_timeout_seconds,
        )
    except Exception as exc:
        LOG.exception("backup creation failed source=%s", source)
        await db.set_setting("backup_last_status", "failed")
        await db.set_setting("backup_last_error", str(exc)[:1000])
        if result is not None:
            with contextlib.suppress(Exception):
                Path(result.archive_path).unlink(missing_ok=True)
        raise

    # Phase 2 — DELIVERY is best-effort: a missing/failed Telegram send must
    # never discard the backup or fail the whole run (that previously deleted
    # every backup and spammed errors each minute when no chat id was set).
    delivered = False
    delivery_error = ""
    try:
        delivered = await send_backup_to_telegram(app, result, source=source)
    except Exception as exc:
        delivery_error = str(exc)
        LOG.warning("backup telegram delivery failed source=%s: %s", source, exc)

    await db.set_setting("backup_last_run_ts", str(now_ts()))
    base_errs = list(result.errors)
    pg = result.pg_export
    await db.set_setting("backup_last_pg_status", "ok" if (pg and pg.restorable) else ("failed" if include_pg else "off"))
    await db.set_setting("backup_last_pg_mode", pg.mode if pg else "")
    await db.set_setting("backup_last_pg_db_bytes", str(pg.db_bytes if pg else 0))
    if delivered:
        await db.set_setting("backup_last_status", "partial" if base_errs else "ok")
        await db.set_setting("backup_last_error", "\n".join(base_errs)[:1000])
        await db.set_setting("backup_last_file", f"sent:{result.archive_path.name}")
        with contextlib.suppress(Exception):
            Path(result.archive_path).unlink(missing_ok=True)
        # The PasarGuard archive is a copy we made; the panel keeps its own in
        # /opt/pasarguard/backup, so dropping ours frees the disk safely.
        if result.pg_path:
            with contextlib.suppress(Exception):
                Path(result.pg_path).unlink(missing_ok=True)
    else:
        # Not delivered → keep the archives on disk as the backup of record and
        # prune old local copies so they can't fill the disk.
        note = delivery_error or "ارسال به تلگرام انجام نشد (backup_telegram_chat_id تنظیم نشده است)"
        await db.set_setting("backup_last_status", "local")
        await db.set_setting("backup_last_error", "\n".join([note, *base_errs])[:1000])
        await db.set_setting("backup_last_file", f"local:{result.archive_path.name}")
        _prune_local_backups(backup_dir, keep=3)
        _prune_local_backups(backup_dir, keep=3, pattern="pasarguard-*.zip")
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
