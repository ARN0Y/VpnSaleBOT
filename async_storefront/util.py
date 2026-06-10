from __future__ import annotations

import os
import re
import secrets
import string
import time
from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

IRAN_TZ = ZoneInfo("Asia/Tehran")
_TRUTHY = {"1", "true", "yes", "on", "y", "t"}
_FALSY = {"0", "false", "no", "off", "n", "f"}


def env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean-ish environment variable.

    Empty / unset falls back to ``default``. Unknown values also fall back so a
    typo can never silently flip behaviour. Accepts 1/true/yes/on and 0/false/no/off.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value == "":
        return default
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    return default


def resolve_proxy_url() -> str:
    """Decide the proxy URL the bot should use to reach Telegram.

    Behaviour is controlled by two env vars:
      * ``BOT_PROXY_URL`` (or legacy ``TELEGRAM_PROXY``) — the proxy address.
      * ``BOT_USE_PROXY`` — explicit on/off switch.

    When ``BOT_USE_PROXY`` is unset we stay backward compatible: the proxy is
    used whenever a URL is configured. When it is set, it wins — so an operator
    can keep the URL on file but turn the proxy off (or on) without deleting it.
    """
    return resolve_proxy_value(
        os.getenv("BOT_PROXY_URL", os.getenv("TELEGRAM_PROXY", "")),
        os.getenv("BOT_USE_PROXY"),
    )


def resolve_proxy_value(proxy_url: str | None, use_proxy_raw: str | None) -> str:
    """Same on/off/auto logic as ``resolve_proxy_url`` but for values that come
    from anywhere (e.g. the settings table for a second panel), not just env.

    * ``use_proxy_raw`` truthy  → use the proxy URL.
    * ``use_proxy_raw`` falsy   → no proxy (direct), even if a URL is set.
    * unset / blank / unknown   → auto: use the proxy iff a URL is present.
    """
    url = (proxy_url or "").strip()
    raw = (use_proxy_raw or "").strip().lower()
    if raw not in _TRUTHY and raw not in _FALSY:
        return url
    return url if raw in _TRUTHY else ""


SUB_ID_CHARSET = string.ascii_letters + string.digits
SUB_ID_LENGTH = 16
CLIENT_EMAIL_SUFFIX_BYTES = 3


def now_ts() -> int:
    return int(time.time())


def now_ms() -> int:
    return int(time.time() * 1000)


def iran_day_bounds_ts() -> tuple[int, int]:
    today = datetime.now(IRAN_TZ).date()
    start = datetime.combine(today, dt_time.min, tzinfo=IRAN_TZ)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def gb_to_bytes(gb: int) -> int:
    return int(gb) * 1024 * 1024 * 1024


def ceil_gb_from_bytes(num_bytes: int) -> int:
    if num_bytes <= 0:
        return 0
    return (int(num_bytes) + 1024 * 1024 * 1024 - 1) // (1024 * 1024 * 1024)


def safe_int(value: object, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def generate_sub_id() -> str:
    return "".join(secrets.choice(SUB_ID_CHARSET) for _ in range(SUB_ID_LENGTH))


def sanitize_client_name(raw: str | None) -> str:
    value = (raw or "").strip()
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^A-Za-z0-9_.-]", "", value)
    return value[:32]


def make_unique_client_name(user_id: int, preferred_name: str | None, existing_lower: set[str]) -> str:
    """Build a client email/name that is guaranteed globally unique.

    The 3x-ui panel keys clients by their email, so two clients with the same
    name make xray raise ``user already exists`` and corrupt the inbound. To make
    collisions impossible — even across concurrent requests or a stale inbound
    cache — we always append a random token to the (sanitized) base name and
    re-roll that token if it still clashes with an existing name.
    """
    base = sanitize_client_name(preferred_name)
    if not base:
        base = f"u{int(user_id)}"
    # Keep room for the unique suffix inside the 32-char client-name limit.
    base = base[:24].rstrip("._-") or f"u{int(user_id)}"
    for _ in range(64):
        token = secrets.token_hex(CLIENT_EMAIL_SUFFIX_BYTES)
        candidate = f"{base}_{token}"
        if candidate.lower() not in existing_lower:
            existing_lower.add(candidate.lower())
            return candidate
    # Astronomically unlikely fallback: widen the entropy until unique.
    while True:
        candidate = f"{base}_{secrets.token_hex(CLIENT_EMAIL_SUFFIX_BYTES * 3)}"
        if candidate.lower() not in existing_lower:
            existing_lower.add(candidate.lower())
            return candidate

