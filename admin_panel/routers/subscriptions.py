from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from urllib.parse import quote

from .common import db, panel, render

router = APIRouter(prefix="/admin/subscriptions")


def detail_redirect(sub_id: str) -> RedirectResponse:
    return RedirectResponse(f"/admin/subscriptions/detail?sub_id={quote(str(sub_id), safe='')}", status_code=303)


@router.get("")
async def subscriptions_index(request: Request, q: str = "", page: int = 1):
    page_size = 30
    safe_page = max(1, int(page))
    database = db(request)
    rows = await database.admin_search_subscriptions(q, safe_page, page_size)
    total = await database.admin_search_subscriptions_count(q)
    total_pages = max(1, (total + page_size - 1) // page_size)
    if safe_page > total_pages:
        safe_page = total_pages
        rows = await database.admin_search_subscriptions(q, safe_page, page_size)
    return render(
        request,
        "subscriptions.html",
        {
            "subs": rows,
            "q": q,
            "page": safe_page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "title": "کانفیگ‌ها",
        },
    )


@router.get("/detail")
async def subscription_detail_query(request: Request, sub_id: str):
    return await subscription_detail(request, sub_id)


@router.post("/sync")
async def sync_subscription_query(request: Request, sub_id: str):
    return await sync_subscription(request, sub_id)


@router.post("/enable")
async def enable_subscription_query(request: Request, sub_id: str):
    return await enable_subscription(request, sub_id)


@router.post("/disable")
async def disable_subscription_query(request: Request, sub_id: str):
    return await disable_subscription(request, sub_id)


@router.post("/volume")
async def update_volume_query(request: Request, sub_id: str, total_gb: int = Form(...)):
    return await update_volume(request, sub_id, total_gb)


@router.get("/{sub_id}")
async def subscription_detail(request: Request, sub_id: str):
    row = await db(request).admin_subscription_detail(sub_id)
    if not row:
        return RedirectResponse("/admin/subscriptions", status_code=303)
    return render(request, "subscription_detail.html", {"sub": row, "title": "جزئیات کانفیگ"})


@router.post("/{sub_id}/sync")
async def sync_subscription(request: Request, sub_id: str):
    detail = await panel(request).find_subscription(sub_id, use_cache=False)
    if detail:
        await db(request).update_subscription_panel_snapshot(detail)
    return detail_redirect(sub_id)


@router.post("/{sub_id}/enable")
async def enable_subscription(request: Request, sub_id: str):
    await panel(request).set_enabled(sub_id, True)
    detail = await panel(request).find_subscription(sub_id, use_cache=False)
    if detail:
        await db(request).update_subscription_panel_snapshot(detail)
    return detail_redirect(sub_id)


@router.post("/{sub_id}/disable")
async def disable_subscription(request: Request, sub_id: str):
    await panel(request).set_enabled(sub_id, False)
    detail = await panel(request).find_subscription(sub_id, use_cache=False)
    if detail:
        await db(request).update_subscription_panel_snapshot(detail)
    return detail_redirect(sub_id)


@router.post("/{sub_id}/volume")
async def update_volume(request: Request, sub_id: str, total_gb: int = Form(...)):
    detail = await panel(request).set_total_volume(sub_id, max(0, int(total_gb)))
    await db(request).update_subscription_panel_snapshot(detail)
    return detail_redirect(sub_id)
