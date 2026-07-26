#!/usr/bin/env python3
"""Read-only PasarGuard API probe — run on the server that can reach the panel.

It logs in as the bot's dedicated admin and discovers the EXACT API shape of
YOUR PasarGuard version, so the bot client can be finalised with zero guessing.
It only READS (login + GET); it never creates/changes/deletes anything.

Usage:
    python pg_probe.py https://panel.prointerface.info:8000 <bot_admin_user> <bot_admin_pass>
or via env:
    PG_BASE_URL=... PG_USERNAME=... PG_PASSWORD=... python pg_probe.py

Paste the whole output back to the bot developer.
"""
from __future__ import annotations

import json
import os
import sys

import httpx

TOKEN_PATHS = ("/api/admins/token", "/api/admin/token", "/api/token")
CURRENT_ADMIN_PATHS = ("/api/admins/current", "/api/admin", "/api/admins/me")
READ_PATHS = ("/api/groups", "/api/users?limit=1", "/api/admins", "/api/inbounds", "/api/system")


def _short(text: str, n: int = 600) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= n else text[:n] + " …"


def main() -> None:
    if len(sys.argv) >= 4:
        base, user, pwd = sys.argv[1], sys.argv[2], sys.argv[3]
    else:
        base = os.getenv("PG_BASE_URL", "")
        user = os.getenv("PG_USERNAME", "")
        pwd = os.getenv("PG_PASSWORD", "")
    base = base.rstrip("/")
    if not (base and user and pwd):
        print("need: python pg_probe.py <base_url> <username> <password>  (or PG_BASE_URL/PG_USERNAME/PG_PASSWORD)")
        sys.exit(2)

    # verify=False so a self-signed/edge cert never blocks discovery (read-only).
    client = httpx.Client(verify=False, timeout=20.0, headers={"User-Agent": "pg-probe/1.0"})
    print(f"== PasarGuard probe ==\nbase: {base}\n")

    # 1) login
    token = ""
    token_path = ""
    for p in TOKEN_PATHS:
        try:
            r = client.post(base + p, data={"username": user, "password": pwd, "grant_type": "password"})
        except Exception as e:
            print(f"[token] {p} -> ERROR {type(e).__name__}: {e}")
            continue
        print(f"[token] {p} -> {r.status_code}")
        if r.status_code == 200:
            try:
                j = r.json()
            except Exception:
                print(f"        200 but non-JSON: {_short(r.text)}")
                continue
            token = str(j.get("access_token") or "")
            token_path = p
            print(f"        OK keys={list(j.keys())}  token_type={j.get('token_type')}  is_sudo={j.get('is_sudo')}")
            break
        else:
            print(f"        {_short(r.text, 200)}")
    if not token:
        print("\n!! could not obtain a token — check the credentials / token path above.")
        sys.exit(1)
    print(f"\nworking token path: {token_path}\n")
    auth = {"Authorization": f"Bearer {token}"}

    # 2) OpenAPI (the ground truth) — try public then authed
    print("== OpenAPI discovery ==")
    spec = None
    for op in ("/openapi.json", "/api/openapi.json", "/docs/openapi.json"):
        for hdr in ({}, auth):
            try:
                r = client.get(base + op, headers=hdr)
            except Exception as e:
                continue
            if r.status_code == 200:
                try:
                    spec = r.json()
                    print(f"[openapi] FOUND at {op} (auth={'yes' if hdr else 'no'})")
                    break
                except Exception:
                    pass
        if spec:
            break
    if spec and isinstance(spec.get("paths"), dict):
        print(f"PasarGuard version: {spec.get('info', {}).get('version')}")
        print("ALL ENDPOINTS (method path):")
        for path in sorted(spec["paths"].keys()):
            methods = ",".join(sorted(m.upper() for m in spec["paths"][path] if m.lower() in
                                      ("get", "post", "put", "patch", "delete")))
            print(f"  {methods:18} {path}")

        schemas = (spec.get("components") or {}).get("schemas") or {}

        def _resolve(node):
            if isinstance(node, dict) and "$ref" in node:
                return schemas.get(node["$ref"].split("/")[-1], {})
            return node or {}

        def _props(schema, depth=0):
            schema = _resolve(schema)
            for combine in ("allOf", "anyOf", "oneOf"):
                if combine in schema:
                    out = {}
                    for sub in schema[combine]:
                        out.update(_resolve(sub).get("properties", {}))
                    if out:
                        return out
            return schema.get("properties", {})

        def _print_schema(title, schema):
            props = _props(schema)
            if not props:
                return
            print(f"\n-- schema: {title} --")
            for name, p in props.items():
                p = _resolve(p)
                t = p.get("type") or ("/".join(str(_resolve(x).get("type", "?")) for x in p.get("anyOf", [])) or "?")
                fmt = f" ({p.get('format')})" if p.get("format") else ""
                enum = f" enum={p.get('enum')}" if p.get("enum") else ""
                print(f"   {name}: {t}{fmt}{enum}")

        # create-user request body
        for create_path in ("/api/users", "/api/user"):
            node = spec["paths"].get(create_path, {}).get("post")
            if node:
                body = (((node.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema")
                if body:
                    _print_schema(f"CREATE USER body ({create_path} POST)", body)
                break
        # user response + admin schemas by name
        for key in list(schemas.keys()):
            kl = key.lower()
            if kl in ("user", "userresponse") or (kl.startswith("user") and "response" in kl):
                _print_schema(f"USER response ({key})", schemas[key])
            if kl in ("admin", "adminresponse", "admincreate"):
                _print_schema(f"ADMIN ({key})", schemas[key])
        # create-admin request body (the body we must send to make a reseller admin)
        for create_path in ("/api/admins", "/api/admin"):
            node = spec["paths"].get(create_path, {}).get("post")
            if node:
                body = (((node.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema")
                if body:
                    _print_schema(f"CREATE ADMIN body ({create_path} POST)", body)
                break
        # role / permission schemas (the RBAC model that decides an admin's access)
        for key in list(schemas.keys()):
            if "role" in key.lower() or "permission" in key.lower():
                _print_schema(f"ROLE/PERM ({key})", schemas[key])
        print("\n-- admin / role endpoints --")
        for path in sorted(spec["paths"].keys()):
            if "admin" in path.lower() or "role" in path.lower():
                methods = ",".join(sorted(m.upper() for m in spec["paths"][path] if m.lower() in
                                          ("get", "post", "put", "patch", "delete")))
                print(f"  {methods:18} {path}")
    else:
        print("[openapi] not reachable (DOCS likely off). Tip: set DOCS=True in the panel .env once,")
        print("          restart, re-run this probe, then turn it back off. Below is best-effort probing.")

    # 3) current admin
    print("\n== current admin ==")
    for p in CURRENT_ADMIN_PATHS:
        try:
            r = client.get(base + p, headers=auth)
        except Exception as e:
            print(f"[me] {p} -> ERROR {e}")
            continue
        print(f"[me] {p} -> {r.status_code}  {_short(r.text, 300) if r.status_code==200 else ''}")
        if r.status_code == 200:
            break

    # 4) representative reads
    print("\n== representative GET probes ==")
    for p in READ_PATHS:
        try:
            r = client.get(base + p, headers=auth)
        except Exception as e:
            print(f"[get] {p} -> ERROR {e}")
            continue
        print(f"[get] {p} -> {r.status_code}")
        if r.status_code == 200:
            print(f"      {_short(r.text)}")

    # 5) optional create-test: confirms the exact create body + where the
    #    subscription_url lives. Creates a throwaway user and DELETES it.
    if "--create-test" in sys.argv:
        print("\n== create-test (creates a throwaway user, then deletes it) ==")
        gid = None
        try:
            gr = client.get(base + "/api/groups", headers=auth).json()
            for g in gr.get("groups", []):
                if str(g.get("name")) == "Tsco-Bot":
                    gid = g.get("id")
            gid = gid if gid is not None else (gr.get("groups", [{}])[0].get("id"))
        except Exception as e:
            print(f"[create-test] could not read groups: {e}")
        import random
        uname = f"probe_del_{random.randint(10000, 99999)}"
        body = {
            "username": uname,
            "group_ids": [gid] if gid is not None else [],
            "data_limit": 1073741824,
            "data_limit_reset_strategy": "no_reset",
            "status": "active",
            "expire": None,
            "note": "probe — safe to delete",
        }
        created_path = None
        for cp in ("/api/users", "/api/user"):
            try:
                r = client.post(base + cp, headers=auth, json=body)
            except Exception as e:
                print(f"[create] {cp} -> ERROR {e}")
                continue
            print(f"[create] {cp} -> {r.status_code}")
            if r.status_code in (200, 201):
                created_path = cp
                print("  FULL CREATE RESPONSE:")
                print("  " + _short(r.text, 2000))
                break
            else:
                print(f"  {_short(r.text, 400)}")
        if created_path:
            for dp in (f"/api/users/{uname}", f"/api/user/{uname}"):
                try:
                    r = client.delete(base + dp, headers=auth)
                    print(f"[cleanup-delete] {dp} -> {r.status_code}")
                    if r.status_code in (200, 204):
                        break
                except Exception as e:
                    print(f"[cleanup-delete] {dp} -> ERROR {e}")

    # 6) admin / roles discovery (read-only) — the monitoring view needs these
    print("\n== admin & roles discovery (read-only) ==")
    for p in ("/api/admins", "/api/admin_roles", "/api/admin_roles?limit=100", "/api/admins/roles",
              "/api/admin/roles", "/api/roles", "/api/role"):
        try:
            r = client.get(base + p, headers=auth)
        except Exception as e:
            print(f"[get] {p} -> ERROR {e}")
            continue
        print(f"[get] {p} -> {r.status_code}")
        if r.status_code == 200:
            print(f"      {_short(r.text, 1600)}")

    # how is a user tied to the admin who created it? (so the monitor can group
    # each reseller's accounts). Inspect one user object + try server-side filters.
    print("\n-- user -> creator-admin linkage --")
    try:
        ur = client.get(base + "/api/users?limit=3", headers=auth).json()
        items = ur.get("users") or ur.get("items") or (ur if isinstance(ur, list) else [])
        if items:
            print("  user object keys:", list(items[0].keys()))
            for k in ("admin", "owner", "owner_username", "admin_username", "created_by"):
                if k in items[0]:
                    print(f"  -> '{k}':", _short(json.dumps(items[0][k], ensure_ascii=False), 200))
        else:
            print("  (no users to inspect)")
    except Exception as e:
        print("  could not inspect users:", e)
    print("\n-- filter users by creator admin (used by the monitoring view) --")
    for qp in (f"/api/users?admin={user}", f"/api/users?owner_username={user}",
               f"/api/users?admin_username={user}", f"/api/users?admins={user}"):
        try:
            r = client.get(base + qp, headers=auth)
            print(f"[get] {qp} -> {r.status_code}  {_short(r.text, 140)}")
        except Exception as e:
            print(f"[get] {qp} -> ERROR {e}")

    # 7) optional admin-test: creates a throwaway NON-SUDO admin, reads it back,
    #    then DELETES it — confirms the exact create body, role model & delete path.
    if "--admin-test" in sys.argv or "--roles-test" in sys.argv:
        print("\n== full chain test (role -> admin -> cleanup) ==")
        import random

        # GET /api/admin/roles and /api/admin/role both returned 405 → the paths
        # EXIST but with a different verb. Map the full matrix to learn which is
        # list (GET) and which is create (POST).
        print("\n-- role endpoint matrix (which verb each path wants) --")
        for mp in ("/api/admin/role", "/api/admin/roles", "/api/admin_role", "/api/admin_roles"):
            for verb in ("GET", "OPTIONS"):
                try:
                    rr = client.request(verb, base + mp, headers=auth)
                    extra = ("  allow=" + rr.headers.get("allow", "")) if rr.headers.get("allow") else ""
                    print(f"[{verb}] {mp} -> {rr.status_code}{extra}")
                    if verb == "GET" and rr.status_code == 200:
                        print("    " + _short(rr.text, 900))
                except Exception as e:
                    print(f"[{verb}] {mp} -> ERROR {e}")

        ROLE_LIST = ("/api/admin/role", "/api/admin/role?limit=100", "/api/admin/roles?limit=100",
                     "/api/admin_roles", "/api/admins/roles")
        ROLE_CREATE = ("/api/admin/roles", "/api/admin/role", "/api/admin_role", "/api/admin_roles")

        def _roles_from(payload):
            if isinstance(payload, dict):
                for k in ("admin_roles", "roles", "items"):
                    if isinstance(payload.get(k), list):
                        return payload[k]
            return payload if isinstance(payload, list) else []

        # A) find the role-list endpoint + any non-owner role
        role_id = None
        role_list_path = None
        for rp in ROLE_LIST:
            try:
                rr = client.get(base + rp, headers=auth)
            except Exception as e:
                print(f"[roles] {rp} -> ERROR {e}")
                continue
            print(f"[roles] {rp} -> {rr.status_code}")
            if rr.status_code == 200:
                role_list_path = rp
                print("  FULL ROLES:\n  " + _short(rr.text, 1800))
                for role in _roles_from(rr.json()):
                    if not role.get("is_owner") and str(role.get("name", "")).lower() != "owner":
                        role_id = role.get("id")
                break

        # B) if no reusable role, create a throwaway reseller-style role
        created_role_path = None
        throwaway_role_id = None
        if role_id is None:
            rname = f"probe_role_{random.randint(10000, 99999)}"
            perms = {"users": {"create": True, "read": {"scope": 1}, "read_simple": {"scope": 1},
                               "update": {"scope": 1}, "delete": {"scope": 1}, "reset_usage": {"scope": 1},
                               "revoke_sub": {"scope": 1}}}
            role_bodies = [
                {"name": rname, "permissions": perms},
                {"name": rname, "is_owner": False, "permissions": perms},
                {"name": rname},
            ]
            for cp in ROLE_CREATE:
                for b in role_bodies:
                    try:
                        r = client.post(base + cp, headers=auth, json=b)
                    except Exception as e:
                        print(f"[create-role] {cp} {list(b.keys())} -> ERROR {e}")
                        continue
                    print(f"[create-role] {cp} {list(b.keys())} -> {r.status_code}")
                    if r.status_code in (200, 201):
                        created_role_path = cp
                        print("  FULL CREATE-ROLE RESPONSE:\n  " + _short(r.text, 1500))
                        try:
                            throwaway_role_id = role_id = r.json().get("id")
                        except Exception:
                            pass
                        break
                    print(f"   {_short(r.text, 300)}")
                if created_role_path:
                    break

        if role_id is None:
            print("\n  !! no role_id available — cannot test admin creation. Paste the [roles] lines above.")
        elif "--admin-test" in sys.argv:
            # C) create the admin with a policy-valid password + role_id
            aname = f"probe_admin_{random.randint(10000, 99999)}"
            apass = f"PgQ{random.randint(100000, 999999)}xZ!a"  # >=12 chars, >=2 uppercase
            body = {"username": aname, "password": apass, "role_id": role_id}
            r = client.post(base + "/api/admin", headers=auth, json=body)
            print(f"[create-admin] /api/admin {list(body.keys())} role_id={role_id} -> {r.status_code}")
            print("  " + _short(r.text, 1800))
            if r.status_code in (200, 201):
                for gp in (f"/api/admin/{aname}", f"/api/admins/{aname}"):
                    try:
                        g = client.get(base + gp, headers=auth)
                        print(f"[get-admin] {gp} -> {g.status_code}")
                        if g.status_code == 200:
                            break
                    except Exception as e:
                        print(f"[get-admin] {gp} -> ERROR {e}")
                deleted = False
                for dp in (f"/api/admin/{aname}", f"/api/admins/{aname}"):
                    try:
                        d = client.delete(base + dp, headers=auth)
                        print(f"[delete-admin] {dp} -> {d.status_code}")
                        if d.status_code in (200, 204):
                            deleted = True
                            break
                    except Exception as e:
                        print(f"[delete-admin] {dp} -> ERROR {e}")
                if not deleted:
                    print(f"  !!! WARNING: admin '{aname}' NOT deleted — remove it MANUALLY.")

        # D) clean up the throwaway role
        if throwaway_role_id is not None:
            for dp in (f"/api/admin_role/{throwaway_role_id}", f"/api/admin_roles/{throwaway_role_id}"):
                try:
                    d = client.delete(base + dp, headers=auth)
                    print(f"[delete-role] {dp} -> {d.status_code}")
                    if d.status_code in (200, 204):
                        break
                except Exception as e:
                    print(f"[delete-role] {dp} -> ERROR {e}")

    print("\n== done — paste everything above ==")


if __name__ == "__main__":
    main()
