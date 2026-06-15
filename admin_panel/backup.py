from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import logging
import shlex
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from async_storefront.db import AsyncDatabase
from async_storefront.panel import PanelClient
from async_storefront.pasarguard import PasarGuardClient
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
    pg_path: Path | None = None
    pg_db_path: Path | None = None


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


async def _build_pg_client(db: AsyncDatabase) -> "PasarGuardClient | None":
    """Build a PasarGuard client from settings for the backup snapshot, or None
    if PasarGuard is disabled / not fully configured."""
    enabled = str(await db.get_setting("pg_enabled", "0") or "0").strip().lower() in {"1", "true", "on", "yes"}
    base = (await db.get_setting("pg_base_url", "")).strip().rstrip("/")
    user = (await db.get_setting("pg_username", "")).strip()
    pwd = (await db.get_setting("pg_password", "")).strip()
    if not (enabled and base and user and pwd):
        return None
    verify = str(await db.get_setting("pg_verify_tls", "1") or "1").strip().lower() not in {"0", "false", "off", "no"}
    try:
        return PasarGuardClient(base_url=base, username=user, password=pwd, verify_tls=verify)
    except Exception:
        LOG.exception("failed to build PasarGuard client for backup")
        return None


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
    if result.pg_path:
        includes.append("سرور PasarGuard (JSON)")
    if result.pg_db_path:
        includes.append("دیتابیس کامل PasarGuard")
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


async def export_pasarguard_snapshot(client: "PasarGuardClient", work_dir: Path, stamp: str, *, max_users: int = 100000) -> Path:
    """Logical backup of the PasarGuard panel via its API: system info, all
    groups, all admins, and every user (paged) — written as JSON so the accounts
    can be inspected/recreated if the panel is lost."""
    async def _safe(coro, default):
        try:
            return await coro
        except Exception:
            return default

    system = await _safe(client.system_info(), {})
    groups = await _safe(client.list_groups(), [])
    admins = await _safe(client.list_admins(), [])
    users: list[Any] = []
    offset, batch, total = 0, 1000, None
    while len(users) < max_users:
        page = await client.list_users(offset=offset, limit=batch)
        chunk = page.get("users") or []
        users.extend(chunk)
        if total is None:
            total = int(page.get("total") or 0)
        offset += batch
        if not chunk or (total is not None and len(users) >= total):
            break
    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "panel_version": (system or {}).get("version"),
        "counts": {"users": len(users), "admins": len(admins), "groups": len(groups), "reported_total_users": total},
        "system": system,
        "groups": groups,
        "admins": admins,
        "users": users,
    }
    return await _write_bytes(work_dir / f"pasarguard-{stamp}.json", await _json_bytes(payload))


DEFAULT_DB_DUMP_TIMEOUT_SECONDS = 600


async def _autodetect_pg_container() -> str:
    """Best-effort: name of the running Postgres/TimescaleDB container."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "ps", "--format", "{{.Names}}\t{{.Image}}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
    except Exception:
        return ""
    for line in out.decode("utf-8", "ignore").splitlines():
        name, _, image = line.partition("\t")
        if any(k in image.lower() for k in ("timescale", "postgres", "postgis")):
            return name.strip()
    return ""


async def export_pasarguard_db_dump(
    work_dir: Path,
    stamp: str,
    *,
    dump_cmd: str = "",
    container: str = "",
    user: str = "pasarguard",
    name: str = "pasarguard",
    timeout_seconds: int = DEFAULT_DB_DUMP_TIMEOUT_SECONDS,
) -> Path:
    """Full SQL dump of the PasarGuard Postgres database, gzipped.

    If ``dump_cmd`` is provided it is executed as-is via the host shell (it MUST
    write the SQL dump to STDOUT; it may even shell out over ssh to a remote
    master). Otherwise a ``docker exec <container> pg_dump -U <user> <db>``
    command is built, auto-detecting the Postgres container when not given.
    """
    if dump_cmd.strip():
        argv = ["bash", "-lc", dump_cmd.strip()]
        label = "custom"
    else:
        cont = container.strip() or await _autodetect_pg_container()
        if not cont:
            raise RuntimeError("کانتینر دیتابیس پیدا نشد؛ نام کانتینر یا یک دستور دلخواه را در تنظیمات وارد کنید.")
        argv = ["docker", "exec", "-i", cont, "pg_dump", "-U", user, name]
        label = f"docker:{cont}"
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=max(30, int(timeout_seconds)))
    except asyncio.TimeoutError:
        with contextlib.suppress(Exception):
            proc.kill()
        raise RuntimeError(f"pg_dump timed out after {timeout_seconds}s ({label})")
    if proc.returncode != 0 or not out:
        detail = (err or b"").decode("utf-8", "ignore").strip()[:300]
        raise RuntimeError(f"pg_dump failed ({label}): {detail or 'empty output'}")
    path = work_dir / f"pasarguard-db-{stamp}.sql.gz"
    payload = out
    await asyncio.to_thread(lambda: path.write_bytes(gzip.compress(payload, compresslevel=6)))
    return path


async def create_full_backup(
    *,
    db: AsyncDatabase,
    panel: PanelClient,
    backup_dir: Path,
    include_bot: bool = True,
    include_xui: bool = True,
    include_pg: bool = False,
    pg_client: "PasarGuardClient | None" = None,
    include_pg_db: bool = False,
    pg_db_dump_cmd: str = "",
    pg_db_container: str = "",
    pg_db_user: str = "pasarguard",
    pg_db_name: str = "pasarguard",
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

    pg_path: Path | None = None
    if include_pg and pg_client is not None:
        try:
            pg_path = await export_pasarguard_snapshot(pg_client, work_dir, stamp)
        except Exception as exc:
            LOG.exception("PasarGuard backup export failed")
            errors.append(f"pasarguard backup failed: {exc}")
            pg_path = await _write_bytes(
                work_dir / f"pasarguard-error-{stamp}.json",
                await _json_bytes({"exported_at": datetime.now(timezone.utc).isoformat(), "error": str(exc)}),
            )

    pg_db_path: Path | None = None
    if include_pg_db:
        try:
            pg_db_path = await export_pasarguard_db_dump(
                work_dir, stamp,
                dump_cmd=pg_db_dump_cmd, container=pg_db_container,
                user=pg_db_user or "pasarguard", name=pg_db_name or "pasarguard",
            )
        except Exception as exc:
            LOG.exception("PasarGuard full DB dump failed")
            errors.append(f"pasarguard db dump failed: {exc}")
            pg_db_path = await _write_bytes(
                work_dir / f"pasarguard-db-error-{stamp}.json",
                await _json_bytes({"exported_at": datetime.now(timezone.utc).isoformat(), "error": str(exc)}),
            )

    settings_rows = await db.admin_list_settings()
    panel_settings = await db.get_panel_settings()
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "include_bot": include_bot,
        "include_xui": include_xui,
        "include_pg": include_pg,
        "include_pg_db": include_pg_db,
        "bot_db": bot_db_path.name if bot_db_path else None,
        "xui_export": xui_path.name if xui_path else None,
        "pasarguard_export": pg_path.name if pg_path else None,
        "pasarguard_db_dump": pg_db_path.name if pg_db_path else None,
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
            for path in (bot_db_path, xui_path, pg_path, pg_db_path, manifest_path):
                if path and path.exists():
                    zf.write(path, arcname=path.name)
        for path in work_dir.iterdir():
            path.unlink(missing_ok=True)
        work_dir.rmdir()

    await asyncio.to_thread(_zip)
    parts = [
        p for p, inc in (
            ("bot", include_bot), ("xui", include_xui),
            ("pg", include_pg and pg_path is not None),
            ("pgdb", include_pg_db and pg_db_path is not None),
        ) if inc
    ]
    mode = "full" if len(parts) > 1 else (parts[0] if parts else "empty")
    return BackupResult(
        archive_path=archive_path, bot_db_path=bot_db_path, xui_path=xui_path,
        mode=mode, errors=tuple(errors), pg_path=pg_path, pg_db_path=pg_db_path,
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
    include_pg_db = await db.get_setting("backup_include_pg_db", "0") == "1"
    pg_db_dump_cmd = str(await db.get_setting("pg_db_dump_cmd", "") or "")
    pg_db_container = str(await db.get_setting("pg_db_container", "") or "")
    pg_db_user = str(await db.get_setting("pg_db_user", "pasarguard") or "pasarguard")
    pg_db_name = str(await db.get_setting("pg_db_name", "pasarguard") or "pasarguard")
    xui_timeout_seconds = normalize_xui_backup_timeout(
        await db.get_setting("backup_xui_timeout_seconds", str(DEFAULT_XUI_BACKUP_TIMEOUT_SECONDS))
    )
    pg_client = await _build_pg_client(db) if include_pg else None
    if pg_client is None:
        include_pg = False
    if not include_bot and not include_xui and not include_pg and not include_pg_db:
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
            include_pg=include_pg,
            pg_client=pg_client,
            include_pg_db=include_pg_db,
            pg_db_dump_cmd=pg_db_dump_cmd,
            pg_db_container=pg_db_container,
            pg_db_user=pg_db_user,
            pg_db_name=pg_db_name,
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
    finally:
        if pg_client is not None:
            with contextlib.suppress(Exception):
                await pg_client.close()
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
