"""Client for the Athena panel's public API (L2TP/SSTP/WireGuard accounts).

Athena is a third backend beside 3x-ui and PasarGuard, and it is shaped
differently from both: an account is a **username and password** plus per-node
endpoints, not a subscription link. Everything here exists to keep that
difference from leaking into the rest of the bot.

Three rules from the panel's API contract drive this file:

  * accounts are addressed by **username**, never by a numeric id;
  * ``extend`` **adds** to what the account has, so an absolute expiry is never
    computed here — that would race with the clock and with any other caller;
  * a ``404`` can mean "not yours" rather than "does not exist", so a missing
    account is reported as missing and never as an error worth retrying.

A create that times out may well have succeeded, so ``create_user`` treats the
panel's ``409`` as success and re-reads the account. The alternative is charging
a customer twice for one account.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

LOG = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 25.0


class AthenaError(RuntimeError):
    """An Athena API call failed. ``status`` is the HTTP code when there was one."""

    def __init__(self, message: str, *, status: int = 0, detail: str = "") -> None:
        super().__init__(message)
        self.status = int(status or 0)
        self.detail = str(detail or "")


class AthenaAuthError(AthenaError):
    """The key is missing, invalid, revoked, or lacks the scope. Not retryable."""


class AthenaClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        verify_tls: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
        proxy_url: str = "",
    ) -> None:
        base = str(base_url or "").strip().rstrip("/")
        # Operators paste a bare host, the panel root, or the full API base.
        # Guessing wrong costs a support ticket, so accept all three.
        if base and not base.startswith(("http://", "https://")):
            base = f"https://{base}"
        if base.endswith("/api/v1"):
            base = base[: -len("/api/v1")]
        self.base_url = base
        self.api_url = f"{base}/api/v1" if base else ""
        self.api_key = str(api_key or "").strip()
        self.verify_tls = bool(verify_tls)
        self._timeout = float(timeout or DEFAULT_TIMEOUT)
        self._proxy = str(proxy_url or "").strip() or None
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    # ───────────────────────── plumbing ─────────────────────────

    async def _http(self) -> httpx.AsyncClient:
        async with self._lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.AsyncClient(
                    base_url=self.api_url,
                    timeout=self._timeout,
                    verify=self.verify_tls,
                    proxy=self._proxy,
                    trust_env=False,
                    headers={"X-API-Key": self.api_key, "Accept": "application/json"},
                )
            return self._client

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None and not client.is_closed:
            await client.aclose()

    @staticmethod
    def _detail(response: httpx.Response) -> str:
        try:
            body = response.json()
        except Exception:
            return (response.text or "")[:200]
        if isinstance(body, dict):
            return str(body.get("detail") or body)[:300]
        return str(body)[:300]

    async def _request(
        self,
        method: str,
        path: str,
        *,
        allow_404: bool = False,
        allow_409: bool = False,
        **kwargs: Any,
    ) -> Any:
        if not self.api_url or not self.api_key:
            raise AthenaAuthError("Athena panel is not configured")
        client = await self._http()
        try:
            response = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise AthenaError(f"Athena request failed: {exc}") from exc

        status = response.status_code
        if status in (401, 403):
            raise AthenaAuthError(
                "Athena rejected the API key", status=status, detail=self._detail(response)
            )
        if status == 429:
            # The window is fixed and resets on the minute. The caller decides
            # whether waiting is acceptable, so surface it instead of sleeping.
            raise AthenaError(
                "Athena rate limit reached", status=429, detail=self._detail(response)
            )
        if status == 404 and allow_404:
            return None
        if status == 409 and allow_409:
            return None
        if status >= 400:
            raise AthenaError(
                f"Athena API error {status}", status=status, detail=self._detail(response)
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except Exception:
            return {}

    # ───────────────────────── identity ─────────────────────────

    async def whoami(self) -> dict[str, Any]:
        data = await self._request("GET", "/me")
        return data if isinstance(data, dict) else {}

    async def probe(self) -> dict[str, Any]:
        """Cheap credential check for the admin panel's test button."""
        me = await self.whoami()
        return {
            "ok": True,
            "admin": str(me.get("admin") or ""),
            "role": str(me.get("role") or ""),
            "scopes": list(me.get("scopes") or []),
            "unrestricted": bool(me.get("unrestricted_scopes")),
            "rate_limit_per_minute": int(me.get("rate_limit_per_minute") or 0),
        }

    # ───────────────────────── accounts ─────────────────────────

    async def get_user(self, username: str) -> dict[str, Any] | None:
        """The account, or None when it does not exist **or is not ours**."""
        name = str(username or "").strip()
        if not name:
            return None
        data = await self._request("GET", f"/users/{name}", allow_404=True)
        return data if isinstance(data, dict) else None

    async def create_user(
        self,
        *,
        username: str,
        limit_gb: int = 0,
        duration_days: int = 0,
        password: str | None = None,
        node_id: int | None = None,
        outbound: str = "",
        note: str = "",
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Create an account and return it.

        ``limit_gb=0`` is unlimited and ``duration_days=0`` is no expiry, which
        is exactly how the plan model expresses those, so nothing is translated
        here. A 409 means the name already exists; for a retried create that is
        success, so the account is read back rather than failing the purchase.
        """
        name = str(username or "").strip()
        if not name:
            raise AthenaError("username is required")
        payload: dict[str, Any] = {
            "username": name,
            "limit_gb": max(0, int(limit_gb or 0)),
            "enabled": bool(enabled),
        }
        if password:
            payload["password"] = str(password)
        if int(duration_days or 0) > 0:
            payload["duration_days"] = int(duration_days)
        if node_id:
            payload["node_id"] = int(node_id)
        if str(outbound or "").strip():
            payload["outbound"] = str(outbound).strip()
        if note:
            payload["note"] = str(note)[:200]

        created = await self._request("POST", "/users", json=payload, allow_409=True)
        if created is None:
            existing = await self.get_user(name)
            if existing is None:
                # A 409 we cannot read back means the name belongs to somebody
                # else on this panel, so a different one is required.
                raise AthenaError(f"username '{name}' is taken", status=409)
            LOG.info("Athena create returned 409; reusing existing account %s", name)
            return existing
        return created if isinstance(created, dict) else {}

    async def extend_user(
        self, username: str, *, days: int = 0, gb: int = 0, reset_usage: bool = False
    ) -> dict[str, Any]:
        """Renew: ADDS days and GB to what the account already has."""
        payload: dict[str, Any] = {}
        if int(days or 0) > 0:
            payload["days"] = int(days)
        if int(gb or 0) > 0:
            payload["gb"] = int(gb)
        if reset_usage:
            payload["reset_usage"] = True
        if not payload:
            raise AthenaError("extend needs at least one of days, gb or reset_usage")
        data = await self._request("POST", f"/users/{str(username).strip()}/extend", json=payload)
        return data if isinstance(data, dict) else {}

    async def patch_user(self, username: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Set values on an account. This overwrites — use extend() to renew."""
        data = await self._request("PATCH", f"/users/{str(username).strip()}", json=dict(fields))
        return data if isinstance(data, dict) else {}

    async def set_enabled(self, username: str, enabled: bool) -> dict[str, Any]:
        action = "enable" if enabled else "disable"
        data = await self._request("POST", f"/users/{str(username).strip()}/{action}")
        return data if isinstance(data, dict) else {}

    async def delete_user(self, username: str) -> None:
        await self._request("DELETE", f"/users/{str(username).strip()}", allow_404=True)

    async def disconnect_user(self, username: str) -> dict[str, Any]:
        data = await self._request("POST", f"/users/{str(username).strip()}/disconnect")
        return data if isinstance(data, dict) else {}

    # ───────────────────────── system ─────────────────────────

    async def list_nodes(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/nodes")
        return list(data) if isinstance(data, list) else []

    async def list_outbounds(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/outbounds")
        return list(data) if isinstance(data, list) else []

    async def stats(self) -> dict[str, Any]:
        data = await self._request("GET", "/stats")
        return data if isinstance(data, dict) else {}


def connection_lines(account: dict[str, Any]) -> list[str]:
    """The endpoints a customer actually needs, in the order they matter.

    The panel resolves ``endpoints`` for the account's own node, so this needs
    to know nothing about nodes. An empty string means the protocol is not
    offered there, and is skipped rather than shown as a blank line.
    """
    endpoints = (account or {}).get("endpoints") or {}
    labels = (
        ("l2tp", "L2TP/IPsec"),
        ("l2tp_raw", "L2TP بدون IPsec"),
        ("sstp", "SSTP"),
        ("wireguard", "WireGuard"),
    )
    out: list[str] = []
    for key, label in labels:
        value = str(endpoints.get(key) or "").strip()
        if value:
            out.append(f"{label}: {value}")
    return out
