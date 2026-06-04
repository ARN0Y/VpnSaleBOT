from __future__ import annotations

from fastapi import APIRouter, Form, Request

from .common import db, render

router = APIRouter(prefix="/admin/broadcast")


AUDIENCE_LABELS = {
    "all": "همه کاربران",
    "agents": "فقط نماینده‌ها",
    "customers": "فقط کاربران عادی",
}


@router.get("")
async def broadcast_index(request: Request, audience: str = "all"):
    normalized = audience if audience in AUDIENCE_LABELS else "all"
    targets = await db(request).admin_broadcast_targets(normalized)
    return render(
        request,
        "broadcast.html",
        {
            "audience": normalized,
            "audience_labels": AUDIENCE_LABELS,
            "target_count": len(targets),
            "sent": None,
            "failed": None,
            "queued_event_id": "",
            "title": "پیام همگانی",
        },
    )


@router.post("")
async def broadcast_send(request: Request, audience: str = Form("all"), message: str = Form(...)):
    normalized = audience if audience in AUDIENCE_LABELS else "all"
    text = message.strip()
    targets = await db(request).admin_broadcast_targets(normalized)
    event_id = ""
    if text:
        event_id = await db(request).create_admin_event(
            kind="manual_broadcast",
            title=f"Manual broadcast to {normalized}",
            payload={"audience": normalized, "text": text},
            total_count=len(targets),
        )
    return render(
        request,
        "broadcast.html",
        {
            "audience": normalized,
            "audience_labels": AUDIENCE_LABELS,
            "target_count": len(targets),
            "sent": None,
            "failed": None,
            "queued_event_id": event_id,
            "title": "پیام همگانی",
        },
    )
