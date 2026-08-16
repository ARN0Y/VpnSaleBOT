"""PasarGuard backup that the official ``pasarguard restore`` can consume.

The panel ships its own backup/restore pair, and its restore path is picky: it
reads ``.env`` for SQLALCHEMY_DATABASE_URL, checks ``docker-compose.yml`` to
decide whether the database is TimescaleDB, and feeds ``db_backup.sql`` through
``timescaledb_pre_restore()`` / ``timescaledb_post_restore()``. An archive that
is merely "a dump of the database" is therefore NOT restorable — restore aborts
without the .env, and a TimescaleDB dump replayed without that wrapper leaves a
broken database.

So we never invent our own format. Two strategies, both producing the exact
layout the official restore expects:

  cli     – run the installed ``pasarguard backup`` (or reuse the archive its
            cron job just wrote) and ship that. Whatever the panel considers a
            backup today is what we deliver.
  native  – same archive, built here: pg_dump straight out of the database
            container (never through pgbouncer, whose transaction pooling breaks
            pg_dump), plus .env, docker-compose.yml and pasarguard_data/.

``auto`` prefers the CLI and falls back to native, so a panel install that has
no CLI (or a broken one) still produces a restorable backup.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

DEFAULT_COMPOSE_FILE = "/opt/pasarguard/docker-compose.yml"
DEFAULT_BACKUP_DIR = "/opt/pasarguard/backup"
DEFAULT_CLI = "/usr/local/bin/pasarguard"
DEFAULT_MAX_AGE_MINUTES = 360  # the stock cron runs every 6h; reuse within that
DEFAULT_TIMEOUT_SECONDS = 900

# Files the official restore reads. Missing .env or db_backup.sql = unrestorable.
REQUIRED_MEMBERS = ("db_backup.sql", ".env")

RESTORE_NOTE = """\
PasarGuard backup — how to restore
==================================

This archive is in the exact format the official PasarGuard CLI expects, so a
restore is simply:

    pasarguard restore /path/to/{name}

Run it ON THE PASARGUARD SERVER as root. The CLI stops the panel, drops and
recreates the database, wraps the load in timescaledb_pre_restore() /
timescaledb_post_restore() when the database is TimescaleDB, restores
pasarguard_data/ (certs + subscription templates) and brings the panel back up.

Contents
--------
  db_backup.sql      full pg_dump of the panel database (--clean --if-exists)
  .env               panel environment — REQUIRED, restore aborts without it
  docker-compose.yml the stack definition restore uses to detect TimescaleDB
  pasarguard_data/   certificates and subscription templates

If the archive arrived split as .partNN.zip pieces, rebuild it first:

    cat {name}.part*.zip > {name}
    unzip -t {name}          # verify before restoring

Taken: {created}
Source: {mode}
"""


@dataclass(frozen=True)
class PgExportResult:
    path: Path
    mode: str          # how it was produced: cli_reused / cli_ran / native
    db_bytes: int      # size of db_backup.sql inside the archive (0 = missing!)
    restorable: bool   # every member the official restore needs is present
    detail: str = ""


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except Exception:
        return default


async def _run(cmd: list[str], *, timeout: int, stdout_path: Path | None = None) -> tuple[int, str]:
    """Run a command, optionally streaming stdout to a file. Returns (rc, stderr)."""
    out = None
    try:
        if stdout_path is not None:
            out = stdout_path.open("wb")
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=out, stderr=asyncio.subprocess.PIPE
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        try:
            stdout_data, stderr_data = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise RuntimeError(f"timed out after {timeout}s: {' '.join(cmd[:3])}")
        err = (stderr_data or b"").decode("utf-8", "replace").strip()
        if stdout_path is None and proc.returncode == 0:
            err = (stdout_data or b"").decode("utf-8", "replace").strip()
        return int(proc.returncode or 0), err
    finally:
        if out is not None:
            out.close()


def parse_database_url(url: str) -> dict[str, str]:
    """Pull user/password/db out of SQLALCHEMY_DATABASE_URL.

    The URL points at pgbouncer, but the credentials are the ones the database
    container itself accepts, which is what pg_dump needs.
    """
    raw = str(url or "").strip().strip('"').strip("'")
    m = re.match(r"^[a-z+]+://(?P<user>[^:/@]+):(?P<password>[^@]*)@(?P<host>[^:/]+)(?::(?P<port>\d+))?/(?P<db>[^?]+)", raw)
    if not m:
        return {}
    return {
        "user": m.group("user"),
        "password": m.group("password"),
        "host": m.group("host"),
        "port": m.group("port") or "5432",
        "db": m.group("db"),
    }


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def archive_report(path: Path) -> tuple[int, bool, str]:
    """Inspect a produced archive: (db_backup.sql size, restorable, detail).

    A backup that cannot be restored is worse than no backup, because it is
    trusted. So every archive is verified before we call it a success.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            bad = zf.testzip()
            if bad is not None:
                return 0, False, f"archive is corrupt at {bad}"
            sizes = {i.filename: i.file_size for i in zf.infolist()}
    except Exception as exc:
        return 0, False, f"archive unreadable: {exc}"

    def _find(member: str) -> str | None:
        for name in names:
            if name == member or name.endswith("/" + member):
                return name
        return None

    missing = [m for m in REQUIRED_MEMBERS if _find(m) is None]
    db_member = _find("db_backup.sql")
    db_bytes = int(sizes.get(db_member, 0)) if db_member else 0
    if missing:
        return db_bytes, False, "missing from archive: " + ", ".join(missing)
    if db_bytes <= 0:
        return 0, False, "db_backup.sql is empty — the database dump failed"
    return db_bytes, True, ""


def _newest_archive(backup_dir: Path) -> Path | None:
    try:
        files = [p for p in backup_dir.glob("backup_*.zip") if p.is_file() and ".part" not in p.name]
        if not files:
            return None
        return max(files, key=lambda p: p.stat().st_mtime)
    except Exception:
        return None


async def _export_via_cli(
    *, cli: Path, backup_dir: Path, max_age_minutes: int, timeout: int
) -> tuple[Path, str]:
    """Reuse a fresh archive from the panel's own backup job, else run the CLI."""
    max_age = max(0, int(max_age_minutes)) * 60
    existing = _newest_archive(backup_dir)
    if existing and max_age > 0:
        age = max(0.0, datetime.now(timezone.utc).timestamp() - existing.stat().st_mtime)
        if age <= max_age:
            LOG.info("PasarGuard backup: reusing %s (%.0f min old)", existing.name, age / 60)
            return existing, "cli_reused"

    before = existing.name if existing else ""
    rc, err = await _run([str(cli), "backup"], timeout=timeout)
    produced = _newest_archive(backup_dir)
    if produced is None or (before and produced.name == before and rc != 0):
        raise RuntimeError(f"`pasarguard backup` failed (rc={rc}): {err[:400]}")
    if rc != 0:
        # The CLI reports non-zero for partial runs; keep the archive but say so.
        LOG.warning("pasarguard backup returned rc=%s: %s", rc, err[:200])
    return produced, "cli_ran"


async def _detect_db_container(compose_file: Path, timeout: int) -> str:
    """Find the database container, preferring compose over a guessed name."""
    project = compose_file.parent.name or "pasarguard"
    for compose_cmd in (["docker", "compose"], ["docker-compose"]):
        rc, out = await _run(
            [*compose_cmd, "-f", str(compose_file), "-p", project, "ps", "-q", "timescaledb"],
            timeout=min(timeout, 60),
        )
        cid = (out or "").strip().splitlines()
        if rc == 0 and cid and cid[0].strip():
            return cid[0].strip()
    # Fall back to the conventional compose container names.
    for name in (f"{project}-timescaledb-1", f"{project}-postgresql-1", f"{project}-db-1"):
        rc, _ = await _run(["docker", "inspect", "-f", "{{.State.Running}}", name], timeout=30)
        if rc == 0:
            return name
    raise RuntimeError("could not find the PasarGuard database container")


async def _export_native(
    *, compose_file: Path, work_dir: Path, stamp: str, timeout: int
) -> tuple[Path, str]:
    """Build a restore-compatible archive without the CLI."""
    if not compose_file.exists():
        raise RuntimeError(f"docker-compose.yml not found at {compose_file}")
    env_file = compose_file.parent / ".env"
    if not env_file.exists():
        raise RuntimeError(f".env not found next to {compose_file} — restore requires it")
    env = read_env_file(env_file)
    creds = parse_database_url(env.get("SQLALCHEMY_DATABASE_URL", ""))
    if not creds:
        raise RuntimeError("SQLALCHEMY_DATABASE_URL missing or unparsable in the panel .env")

    container = await _detect_db_container(compose_file, timeout)
    staging = work_dir / f"pg-native-{stamp}"
    staging.mkdir(parents=True, exist_ok=True)
    dump_path = staging / "db_backup.sql"

    # Straight at the database container: pgbouncer's transaction pooling makes
    # pg_dump fail or silently produce an inconsistent dump.
    # --clean --if-exists matches what the official restore expects to replay.
    rc, err = await _run(
        [
            "docker", "exec", "-e", f"PGPASSWORD={creds['password']}", container,
            "pg_dump", "-U", creds["user"], "-d", creds["db"], "--clean", "--if-exists",
        ],
        timeout=timeout,
        stdout_path=dump_path,
    )
    if rc != 0 or not dump_path.exists() or dump_path.stat().st_size == 0:
        raise RuntimeError(f"pg_dump failed (rc={rc}): {err[:400]}")

    shutil.copy2(env_file, staging / ".env")
    shutil.copy2(compose_file, staging / "docker-compose.yml")

    # pasarguard_data holds certs and subscription templates. The database
    # volumes live under it too and must NOT be copied — they are already in the
    # dump and would multiply the archive size.
    data_dir = Path("/var/lib/pasarguard")
    if data_dir.is_dir():
        dest = staging / "pasarguard_data"
        skip = {"xray-core", "mysql", "mariadb", "postgresql", "timescaledb", "pgadmin"}
        with contextlib.suppress(Exception):
            shutil.copytree(
                data_dir, dest,
                ignore=lambda d, names: {n for n in names if n in skip},
                dirs_exist_ok=True, symlinks=True,
            )

    archive = work_dir / f"backup_{stamp}.zip"

    def _zip() -> None:
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for item in sorted(staging.rglob("*")):
                if item.is_file():
                    zf.write(item, arcname=str(item.relative_to(staging)))

    await asyncio.to_thread(_zip)
    with contextlib.suppress(Exception):
        shutil.rmtree(staging, ignore_errors=True)
    return archive, "native"


async def export_pasarguard_backup(
    *,
    work_dir: Path,
    stamp: str,
    mode: str = "auto",
    compose_file: str | Path = DEFAULT_COMPOSE_FILE,
    backup_dir: str | Path = DEFAULT_BACKUP_DIR,
    cli_path: str | Path = DEFAULT_CLI,
    max_age_minutes: int = DEFAULT_MAX_AGE_MINUTES,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> PgExportResult:
    """Produce a PasarGuard archive that ``pasarguard restore`` accepts.

    Raises when no strategy could produce a *verified* restorable archive — a
    backup nobody can restore must not be reported as a success.
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    compose_file = Path(compose_file)
    backup_dir = Path(backup_dir)
    cli = Path(cli_path)
    timeout = max(60, _as_int(timeout_seconds, DEFAULT_TIMEOUT_SECONDS))
    chosen = str(mode or "auto").strip().lower()

    strategies: list[str] = []
    if chosen in ("auto", "cli") and cli.exists() and os.access(cli, os.X_OK):
        strategies.append("cli")
    if chosen in ("auto", "native"):
        strategies.append("native")
    if chosen == "cli" and not strategies:
        raise RuntimeError(f"PasarGuard CLI not available at {cli}")
    if not strategies:
        raise RuntimeError(f"no usable PasarGuard backup strategy for mode={chosen!r}")

    failures: list[str] = []
    for strategy in strategies:
        try:
            if strategy == "cli":
                source, how = await _export_via_cli(
                    cli=cli, backup_dir=backup_dir,
                    max_age_minutes=_as_int(max_age_minutes, DEFAULT_MAX_AGE_MINUTES),
                    timeout=timeout,
                )
                # Copy out of the panel's backup dir: its own retention prunes
                # that folder, and we must not delete the panel's copy.
                target = work_dir / f"pasarguard-{stamp}.zip"
                await asyncio.to_thread(shutil.copy2, source, target)
            else:
                produced, how = await _export_native(
                    compose_file=compose_file, work_dir=work_dir, stamp=stamp, timeout=timeout
                )
                target = work_dir / f"pasarguard-{stamp}.zip"
                if produced != target:
                    await asyncio.to_thread(shutil.move, str(produced), str(target))

            db_bytes, restorable, detail = await asyncio.to_thread(archive_report, target)
            if not restorable:
                failures.append(f"{strategy}: {detail}")
                with contextlib.suppress(Exception):
                    target.unlink(missing_ok=True)
                continue

            await asyncio.to_thread(_append_restore_note, target, how)
            LOG.info(
                "PasarGuard backup ready via %s: %s (db dump %.1f MB)",
                how, target.name, db_bytes / (1024 * 1024),
            )
            return PgExportResult(path=target, mode=how, db_bytes=db_bytes, restorable=True, detail=detail)
        except Exception as exc:
            LOG.warning("PasarGuard backup strategy %s failed: %s", strategy, exc)
            failures.append(f"{strategy}: {exc}")

    raise RuntimeError("PasarGuard backup failed — " + " | ".join(failures)[:600])


def _append_restore_note(archive: Path, mode: str) -> None:
    """Drop RESTORE.txt in beside the data so whoever downloads it knows what to do."""
    with contextlib.suppress(Exception):
        with zipfile.ZipFile(archive, "a", compression=zipfile.ZIP_DEFLATED) as zf:
            if "RESTORE.txt" in zf.namelist():
                return
            zf.writestr(
                "RESTORE.txt",
                RESTORE_NOTE.format(
                    name=archive.name,
                    created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    mode=mode,
                ),
            )
