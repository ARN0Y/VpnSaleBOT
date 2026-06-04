from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from .common import db, is_htmx, notify_telegram_user, render, render_fragment

router = APIRouter(prefix="/admin/topups")


async def topup_row_context(request: Request, topup_id: str) -> dict:
    rows = await db(request).admin_list_topups("all")
    for row in rows:
        if str(row["topup_id"]) == str(topup_id):
            return {"t": row}
    fallback = await db(request).get_wallet_topup(topup_id)
    return {"t": fallback} if fallback else {}


@router.get("")
async def topups_index(request: Request, status: str = "pending"):
    rows = await db(request).admin_list_topups(status)
    return render(request, "topups.html", {"topups": rows, "status": status, "title": "شارژها"})


@router.post("/{topup_id}/approve")
async def approve_topup(request: Request, topup_id: str):
    topup = await db(request).get_wallet_topup(topup_id)
    if await db(request).approve_wallet_topup(topup_id) and topup:
        await notify_telegram_user(
            request,
            int(topup["user_id"]),
            f"🎉 شارژ کیف پول شما تایید شد!\nمبلغ: <b>{int(topup['amount_toman']):,}</b> تومان",
        )
    if is_htmx(request):
        context = await topup_row_context(request, topup_id)
        if context:
            return render_fragment(request, "_topup_row.html", context, {"HX-Trigger": "topup-updated"})
    return RedirectResponse("/admin/topups", status_code=303)


@router.post("/{topup_id}/reject")
async def reject_topup(request: Request, topup_id: str):
    topup = await db(request).get_wallet_topup(topup_id)
    if await db(request).reject_wallet_topup(topup_id) and topup:
        await notify_telegram_user(
            request,
            int(topup["user_id"]),
            f"❌ درخواست شارژ کیف پول شما رد شد.\nمبلغ: <b>{int(topup['amount_toman']):,}</b> تومان",
        )
    if is_htmx(request):
        context = await topup_row_context(request, topup_id)
        if context:
            return render_fragment(request, "_topup_row.html", context, {"HX-Trigger": "topup-updated"})
    return RedirectResponse("/admin/topups", status_code=303)
