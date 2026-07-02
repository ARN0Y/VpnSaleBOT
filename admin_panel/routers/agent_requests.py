from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from async_storefront.models import AgentAccess

from .common import db, notify_telegram_user, render

router = APIRouter(prefix="/admin/agent-requests")


@router.get("")
async def requests_index(request: Request, status: str = "pending"):
    database = db(request)
    rows = await database.admin_list_agent_requests(status)
    default_price = await database.get_setting("default_agent_price_per_gb", "0")
    return render(
        request,
        "agent_requests.html",
        {
            "items": rows,
            "status": status,
            "default_price": default_price,
            "title": "درخواست‌های نمایندگی",
        },
    )


@router.post("/{req_id}/approve")
async def approve_request(
    request: Request,
    req_id: str,
    price_per_gb: int = Form(0),
):
    database = db(request)
    result = await database.approve_agent_request_as_agent(
        req_id=req_id,
        access_level=AgentAccess.CLOSED,
        credit_limit_toman=0,
        price_per_gb=max(0, int(price_per_gb)),
        created_by=0,
    )
    if result:
        await notify_telegram_user(
            request,
            int(result["user_id"]),
            (
                f"✅ درخواست نمایندگی شما تایید شد.\n"
                "از این به بعد خریدهای شما با تعرفه نمایندگی و از کیف پول انجام می‌شود."
            ),
        )
    return RedirectResponse("/admin/agent-requests", status_code=303)


@router.post("/{req_id}/reject")
async def reject_request(request: Request, req_id: str):
    database = db(request)
    row = await database.get_agent_request(req_id)
    if await database.update_agent_request_status(req_id, "rejected") and row:
        await notify_telegram_user(
            request,
            int(row["user_id"]),
            "❌ درخواست نمایندگی شما رد شد. برای پیگیری، با پشتیبانی پیام دهید.",
        )
    return RedirectResponse("/admin/agent-requests", status_code=303)
