"""Reseller panels: what is on sale, and what a buyer gets.

A reseller panel is an administrator account inside the connected PasarGuard
panel, with a traffic allowance of its own. The buyer signs in there and creates
and manages their own customers, within that allowance and nothing else.

This module is the pure part — the shape of a package, its price, and the checks
that stop a broken one from being sold. Creating the account and taking the money
live in provisioning.py, and the settings that define the packages live in the
database like everything else.
"""
from __future__ import annotations

import json
import re
import secrets
from typing import Any

SETTING_ENABLED = "reseller_panels_enabled"
SETTING_PACKAGES = "reseller_packages"
SETTING_LOGIN_URL = "reseller_login_url"
SETTING_ROLE_NAME = "reseller_role_name"
SETTING_PREFIX = "reseller_username_prefix"
SETTING_TOPUP_ENABLED = "reseller_topup_enabled"

DEFAULT_ROLE_NAME = "reseller"
DEFAULT_PREFIX = "rs"
BYTES_PER_GB = 1024 ** 3

# PasarGuard validates admin passwords panel-side: at least twelve characters
# with at least two upper-case letters. Generating one that satisfies the policy
# beats discovering the rule from a rejected request.
_PW_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_PW_LOWER = "abcdefghijkmnopqrstuvwxyz"
_PW_DIGIT = "23456789"
USERNAME_RE = re.compile(r"^[a-z0-9_]{3,32}$")


def _int(value: Any, default: int = 0, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        out = int(float(str(value).strip()))
    except (TypeError, ValueError):
        out = int(default)
    if minimum is not None:
        out = max(minimum, out)
    if maximum is not None:
        out = min(maximum, out)
    return out


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "on", "yes"}


def _str(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def parse_package(raw: Any) -> dict | None:
    """One sellable package, with every field forced into range."""
    if not isinstance(raw, dict):
        return None
    title = _str(raw.get("title"))[:80]
    if not title:
        return None
    return {
        "id": _str(raw.get("id")) or f"rp_{secrets.token_hex(4)}",
        "title": title,
        # Traffic is authored in gigabytes because that is what the panel
        # measures; the bot shows terabytes once it is past a thousand.
        "traffic_gb": _int(raw.get("traffic_gb"), 0, minimum=0),
        "price": _int(raw.get("price"), 0, minimum=0),
        # 0 = no time limit. The panel has no expiry field for an admin, so the
        # bot enforces this itself by disabling the account when it lapses.
        "days": _int(raw.get("days"), 0, minimum=0),
        "user_limit": _int(raw.get("user_limit"), 0, minimum=0),
        "note": _str(raw.get("note"))[:300],
        "enabled": _bool(raw.get("enabled"), True),
        "sort": _int(raw.get("sort"), 0),
    }


def parse_packages(raw: Any) -> list[dict]:
    """The package list as stored. Malformed entries are dropped, not guessed at."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw[:60]:
        parsed = parse_package(item)
        if not parsed:
            continue
        # A duplicate id would make two packages indistinguishable on a button,
        # and the wrong one would be sold.
        if parsed["id"] in seen:
            parsed["id"] = f"rp_{secrets.token_hex(4)}"
        seen.add(parsed["id"])
        out.append(parsed)
    out.sort(key=lambda p: (p["sort"], p["title"]))
    return out


def dump_packages(packages: list[dict]) -> str:
    return json.dumps(packages, ensure_ascii=False, separators=(",", ":"))


def on_sale(packages: list[dict]) -> list[dict]:
    return [p for p in packages if p.get("enabled") and validate_package(p) == []]


def find_package(packages: list[dict], package_id: str) -> dict | None:
    wanted = _str(package_id)
    for package in packages:
        if package["id"] == wanted:
            return package
    return None


def validate_package(package: dict) -> list[str]:
    """Why this package cannot be sold. Empty list = sellable."""
    problems: list[str] = []
    if not _str(package.get("title")):
        problems.append("نام بسته خالی است.")
    if _int(package.get("price"), 0) <= 0:
        problems.append("قیمت بسته صفر است.")
    if _int(package.get("traffic_gb"), 0) <= 0:
        problems.append("حجم بسته صفر است؛ پنل بدون حجم قابل استفاده نیست.")
    return problems


def traffic_bytes(package: dict) -> int:
    return _int(package.get("traffic_gb"), 0, minimum=0) * BYTES_PER_GB


def traffic_label(gigabytes: int) -> str:
    """"۲۰ ترابایت" past a thousand gigabytes, otherwise gigabytes."""
    total = _int(gigabytes, 0, minimum=0)
    if total <= 0:
        return "بدون حجم"
    if total >= 1024 and total % 1024 == 0:
        return f"{total // 1024} ترابایت"
    if total >= 1024:
        return f"{total / 1024:.1f} ترابایت".replace(".0 ", " ")
    return f"{total} گیگابایت"


def duration_label(package: dict) -> str:
    days = _int(package.get("days"), 0, minimum=0)
    return f"{days} روز" if days > 0 else "بدون محدودیت زمانی"


def summary(package: dict) -> str:
    parts = [traffic_label(_int(package.get("traffic_gb"), 0)), duration_label(package)]
    limit = _int(package.get("user_limit"), 0)
    if limit > 0:
        parts.append(f"تا {limit} کاربر")
    return " · ".join(parts)


def button_label(package: dict) -> str:
    return f"📦 {traffic_label(_int(package.get('traffic_gb'), 0))} - {_int(package.get('price'), 0):,} تومان"


def generate_username(prefix: str, user_id: int) -> str:
    """A panel username that is unique, valid, and says nothing about the buyer
    beyond their id — no phone numbers or names end up in the panel."""
    clean = re.sub(r"[^a-z0-9_]", "", _str(prefix).lower()) or DEFAULT_PREFIX
    return f"{clean[:8]}_{int(user_id)}_{secrets.token_hex(2)}"[:32]


def generate_password(length: int = 16) -> str:
    """Meets PasarGuard's policy by construction: length, and two upper-case."""
    size = max(12, int(length))
    alphabet = _PW_UPPER + _PW_LOWER + _PW_DIGIT
    body = [secrets.choice(_PW_UPPER), secrets.choice(_PW_UPPER),
            secrets.choice(_PW_DIGIT), secrets.choice(_PW_LOWER)]
    body += [secrets.choice(alphabet) for _ in range(size - len(body))]
    secrets.SystemRandom().shuffle(body)
    return "".join(body)


async def load_packages(db) -> list[dict]:
    return parse_packages(await db.get_setting(SETTING_PACKAGES, "[]"))


async def save_packages(db, packages: list[dict]) -> list[dict]:
    normalised = parse_packages(packages)
    await db.set_setting(SETTING_PACKAGES, dump_packages(normalised))
    return normalised


async def is_enabled(db) -> bool:
    return _bool(await db.get_setting(SETTING_ENABLED, "0"))


async def login_url(db) -> str:
    return _str(await db.get_setting(SETTING_LOGIN_URL, ""))


async def role_name(db) -> str:
    return _str(await db.get_setting(SETTING_ROLE_NAME, "")) or DEFAULT_ROLE_NAME


async def username_prefix(db) -> str:
    return _str(await db.get_setting(SETTING_PREFIX, "")) or DEFAULT_PREFIX


async def topup_enabled(db) -> bool:
    return _bool(await db.get_setting(SETTING_TOPUP_ENABLED, "1"), True)
