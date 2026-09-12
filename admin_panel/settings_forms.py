"""Shared helpers for the settings the JSON API writes.

The Jinja settings page these grew up in is gone; the React panel posts
to /admin/api/v1/settings, which normalises through the same functions so
a value can only be interpreted one way.
"""
from __future__ import annotations

import logging



from .backup import DEFAULT_XUI_BACKUP_TIMEOUT_SECONDS, normalize_xui_backup_timeout

LOG = logging.getLogger(__name__)

EDITABLE_KEYS = (
    # The bot's own connection. It lives here rather than in a file so a fresh
    # install is configured entirely from the panel.
    "bot_token",
    "bot_username",
    "proxy_url",
    "proxy_enabled",
    "price_per_gb",
    "minimum_purchase_gb",
    "card_number",
    "card_name",
    "crypto_address",
    "support_id",
    "admin_user_ids",
    "default_agent_price_per_gb",
    "broadcast_rate_per_second",
    "broadcast_concurrency",
)
INT_SETTING_KEYS = {
    "price_per_gb",
    "minimum_purchase_gb",
    "default_agent_price_per_gb",
    "broadcast_rate_per_second",
    "broadcast_concurrency",
}
SETTING_FORM_DEFAULTS = {
    "price_per_gb": "200000",
    "minimum_purchase_gb": "1",
    "default_agent_price_per_gb": "0",
    "broadcast_rate_per_second": "25",
    "broadcast_concurrency": "16",
}
BACKUP_UNITS = {"minutes", "hours", "days", "weeks"}
PANEL_FORM_KEYS = {"panel_base_url", "panel_username", "panel_password", "panel_inbound_id", "sub_link_base"}


def as_int(value, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except Exception:
        return default


def normalize_admin_ids(value) -> str:
    ids: list[str] = []
    for part in str(value or "").replace("\n", ",").split(","):
        item = part.strip()
        if item.isdigit() and item not in ids:
            ids.append(item)
    return ",".join(ids)


def settings_values_from_form(form, current: dict[str, str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for key in EDITABLE_KEYS:
        fallback = current.get(key, SETTING_FORM_DEFAULTS.get(key, ""))
        raw = form.get(key, fallback)
        if key in INT_SETTING_KEYS:
            parsed = max(0, as_int(raw, as_int(fallback, 0)))
            if key == "minimum_purchase_gb":
                parsed = min(100000, max(1, parsed))
            elif key == "broadcast_rate_per_second":
                parsed = min(28, max(1, parsed))
            elif key == "broadcast_concurrency":
                parsed = min(64, max(1, parsed))
            values[key] = str(parsed)
        elif key == "admin_user_ids":
            values[key] = normalize_admin_ids(raw)
        else:
            values[key] = str(raw or "").strip()
    return values


def backup_values_from_form(form, current: dict[str, str] | None = None) -> dict[str, str]:
    current = current or {}
    unit = str(form.get("backup_interval_unit", "minutes") or "minutes").strip().lower()
    if unit not in BACKUP_UNITS:
        unit = "minutes"
    interval_value = max(1, as_int(form.get("backup_interval_value", form.get("backup_interval_days", "20")), 20))
    xui_timeout_seconds = normalize_xui_backup_timeout(
        form.get("backup_xui_timeout_seconds", str(DEFAULT_XUI_BACKUP_TIMEOUT_SECONDS))
    )
    pg_mode = str(form.get("pg_backup_mode", current.get("pg_backup_mode", "auto")) or "auto").strip().lower()
    if pg_mode not in {"auto", "cli", "native"}:
        pg_mode = "auto"
    # An empty token means "keep the current one" — the field is rendered blank
    # (it is a secret), so saving the card must not wipe a configured token.
    token = str(form.get("backup_bot_token", "") or "").strip()
    if not token:
        token = str(current.get("backup_bot_token", "") or "")
    return {
        "backup_enabled": "1" if form.get("backup_enabled") == "on" else "0",
        "backup_include_bot": "1" if form.get("backup_include_bot") == "on" else "0",
        "backup_include_xui": "1" if form.get("backup_include_xui") == "on" else "0",
        "backup_include_pg": "1" if form.get("backup_include_pg") == "on" else "0",
        "backup_interval_value": str(interval_value),
        "backup_interval_unit": unit,
        "backup_interval_days": str(interval_value),
        "backup_xui_timeout_seconds": str(xui_timeout_seconds),
        "backup_send_to_telegram": "1",
        "backup_telegram_chat_id": str(form.get("backup_telegram_chat_id", "") or "").strip(),
        "backup_bot_token": token,
        "pg_backup_mode": pg_mode,
        "pg_backup_compose_file": str(
            form.get("pg_backup_compose_file", current.get("pg_backup_compose_file", "")) or ""
        ).strip(),
        "pg_backup_dir": str(form.get("pg_backup_dir", current.get("pg_backup_dir", "")) or "").strip(),
        "pg_backup_max_age_minutes": str(
            max(0, as_int(form.get("pg_backup_max_age_minutes", current.get("pg_backup_max_age_minutes", "360")), 360))
        ),
        "pg_backup_timeout_seconds": str(
            min(3600, max(60, as_int(form.get("pg_backup_timeout_seconds", current.get("pg_backup_timeout_seconds", "900")), 900)))
        ),
    }


def panel_values_from_form(form, current) -> dict[str, str | int]:
    current_base_url = str(current["base_url"] or "") if current else ""
    current_username = str(current["username"] or "") if current else ""
    current_password = str(current["password"] or "") if current else ""
    current_inbound_id = int(current["inbound_id"] or 0) if current else 0
    current_sub_link_base = str(current["sub_link_base"] or "") if current else ""
    return {
        "base_url": str(form.get("panel_base_url", current_base_url) or "").strip().rstrip("/"),
        "username": str(form.get("panel_username", current_username) or "").strip(),
        "password": str(form.get("panel_password", current_password) or ""),
        "inbound_id": max(0, as_int(form.get("panel_inbound_id", current_inbound_id), current_inbound_id)),
        "sub_link_base": str(form.get("sub_link_base", current_sub_link_base) or "").strip().rstrip("/"),
    }


# audience -> (broadcast target group, human label used in messages)
SALES_AUDIENCES = {
    "all": ("all", "همه کاربران"),
    "user": ("customers", "کاربران عادی"),
    "agent": ("agents", "نماینده‌ها"),
}


def normalize_sales_audience(value) -> str:
    audience = str(value or "all").strip().lower()
    return audience if audience in SALES_AUDIENCES else "all"


def sales_broadcast(new_sales_status: str, audience: str = "all") -> tuple[str, str]:
    _, label = SALES_AUDIENCES.get(audience, SALES_AUDIENCES["all"])
    scope = "" if audience == "all" else f" (مخصوص {label})"
    if new_sales_status == "closed":
        return (
            f"Sales closed broadcast ({audience})",
            (
                f"🔴 <b>اطلاعیه وضعیت فروش</b>{scope}\n\n"
                "فروش سرویس به‌صورت موقت بسته شد. سفارش جدید و تمدید تا اطلاع بعدی انجام نمی‌شود.\n"
                "به محض باز شدن فروش، همین‌جا اطلاع‌رسانی خواهد شد."
            ),
        )
    return (
        f"Sales opened broadcast ({audience})",
        (
            f"🟢 <b>اطلاعیه وضعیت فروش</b>{scope}\n\n"
            "فروش سرویس دوباره فعال شد. اکنون می‌توانید خرید جدید یا تمدید اشتراک انجام دهید."
        ),
    )

