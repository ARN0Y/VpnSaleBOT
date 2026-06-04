from __future__ import annotations

from fastapi import APIRouter, Request

from .common import db, render

router = APIRouter(prefix="/admin")


@router.get("")
async def overview(request: Request, q: str = ""):
    database = db(request)
    stats = await database.dashboard_stats()
    # Fetch only what the dashboard renders instead of pulling 300 rows (each
    # with correlated subqueries) and slicing 12 in Python.
    recent_orders = await database.admin_list_orders(q, limit=12)
    return render(request, "dashboard.html", {"stats": stats, "recent_orders": recent_orders, "q": q, "title": "داشبورد"})
