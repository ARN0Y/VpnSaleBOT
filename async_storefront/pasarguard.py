"""PasarGuard panel client (v5.x, Marzban-lineage).

PasarGuard is a FastAPI panel whose admin API is JWT-based (OAuth2 password
flow). It is a completely different backend from 3x-ui, so it lives in its own
module and never touches PanelClient.

Endpoint paths are centralised in ``PG_API`` and the auth path is auto-detected
from a small candidate list, so a version difference is a one-line fix and the
``test_connection``/probe surfaces the exact live shape. All connection settings
come from the bot's settings table (``pg_*`` keys) — no DB schema change.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

import httpx


class PasarGuardError(RuntimeError):
    """Raised for any PasarGuard API failure with a human-readable message."""


# Centralised endpoint templates. PasarGuard v5 (Marzban-lineage) conventions;
# confirmed/adjusted against the live panel via the probe. `{u}` = username.
# Confirmed against a live PasarGuard v5.0.1 panel (see pg_probe output): token
# is /api/admin/token, current admin is /api/admin, groups/users/admins as below.
# Single-user create/CRUD path (singular vs plural) is resolved at runtime from
# a candidate list and cached, so it self-adapts across point releases.
# Confirmed against live PasarGuard v5.0.1 (pg_probe --create-test):
#   token=/api/admin/token, me=/api/admin, list=/api/users (GET),
#   create=/api/user (POST 201), single=/api/user/{username} (GET/PUT/DELETE 204).
#   subscription_url is a top-level field on the user object.
#   Phase-2 admin/role paths confirmed from the official v5.0.1 source
#   (app/routers/admin.py, app/routers/admin_role.py): admins list=/api/admins,
#   create admin=POST /api/admin, single admin=/api/admin/{username} (PUT/DELETE),
#   usage=/api/admin/{username}/usage; roles list=/api/admin-roles (+/simple),
#   create role=POST /api/admin-role, single role=/api/admin-role/{id} (hyphen!).
#   Users carry admin:{id,username}; GET /api/users?admin=<username> filters.
PG_API = {
    "token_candidates": ("/api/admin/token", "/api/admins/token", "/api/token"),
    "current_admin_candidates": ("/api/admin", "/api/admins/current", "/api/admins/me"),
    "groups": "/api/groups",
    "users": "/api/users",          # list (GET) — supports ?admin=<username>
    "create": "/api/user",          # create (POST) — singular
    "user": "/api/user/{u}",        # single GET/PUT/DELETE — singular
    "user_reset": "/api/user/{u}/reset",
    "admins": "/api/admins",        # list (GET)
    "admin_create": "/api/admin",   # create (POST)
    "admin": "/api/admin/{u}",      # single GET/PUT/DELETE (username)
    "admin_usage": "/api/admin/{u}/usage",
    "roles": "/api/admin-roles",            # list (GET)
    "roles_simple": "/api/admin-roles/simple",
    "role_create": "/api/admin-role",       # create (POST)
    "role": "/api/admin-role/{rid}",        # single GET/PUT/DELETE
}


def _jwt_exp(token: str) -> int:
    """Best-effort read of a JWT's exp claim; 0 if not parseable."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8", "ignore"))
        return int(data.get("exp") or 0)
    except Exception:
        return 0


class PasarGuardClient:
    RETRY_DELAYS_SECONDS = (1.0, 2.0, 4.0)

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        verify_tls: bool = True,
        timeout_seconds: float = 20.0,
        proxy_url: str = "",
    ):
        self.base_url = (base_url or "").strip().rstrip("/")
        self.username = (username or "").strip()
        self.password = password or ""
        self._client = httpx.AsyncClient(
            verify=verify_tls,
            timeout=httpx.Timeout(timeout_seconds, connect=timeout_seconds, pool=timeout_seconds),
            proxy=(proxy_url or None),
            trust_env=False,
            headers={"User-Agent": "Bot-Shayan-PasarGuard/1.0", "Accept": "application/json"},
        )
        self._token: str | None = None
        self._token_exp: int = 0
        self._token_path: str | None = None
        self._login_lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    # ───────────────────────────── auth ─────────────────────────────
    async def _login(self, *, force: bool = False) -> str:
        async with self._login_lock:
            if not force and self._token and time.time() < (self._token_exp - 30):
                return self._token
            if not self.base_url or not self.username or not self.password:
                raise PasarGuardError("PasarGuard settings are incomplete (base url / username / password).")
            data = {"username": self.username, "password": self.password, "grant_type": "password"}
            candidates = (self._token_path,) if self._token_path else PG_API["token_candidates"]
            last_detail = ""
            for path in candidates:
                if not path:
                    continue
                try:
                    resp = await self._client.post(f"{self.base_url}{path}", data=data)
                except httpx.RequestError as exc:
                    last_detail = f"{path}: {exc.__class__.__name__}: {exc}"
                    continue
                if resp.status_code == 200:
                    try:
                        payload = resp.json()
                    except Exception:
                        last_detail = f"{path}: 200 but non-JSON body"
                        continue
                    token = str(payload.get("access_token") or "")
                    if token:
                        self._token = token
                        self._token_path = path
                        exp = _jwt_exp(token)
                        self._token_exp = exp if exp > 0 else int(time.time()) + 3600
                        return token
                    last_detail = f"{path}: 200 but no access_token in body"
                else:
                    last_detail = f"{path}: HTTP {resp.status_code} {(resp.text or '')[:160]}"
            raise PasarGuardError(f"PasarGuard login failed. Last: {last_detail}")

    async def _request(self, method: str, path: str, *, expect_json: bool = True, **kwargs: Any) -> Any:
        token = await self._login()
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {token}"}
        resp = await self._send_with_retries(method, url, headers=headers, **kwargs)
        if resp.status_code == 401:
            token = await self._login(force=True)
            headers["Authorization"] = f"Bearer {token}"
            resp = await self._send_with_retries(method, url, headers=headers, **kwargs)
        if resp.status_code >= 400:
            raise PasarGuardError(f"PasarGuard {method} {path} → HTTP {resp.status_code}: {(resp.text or '')[:240]}")
        if not expect_json:
            return resp
        try:
            return resp.json()
        except Exception:
            return None

    async def _send_with_retries(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_exc: httpx.RequestError | None = None
        for attempt in range(len(self.RETRY_DELAYS_SECONDS) + 1):
            try:
                return await self._client.request(method, url, **kwargs)
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt >= len(self.RETRY_DELAYS_SECONDS):
                    break
                await asyncio.sleep(self.RETRY_DELAYS_SECONDS[attempt])
        raise PasarGuardError(f"PasarGuard request error: {last_exc}")

    # ─────────────────────────── read ops ───────────────────────────
    async def current_admin(self) -> dict[str, Any]:
        token = await self._login()
        headers = {"Authorization": f"Bearer {token}"}
        last = ""
        for path in PG_API["current_admin_candidates"]:
            try:
                resp = await self._client.get(f"{self.base_url}{path}", headers=headers)
            except httpx.RequestError as exc:
                last = f"{path}: {exc}"
                continue
            if resp.status_code == 200:
                try:
                    return resp.json()
                except Exception:
                    return {}
            last = f"{path}: HTTP {resp.status_code}"
        raise PasarGuardError(f"current_admin failed. Last: {last}")

    async def list_groups(self) -> list[dict[str, Any]]:
        data = await self._request("GET", PG_API["groups"])
        if isinstance(data, dict):
            return list(data.get("groups") or data.get("items") or [])
        return list(data or [])

    async def resolve_group_ids(self, names: list[str]) -> list[int]:
        wanted = {str(n).strip().lower() for n in names if str(n).strip()}
        ids: list[int] = []
        for g in await self.list_groups():
            name = str(g.get("name") or g.get("group_name") or "").strip().lower()
            gid = g.get("id") if g.get("id") is not None else g.get("group_id")
            if name in wanted and gid is not None:
                try:
                    ids.append(int(gid))
                except (TypeError, ValueError):
                    continue
        return ids

    async def get_user(self, username: str) -> dict[str, Any] | None:
        try:
            return await self._request("GET", PG_API["user"].format(u=username))
        except PasarGuardError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise

    async def list_users(self, *, admin: str | None = None, offset: int = 0, limit: int = 100) -> Any:
        params: dict[str, Any] = {"offset": offset, "limit": limit}
        if admin:
            # field name varies by version (admin / owner_username / username); the
            # probe confirms it. Send the common one; harmless if ignored.
            params["owner_username"] = admin
        return await self._request("GET", PG_API["users"], params=params)

    # ────────────────────────── write ops ───────────────────────────
    @staticmethod
    def _iso(ts: int) -> str:
        import datetime as _dt

        return _dt.datetime.fromtimestamp(int(ts), tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def create_user(
        self,
        *,
        username: str,
        group_ids: list[int],
        data_limit_bytes: int = 0,
        expire: int = 0,
        status: str = "active",
        note: str = "",
    ) -> dict[str, Any]:
        """Create a user. ``expire`` is a unix timestamp (0 = never). v5 uses an
        ISO datetime for ``expire``; if the panel rejects that we retry with the
        raw unix int so it works across point releases."""
        base_body: dict[str, Any] = {
            "username": username,
            "group_ids": group_ids,
            "data_limit": int(data_limit_bytes),
            "data_limit_reset_strategy": "no_reset",
            "status": status,
            "note": note,
            "expire": None,
        }
        bodies: list[dict[str, Any]] = []
        if not expire or int(expire) <= 0:
            bodies.append(base_body)
        else:
            bodies.append({**base_body, "expire": self._iso(int(expire))})
            bodies.append({**base_body, "expire": int(expire)})  # unix fallback if ISO is rejected
        last_exc: PasarGuardError | None = None
        for i, body in enumerate(bodies):
            try:
                return await self._request("POST", PG_API["create"], json=body)
            except PasarGuardError as exc:
                last_exc = exc
                msg = str(exc).lower()
                # Only fall through to the unix-expire body on a validation error.
                if i + 1 < len(bodies) and ("http 422" in msg or "expire" in msg):
                    continue
                break
        # The create may have actually landed even though the response was lost
        # (timeout/retry). If the user now exists, treat it as success — never
        # create a duplicate or leave the buyer unpaid-for.
        existing = await self.get_user(username)
        if existing:
            return existing
        raise last_exc if last_exc else PasarGuardError("PasarGuard create_user failed")

    async def modify_user(self, username: str, fields: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", PG_API["user"].format(u=username), json=fields)

    async def reset_user_usage(self, username: str) -> Any:
        return await self._request("POST", PG_API["user_reset"].format(u=username))

    async def delete_user(self, username: str) -> None:
        await self._request("DELETE", PG_API["user"].format(u=username), expect_json=False)

    # ───────────────────────── admins (phase 2) ─────────────────────────
    @staticmethod
    def _as_list(data: Any, *keys: str) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for k in keys:
                if isinstance(data.get(k), list):
                    return data[k]
        return []

    async def list_admins(self) -> list[dict[str, Any]]:
        data = await self._request("GET", PG_API["admins"])
        return self._as_list(data, "admins", "items")

    async def get_admin(self, username: str) -> dict[str, Any] | None:
        """Fetch one admin by exact username. v5.0.1 has NO GET /api/admin/{u}
        (that path is PUT/DELETE only), so we filter the list endpoint."""
        target = (username or "").strip().lower()
        for admin in await self.list_admins():
            if str(admin.get("username", "")).strip().lower() == target:
                return admin
        return None

    async def create_admin(
        self,
        *,
        username: str,
        password: str,
        role_id: int,
        data_limit: int | None = None,
        note: str = "",
        telegram_id: int | None = None,
    ) -> dict[str, Any]:
        """Create a panel admin. role_id is REQUIRED by v5.0.1; the password
        policy is >=12 chars with >=2 uppercase (validated panel-side)."""
        body: dict[str, Any] = {"username": username, "password": password, "role_id": int(role_id)}
        if data_limit is not None:
            body["data_limit"] = int(data_limit)
        if note:
            body["note"] = note
        if telegram_id:
            body["telegram_id"] = int(telegram_id)
        return await self._request("POST", PG_API["admin_create"], json=body)

    async def modify_admin(self, username: str, **fields: Any) -> dict[str, Any]:
        return await self._request("PUT", PG_API["admin"].format(u=username), json=fields)

    async def delete_admin(self, username: str) -> None:
        await self._request("DELETE", PG_API["admin"].format(u=username), expect_json=False)

    async def admin_usage(self, username: str, *, period: str = "month") -> Any:
        try:
            return await self._request("GET", PG_API["admin_usage"].format(u=username) + f"?period={period}")
        except PasarGuardError:
            return None

    async def list_users_by_admin(self, username: str, *, limit: int = 0) -> list[dict[str, Any]]:
        """Every user created by a given admin (admin:{username} on each user)."""
        path = PG_API["users"] + f"?admin={username}"
        if limit:
            path += f"&limit={int(limit)}"
        data = await self._request("GET", path)
        return self._as_list(data, "users", "items")

    # ───────────────────────── admin roles ─────────────────────────
    async def list_roles(self, *, simple: bool = False) -> list[dict[str, Any]]:
        data = await self._request("GET", PG_API["roles_simple"] if simple else PG_API["roles"])
        return self._as_list(data, "admin_roles", "roles", "items")

    async def get_role(self, role_id: int) -> dict[str, Any] | None:
        try:
            data = await self._request("GET", PG_API["role"].format(rid=int(role_id)))
        except PasarGuardError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise
        return data if isinstance(data, dict) else None

    async def create_role(self, *, name: str, permissions: dict[str, Any], limits: dict[str, Any] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "permissions": permissions}
        if limits:
            body["limits"] = limits
        return await self._request("POST", PG_API["role_create"], json=body)

    async def delete_role(self, role_id: int) -> None:
        await self._request("DELETE", PG_API["role"].format(rid=int(role_id)), expect_json=False)

    @staticmethod
    def reseller_permissions() -> dict[str, Any]:
        """A safe default reseller permission set: full control over the
        admin's OWN users only (scope OWN=1), read-only on groups so they can
        assign one; no access to other admins, nodes, settings or system."""
        own = {"scope": 1}
        return {
            "users": {
                "create": True, "read": own, "read_simple": own, "update": own,
                "delete": own, "reset_usage": own, "revoke_sub": own, "activate_next_plan": own,
            },
            "groups": {"read": True, "read_simple": True},
        }

    async def ensure_reseller_role(self, name: str = "reseller") -> int:
        """Return the id of a reusable reseller role, creating it once if absent."""
        for role in await self.list_roles():
            if str(role.get("name", "")).strip().lower() == name.strip().lower() and not role.get("is_owner"):
                return int(role["id"])
        created = await self.create_role(name=name, permissions=self.reseller_permissions())
        return int(created["id"])

    async def system_info(self) -> dict[str, Any]:
        try:
            data = await self._request("GET", "/api/system")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    async def test_connection(self) -> dict[str, Any]:
        """Authenticate and read back enough to prove the bot can drive the panel.
        Returns a report dict (never raises) for the admin 'Test Connection' UI."""
        report: dict[str, Any] = {"ok": False}
        try:
            await self._login(force=True)
            report["token_path"] = self._token_path
            admin = await self.current_admin()
            report["admin_username"] = admin.get("username")
            role = admin.get("role") or {}
            report["is_owner"] = bool(role.get("is_owner")) or bool(admin.get("is_sudo"))
            groups = await self.list_groups()
            report["groups"] = [
                {"id": g.get("id"), "name": g.get("name"), "inbound_tags": g.get("inbound_tags")}
                for g in groups
            ]
            sysinfo = await self.system_info()
            if sysinfo:
                report["panel_version"] = sysinfo.get("version")
                report["total_users"] = sysinfo.get("total_user")
            report["ok"] = True
        except Exception as exc:
            report["error"] = str(exc)
        return report
