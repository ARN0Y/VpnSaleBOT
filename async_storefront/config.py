"""Where configuration comes from.

There are exactly two tiers, and the split is deliberate:

  Runtime   — paths and pool sizes. Needed *before* a database can be opened,
              so this is the only tier the environment can speak to. Every
              value has a working default, which is why a deployment needs no
              environment file at all.

  Settings  — everything about the product: the bot token, who may sign in to
              the panel, the proxy, prices, texts. It lives in the database and
              is edited from the panel, so the operator never edits a file and
              never restarts a service to change how the shop behaves.

An earlier generation of this bot kept twenty-five environment variables, with
the panel password in plain text among them. Anything that moved between the
file and the database had to be kept in sync by hand, and drift between the two
was a recurring source of "I changed it and nothing happened".
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(str(os.getenv(name, "")).strip() or default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float, *, minimum: float = 0.1) -> float:
    try:
        return max(minimum, float(str(os.getenv(name, "")).strip() or default))
    except (TypeError, ValueError):
        return default


def _path(name: str, default: Path) -> Path:
    raw = str(os.getenv(name, "")).strip()
    return (Path(raw) if raw else default).resolve()


@dataclass(frozen=True)
class Runtime:
    """Filesystem layout and concurrency. Overridable, never required."""

    base_dir: Path
    db_path: Path
    backup_dir: Path
    logs_dir: Path
    telegram_pool_size: int = 64
    panel_pool_size: int = 128
    panel_timeout_seconds: float = 20.0
    qr_workers: int = 4

    @classmethod
    def load(cls) -> "Runtime":
        base = _path("BOT_BASE_DIR", Path(__file__).resolve().parent.parent)
        # The data directory keeps the database, its write-ahead log and the
        # backups together, so "back up the bot" is one directory.
        data = _path("BOT_DATA_DIR", base / "data")
        cpus = os.cpu_count() or 2
        return cls(
            base_dir=base,
            db_path=_path("BOT_DB_PATH", data / "bot.db"),
            backup_dir=_path("BOT_BACKUP_DIR", data / "backup"),
            logs_dir=_path("BOT_LOGS_DIR", data / "logs"),
            telegram_pool_size=_int("BOT_TELEGRAM_POOL_SIZE", 64),
            panel_pool_size=_int("BOT_PANEL_POOL_SIZE", 128),
            panel_timeout_seconds=_float("BOT_PANEL_TIMEOUT_SECONDS", 20.0),
            qr_workers=_int("BOT_QR_WORKERS", max(2, min(6, cpus - 1))),
        )

    def prepare(self) -> "Runtime":
        """Create the directories the process writes to."""
        for directory in (self.db_path.parent, self.backup_dir, self.logs_dir):
            directory.mkdir(parents=True, exist_ok=True)
        return self


@dataclass(frozen=True)
class WebRuntime:
    """How the admin panel binds to the network — a systemd concern, not a
    product setting, so it stays outside the database."""

    host: str = "127.0.0.1"
    port: int = 8080
    access_log: bool = False
    log_level: str = "info"

    @classmethod
    def load(cls) -> "WebRuntime":
        flag = str(os.getenv("ADMIN_ACCESS_LOG", "")).strip().lower()
        return cls(
            host=str(os.getenv("ADMIN_HOST", "") or "127.0.0.1").strip(),
            port=_int("ADMIN_PORT", 8080),
            access_log=flag in {"1", "true", "yes", "on"},
            log_level=str(os.getenv("ADMIN_LOG_LEVEL", "") or "info").strip(),
        )
