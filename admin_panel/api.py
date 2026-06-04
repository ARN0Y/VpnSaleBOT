"""JSON API (v1) for the React/shadcn admin dashboard.

Mounted under ``/admin/api/v1`` so it sits behind the same session-cookie auth
middleware as the rest of the panel. The SPA calls these endpoints with the
session cookie (sent automatically, same-origin); mutating calls must also send
the ``x-csrf-token`` header, which the middleware validates.

This is the data backbone for the new dashboard. The legacy Jinja panel keeps
working in parallel until the SPA fully replaces it (strangler migration).
"""
from __future__ import annotations

import secrets
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from async_storefront.models import AgentAccess

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


@router.get("/orders")
async def orders(request: Request, q: str = "", period: str = "all", page: int = 1, page_size: int = 20):
    pg = max(1, int(page))
    ps = max(1, min(100, int(page_size)))
    # Fetch one extra row to know if a next page exists (no expensive COUNT).
    rows = await db(request).admin_list_orders(q, period, limit=ps + 1, offset=(pg - 1) * ps)
    return {"items": rows[:ps], "page": pg, "page_size": ps, "has_more": len(rows) > ps}


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
    return {
        "user": user,
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
    if any(str(k).startswith("backup_") for k in body):
        values.update(backup_values_from_form(body))
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


@router.post("/ui-mode")
async def set_ui_mode(request: Request):
    """Switch the panel UI between the modern SPA and the classic Jinja panel.
    Applies instantly (the /admin entry point reads this on each request — no
    restart needed)."""
    body = await _json_body(request)
    mode = "classic" if str(body.get("mode")) == "classic" else "modern"
    await db(request).admin_update_settings({"ui_mode": mode})
    return {"ok": True, "mode": mode}
