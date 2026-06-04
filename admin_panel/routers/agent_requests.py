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
    default_credit = await database.get_setting("default_agent_credit_limit_toman", "0")
    default_price = await database.get_setting("default_agent_price_per_gb", "0")
    return render(
        request,
        "agent_requests.html",
        {
            "items": rows,
            "status": status,
            "default_credit": default_credit,
            "default_price": default_price,
            "title": "درخواست‌های نمایندگی",
        },
    )


@router.post("/{req_id}/approve")
async def approve_request(
    request: Request,
    req_id: str,
    access_level: str = Form("closed"),
    credit_limit_toman: int = Form(0),
    price_per_gb: int = Form(0),
):
    database = db(request)
    access = AgentAccess.OPEN if access_level == "open" else AgentAccess.CLOSED
    result = await database.approve_agent_request_as_agent(
        req_id=req_id,
        access_level=access,
        credit_limit_toman=max(0, int(credit_limit_toman)),
        price_per_gb=max(0, int(price_per_gb)),
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
