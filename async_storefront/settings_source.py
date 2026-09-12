"""Reading product configuration out of the database.

These are the few settings both processes need before they can do anything —
the bot token, the proxy — kept here so neither the bot nor the panel has to
know how they are stored, and so there is one place that defines what happens
when they are missing.
"""
from __future__ import annotations

from .util import resolve_proxy_value

SETTING_BOT_TOKEN = "bot_token"
SETTING_BOT_USERNAME = "bot_username"
SETTING_PROXY_URL = "proxy_url"
SETTING_PROXY_ENABLED = "proxy_enabled"


async def resolve_proxy_url(db) -> str:
    """The proxy the bot should use to reach Telegram, or "" for a direct
    connection. The address is kept even when the proxy is switched off, so
    turning it back on does not mean typing it again."""
    return resolve_proxy_value(
        await db.get_setting(SETTING_PROXY_URL, ""),
        await db.get_setting(SETTING_PROXY_ENABLED, ""),
    )


async def bot_token(db) -> str:
    return str(await db.get_setting(SETTING_BOT_TOKEN, "") or "").strip()


async def set_bot_token(db, token: str) -> None:
    await db.set_setting(SETTING_BOT_TOKEN, str(token or "").strip())


async def bot_username(db) -> str:
    return str(await db.get_setting(SETTING_BOT_USERNAME, "") or "").strip().lstrip("@")
