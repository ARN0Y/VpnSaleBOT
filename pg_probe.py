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

    print("\n== done — paste everything above ==")


if __name__ == "__main__":
    main()
