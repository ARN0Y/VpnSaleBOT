from __future__ import annotations

import asyncio
import contextlib
import html
import json
import logging
from typing import Any

import httpx

from async_storefront.db import AsyncDatabase
from async_storefront.panel import PanelClient

LOG = logging.getLogger(__name__)

RETRY_DELAYS = (1.0, 2.0, 4.0)
MAX_BROADCAST_RATE_PER_SECOND = 28.0
MAX_BROADCAST_CONCURRENCY = 64
PROGRESS_FLUSH_SECONDS = 1.0
STALE_EVENT_RECOVERY_INTERVAL_SECONDS = 60.0
STALE_EVENT_TIMEOUT_SECONDS = 900


class TelegramSendError(RuntimeError):
    def __init__(self, message: str, *, retry_after: float | None = None, permanent: bool = False):
        super().__init__(message)
        self.retry_after = retry_after
        self.permanent = permanent


class AsyncRateLimiter:
    def __init__(self, rate_per_second: float):
        self.interval = 1.0 / max(1.0, float(rate_per_second))
        self._next_at = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            delay = self._next_at - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = loop.time()
            self._next_at = max(self._next_at, now) + self.interval

    async def pause(self, delay: float) -> None:
        if delay <= 0:
            return
        async with self._lock:
            loop = asyncio.get_running_loop()
            self._next_at = max(self._next_at, loop.time() + min(float(delay), 60.0))


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(str(event.get("payload_json") or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _safe_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _safe_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(str(value).strip())
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _retry_after(response: httpx.Response) -> float | None:
    header_value = response.headers.get("retry-after")
    if header_value:
        with contextlib.suppress(Exception):
            return max(0.0, float(header_value))
    with contextlib.suppress(Exception):
        payload = response.json()
        if isinstance(payload, dict):
            params = payload.get("parameters")
            if isinstance(params, dict) and params.get("retry_after") is not None:
                return max(0.0, float(params["retry_after"]))
    return None


def _telegram_error_description(response: httpx.Response) -> str:
    with contextlib.suppress(Exception):
        payload = response.json()
        if isinstance(payload, dict) and payload.get("description"):
            return str(payload["description"])
    return response.text[:200]


async def _send_telegram_message(client: httpx.AsyncClient, token: str, user_id: int, text: str) -> None:
    response = await client.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": int(user_id), "text": text, "parse_mode": "HTML"},
    )
    if 200 <= response.status_code < 300:
        return
    retry_after = _retry_after(response) if response.status_code == 429 else None
    permanent = response.status_code in {400, 403}
    raise TelegramSendError(
        f"telegram http {response.status_code}: {_telegram_error_description(response)}",
        retry_after=retry_after,
        permanent=permanent,
    )


async def _send_with_retries(
    *,
    client: httpx.AsyncClient,
    token: str,
    limiter: AsyncRateLimiter,
    user_id: int,
    text: str,
) -> None:
    last_exc: Exception | None = None
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            await limiter.wait()
            await _send_telegram_message(client, token, user_id, text)
            return
        except TelegramSendError as exc:
            last_exc = exc
            if exc.retry_after is not None:
                await limiter.pause(exc.retry_after)
            if exc.permanent or attempt >= len(RETRY_DELAYS):
                raise
            delay = exc.retry_after if exc.retry_after is not None else RETRY_DELAYS[attempt]
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt >= len(RETRY_DELAYS):
                raise
            delay = RETRY_DELAYS[attempt]
        await asyncio.sleep(min(max(float(delay), 0.1), 60.0))
    assert last_exc is not None
    raise last_exc


async def _with_retries(fn, *args, **kwargs):
    last_exc: Exception | None = None
    for attempt, delay in enumerate((0.0, *RETRY_DELAYS)):
        if delay:
            await asyncio.sleep(delay)
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt >= len(RETRY_DELAYS):
                break
    assert last_exc is not None
    raise last_exc


async def _process_bulk(app, event: dict[str, Any], *, enabled: bool) -> None:
    db: AsyncDatabase = app.state.db
    panel: PanelClient = app.state.panel
    payload = _payload(event)
    user_id = int(payload.get("user_id") or 0)
    sub_ids = [str(x).strip() for x in (payload.get("sub_ids") or []) if str(x).strip()]
    if not sub_ids and user_id:
        sub_ids = await db.admin_user_subscription_ids(user_id)
        await db.update_admin_event_progress(event["event_id"], total_count=len(sub_ids))

    success = 0
    failed = 0
    errors: list[str] = []
    for sub_id in sub_ids:
        try:
            await _with_retries(panel.set_enabled, sub_id, enabled)
            await db.mark_subscription_enabled(sub_id, enabled)
            success += 1
        except Exception as exc:
            failed += 1
            errors.append(f"{sub_id}: {exc}")
        await db.update_admin_event_progress(
            event["event_id"],
            success_count=success,
            failed_count=failed,
            last_error="\n".join(errors[-5:]) if errors else "",
        )

    status = "completed" if failed == 0 else "completed_with_errors"
    await db.finish_admin_event(event["event_id"], status, "\n".join(errors[-10:]))


async def _broadcast_tuning(db: AsyncDatabase, target_count: int) -> tuple[float, int]:
    rate = _safe_float(
        await db.get_setting("broadcast_rate_per_second", "25"),
        25.0,
        minimum=1.0,
        maximum=MAX_BROADCAST_RATE_PER_SECOND,
    )
    concurrency = _safe_int(
        await db.get_setting("broadcast_concurrency", "16"),
        16,
        minimum=1,
        maximum=MAX_BROADCAST_CONCURRENCY,
    )
    return rate, min(concurrency, max(1, int(target_count)))


async def _process_broadcast(app, event: dict[str, Any]) -> None:
    db: AsyncDatabase = app.state.db
    payload = _payload(event)
    audience = str(payload.get("audience") or "all")
    text = str(payload.get("text") or "").strip()
    token = str(getattr(app.state, "bot_token", "") or "").strip()
    if not text:
        await db.finish_admin_event(event["event_id"], "failed", "broadcast text is empty")
        return
    if not token:
        await db.finish_admin_event(event["event_id"], "failed", "BOT_TOKEN is not configured")
        return

    targets = await db.admin_broadcast_targets(audience)
    await db.update_admin_event_progress(event["event_id"], total_count=len(targets))
    if not targets:
        await db.finish_admin_event(event["event_id"], "completed", "")
        return

    message = f"📣 <b>پیام مدیریت</b>\n\n{html.escape(text)}"
    if payload.get("already_html"):
        message = text

    rate, concurrency = await _broadcast_tuning(db, len(targets))
    proxy = getattr(app.state, "proxy_url", "") or None
    limiter = AsyncRateLimiter(rate)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    for target in targets:
        queue.put_nowait(target)

    success = 0
    failed = 0
    errors: list[str] = []
    state_lock = asyncio.Lock()
    done = asyncio.Event()

    async def snapshot() -> tuple[int, int, str]:
        async with state_lock:
            last_error = ("ارسال ناموفق برای: " + ", ".join(errors[-20:])) if errors else ""
            return success, failed, last_error

    async def flush_progress() -> None:
        success_count, failed_count, last_error = await snapshot()
        await db.update_admin_event_progress(
            event["event_id"],
            success_count=success_count,
            failed_count=failed_count,
            last_error=last_error,
        )

    async def progress_reporter() -> None:
        while not done.is_set():
            await asyncio.sleep(PROGRESS_FLUSH_SECONDS)
            await flush_progress()

    async def worker(worker_id: int, client: httpx.AsyncClient) -> None:
        nonlocal success, failed
        while True:
            target = await queue.get()
            user_id = 0
            try:
                user_id = int(target["user_id"])
                await _send_with_retries(client=client, token=token, limiter=limiter, user_id=user_id, text=message)
                async with state_lock:
                    success += 1
            except Exception as exc:
                LOG.warning(
                    "broadcast send failed event_id=%s worker=%s user_id=%s: %s",
                    event.get("event_id"),
                    worker_id,
                    user_id,
                    exc,
                )
                async with state_lock:
                    failed += 1
                    errors.append(str(user_id))
            finally:
                queue.task_done()

    timeout = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)
    limits = httpx.Limits(max_connections=max(8, concurrency + 4), max_keepalive_connections=max(4, concurrency))
    async with httpx.AsyncClient(proxy=proxy, timeout=timeout, limits=limits, trust_env=False) as client:
        worker_tasks = [asyncio.create_task(worker(idx + 1, client)) for idx in range(concurrency)]
        reporter_task = asyncio.create_task(progress_reporter())
        try:
            await queue.join()
        finally:
            done.set()
            reporter_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reporter_task
            for task in worker_tasks:
                task.cancel()
            await asyncio.gather(*worker_tasks, return_exceptions=True)

    await flush_progress()
    status = "completed" if failed == 0 else "completed_with_errors"
    final_error = ("ارسال ناموفق برای: " + ", ".join(errors[-50:])) if errors else ""
    await db.finish_admin_event(event["event_id"], status, final_error)


async def process_event(app, event: dict[str, Any]) -> None:
    kind = str(event.get("kind") or "")
    if kind == "bulk_enable_subscriptions":
        await _process_bulk(app, event, enabled=True)
    elif kind == "bulk_disable_subscriptions":
        await _process_bulk(app, event, enabled=False)
    elif kind in {"manual_broadcast", "sales_status_broadcast"}:
        await _process_broadcast(app, event)
    else:
        await app.state.db.finish_admin_event(str(event["event_id"]), "failed", f"unknown event kind: {kind}")


async def event_worker(app) -> None:
    last_recovery_at = 0.0
    while True:
        try:
            loop = asyncio.get_running_loop()
            now = loop.time()
            if now - last_recovery_at >= STALE_EVENT_RECOVERY_INTERVAL_SECONDS:
                last_recovery_at = now
                try:
                    recovered = await app.state.db.recover_stale_admin_events(timeout_seconds=STALE_EVENT_TIMEOUT_SECONDS)
                    if recovered:
                        LOG.warning("recovered %s stale admin event(s)", recovered)
                except Exception:
                    LOG.exception("failed to recover stale admin events")
            event = await app.state.db.claim_next_admin_event()
            if not event:
                await asyncio.sleep(2.0)
                continue
            try:
                await process_event(app, event)
            except Exception as exc:
                LOG.exception("admin event failed event_id=%s", event.get("event_id"))
                await app.state.db.finish_admin_event(str(event["event_id"]), "failed", str(exc))
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("admin event worker loop failed")
            await asyncio.sleep(5.0)
