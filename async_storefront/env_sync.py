from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping

BUSINESS_SETTING_TO_ENV = {
    "price_per_gb": "PRICE_PER_GB",
    "card_number": "CARD_NUMBER",
    "card_name": "CARD_NAME",
    "crypto_address": "CRYPTO_ADDRESS",
    "support_id": "SUPPORT_ID",
    "sales_status": "SALES_STATUS",
    "sales_status_user": "SALES_STATUS_USER",
    "sales_status_agent": "SALES_STATUS_AGENT",
    "minimum_purchase_gb": "MIN_PURCHASE_GB",
}

INFRA_SETTING_TO_ENV = {
    "admin_user_ids": "ADMIN_USER_IDS",
    "broadcast_rate_per_second": "BROADCAST_RATE_PER_SECOND",
    "broadcast_concurrency": "BROADCAST_CONCURRENCY",
    "backup_xui_timeout_seconds": "BACKUP_XUI_TIMEOUT_SECONDS",
}

SETTING_TO_ENV = {
    **BUSINESS_SETTING_TO_ENV,
    **INFRA_SETTING_TO_ENV,
}

BUSINESS_ENV_TO_SETTING = {env_key: key for key, env_key in BUSINESS_SETTING_TO_ENV.items()}
INFRA_ENV_TO_SETTING = {env_key: key for key, env_key in INFRA_SETTING_TO_ENV.items()}

PANEL_COLUMN_TO_ENV = {
    "base_url": "PANEL_BASE_URL",
    "username": "PANEL_USERNAME",
    "password": "PANEL_PASSWORD",
    "inbound_id": "PANEL_INBOUND_ID",
    "sub_link_base": "SUB_LINK_BASE",
}

PANEL_KV_TO_ENV = {
    "panel_base_url": "PANEL_BASE_URL",
    "panel_username": "PANEL_USERNAME",
    "panel_password": "PANEL_PASSWORD",
    "panel_inbound_id": "PANEL_INBOUND_ID",
    "sub_link_base": "SUB_LINK_BASE",
}

PANEL_ENV_TO_COLUMN = {env_key: column for column, env_key in PANEL_COLUMN_TO_ENV.items()}

_ENV_LINE_RE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*?)(\r?\n)?$")


def _quote(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )
    return f'"{escaped}"'


def _format_value(value: Any, previous: str = "") -> str:
    raw = str(value)
    if raw == "":
        return ""
    stripped_previous = previous.strip()
    if stripped_previous.startswith('"') or stripped_previous.startswith("'"):
        return _quote(raw)
    if raw != raw.strip() or "#" in raw or any(ch.isspace() for ch in raw):
        return _quote(raw)
    return raw


def update_env_file(path: Path, values: Mapping[str, Any]) -> None:
    env_path = Path(path)
    updates = {str(key): str(value) for key, value in values.items() if str(key).strip()}
    if not updates:
        return

    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True) if env_path.exists() else []
    seen: set[str] = set()
    output: list[str] = []

    for line in lines:
        match = _ENV_LINE_RE.match(line)
        if not match:
            output.append(line)
            continue
        prefix, key, sep, previous, newline = match.groups()
        if key not in updates:
            output.append(line)
            continue
        output.append(f"{prefix}{key}{sep}{_format_value(updates[key], previous)}{newline or os.linesep}")
        seen.add(key)

    missing = [key for key in updates if key not in seen]
    if missing and output and not output[-1].endswith(("\n", "\r")):
        output[-1] = f"{output[-1]}{os.linesep}"
    for key in missing:
        output.append(f"{key}={_format_value(updates[key])}{os.linesep}")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = env_path.with_name(f".{env_path.name}.tmp")
    tmp_path.write_text("".join(output), encoding="utf-8")
    os.replace(tmp_path, env_path)


def env_values_from_admin(
    *,
    settings: Mapping[str, Any] | None = None,
    panel: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, value in (settings or {}).items():
        env_key = SETTING_TO_ENV.get(str(key))
        if env_key:
            values[env_key] = str(value)
    for key, value in (panel or {}).items():
        normalized = str(key)
        env_key = PANEL_COLUMN_TO_ENV.get(normalized) or PANEL_KV_TO_ENV.get(normalized)
        if env_key:
            values[env_key] = str(value)
    return values


def sync_env_from_admin(
    path: Path,
    *,
    settings: Mapping[str, Any] | None = None,
    panel: Mapping[str, Any] | None = None,
) -> None:
    update_env_file(Path(path), env_values_from_admin(settings=settings, panel=panel))
