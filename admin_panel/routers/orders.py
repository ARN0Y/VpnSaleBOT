from __future__ import annotations

from fastapi import APIRouter, Request

from .common import db, render

router = APIRouter(prefix="/admin/orders")


@router.get("")
async def orders_index(request: Request, q: str = "", period: str = "all"):
    normalized_period = period if period in {"all", "24h", "7d", "30d"} else "all"
    rows = await db(request).admin_list_orders(q, normalized_period)
    return render(
        request,
        "orders.html",
        {"orders": rows, "q": q, "period": normalized_period, "title": "سفارش‌ها"},
    )
