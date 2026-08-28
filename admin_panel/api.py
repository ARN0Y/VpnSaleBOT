"""JSON API (v1) for the React/shadcn admin dashboard.

Mounted under ``/admin/api/v1`` so it sits behind the same session-cookie auth
middleware as the rest of the panel. The SPA calls these endpoints with the
session cookie (sent automatically, same-origin); mutating calls must also send
the ``x-csrf-token`` header, which the middleware validates.

This is the data backbone for the new dashboard. The legacy Jinja panel keeps
working in parallel until the SPA fully replaces it (strangler migration).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import csv
import io
import secrets
import string
import time
from datetime import datetime
from zoneinfo import ZoneInfo

try:  # optional: only affects how dates are labelled in exports
    import jdatetime
except Exception:  # pragma: no cover - the fallback is Gregorian
    jdatetime = None

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from async_storefront import catalog
from async_storefront.db import AsyncDatabase
from async_storefront.models import AgentAccess
from async_storefront.pasarguard import PasarGuardClient

from .auth import COOKIE_NAME, current_admin_username, csrf_token, sign_session
from .routers.common import db, notify_telegram_user, panel
from .routers.settings import (
    PANEL_FORM_KEYS,
    SALES_AUDIENCES,
    backup_values_from_form,
    normalize_sales_audience,
    panel_values_from_form,
    sales_broadcast,
    settings_values_from_form,
    sync_env,
)

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/api/v1")


async def _json_body(request: Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

# Paths the auth middleware must let through unauthenticated (see auth.py).
PUBLIC_API_PATHS = {"/admin/api/v1/login"}


@router.post("/login")
async def login(request: Request):
    config = request.app.state.auth_config
    limiter = request.app.state.auth_limiter
    from .auth import client_key

    ip = client_key(request)
    if limiter.is_blocked(ip):
        return JSONResponse(
            {"ok": False, "error": "too_many_attempts"}, status_code=429
        )
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    valid = secrets.compare_digest(username, config.username) and secrets.compare_digest(
        password, config.password
    )
    if not valid:
        limiter.record_failure(ip)
        return JSONResponse({"ok": False, "error": "invalid_credentials"}, status_code=401)

    limiter.record_success(ip)
    csrf = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + config.ttl_seconds
    token = sign_session(
        {"u": username, "exp": expires_at, "csrf": csrf, "n": secrets.token_urlsafe(12)},
        config.secret,
    )
    response = JSONResponse({"ok": True, "username": username, "csrf": csrf})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=config.ttl_seconds,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout")
async def logout(request: Request):
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/me")
async def me(request: Request):
    return {
        "username": current_admin_username(request),
        "csrf": csrf_token(request),
    }


@router.get("/dashboard")
async def dashboard(request: Request, q: str = ""):
    database = db(request)
    metrics = await database.dashboard_stats()
    recent_orders = await database.admin_list_orders(q, limit=12)
    return {"metrics": metrics, "recent_orders": recent_orders}


def _order_filter_args(request: Request) -> dict:
    """Read the orders filter off the query string.

    Unknown enum values are rejected rather than ignored: silently dropping a
    mistyped ``status`` would show the operator unfiltered revenue under a
    filtered heading, which is worse than an error.
    """
    qp = request.query_params

    def _int(name: str) -> int:
        raw = str(qp.get(name) or "").strip()
        if not raw:
            return 0
        try:
            return max(0, int(float(raw)))
        except ValueError:
            raise _BadFilter(name)

    def _enum(name: str, allowed: tuple[str, ...]) -> str:
        raw = str(qp.get(name) or "").strip().lower()
        if not raw or raw == "all":
            return ""
        if raw not in allowed:
            raise _BadFilter(name)
        return raw

    date_from = _int("from")
    date_to = _int("to")
    if date_from and date_to and date_to <= date_from:
        raise _BadFilter("range")
    return {
        "search": str(qp.get("q") or "").strip(),
        "period": str(qp.get("period") or "all").strip() or "all",
        "date_from": date_from,
        "date_to": date_to,
        "status": _enum("status", AsyncDatabase.ORDER_STATUSES),
        "order_type": _enum("type", AsyncDatabase.ORDER_TYPES),
        "payment_method": _enum("method", AsyncDatabase.ORDER_PAYMENT_METHODS),
        "user_id": _int("user_id"),
        "min_amount": _int("min_amount"),
        "max_amount": _int("max_amount"),
        "sort": _enum("sort", ORDER_SORTS) or "newest",
    }


class _BadFilter(Exception):
    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.field = field


ORDER_SORTS = ("newest", "oldest", "amount_desc", "amount_asc")


@router.get("/orders")
async def orders(request: Request, page: int = 1, page_size: int = 20):
    try:
        filters = _order_filter_args(request)
    except _BadFilter as exc:
        return JSONResponse({"ok": False, "error": "bad_filter", "field": exc.field}, status_code=400)
    pg = max(1, int(page))
    ps = max(1, min(100, int(page_size)))
    report = await db(request).admin_orders_report(limit=ps, offset=(pg - 1) * ps, **filters)
    return {
        "items": report["items"],
        "summary": report["summary"],
        "page": pg,
        "page_size": ps,
        "has_more": report["has_more"],
    }


@router.get("/orders/export.csv")
async def orders_export(request: Request):
    """The filtered orders as a spreadsheet, exactly as shown on screen."""
    try:
        filters = _order_filter_args(request)
    except _BadFilter as exc:
        return JSONResponse({"ok": False, "error": "bad_filter", "field": exc.field}, status_code=400)
    rows = await db(request).admin_orders_export_rows(limit=20000, **filters)
    headers = [
        ("order_id", "شناسه سفارش"),
        ("created_at", "تاریخ شمسی"),
        ("created_at_greg", "تاریخ میلادی"),
        ("user_id", "شناسه کاربر"),
        ("first_name", "نام"),
        ("username", "یوزرنیم"),
        ("subscription_name", "نام سرویس"),
        ("subscription_id", "شناسه اشتراک"),
        ("order_type_label", "نوع"),
        ("gb", "حجم (GB)"),
        ("qty", "تعداد"),
        ("unit_price", "قیمت واحد"),
        ("discount_code", "کد تخفیف"),
        ("discount_amount", "تخفیف"),
        ("final_price", "مبلغ نهایی"),
        ("payment_method", "روش پرداخت"),
        ("status", "وضعیت"),
    ]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([label for _, label in headers])
    for row in rows:
        out = []
        for key, _ in headers:
            if key == "created_at":
                value = _jalali_label(int(row.get("created_at") or 0))
            elif key == "created_at_greg":
                value = _tehran_label(int(row.get("created_at") or 0))
            else:
                value = row.get(key)
            out.append("" if value is None else str(value))
        writer.writerow(out)
    # BOM so Excel opens the Persian headers in UTF-8 instead of mojibake.
    payload = ("\ufeff" + buf.getvalue()).encode("utf-8")
    stamp = datetime.now(TEHRAN).strftime("%Y%m%d-%H%M%S")
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"content-disposition": f'attachment; filename="orders-{stamp}.csv"'},
    )


# The panel renders every date in Tehran time; the server runs on UTC. An
# export stamped in UTC would be three and a half hours away from the row the
# operator is reconciling it against.
TEHRAN = ZoneInfo("Asia/Tehran")


def _tehran_label(ts: int) -> str:
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(int(ts), TEHRAN).strftime("%Y-%m-%d %H:%M:%S")


def _jalali_label(ts: int) -> str:
    """The date as the operator reads it. Falls back to Gregorian without jdatetime."""
    if ts <= 0:
        return ""
    moment = datetime.fromtimestamp(int(ts), TEHRAN)
    if jdatetime is not None:
        return jdatetime.datetime.fromgregorian(datetime=moment).strftime("%Y/%m/%d %H:%M")
    return moment.strftime("%Y-%m-%d %H:%M")


@router.get("/users")
async def users(request: Request, q: str = "", filter: str = "all", page: int = 1, page_size: int = 20):
    filter_by = filter if filter in {"all", "agents", "users"} else "all"
    pg = max(1, int(page))
    ps = max(1, min(100, int(page_size)))
    rows = await db(request).admin_list_users(q, filter_by, limit=ps + 1, offset=(pg - 1) * ps)
    return {"items": rows[:ps], "page": pg, "page_size": ps, "has_more": len(rows) > ps}


@router.get("/topups")
async def topups(request: Request, status: str = "pending"):
    rows = await db(request).admin_list_topups(status)
    return {"items": rows, "count": len(rows)}


@router.get("/agent-requests")
async def agent_requests(request: Request, status: str = "pending"):
    rows = await db(request).admin_list_agent_requests(status)
    return {"items": rows, "count": len(rows)}


@router.get("/settings")
async def settings(request: Request):
    database = db(request)
    items = {row["key"]: row["value"] for row in await database.admin_list_settings()}
    # Merge live panel settings (different table) so the SPA pre-fills real
    # values; password is never exposed and blank means "keep unchanged".
    panel_row = await database.get_panel_settings()
    if panel_row:
        items["panel_base_url"] = str(panel_row["base_url"] or "")
        items["panel_username"] = str(panel_row["username"] or "")
        items["panel_inbound_id"] = str(panel_row["inbound_id"] or 0)
        items["sub_link_base"] = str(panel_row["sub_link_base"] or "")
    items["panel_password"] = ""
    items["pg_password"] = ""  # PasarGuard admin password — never exposed
    # The backup bot token controls the archive channel — never send it to the
    # browser. Report only whether one is configured so the UI can say so.
    items["backup_bot_token_set"] = "1" if str(items.get("backup_bot_token") or "").strip() else "0"
    items["backup_bot_token"] = ""
    return {"items": items}


@router.get("/subscriptions")
async def subscriptions(request: Request, q: str = "", page: int = 1, page_size: int = 30):
    database = db(request)
    safe_page = max(1, int(page))
    ps = max(1, min(100, int(page_size)))
    rows = await database.admin_search_subscriptions(q, safe_page, ps)
    total = await database.admin_search_subscriptions_count(q)
    return {"items": rows, "total": total, "page": safe_page, "page_size": ps}


@router.get("/users/{user_id}")
async def user_detail(request: Request, user_id: int):
    row = await db(request).admin_user_detail(user_id)
    if not row:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return row


# ─────────────────────────── mutations ───────────────────────────
# Each mirrors the matching classic router exactly (same DB calls + Telegram
# notification) so behavior is identical and money flows stay correct.


@router.post("/topups/{topup_id}/approve")
async def approve_topup(request: Request, topup_id: str):
    database = db(request)
    topup = await database.get_wallet_topup(topup_id)
    ok = await database.approve_wallet_topup(topup_id)
    if ok and topup:
        await notify_telegram_user(
            request,
            int(topup["user_id"]),
            f"🎉 شارژ کیف پول شما تایید شد!\nمبلغ: <b>{int(topup['amount_toman']):,}</b> تومان",
        )
    return {"ok": bool(ok)}


@router.post("/topups/{topup_id}/reject")
async def reject_topup(request: Request, topup_id: str):
    database = db(request)
    topup = await database.get_wallet_topup(topup_id)
    ok = await database.reject_wallet_topup(topup_id)
    if ok and topup:
        await notify_telegram_user(
            request,
            int(topup["user_id"]),
            f"❌ درخواست شارژ کیف پول شما رد شد.\nمبلغ: <b>{int(topup['amount_toman']):,}</b> تومان",
        )
    return {"ok": bool(ok)}


@router.post("/agent-requests/{req_id}/approve")
async def approve_agent_request(request: Request, req_id: str):
    body = await _json_body(request)
    access = AgentAccess.OPEN if str(body.get("access_level")) == "open" else AgentAccess.CLOSED
    database = db(request)
    result = await database.approve_agent_request_as_agent(
        req_id=req_id,
        access_level=access,
        credit_limit_toman=max(0, int(body.get("credit_limit_toman") or 0)),
        price_per_gb=max(0, int(body.get("price_per_gb") or 0)),
        created_by=0,
    )
    if result:
        label = "نماینده با دسترسی باز" if access == AgentAccess.OPEN else "نماینده نیازمند پرداخت"
        await notify_telegram_user(
            request,
            int(result["user_id"]),
            (
                f"✅ درخواست نمایندگی شما تایید شد.\n"
                f"سطح جدید: <b>{label}</b>\n"
                f"سقف اعتبار: <b>{int(result['credit_limit_toman']):,}</b> تومان"
            ),
        )
    return {"ok": bool(result)}


@router.post("/agent-requests/{req_id}/reject")
async def reject_agent_request(request: Request, req_id: str):
    database = db(request)
    row = await database.get_agent_request(req_id)
    ok = await database.update_agent_request_status(req_id, "rejected")
    if ok and row:
        await notify_telegram_user(
            request,
            int(row["user_id"]),
            "❌ درخواست نمایندگی شما رد شد. برای پیگیری، با پشتیبانی پیام دهید.",
        )
    return {"ok": bool(ok)}


@router.post("/users/{user_id}/ban")
async def ban_user(request: Request, user_id: int):
    body = await _json_body(request)
    reason = str(body.get("reason") or "").strip()
    await db(request).set_user_disabled(user_id, True, reason)
    await notify_telegram_user(
        request,
        user_id,
        "⛔️ دسترسی شما به ربات محدود شد.\n"
        f"دلیل: <b>{reason or 'ثبت نشده'}</b>\n"
        "برای پیگیری با پشتیبانی تماس بگیرید.",
    )
    return {"ok": True}


@router.post("/users/{user_id}/unban")
async def unban_user(request: Request, user_id: int):
    await db(request).set_user_disabled(user_id, False)
    await notify_telegram_user(request, user_id, "✅ دسترسی شما به ربات دوباره فعال شد.")
    return {"ok": True}


@router.post("/users/{user_id}/wallet")
async def set_wallet(request: Request, user_id: int):
    body = await _json_body(request)
    balance = await db(request).set_wallet_balance(user_id, int(body.get("wallet_balance") or 0))
    await notify_telegram_user(
        request,
        user_id,
        f"💳 موجودی کیف پول شما توسط مدیریت به <b>{balance:,}</b> تومان تغییر کرد.",
    )
    return {"ok": True, "balance": balance}


@router.post("/subscriptions/{sub_id}/enabled")
async def set_subscription_enabled(request: Request, sub_id: str):
    body = await _json_body(request)
    enabled = bool(body.get("enabled"))
    await panel(request).set_enabled(sub_id, enabled)
    detail = await panel(request).find_subscription(sub_id, use_cache=False)
    if detail:
        await db(request).update_subscription_panel_snapshot(detail)
    return {"ok": True, "enabled": enabled}


@router.get("/subscriptions/{sub_id}")
async def subscription_detail(request: Request, sub_id: str):
    row = await db(request).admin_subscription_detail(sub_id)
    if not row:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return row


@router.post("/subscriptions/{sub_id}/sync")
async def sync_subscription(request: Request, sub_id: str):
    detail = await panel(request).find_subscription(sub_id, use_cache=False)
    if detail:
        await db(request).update_subscription_panel_snapshot(detail)
    return {"ok": bool(detail)}


@router.post("/subscriptions/{sub_id}/volume")
async def set_subscription_volume(request: Request, sub_id: str):
    body = await _json_body(request)
    detail = await panel(request).set_total_volume(sub_id, max(0, int(body.get("total_gb") or 0)))
    await db(request).update_subscription_panel_snapshot(detail)
    return {"ok": True}


# ─────────────────────────── user detail (full bundle) ───────────────────────────


@router.get("/users/{user_id}/detail")
async def user_detail_bundle(
    request: Request,
    user_id: int,
    period: str = "all",
    topup_period: str = "all",
    subs_page: int = 1,
):
    database = db(request)
    user = await database.admin_user_detail(user_id)
    if not user:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    p = period if period in {"all", "24h", "7d", "30d"} else "all"
    tp = topup_period if topup_period in {"all", "24h", "7d", "30d"} else "all"
    page_size = 30
    safe_page = max(1, int(subs_page))
    subs_total = await database.admin_user_subscriptions_count(user_id)
    total_pages = max(1, (subs_total + page_size - 1) // page_size)
    safe_page = min(safe_page, total_pages)
    subs = await database.admin_user_subscriptions(user_id, safe_page, page_size)
    orders = await database.admin_user_orders_for_period(user_id, p)
    topups = await database.admin_user_topups(user_id, tp)
    topups_total = await database.admin_user_topups_total(user_id, tp)
    ledger = await database.admin_user_agent_ledger(user_id)
    agent_24h = None
    if user.get("access_level"):
        agent_24h = await database.get_agent_recent_purchase_summary(user_id, seconds=86400)
    settings_rows = {row["key"]: row["value"] for row in await database.admin_list_settings()}
    return {
        "user": user,
        "pg_admin_username": settings_rows.get(f"pg_admin_user_{user_id}", ""),
        "subscriptions": subs,
        "subs_total": subs_total,
        "subs_page": safe_page,
        "subs_total_pages": total_pages,
        "orders": orders,
        "topups": topups,
        "topups_total": topups_total,
        "ledger": ledger,
        "agent_24h": agent_24h,
        "period": p,
        "topup_period": tp,
    }


@router.post("/users/{user_id}/agent")
async def update_agent(request: Request, user_id: int):
    body = await _json_body(request)
    database = db(request)
    old = await database.get_agent(user_id)
    access = AgentAccess.OPEN if str(body.get("access_level")) == "open" else AgentAccess.CLOSED
    await database.upsert_agent(
        user_id=user_id,
        price_per_gb=max(0, int(body.get("price_per_gb") or 0)),
        created_by=0,
        access_level=access,
        credit_limit_toman=max(0, int(body.get("credit_limit_toman") or 0)),
        credit_used_toman=max(0, int(body.get("credit_used_toman") or 0)),
        daily_test_limit=max(0, int(body.get("daily_test_limit") or 0)),
        disabled=bool(old["disabled"]) if old else False,
    )
    label = "نماینده باز" if access == AgentAccess.OPEN else "نماینده بسته (نیاز به پرداخت)"
    await notify_telegram_user(
        request,
        user_id,
        f"✅ وضعیت نمایندگی شما به <b>{label}</b> به‌روزرسانی شد.",
    )
    return {"ok": True}


@router.post("/users/{user_id}/message")
async def send_message(request: Request, user_id: int):
    import html as _html

    body = await _json_body(request)
    text = str(body.get("message") or "").strip()
    if text:
        await notify_telegram_user(request, user_id, f"📩 <b>پیام مدیریت</b>\n\n{_html.escape(text)}")
    return {"ok": bool(text)}


@router.post("/users/{user_id}/sync-subscriptions")
async def sync_user_subscriptions(request: Request, user_id: int):
    database = db(request)
    rows = await database.admin_user_subscriptions(user_id, page=1, page_size=10000)
    synced = 0
    for row in rows:
        detail = await panel(request).find_subscription(str(row["sub_id"]), use_cache=False)
        if detail:
            await database.update_subscription_panel_snapshot(detail)
            synced += 1
    return {"ok": True, "synced": synced}


@router.post("/users/{user_id}/subscriptions/bulk")
async def bulk_subscriptions(request: Request, user_id: int):
    body = await _json_body(request)
    enabled = bool(body.get("enabled"))
    database = db(request)
    sub_ids = await database.admin_user_subscription_ids(user_id)
    event_id = await database.create_admin_event(
        kind="bulk_enable_subscriptions" if enabled else "bulk_disable_subscriptions",
        title=f"{'Enable' if enabled else 'Disable'} all subscriptions for user {user_id}",
        payload={"user_id": int(user_id), "sub_ids": sub_ids, "enabled": enabled},
        total_count=len(sub_ids),
    )
    return {"ok": True, "event_id": event_id, "count": len(sub_ids)}


# ─────────────────────────── broadcast & events ───────────────────────────

_BROADCAST_AUDIENCES = {"all", "agents", "customers"}


@router.get("/broadcast")
async def broadcast_info(request: Request, audience: str = "all"):
    a = audience if audience in _BROADCAST_AUDIENCES else "all"
    targets = await db(request).admin_broadcast_targets(a)
    return {"audience": a, "target_count": len(targets)}


@router.post("/broadcast")
async def broadcast_send(request: Request):
    body = await _json_body(request)
    a = str(body.get("audience")) if str(body.get("audience")) in _BROADCAST_AUDIENCES else "all"
    text = str(body.get("message") or "").strip()
    database = db(request)
    targets = await database.admin_broadcast_targets(a)
    event_id = ""
    if text:
        event_id = await database.create_admin_event(
            kind="manual_broadcast",
            title=f"Manual broadcast to {a}",
            payload={"audience": a, "text": text},
            total_count=len(targets),
        )
    return {"ok": bool(text), "event_id": event_id, "target_count": len(targets)}


@router.get("/events")
async def events_list(request: Request, status: str = "active"):
    s = status if status in {"active", "completed", "all"} else "active"
    rows = await db(request).list_admin_events(s, limit=120)
    return {"items": rows, "count": len(rows)}


@router.post("/events/{event_id}/dismiss")
async def dismiss_event(request: Request, event_id: str):
    await db(request).dismiss_admin_event(event_id)
    return {"ok": True}


# ─────────────────────────── full settings update ───────────────────────────


@router.post("/settings")
async def update_settings(request: Request):
    body = await _json_body(request)
    database = db(request)
    current = {row["key"]: row["value"] for row in await database.admin_list_settings()}
    # Runtime keys missing from the body keep their current value (no reset).
    values = settings_values_from_form(body, current)
    # Only touch backup settings if the caller actually sent backup_* fields,
    # otherwise backup_values_from_form would reset them to its hardcoded defaults.
    if any(str(k).startswith(("backup_", "pg_backup_")) for k in body):
        values.update(backup_values_from_form(body, current))
    current_panel = await database.get_panel_settings()
    await database.admin_update_settings(values)
    panel_values = None
    # Only update panel credentials when a base_url is provided; an empty
    # password means "keep the existing one" (never wipe it).
    if str(body.get("panel_base_url") or "").strip():
        panel_values = panel_values_from_form(body, current_panel)
        if not str(body.get("panel_password") or "").strip():
            panel_values["password"] = str(current_panel["password"] or "") if current_panel else ""
        await database.upsert_panel_settings(
            **panel_values,
            cookie=str(current_panel["cookie"] or "") if current_panel else "",
            cookie_ts=int(current_panel["cookie_ts"] or 0) if current_panel else 0,
        )
    sync_env(request, settings=values, panel=panel_values)
    return {"ok": True}


@router.post("/sales")
async def set_sales(request: Request):
    """Open/close sales for a given audience (mirrors /admin/settings/sales)."""
    from async_storefront.util import now_ts

    body = await _json_body(request)
    audience = normalize_sales_audience(body.get("audience"))
    new_status = "closed" if str(body.get("sales_status")) == "closed" else "open"
    database = db(request)
    master = str(await database.get_setting("sales_status", "open") or "open").strip().lower()
    admin_name = current_admin_username(request) or "admin"
    now = str(now_ts())

    if audience == "all":
        target_keys = ("sales_status", "sales_status_user", "sales_status_agent")
    elif audience == "agent":
        target_keys = ("sales_status_agent",)
    else:
        target_keys = ("sales_status_user",)

    changed = False
    for key in target_keys:
        current = str(await database.get_setting(key, master) or master).strip().lower()
        if current != new_status:
            changed = True
            break
    if not changed:
        return {"ok": True, "changed": False}

    values: dict[str, str] = {}
    for key in target_keys:
        values[key] = new_status
        values[f"{key}_updated_at"] = now
        values[f"{key}_updated_by"] = admin_name
    await database.admin_update_settings(values)

    broadcast_target, _label = SALES_AUDIENCES[audience]
    title, text = sales_broadcast(new_status, audience)
    targets = await database.admin_broadcast_targets(broadcast_target)
    await database.create_admin_event(
        kind="sales_status_broadcast",
        title=title,
        payload={"audience": broadcast_target, "text": text, "already_html": True, "sales_status": new_status},
        total_count=len(targets),
    )
    return {"ok": True, "changed": True, "status": new_status, "audience": audience}


@router.post("/payment-cards")
async def set_payment_cards(request: Request):
    """Save the rotating payment-card list ([{number, name}, ...], max 8)."""
    import json as _json

    body = await _json_body(request)
    raw = body.get("cards")
    cards: list[dict[str, str]] = []
    if isinstance(raw, list):
        for item in raw[:8]:
            number = str((item or {}).get("number", "")).strip()
            if number:
                cards.append({"number": number, "name": str((item or {}).get("name", "")).strip()})
    await db(request).admin_update_settings({"payment_cards": _json.dumps(cards, ensure_ascii=False)})
    return {"ok": True, "count": len(cards)}


@router.post("/price-tiers")
async def set_price_tiers(request: Request):
    """Save volume-based pricing brackets: [{min_gb, price_per_gb}, ...].
    An empty list clears tiers and reverts to the flat per-GB price."""
    import json as _json

    body = await _json_body(request)
    raw = body.get("tiers")
    tiers: list[dict[str, int]] = []
    if isinstance(raw, list):
        seen: set[int] = set()
        for item in raw[:12]:
            try:
                min_gb = int(float((item or {}).get("min_gb")))
                price = int(float((item or {}).get("price_per_gb")))
            except (TypeError, ValueError):
                continue
            if min_gb < 0 or price <= 0 or min_gb in seen:
                continue
            seen.add(min_gb)
            tiers.append({"min_gb": min_gb, "price_per_gb": price})
    tiers.sort(key=lambda x: x["min_gb"])
    await db(request).admin_update_settings(
        {"price_tiers": _json.dumps(tiers, ensure_ascii=False) if tiers else ""}
    )
    return {"ok": True, "count": len(tiers), "tiers": tiers}


@router.post("/infinite-package")
async def set_infinite_package(request: Request):
    """Configure the infinite (fair-usage) package: enabled flag, fair-usage cap
    in GB, validity in days, and the custom price in Toman."""
    body = await _json_body(request)

    def _int(value, default=0, minimum=0):
        try:
            n = int(float(value))
        except (TypeError, ValueError):
            return default
        return max(minimum, n)

    enabled = "1" if bool(body.get("enabled")) else "0"
    cap_gb = _int(body.get("cap_gb"), default=100, minimum=1)
    duration_days = _int(body.get("duration_days"), default=30, minimum=1)
    price = _int(body.get("price"), default=0, minimum=0)
    await db(request).admin_update_settings(
        {
            "infinite_enabled": enabled,
            "infinite_cap_gb": str(cap_gb),
            "infinite_duration_days": str(duration_days),
            "infinite_price": str(price),
        }
    )
    return {
        "ok": True,
        "enabled": enabled == "1",
        "cap_gb": cap_gb,
        "duration_days": duration_days,
        "price": price,
    }


async def _pg_settings(database) -> dict[str, str]:
    rows = {row["key"]: row["value"] for row in await database.admin_list_settings()}
    return {k: str(rows.get(k, "")) for k in (
        "pg_enabled", "pg_label", "pg_base_url", "pg_username", "pg_password",
        "pg_group", "pg_verify_tls", "pg_price_per_gb", "pg_default_days",
    )}


@router.post("/primary-backend")
async def set_primary_backend(request: Request):
    """Choose which backend the main buy flow sells from: 'xui' or 'pasarguard'."""
    body = await _json_body(request)
    backend = "pasarguard" if str(body.get("backend")) == "pasarguard" else "xui"
    await db(request).admin_update_settings({"primary_backend": backend})
    return {"ok": True, "backend": backend}


@router.post("/pasarguard")
async def set_pasarguard(request: Request):
    """Configure the PasarGuard panel (settings KV, no schema change). Empty
    password keeps the current one."""
    body = await _json_body(request)
    database = db(request)

    def _s(key: str) -> str:
        return str(body.get(key, "") or "").strip()

    def _int(value, default=0, minimum=0):
        try:
            return max(minimum, int(float(value)))
        except (TypeError, ValueError):
            return default

    values: dict[str, str] = {
        "pg_enabled": "1" if bool(body.get("enabled")) else "0",
        "pg_label": _s("label") or "سرور اختصاصی",
        "pg_base_url": _s("base_url").rstrip("/"),
        "pg_username": _s("username"),
        "pg_group": _s("group") or "Tsco-Bot",
        "pg_verify_tls": "1" if (body.get("verify_tls", True) and str(body.get("verify_tls")).lower() not in {"0", "false", "off", "no"}) else "0",
        "pg_price_per_gb": str(_int(body.get("price_per_gb"), 0, 0)),
        "pg_default_days": str(_int(body.get("default_days"), 30, 0)),
    }
    password = str(body.get("password", "") or "")
    if password.strip():
        values["pg_password"] = password
    await database.admin_update_settings(values)
    return {"ok": True, "enabled": values["pg_enabled"] == "1"}


@router.post("/pasarguard/test")
async def test_pasarguard(request: Request):
    """Live connection test: build a client from saved settings (with any
    just-typed overrides in the body) and authenticate against the panel."""
    body = await _json_body(request)
    database = db(request)
    cur = await _pg_settings(database)
    base_url = str(body.get("base_url") or cur["pg_base_url"]).strip().rstrip("/")
    username = str(body.get("username") or cur["pg_username"]).strip()
    password = str(body.get("password") or "").strip() or cur["pg_password"]
    verify_tls = cur["pg_verify_tls"] != "0"
    if "verify_tls" in body:
        verify_tls = str(body.get("verify_tls")).lower() not in {"0", "false", "off", "no"}
    if not (base_url and username and password):
        return {"ok": False, "error": "آدرس پنل، یوزرنیم و پسورد را کامل کنید."}
    client = PasarGuardClient(base_url=base_url, username=username, password=password, verify_tls=verify_tls)
    try:
        report = await client.test_connection()
    finally:
        await client.close()
    return report


# ───────────────────── PasarGuard admin delegation (phase 2) ─────────────────────
async def _pg_client(database) -> PasarGuardClient | None:
    """Build a PasarGuard client from saved settings (None if not configured)."""
    cur = await _pg_settings(database)
    base_url, username, password = cur["pg_base_url"], cur["pg_username"], cur["pg_password"]
    if not (base_url and username and password):
        return None
    return PasarGuardClient(
        base_url=base_url, username=username, password=password,
        verify_tls=cur["pg_verify_tls"] != "0",
    )


def _gen_admin_password() -> str:
    """A PasarGuard-policy-valid password: 13 chars, 3 uppercase, digits + symbol."""
    pool = (
        [secrets.choice(string.ascii_uppercase) for _ in range(3)]
        + [secrets.choice(string.ascii_lowercase) for _ in range(6)]
        + [secrets.choice(string.digits) for _ in range(3)]
        + [secrets.choice("!@#$%*-_")]
    )
    secrets.SystemRandom().shuffle(pool)
    return "".join(pool)


def _slim_admin(a: dict) -> dict:
    role = a.get("role") or {}
    return {
        "username": a.get("username"),
        "status": a.get("status"),
        "total_users": int(a.get("total_users") or 0),
        "used_traffic": int(a.get("used_traffic") or 0),
        "lifetime_used_traffic": int(a.get("lifetime_used_traffic") or 0),
        "data_limit": a.get("data_limit"),
        "role_name": role.get("name"),
        "is_owner": bool(role.get("is_owner")),
        "telegram_id": a.get("telegram_id"),
        "note": a.get("note"),
    }


def _slim_pg_user(u: dict) -> dict:
    return {
        "username": u.get("username"),
        "status": u.get("status"),
        "used_traffic": int(u.get("used_traffic") or 0),
        "data_limit": u.get("data_limit"),
        "expire": u.get("expire"),
        "online_at": u.get("online_at"),
        "created_at": u.get("created_at"),
        "subscription_url": u.get("subscription_url"),
    }


@router.get("/pasarguard/admins")
async def pg_list_admins(request: Request):
    """All admin accounts we created in the PasarGuard panel, EXCLUDING the
    owner and the bot's own account — the monitoring roster."""
    database = db(request)
    client = await _pg_client(database)
    if client is None:
        return {"ok": False, "error": "پنل پاسارگارد پیکربندی نشده است.", "admins": []}
    self_user = (await _pg_settings(database))["pg_username"].strip().lower()
    try:
        admins = await client.list_admins()
        roster = [
            a for a in admins
            if not (a.get("role") or {}).get("is_owner")
            and str(a.get("username", "")).strip().lower() != self_user
        ]
        # Total allocated volume per reseller (Σ data_limit of their accounts),
        # computed concurrently with a small semaphore so the roster stays
        # responsive and never floods the panel even with many resellers.
        sem = asyncio.Semaphore(5)

        async def _allocated(a: dict) -> tuple[int, bool]:
            async with sem:
                try:
                    agg = await client.admin_user_aggregates(str(a.get("username")), max_scan=20000)
                    return int(agg.get("allocated") or 0), bool(agg.get("capped"))
                except Exception:
                    return 0, False

        aggs = await asyncio.gather(*[_allocated(a) for a in roster])
    except Exception as exc:
        return {"ok": False, "error": str(exc), "admins": []}
    finally:
        await client.close()
    out = []
    for a, (allocated, capped) in zip(roster, aggs):
        row = _slim_admin(a)
        row["allocated"] = allocated
        row["allocated_capped"] = capped
        out.append(row)
    return {"ok": True, "admins": out, "total": len(out)}


@router.get("/pasarguard/admins/{username}/users")
async def pg_admin_users(request: Request, username: str, offset: int = 0, limit: int = 25, search: str = ""):
    """One server-side page of a reseller's accounts (heavy-scale safe)."""
    database = db(request)
    client = await _pg_client(database)
    if client is None:
        return {"ok": False, "error": "پنل پاسارگارد پیکربندی نشده است.", "users": [], "total": 0}
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    try:
        admin = await client.get_admin(username)
        page = await client.list_users_by_admin(username, offset=offset, limit=limit, search=search)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "users": [], "total": 0}
    finally:
        await client.close()
    return {
        "ok": True,
        "admin": _slim_admin(admin) if admin else {"username": username},
        "users": [_slim_pg_user(u) for u in page["users"]],
        "total": int(page["total"]),
        "offset": offset,
        "limit": limit,
    }


@router.get("/pasarguard/admins/{username}/stats")
async def pg_admin_stats(request: Request, username: str):
    """Fleet roll-up for the KPI tiles: account count, used traffic, total
    allocated volume, and volume created in the last 24h."""
    client = await _pg_client(db(request))
    if client is None:
        return {"ok": False, "error": "پنل پاسارگارد پیکربندی نشده است."}
    try:
        agg = await client.admin_user_aggregates(username)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        await client.close()
    return {"ok": True, **agg}


@router.get("/pasarguard/roles")
async def pg_list_roles(request: Request):
    """Roles available to assign when creating an admin (for the form dropdown)."""
    client = await _pg_client(db(request))
    if client is None:
        return {"ok": False, "error": "پنل پاسارگارد پیکربندی نشده است.", "roles": []}
    try:
        roles = await client.list_roles()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "roles": []}
    finally:
        await client.close()
    out = [{"id": r.get("id"), "name": r.get("name"), "is_owner": bool(r.get("is_owner"))} for r in roles]
    return {"ok": True, "roles": out}


@router.post("/pasarguard/admins")
async def pg_create_admin(request: Request):
    """Create a PasarGuard admin with a chosen username/password and role. If no
    role_id is given, a safe default 'reseller' role is ensured and used."""
    body = await _json_body(request)
    database = db(request)
    client = await _pg_client(database)
    if client is None:
        return JSONResponse({"ok": False, "error": "پنل پاسارگارد پیکربندی نشده است."}, status_code=400)
    username = str(body.get("username") or "").strip()
    username = "".join(c for c in username if c.isalnum() or c == "_")
    if len(username) < 3:
        await client.close()
        return JSONResponse({"ok": False, "error": "یوزرنیم باید حداقل ۳ کاراکتر (حروف/عدد/زیرخط) باشد."}, status_code=400)
    password = str(body.get("password") or "").strip() or _gen_admin_password()
    try:
        role_id = body.get("role_id")
        role_id = int(role_id) if role_id not in (None, "", 0, "0") else await client.ensure_reseller_role()
        data_limit = body.get("data_limit_gb")
        data_limit = int(float(data_limit) * (1024 ** 3)) if data_limit not in (None, "", 0, "0") else None
        admin = await client.create_admin(
            username=username, password=password, role_id=role_id,
            data_limit=data_limit, note=str(body.get("note") or "").strip(),
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    finally:
        await client.close()
    return {"ok": True, "username": username, "password": password,
            "role_id": role_id, "panel_url": (await _pg_settings(database))["pg_base_url"],
            "admin": _slim_admin(admin) if isinstance(admin, dict) else None}


@router.post("/pasarguard/admins/{username}/delete")
async def pg_delete_admin(request: Request, username: str):
    client = await _pg_client(db(request))
    if client is None:
        return JSONResponse({"ok": False, "error": "پنل پاسارگارد پیکربندی نشده است."}, status_code=400)
    try:
        await client.delete_admin(username)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    finally:
        await client.close()
    return {"ok": True}


@router.post("/pasarguard/admins/{username}/status")
async def pg_set_admin_status(request: Request, username: str):
    """Enable/disable a reseller-admin (status: active|disabled)."""
    body = await _json_body(request)
    status = "disabled" if str(body.get("status")) == "disabled" else "active"
    client = await _pg_client(db(request))
    if client is None:
        return JSONResponse({"ok": False, "error": "پنل پاسارگارد پیکربندی نشده است."}, status_code=400)
    try:
        await client.modify_admin(username, status=status)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    finally:
        await client.close()
    return {"ok": True, "status": status}


@router.post("/users/{user_id}/pasarguard-admin")
async def pg_create_admin_for_reseller(request: Request, user_id: int):
    """One-click: issue a personal PasarGuard admin account for a reseller, with
    username/password derived from their Telegram id. Stores the username so the
    UI can show it was already created."""
    database = db(request)
    client = await _pg_client(database)
    if client is None:
        return JSONResponse({"ok": False, "error": "پنل پاسارگارد پیکربندی نشده است."}, status_code=400)
    username = f"rs{int(user_id)}"
    password = _gen_admin_password()
    try:
        existing = await client.get_admin(username)
        if existing:
            return JSONResponse(
                {"ok": False, "exists": True, "username": username,
                 "error": f"این نماینده از قبل اکانت ادمین دارد: {username}"},
                status_code=409,
            )
        role_id = await client.ensure_reseller_role()
        await client.create_admin(
            username=username, password=password, role_id=role_id,
            note=f"reseller tg:{int(user_id)}", telegram_id=int(user_id),
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    finally:
        await client.close()
    await database.admin_update_settings({f"pg_admin_user_{int(user_id)}": username})
    return {"ok": True, "username": username, "password": password,
            "panel_url": (await _pg_settings(database))["pg_base_url"]}


@router.post("/ui-mode")
async def set_ui_mode(request: Request):
    """Switch the panel UI between the modern SPA and the classic Jinja panel.
    Applies instantly (the /admin entry point reads this on each request — no
    restart needed)."""
    body = await _json_body(request)
    mode = "classic" if str(body.get("mode")) == "classic" else "modern"
    await db(request).admin_update_settings({"ui_mode": mode})
    return {"ok": True, "mode": mode}


@router.get("/catalog")
async def get_catalog(request: Request):
    """The sales catalog plus everything the editor needs to be usable.

    Every panel a plan can target is resolved live — PasarGuard groups and the
    configured 3x-ui panel — so a target is picked from what actually exists
    instead of typed from memory. A mistyped group is the difference between a
    sale and a failed purchase.
    """
    database = db(request)
    data = await catalog.load_catalog(database)

    groups: list[dict] = []
    groups_error = ""
    client = None
    try:
        client = await _pg_client(database)
    except Exception as exc:
        groups_error = str(exc)[:200]
    if client is not None:
        try:
            for group in await client.list_groups():
                groups.append({"id": group.get("id"), "name": str(group.get("name") or "")})
        except Exception as exc:
            groups_error = str(exc)[:200]
        finally:
            with contextlib.suppress(Exception):
                await client.close()

    panels = [{"key": "1", "label": "پنل اصلی 3x-ui"}]

    # Report per-plan problems so the editor can flag a plan the bot would
    # refuse to sell, rather than the admin finding out from a failed purchase.
    problems = {p["id"]: catalog.validate_plan(p) for p in data.get("plans") or []}
    return {
        "catalog": data,
        "groups": groups,
        "groups_error": groups_error,
        "panels": panels,
        "problems": {k: v for k, v in problems.items() if v},
        "migrated_from_packages": str(await database.get_setting("catalog_migrated_from_packages", "0")) == "1",
    }


@router.post("/catalog")
async def save_catalog_endpoint(request: Request):
    """Replace the catalog. Normalised and validated server-side, so a malformed
    plan can never reach the buy flow."""
    body = await _json_body(request)
    incoming = body.get("catalog") if isinstance(body.get("catalog"), dict) else body
    if not isinstance(incoming, dict):
        return JSONResponse({"ok": False, "error": "invalid catalog payload"}, status_code=400)

    categories = incoming.get("categories")
    plans = incoming.get("plans")
    if not isinstance(categories, list) or not isinstance(plans, list):
        return JSONResponse({"ok": False, "error": "catalog must contain categories and plans"}, status_code=400)
    if len(categories) > 40 or len(plans) > 200:
        return JSONResponse({"ok": False, "error": "too many categories or plans"}, status_code=400)

    database = db(request)
    saved = await catalog.save_catalog(database, {"categories": categories, "plans": plans})
    problems = {p["id"]: catalog.validate_plan(p) for p in saved.get("plans") or []}
    return {"ok": True, "catalog": saved, "problems": {k: v for k, v in problems.items() if v}}


@router.post("/backup/run")
async def api_run_backup(request: Request):
    """Take a backup right now and report what it produced.

    Runs inline rather than fire-and-forget so the panel can show the real
    outcome — including whether the PasarGuard archive came out restorable,
    which is the only part that matters when you actually need it.
    """
    from pathlib import Path

    from .backup import run_backup_now

    try:
        result = await run_backup_now(request.app, source="manual")
    except Exception as exc:
        LOG.exception("manual backup failed")
        return {"ok": False, "error": str(exc)[:500]}
    pg = result.pg_export
    database = db(request)
    status = str(await database.get_setting("backup_last_status", ""))
    return {
        "ok": True,
        "mode": result.mode,
        "file": Path(result.archive_path).name,
        "delivered": status in {"ok", "partial"},
        "status": status,
        "errors": list(result.errors),
        "pg": {
            "included": bool(pg),
            "mode": pg.mode if pg else "",
            "db_mb": round((pg.db_bytes if pg else 0) / (1024 * 1024), 1),
            "restorable": bool(pg.restorable) if pg else False,
        },
    }
