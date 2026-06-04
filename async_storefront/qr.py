from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor


def _make_qr_png(payload: str) -> bytes:
    from io import BytesIO

    import segno

    buf = BytesIO()
    qr = segno.make_qr(payload, error="M")
    qr.save(buf, kind="png", scale=5, border=2)
    return buf.getvalue()


class QRService:
    def __init__(self, workers: int):
        self._pool = ProcessPoolExecutor(max_workers=max(1, int(workers)))

    async def png(self, payload: str) -> bytes:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, _make_qr_png, payload)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

