from __future__ import annotations

import asyncio
import html
import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from .db import AsyncDatabase
from .models import PanelClientPayload, SubscriptionDetail
from .util import (
    ceil_gb_from_bytes,
    gb_to_bytes,
    generate_sub_id,
    make_unique_client_name,
    now_ms,
    safe_int,
)


@dataclass
class _PanelSettings:
    base_url: str
    username: str
    password: str
    inbound_id: int
    sub_link_base: str
    cookie: str


class PanelClient:
    RETRY_DELAYS_SECONDS = (1.0, 2.0, 4.0)
    INBOUNDS_CACHE_TTL_SECONDS = 12.0

    def __init__(self, db: AsyncDatabase, *, proxy_url: str = "", pool_size: int = 128, timeout_seconds: float = 20.0):
        limits = httpx.Limits(max_connections=pool_size, max_keepalive_connections=max(8, pool_size // 2))
        timeout = self._make_timeout(timeout_seconds)
        self.db = db
        self._client = httpx.AsyncClient(
            proxy=proxy_url or None,
            timeout=timeout,
            limits=limits,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "NavidVPN-Bot/1.0"},
        )
        self._login_lock = asyncio.Lock()
        self._inbounds_cache_lock = asyncio.Lock()
        self._mutation_semaphore = asyncio.Semaphore(1)
        self._inbounds_cache: list[dict[str, Any]] | None = None
        self._inbounds_cache_expires_at = 0.0

    @staticmethod
    def _make_timeout(timeout_seconds: float) -> httpx.Timeout:
        seconds = max(1.0, float(timeout_seconds or 1.0))
        return httpx.Timeout(seconds, connect=seconds, pool=seconds)

    async def close(self) -> None:
        await self._client.aclose()

    async def _settings(self) -> _PanelSettings:
        row = await self.db.get_panel_settings()
        if row and str(row["base_url"] or "").strip():
            base = str(row["base_url"] or "").strip().rstrip("/")
            custom_sub = str(row["sub_link_base"] or "").strip().rstrip("/")
            fallback_inbound_id = safe_int(await self.db.get_setting("panel_inbound_id", "0"))
            return _PanelSettings(
                base_url=base,
                username=str(row["username"] or "").strip(),
                password=str(row["password"] or ""),
                inbound_id=safe_int(row["inbound_id"]) or fallback_inbound_id,
                sub_link_base=(custom_sub + "/") if custom_sub else (base + "/sub/" if base else ""),
                cookie=str(row["cookie"] or "").strip(),
            )

        base = (await self.db.get_setting("panel_base_url", "")).strip().rstrip("/")
        custom_sub = (await self.db.get_setting("sub_link_base", "")).strip().rstrip("/")
        return _PanelSettings(
            base_url=base,
            username=(await self.db.get_setting("panel_username", "")).strip(),
            password=(await self.db.get_setting("panel_password", "")).strip(),
            inbound_id=safe_int(await self.db.get_setting("panel_inbound_id", "0")),
            sub_link_base=(custom_sub + "/") if custom_sub else (base + "/sub/" if base else ""),
            cookie=(await self.db.get_setting("panel_cookie", "")).strip(),
        )

    async def login(self, *, force: bool = False, timeout_seconds: float | None = None) -> str:
        async with self._login_lock:
            settings = await self._settings()
            if settings.cookie and not force:
                self._client.cookies.set("3x-ui", settings.cookie)
                return settings.cookie
            if not settings.base_url or not settings.username or not settings.password:
                raise RuntimeError("3x-ui panel settings are incomplete.")

            request_kwargs: dict[str, Any] = {
                "data": {"username": settings.username, "password": settings.password, "twoFactorCode": ""},
            }
            if timeout_seconds is not None:
                request_kwargs["timeout"] = self._make_timeout(timeout_seconds)
            response = await self._request_with_retries(
                "POST",
                f"{settings.base_url}/login",
                operation="login",
                **request_kwargs,
            )
            payload = await self._json(response, "login")
            if payload.get("success") is not True:
                raise RuntimeError(f"3x-ui login failed: {payload.get('msg', 'unknown')}")
            # Different 3x-ui builds name the session cookie "3x-ui" or "session".
            # httpx already stored whatever Set-Cookie the panel returned, so the
            # current process is authenticated regardless; we only persist a known
            # name so it can survive a restart. If neither is found we don't fail —
            # a later 401 just triggers a fresh forced login.
            cookie = (
                self._client.cookies.get("3x-ui")
                or self._client.cookies.get("session")
                or response.cookies.get("3x-ui")
                or response.cookies.get("session")
                or ""
            )
            if cookie:
                await self.db.update_panel_cookie(cookie, int(now_ms() / 1000))
                self._client.cookies.set("3x-ui", cookie)
            return cookie

    async def request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        settings = await self._settings()
        if not settings.base_url:
            raise RuntimeError("panel_base_url is not configured.")
        if settings.cookie:
            self._client.cookies.set("3x-ui", settings.cookie)
        else:
            await self.login(timeout_seconds=timeout_seconds)

        request_kwargs: dict[str, Any] = {"data": data}
        if timeout_seconds is not None:
            request_kwargs["timeout"] = self._make_timeout(timeout_seconds)

        response = await self._request_with_retries(
            method,
            f"{settings.base_url}{path}",
            operation=path,
            **request_kwargs,
        )
        payload = await self._json(response, path)
        if payload.get("success") is True:
            return payload

        msg = str(payload.get("msg", "") or "")
        if response.status_code in (401, 403) or "login" in msg.lower() or "unauthorized" in msg.lower():
            await self.login(force=True, timeout_seconds=timeout_seconds)
            response = await self._request_with_retries(
                method,
                f"{settings.base_url}{path}",
                operation=path,
                **request_kwargs,
            )
            payload = await self._json(response, path)
            if payload.get("success") is True:
                return payload
        raise RuntimeError(f"3x-ui API error on {path}: {payload.get('msg', response.status_code)}")

    async def raw_request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> httpx.Response:
        settings = await self._settings()
        if not settings.base_url:
            raise RuntimeError("panel_base_url is not configured.")
        if settings.cookie:
            self._client.cookies.set("3x-ui", settings.cookie)
        else:
            await self.login(timeout_seconds=timeout_seconds)
        request_kwargs: dict[str, Any] = {"data": data}
        if timeout_seconds is not None:
            request_kwargs["timeout"] = self._make_timeout(timeout_seconds)
        response = await self._request_with_retries(
            method,
            f"{settings.base_url}{path}",
            operation=path,
            **request_kwargs,
        )
        if response.status_code in (401, 403):
            await self.login(force=True, timeout_seconds=timeout_seconds)
            response = await self._request_with_retries(
                method,
                f"{settings.base_url}{path}",
                operation=path,
                **request_kwargs,
            )
        response.raise_for_status()
        return response

    async def _request_with_retries(self, method: str, url: str, *, operation: str, **kwargs: Any) -> httpx.Response:
        last_exc: httpx.RequestError | None = None
        attempts = len(self.RETRY_DELAYS_SECONDS) + 1
        for attempt in range(attempts):
            try:
                return await self._client.request(method, url, **kwargs)
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt >= len(self.RETRY_DELAYS_SECONDS):
                    break
                await asyncio.sleep(self.RETRY_DELAYS_SECONDS[attempt])
        assert last_exc is not None
        detail = str(last_exc).strip() or last_exc.__class__.__name__
        if isinstance(last_exc, httpx.TimeoutException):
            raise RuntimeError(
                f"3x-ui panel timeout during {operation}. "
                "Check PANEL_BASE_URL reachability and x-ui panel health."
            ) from last_exc
        raise RuntimeError(f"3x-ui panel request failed during {operation}: {detail}") from last_exc

    async def _json(self, response: httpx.Response, operation: str) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(response.json)
        except Exception as exc:
            body = await asyncio.to_thread(lambda: (response.text or "")[:300])
            if response.is_success:
                return {"success": True, "msg": body, "obj": None}
            raise RuntimeError(f"3x-ui non-JSON response during {operation}: HTTP {response.status_code}") from exc

    async def list_inbounds(self) -> list[dict[str, Any]]:
        cached = self._get_valid_inbounds_cache()
        if cached is not None:
            return cached

        async with self._inbounds_cache_lock:
            cached = self._get_valid_inbounds_cache()
            if cached is not None:
                return cached

            payload = await self.request("GET", "/panel/api/inbounds/list")
            obj = payload.get("obj") or []
            inbounds = obj if isinstance(obj, list) else []
            self._inbounds_cache = inbounds
            self._inbounds_cache_expires_at = time.monotonic() + self.INBOUNDS_CACHE_TTL_SECONDS
            return list(inbounds)

    def _get_valid_inbounds_cache(self) -> list[dict[str, Any]] | None:
        if self._inbounds_cache is None:
            return None
        if time.monotonic() >= self._inbounds_cache_expires_at:
            return None
        return list(self._inbounds_cache)

    def _invalidate_inbounds_cache(self) -> None:
        self._inbounds_cache = None
        self._inbounds_cache_expires_at = 0.0

    async def _parse_settings(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        try:
            parsed = await asyncio.to_thread(json.loads, raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    async def list_inbounds_uncached(self, *, timeout_seconds: float | None = None) -> list[dict[str, Any]]:
        payload = await self.request("GET", "/panel/api/inbounds/list", timeout_seconds=timeout_seconds)
        obj = payload.get("obj") or []
        return obj if isinstance(obj, list) else []

    async def collect_existing_emails(self, *, use_cache: bool = True) -> set[str]:
        emails: set[str] = set()
        inbounds = await self.list_inbounds() if use_cache else await self.list_inbounds_uncached()
        for inbound in inbounds:
            settings = await self._parse_settings(inbound.get("settings"))
            for client in settings.get("clients") or []:
                email_value = str(client.get("email", "")).strip()
                if email_value:
                    emails.add(email_value.lower())
            for stat in inbound.get("clientStats") or []:
                email_value = str(stat.get("email", "")).strip()
                if email_value:
                    emails.add(email_value.lower())
        return emails

    async def _present_sub_ids(self, sub_ids: list[str]) -> set[str]:
        """Return the subset of ``sub_ids`` that currently exist on the panel.

        Always queries the panel uncached so it reflects writes that may have
        landed despite a timeout/duplicate error on the client side.
        """
        wanted = {str(s).strip() for s in sub_ids if str(s).strip()}
        if not wanted:
            return set()
        found: set[str] = set()
        for inbound in await self.list_inbounds_uncached():
            parsed = await self._parse_settings(inbound.get("settings"))
            for client in parsed.get("clients") or []:
                sid = str(client.get("subId", "")).strip()
                if sid in wanted:
                    found.add(sid)
            for stat in inbound.get("clientStats") or []:
                sid = str(stat.get("subId", "")).strip()
                if sid in wanted:
                    found.add(sid)
        return found

    async def _add_clients_idempotent(self, inbound_id: int, clients: list[dict[str, Any]]) -> None:
        """Add clients to an inbound exactly once, even under lag/retries.

        Each client carries a random 16-char ``subId``. If ``addClient`` raises —
        whether from a lost-response timeout that was actually applied, or from a
        panel-side "already exists" replay — we reconcile against the live panel
        state by subId:

        * all of our subIds already present  -> the write truly succeeded; return.
        * none present                        -> nothing landed; re-raise so the
                                                 caller can roll back.
        * some present (rare partial write)   -> re-send only the missing clients.

        This prevents the duplicate-config / ``user already exists`` corruption
        without ever creating two clients for one purchase.
        """
        if not clients:
            return
        sub_ids = [str(c["subId"]) for c in clients]
        payload = json.dumps({"clients": clients}, separators=(",", ":"))
        try:
            await self.request(
                "POST",
                "/panel/api/inbounds/addClient",
                data={"id": str(inbound_id), "settings": payload},
            )
            return
        except Exception:
            present = await self._present_sub_ids(sub_ids)
            if present.issuperset(sub_ids):
                # Every client we meant to create is already on the panel: the
                # original request was applied; the error was a duplicate replay.
                return
            missing = [c for c in clients if str(c["subId"]) not in present]
            if len(missing) == len(clients):
                # Nothing landed at all -> genuine failure; let caller roll back.
                raise
            # Partial landing: add only what is missing, idempotently.
            retry_payload = json.dumps({"clients": missing}, separators=(",", ":"))
            try:
                await self.request(
                    "POST",
                    "/panel/api/inbounds/addClient",
                    data={"id": str(inbound_id), "settings": retry_payload},
                )
            except Exception:
                still_missing = {str(c["subId"]) for c in missing} - await self._present_sub_ids(
                    [str(c["subId"]) for c in missing]
                )
                if still_missing:
                    raise

    async def add_subscriptions(self, *, user_id: int, gb: int, qty: int, preferred_name: str | None = None, expiry_ms: int = 0) -> list[PanelClientPayload]:
        settings = await self._settings()
        if settings.inbound_id <= 0:
            raise RuntimeError("panel_inbound_id is not configured.")
        total_bytes = gb_to_bytes(gb)
        expiry_value = int(expiry_ms) if expiry_ms and int(expiry_ms) > 0 else 0
        async with self._mutation_semaphore:
            # Collect existing names uncached *inside* the mutation lock so two
            # concurrent purchases can never observe the same stale snapshot and
            # mint colliding client names.
            existing = await self.collect_existing_emails(use_cache=False)
            clients: list[dict[str, Any]] = []
            rows: list[PanelClientPayload] = []
            for _ in range(int(qty)):
                sub_id = generate_sub_id()
                while sub_id in {str(c["subId"]) for c in clients}:
                    sub_id = generate_sub_id()
                client_uuid = str(uuid.uuid4())
                email_value = make_unique_client_name(user_id, preferred_name, existing)
                created_ms = now_ms()
                clients.append(
                    {
                        "id": client_uuid,
                        "flow": "",
                        "email": email_value,
                        "limitIp": 0,
                        "totalGB": total_bytes,
                        "expiryTime": expiry_value,
                        "enable": True,
                        "tgId": str(user_id),
                        "subId": sub_id,
                        "comment": "",
                        "reset": 0,
                        "created_at": created_ms,
                        "updated_at": created_ms,
                    }
                )
                rows.append(
                    PanelClientPayload(
                        user_id=int(user_id),
                        sub_id=sub_id,
                        sub_link=f"{settings.sub_link_base}{sub_id}",
                        inbound_id=settings.inbound_id,
                        client_uuid=client_uuid,
                        client_email=email_value,
                        gb=int(gb),
                    )
                )
            await self._add_clients_idempotent(settings.inbound_id, clients)
            self._invalidate_inbounds_cache()
        return rows

    async def add_test_subscription(self, *, user_id: int, total_bytes: int, ttl_seconds: int = 600) -> PanelClientPayload:
        settings = await self._settings()
        if settings.inbound_id <= 0:
            raise RuntimeError("panel_inbound_id is not configured.")
        async with self._mutation_semaphore:
            existing = await self.collect_existing_emails(use_cache=False)
            sub_id = generate_sub_id()
            client_uuid = str(uuid.uuid4())
            email_value = make_unique_client_name(user_id, f"test_{int(user_id)}", existing)
            created_ms = now_ms()
            expiry_ms = created_ms + (max(60, int(ttl_seconds)) * 1000)
            client = {
                "id": client_uuid,
                "flow": "",
                "email": email_value,
                "limitIp": 0,
                "totalGB": int(total_bytes),
                "expiryTime": expiry_ms,
                "enable": True,
                "tgId": str(user_id),
                "subId": sub_id,
                "comment": "agent-test-config",
                "reset": 0,
                "created_at": created_ms,
                "updated_at": created_ms,
            }
            await self._add_clients_idempotent(settings.inbound_id, [client])
            self._invalidate_inbounds_cache()
        return PanelClientPayload(
            user_id=int(user_id),
            sub_id=sub_id,
            sub_link=f"{settings.sub_link_base}{sub_id}",
            inbound_id=settings.inbound_id,
            client_uuid=client_uuid,
            client_email=email_value,
            gb=0,
        )

    async def upsert_client(
        self,
        detail: SubscriptionDetail,
        *,
        total_bytes: int | None = None,
        enable: bool | None = None,
        expiry_time: int | None = None,
    ) -> None:
        payload = {
            "id": detail.client_uuid,
            "flow": "",
            "email": detail.email,
            "limitIp": 0,
            "totalGB": int(total_bytes if total_bytes is not None else detail.total_bytes),
            "expiryTime": int(expiry_time if expiry_time is not None else detail.expiry_time or 0),
            "enable": bool(detail.enabled if enable is None else enable),
            "tgId": detail.tg_id,
            "subId": detail.sub_id,
            "comment": detail.comment,
            "reset": int(detail.reset or 0),
            "created_at": int(detail.created_at_ms or now_ms()),
            "updated_at": now_ms(),
        }
        async with self._mutation_semaphore:
            await self.request(
                "POST",
                f"/panel/api/inbounds/updateClient/{detail.client_uuid}",
                data={"id": str(int(detail.inbound_id)), "settings": json.dumps({"clients": [payload]}, separators=(",", ":"))},
            )
            self._invalidate_inbounds_cache()

    async def find_subscription(self, sub_id: str, *, use_cache: bool = True) -> SubscriptionDetail | None:
        sub_id = (sub_id or "").strip()
        if not sub_id:
            return None
        settings = await self._settings()
        inbounds = await self.list_inbounds() if use_cache else await self.list_inbounds_uncached()
        for inbound in inbounds:
            inbound_id = safe_int(inbound.get("id") or inbound.get("inboundId"))
            parsed = await self._parse_settings(inbound.get("settings"))
            client_match = None
            stat_match = None
            for client in parsed.get("clients") or []:
                if str(client.get("subId", "")).strip() == sub_id:
                    client_match = client
                    break
            for stat in inbound.get("clientStats") or []:
                if str(stat.get("subId", "")).strip() == sub_id:
                    stat_match = stat
                    break
            if not client_match and not stat_match:
                continue

            total_bytes = safe_int(
                (client_match or {}).get("totalGB"),
                safe_int((stat_match or {}).get("total"), safe_int((stat_match or {}).get("totalGB"))),
            )
            used_bytes = safe_int((stat_match or {}).get("up")) + safe_int((stat_match or {}).get("down"))
            return SubscriptionDetail(
                sub_id=sub_id,
                sub_link=f"{settings.sub_link_base}{sub_id}",
                inbound_id=inbound_id,
                client_uuid=str((client_match or {}).get("id") or (stat_match or {}).get("id") or "").strip(),
                email=str((client_match or {}).get("email") or (stat_match or {}).get("email") or "").strip(),
                enabled=bool((client_match or {}).get("enable", True)),
                total_bytes=max(0, total_bytes),
                used_bytes=max(0, used_bytes),
                remaining_bytes=max(0, total_bytes - used_bytes),
                up_bytes=safe_int((stat_match or {}).get("up")),
                down_bytes=safe_int((stat_match or {}).get("down")),
                last_online_ms=safe_int((stat_match or {}).get("lastOnline")),
                expiry_time=safe_int((client_match or {}).get("expiryTime"), safe_int((stat_match or {}).get("expiryTime"))),
                tg_id=str((client_match or {}).get("tgId") or (stat_match or {}).get("tgId") or "").strip(),
                reset=safe_int((client_match or {}).get("reset"), safe_int((stat_match or {}).get("reset"))),
                comment=str((client_match or {}).get("comment") or "").strip(),
                created_at_ms=safe_int((client_match or {}).get("created_at"), now_ms()),
                updated_at_ms=safe_int((client_match or {}).get("updated_at"), now_ms()),
            )
        return None

    async def add_volume(self, sub_id: str, gb_add: int) -> SubscriptionDetail:
        detail = await self.find_subscription(sub_id, use_cache=False)
        if not detail:
            raise RuntimeError("subscription was not found on the panel")
        await self.upsert_client(detail, total_bytes=detail.total_bytes + gb_to_bytes(gb_add))
        return await self.find_subscription(sub_id) or detail

    async def renew_subscription(self, sub_id: str, gb_add: int) -> SubscriptionDetail:
        detail = await self.find_subscription(sub_id, use_cache=False)
        if not detail:
            raise RuntimeError("subscription was not found on the panel")
        expiry_time = int(detail.expiry_time or 0)
        if expiry_time > 0 and expiry_time < now_ms():
            expiry_time = now_ms() + (30 * 24 * 60 * 60 * 1000)
        await self.upsert_client(
            detail,
            total_bytes=detail.total_bytes + gb_to_bytes(gb_add),
            enable=True,
            expiry_time=expiry_time,
        )
        return await self.find_subscription(sub_id, use_cache=False) or detail

    async def set_total_volume(self, sub_id: str, total_gb: int) -> SubscriptionDetail:
        detail = await self.find_subscription(sub_id, use_cache=False)
        if not detail:
            raise RuntimeError("subscription was not found on the panel")
        total_bytes = gb_to_bytes(total_gb)
        if total_bytes < detail.used_bytes:
            raise RuntimeError("new total volume is lower than already-used traffic")
        await self.upsert_client(detail, total_bytes=total_bytes)
        return await self.find_subscription(sub_id) or detail

    async def delete_subscription(self, sub_id: str) -> None:
        detail = await self.find_subscription(sub_id, use_cache=False)
        if not detail:
            return
        async with self._mutation_semaphore:
            await self.request("POST", f"/panel/api/inbounds/{int(detail.inbound_id)}/delClient/{detail.client_uuid}")
            self._invalidate_inbounds_cache()

    async def set_enabled(self, sub_id: str, enabled: bool) -> None:
        detail = await self.find_subscription(sub_id, use_cache=False)
        if not detail:
            raise RuntimeError("subscription was not found on the panel")
        await self.upsert_client(detail, enable=enabled)

    async def fetch_subscription_config(self, sub_link: str) -> str:
        response = await self._request_with_retries(
            "GET",
            sub_link,
            operation="fetch_subscription_config",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        response.raise_for_status()
        body = await asyncio.to_thread(lambda: response.text or "")
        match = await asyncio.to_thread(
            re.search,
            r'<textarea id="subscription-links"[^>]*>(.*?)</textarea>',
            body,
            re.DOTALL,
        )
        return html.unescape(match.group(1)).strip() if match else ""

    async def fetch_config_uris(self, sub_link: str) -> list[str]:
        """Return the raw config URIs (vless://, vmess://, trojan://, ...) for a
        subscription, by reading the sub endpoint as a normal client would.

        Used for the infinite (fair-usage) package, where we deliver the actual
        config links instead of the subscription URL — so the buyer cannot derive
        the sub link from what they receive.
        """
        import base64

        response = await self._request_with_retries(
            "GET",
            sub_link,
            operation="fetch_config_uris",
            headers={"User-Agent": "v2rayNG/1.8.18", "Accept": "*/*"},
        )
        response.raise_for_status()
        text = (await asyncio.to_thread(lambda: response.text or "")).strip()

        def _lines(blob: str) -> list[str]:
            return [ln.strip() for ln in blob.splitlines() if "://" in ln]

        # 3x-ui returns a base64 blob for client User-Agents.
        try:
            decoded = base64.b64decode(text + "=" * (-len(text) % 4)).decode("utf-8", "ignore")
            if "://" in decoded:
                return _lines(decoded)
        except Exception:
            pass
        if "://" in text:
            return _lines(text)
        # HTML fallback (older panels with a textarea).
        match = re.search(r'<textarea id="subscription-links"[^>]*>(.*?)</textarea>', text, re.DOTALL)
        return _lines(html.unescape(match.group(1))) if match else []
