from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

router = APIRouter(prefix="/admin/file")


@router.get("/{file_id}")
async def telegram_file(request: Request, file_id: str):
    token = request.app.state.bot_token
    if not token:
        raise HTTPException(status_code=500, detail="BOT_TOKEN is not configured")
    proxy = request.app.state.proxy_url or None
    async with httpx.AsyncClient(proxy=proxy, timeout=30, trust_env=False) as client:
        meta = await client.get(f"https://api.telegram.org/bot{token}/getFile", params={"file_id": file_id})
        data = meta.json()
        if not data.get("ok"):
            raise HTTPException(status_code=404, detail="Telegram file not found")
        file_path = data["result"]["file_path"]
        content = await client.get(f"https://api.telegram.org/file/bot{token}/{file_path}")
        content.raise_for_status()
        content_type = content.headers.get("content-type", "application/octet-stream")
        if content_type == "application/octet-stream":
            content_type = "image/jpeg"
        return Response(
            content.content,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=86400, immutable",
                "Content-Disposition": "inline",
            },
        )
