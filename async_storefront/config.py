from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_id: int
    bot_username: str
    db_path: Path
    backup_dir: Path
    logs_dir: Path
    proxy_url: str
    telegram_pool_size: int = 64
    panel_pool_size: int = 128
    panel_timeout_seconds: float = 20.0
    qr_workers: int = max(2, min(6, (os.cpu_count() or 2) - 1))

    @classmethod
    def from_env(cls) -> "Settings":
        base_dir = Path(os.getenv("BOT_BASE_DIR", ".")).resolve()
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("BOT_TOKEN must be provided via environment (.env) before starting the bot.")

        proxy = os.getenv("BOT_PROXY_URL", os.getenv("TELEGRAM_PROXY", "")).strip()
        return cls(
            bot_token=token,
            admin_id=int(os.getenv("ADMIN_ID", "0") or "0"),
            bot_username=os.getenv("BOT_USERNAME", "").strip(),
            db_path=Path(os.getenv("BOT_DB_PATH", str(base_dir / "navidvpn.db"))).resolve(),
            backup_dir=Path(os.getenv("BOT_BACKUP_DIR", str(base_dir / "backup"))).resolve(),
            logs_dir=Path(os.getenv("BOT_LOGS_DIR", str(base_dir / "logs"))).resolve(),
            proxy_url=proxy,
            telegram_pool_size=int(os.getenv("BOT_TELEGRAM_POOL_SIZE", "64")),
            panel_pool_size=int(os.getenv("BOT_PANEL_POOL_SIZE", "128")),
            panel_timeout_seconds=float(os.getenv("BOT_PANEL_TIMEOUT_SECONDS", "20")),
            qr_workers=int(os.getenv("BOT_QR_WORKERS", str(cls.qr_workers))),
        )
