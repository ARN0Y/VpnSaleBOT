from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import Request
from fastapi.templating import Jinja2Templates

from async_storefront.db import AsyncDatabase
from async_storefront.panel import PanelClient

from ..auth import csrf_token, current_admin_username

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


def templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def toman(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except Exception:
        return "0"


def gb_from_bytes(value: Any) -> str:
    try:
        raw = max(0, int(value or 0))
    except Exception:
        raw = 0
    return f"{raw / (1024 ** 3):,.2f}"


def jalaliish(ts: Any) -> str:
    try:
        value = int(ts or 0)
    except Exception:
        value = 0
    if value <= 0:
        return "-"
    return datetime.fromtimestamp(value, ZoneInfo("Asia/Tehran")).strftime("%Y/%m/%d %H:%M")


def render(request: Request, template: str, context: dict[str, Any]):
    db_path = getattr(request.app.state, "db_path", None)
    base = {
        "request": request,
        "toman": toman,
        "gb": gb_from_bytes,
        "date": jalaliish,
        "path": request.url.path,
        "db_name": getattr(db_path, "name", "navidvpn.db"),
        "csrf_token": csrf_token(request),
        "admin_username": current_admin_username(request),
    }
    base.update(context)
    return templates(request).TemplateResponse(request=request, name=template, context=base)


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def render_fragment(request: Request, template: str, context: dict[str, Any], headers: dict[str, str] | None = None):
    base = {
        "request": request,
        "toman": toman,
        "gb": gb_from_bytes,
        "date": jalaliish,
        "path": request.url.path,
        "csrf_token": csrf_token(request),
        "admin_username": current_admin_username(request),
    }
    base.update(context)
    return templates(request).TemplateResponse(request=request, name=template, context=base, headers=headers)


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
