from __future__ import annotations

import logging

import httpx
from fastapi import Request

from async_storefront.db import AsyncDatabase
from async_storefront.panel import PanelClient


LOG = logging.getLogger(__name__)


def db(request: Request) -> AsyncDatabase:
    return request.app.state.db


def panel(request: Request) -> PanelClient:
    existing = getattr(request.app.state, "panel", None)
    if existing is not None:
        return existing
    created = PanelClient(
        db(request),
        pool_size=16,
        timeout_seconds=getattr(request.app.state, "panel_timeout_seconds", 45.0),
    )
    request.app.state.panel = created
    LOG.warning("admin PanelClient was missing from app.state; created lazily")
    return created


async def notify_telegram_user(request: Request, user_id: int, text: str) -> bool:
    token = request.app.state.bot_token
    if not token:
        return False
    proxy = request.app.state.proxy_url or None
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=20, trust_env=False) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": int(user_id), "text": text, "parse_mode": "HTML"},
            )
            response.raise_for_status()
            return True
    except Exception:
        LOG.exception("failed to notify telegram user_id=%s", user_id)
        return False
