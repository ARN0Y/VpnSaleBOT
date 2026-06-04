from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from .common import db, is_htmx, render, render_fragment

router = APIRouter(prefix="/admin/events")

VALID_FILTERS = {"active", "completed", "all"}


def _normalize_filter(status: str) -> str:
    return status if status in VALID_FILTERS else "active"


@router.get("")
async def events_index(request: Request, status: str = "active"):
    normalized = _normalize_filter(status)
    events = await db(request).list_admin_events(normalized, limit=120)
    return render(
        request,
        "events.html",
        {
            "events": events,
            "status": normalized,
            "title": "رویدادها",
        },
    )


@router.get("/feed")
async def events_feed(request: Request, status: str = "active"):
    normalized = _normalize_filter(status)
    events = await db(request).list_admin_events(normalized, limit=120)
    return render_fragment(
        request,
        "_events_list.html",
        {
            "events": events,
            "status": normalized,
        },
    )


@router.post("/{event_id}/dismiss")
async def dismiss_event(request: Request, event_id: str, status: str = "active"):
    normalized = _normalize_filter(status)
    await db(request).dismiss_admin_event(event_id)
    if is_htmx(request):
        events = await db(request).list_admin_events(normalized, limit=120)
        return render_fragment(
            request,
            "_events_list.html",
            {
                "events": events,
                "status": normalized,
            },
            {"HX-Trigger": "event-dismissed"},
        )
    return RedirectResponse(f"/admin/events?status={normalized}", status_code=303)
