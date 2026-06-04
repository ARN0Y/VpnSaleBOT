from __future__ import annotations

import html

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from async_storefront.models import AgentAccess

from .common import db, is_htmx, notify_telegram_user, panel, render, render_fragment

router = APIRouter(prefix="/admin/users")


async def user_row_context(request: Request, user_id: int) -> dict:
    user = await db(request).admin_user_detail(user_id)
    if not user:
        return {}
    user["disabled"] = user.get("user_disabled", 0)
    return {"u": user}


@router.get("")
async def users_index(request: Request, q: str = "", filter_by: str = "all"):
    safe_filter = filter_by if filter_by in {"all", "agents", "users"} else "all"
    users = await db(request).admin_list_users(q, filter_by=safe_filter)
    return render(request, "users.html", {"users": users, "q": q, "filter_by": safe_filter, "title": "کاربران"})


@router.get("/{user_id}")
async def user_detail(
    request: Request,
    user_id: int,
    period: str = "all",
    topup_period: str = "all",
    subs_page: int = 1,
    bulk_disabled: int | None = None,
    bulk_enabled: int | None = None,
    bulk_failed: int | None = None,
    event_queued: str = "",
):
    normalized_period = period if period in {"all", "24h", "7d", "30d"} else "all"
    normalized_topup_period = topup_period if topup_period in {"all", "24h", "7d", "30d"} else "all"
    database = db(request)
    user = await database.admin_user_detail(user_id)
    if not user:
        return RedirectResponse("/admin/users", status_code=303)
    page_size = 30
    safe_subs_page = max(1, int(subs_page))
    subs_total = await database.admin_user_subscriptions_count(user_id)
    subs_total_pages = max(1, (subs_total + page_size - 1) // page_size)
    safe_subs_page = min(safe_subs_page, subs_total_pages)
    subs = await database.admin_user_subscriptions(user_id, safe_subs_page, page_size)
    orders = await database.admin_user_orders_for_period(user_id, normalized_period)
    topups = await database.admin_user_topups(user_id, normalized_topup_period)
    topups_total = await database.admin_user_topups_total(user_id, normalized_topup_period)
    ledger = await database.admin_user_agent_ledger(user_id)
    agent_24h = None
    if user.get("access_level"):
        agent_24h = await database.get_agent_recent_purchase_summary(user_id, seconds=86400)
    return render(
        request,
        "user_detail.html",
        {
            "user": user,
            "subs": subs,
            "subs_page": safe_subs_page,
            "subs_total": subs_total,
            "subs_total_pages": subs_total_pages,
            "orders": orders,
            "topups": topups,
            "topups_total": topups_total,
            "ledger": ledger,
            "period": normalized_period,
            "topup_period": normalized_topup_period,
            "bulk_disabled": bulk_disabled,
            "bulk_enabled": bulk_enabled,
            "bulk_failed": bulk_failed,
            "event_queued": event_queued,
            "agent_24h": agent_24h,
            "title": "جزئیات کاربر",
        },
    )


@router.post("/{user_id}/agent")
async def update_agent(
    request: Request,
    user_id: int,
    access_level: str = Form(...),
    credit_limit_toman: int = Form(0),
    credit_used_toman: int = Form(0),
    daily_test_limit: int = Form(0),
    price_per_gb: int = Form(0),
):
    database = db(request)
    old = await database.get_agent(user_id)
    access = AgentAccess.OPEN if access_level == "open" else AgentAccess.CLOSED
    limit = max(0, int(credit_limit_toman))
    used = max(0, int(credit_used_toman))
    test_limit = max(0, int(daily_test_limit))
    price = max(0, int(price_per_gb))
    await database.upsert_agent(
        user_id=user_id,
        price_per_gb=price,
        created_by=0,
        access_level=access,
        credit_limit_toman=limit,
        credit_used_toman=used,
        daily_test_limit=test_limit,
        disabled=bool(old["disabled"]) if old else False,
    )
    old_level = database.normalize_agent_access_value(old["access_level"]) if old else "normal"
    old_limit = int(old["credit_limit_toman"] or 0) if old else 0
    old_used = int(old["credit_used_toman"] or 0) if old else 0
    old_price = int(old["price_per_gb"] or 0) if old else 0
    old_test_limit = int(old["daily_test_limit"] or 0) if old and "daily_test_limit" in old.keys() else 0
    if old_level != database.normalize_agent_access_value(access) or old_limit != limit or old_used != used or old_price != price or old_test_limit != test_limit:
        label = "نماینده باز" if access == AgentAccess.OPEN else "نماینده بسته (نیاز به پرداخت)"
        price_label = f"{price:,} تومان" if price > 0 else "تعرفه عمومی"
        await notify_telegram_user(
            request,
            user_id,
            f"✅ وضعیت نمایندگی شما به <b>{label}</b> تغییر کرد.\n"
            f"اعتبار مصرف‌شده: <b>{used:,}</b> تومان\n"
            f"سقف اعتبار: <b>{limit:,}</b> تومان\n"
            f"تعرفه اختصاصی هر گیگ: <b>{price_label}</b>",
        )
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.post("/{user_id}/wallet")
async def update_wallet(request: Request, user_id: int, wallet_balance: int = Form(0)):
    balance = await db(request).set_wallet_balance(user_id, wallet_balance)
    await notify_telegram_user(
        request,
        user_id,
        f"💳 موجودی کیف پول شما توسط مدیریت به <b>{balance:,}</b> تومان تغییر کرد.",
    )
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.post("/{user_id}/message")
async def send_personal_message(request: Request, user_id: int, message: str = Form(...)):
    text = message.strip()
    if text:
        await notify_telegram_user(request, user_id, f"📩 <b>پیام مدیریت</b>\n\n{html.escape(text)}")
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.post("/{user_id}/ban")
async def ban_user(request: Request, user_id: int, reason: str = Form("")):
    await db(request).set_user_disabled(user_id, True, reason)
    await notify_telegram_user(
        request,
        user_id,
        "⛔️ دسترسی شما به ربات محدود شد.\n"
        f"دلیل: <b>{reason or 'ثبت نشده'}</b>\n"
        "برای پیگیری با پشتیبانی تماس بگیرید.",
    )
    if is_htmx(request):
        return render_fragment(request, "_user_row.html", await user_row_context(request, user_id), {"HX-Trigger": "user-updated"})
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.post("/{user_id}/unban")
async def unban_user(request: Request, user_id: int):
    await db(request).set_user_disabled(user_id, False)
    await notify_telegram_user(request, user_id, "✅ دسترسی شما به ربات دوباره فعال شد.")
    if is_htmx(request):
        return render_fragment(request, "_user_row.html", await user_row_context(request, user_id), {"HX-Trigger": "user-updated"})
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.post("/{user_id}/sync-subscriptions")
async def sync_user_subscriptions(request: Request, user_id: int):
    database = db(request)
    rows = await database.admin_user_subscriptions(user_id, page=1, page_size=10000)
    for row in rows:
        detail = await panel(request).find_subscription(str(row["sub_id"]), use_cache=False)
        if detail:
            await database.update_subscription_panel_snapshot(detail)
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.post("/{user_id}/subscriptions/disable-all")
async def disable_all_user_subscriptions(request: Request, user_id: int):
    database = db(request)
    sub_ids = await database.admin_user_subscription_ids(user_id)
    event_id = await database.create_admin_event(
        kind="bulk_disable_subscriptions",
        title=f"Disable all subscriptions for user {user_id}",
        payload={"user_id": int(user_id), "sub_ids": sub_ids, "enabled": False},
        total_count=len(sub_ids),
    )
    suffix = f"?event_queued={event_id}"
    return RedirectResponse(f"/admin/users/{user_id}{suffix}", status_code=303)


@router.post("/{user_id}/subscriptions/enable-all")
async def enable_all_user_subscriptions(request: Request, user_id: int):
    database = db(request)
    sub_ids = await database.admin_user_subscription_ids(user_id)
    event_id = await database.create_admin_event(
        kind="bulk_enable_subscriptions",
        title=f"Enable all subscriptions for user {user_id}",
        payload={"user_id": int(user_id), "sub_ids": sub_ids, "enabled": True},
        total_count=len(sub_ids),
    )
    suffix = f"?event_queued={event_id}"
    return RedirectResponse(f"/admin/users/{user_id}{suffix}", status_code=303)
