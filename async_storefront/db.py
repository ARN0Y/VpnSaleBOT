from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

import aiosqlite

from .env_sync import BUSINESS_ENV_TO_SETTING, INFRA_ENV_TO_SETTING, PANEL_ENV_TO_COLUMN
from .models import AgentAccess, PaymentMethod
from .util import iran_day_bounds_ts, now_ts


class AsyncDatabase:
    """Single async SQLite gateway.

    aiosqlite runs SQLite calls in a worker thread, so handlers do not block the
    asyncio loop. The explicit transaction lock prevents nested BEGIN IMMEDIATE
    collisions and makes wallet/credit updates deterministic under load.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None
        self._tx_lock = asyncio.Lock()
        self._schema_lock = asyncio.Lock()
        # Run-once guards: schema repair and legacy normalization are expensive
        # and were previously executed on *every* admin read. They only need to
        # run once per process, so flag them after init_schema/first repair.
        self._admin_schema_ready = False
        self._access_levels_normalized = False
        # Short-TTL cache for the dashboard aggregate so rapid refreshes do not
        # re-scan the tables. Indexed queries are fast; this just makes bursts O(1).
        self._stats_cache: tuple[int, dict[str, int]] | None = None
        self._stats_cache_ttl = 10
        self._stats_cache_lock = asyncio.Lock()

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.path, isolation_level=None, timeout=30)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=30000")
        await conn.execute("PRAGMA temp_store=MEMORY")
        await conn.execute("PRAGMA cache_size=-16000")       # ~16MB page cache
        await conn.execute("PRAGMA mmap_size=268435456")     # 256MB memory-mapped IO
        await conn.execute("PRAGMA wal_autocheckpoint=1000")
        self._conn = conn

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not connected")
        return self._conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def init_schema(self) -> None:
        await self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS panel_settings (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              base_url TEXT NOT NULL DEFAULT '',
              username TEXT NOT NULL DEFAULT '',
              password TEXT NOT NULL DEFAULT '',
              inbound_id INTEGER NOT NULL DEFAULT 0,
              sub_link_base TEXT NOT NULL DEFAULT '',
              cookie TEXT NOT NULL DEFAULT '',
              cookie_ts INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
              updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS users (
              user_id INTEGER PRIMARY KEY,
              first_name TEXT,
              username TEXT,
              joined_at INTEGER NOT NULL,
              referred_by INTEGER,
              approved_orders INTEGER NOT NULL DEFAULT 0,
              total_spent INTEGER NOT NULL DEFAULT 0,
              test_received INTEGER NOT NULL DEFAULT 0,
              last_spin_date TEXT,
              disabled INTEGER NOT NULL DEFAULT 0,
              disabled_reason TEXT,
              disabled_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS agents (
              user_id INTEGER PRIMARY KEY,
              price_per_gb INTEGER NOT NULL,
              access_level TEXT NOT NULL DEFAULT 'closed',
              created_at INTEGER NOT NULL,
              created_by INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wallets (
              user_id INTEGER PRIMARY KEY,
              balance_toman INTEGER NOT NULL DEFAULT 0,
              updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orders (
              order_id TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL,
              plan_id INTEGER NOT NULL,
              gb INTEGER NOT NULL,
              qty INTEGER NOT NULL DEFAULT 1,
              unit_price INTEGER NOT NULL DEFAULT 0,
              price INTEGER NOT NULL,
              discount_code TEXT,
              discount_amount INTEGER NOT NULL DEFAULT 0,
              final_price INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              created_at INTEGER NOT NULL,
              approved_at INTEGER,
              rejected_at INTEGER,
              receipt_file_id TEXT,
              payment_method TEXT NOT NULL DEFAULT 'rial',
              crypto_trx_total INTEGER NOT NULL DEFAULT 0,
              crypto_usdt_total INTEGER NOT NULL DEFAULT 0,
              order_type TEXT NOT NULL DEFAULT 'purchase',
              target_sub_id TEXT,
              client_name TEXT
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
              sub_id TEXT PRIMARY KEY,
              order_id TEXT,
              user_id INTEGER NOT NULL,
              inbound_id INTEGER NOT NULL,
              gb INTEGER NOT NULL,
              base_gb INTEGER,
              qty INTEGER NOT NULL,
              created_at INTEGER NOT NULL,
              client_uuid TEXT,
              client_email TEXT,
              renewed_count INTEGER NOT NULL DEFAULT 0,
              last_renewed_at INTEGER,
              panel_total_bytes INTEGER NOT NULL DEFAULT 0,
              panel_used_bytes INTEGER NOT NULL DEFAULT 0,
              panel_remaining_bytes INTEGER NOT NULL DEFAULT 0,
              panel_up_bytes INTEGER NOT NULL DEFAULT 0,
              panel_down_bytes INTEGER NOT NULL DEFAULT 0,
              panel_enabled INTEGER,
              panel_last_online_ms INTEGER NOT NULL DEFAULT 0,
              panel_expiry_time INTEGER NOT NULL DEFAULT 0,
              panel_synced_at INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS stats (
              key TEXT PRIMARY KEY,
              value INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_ledger (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL,
              amount_toman INTEGER NOT NULL,
              kind TEXT NOT NULL,
              ref_id TEXT NOT NULL UNIQUE,
              created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS idempotency_keys (
              key TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL,
              order_id TEXT,
              status TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wallet_topups (
              topup_id TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL,
              method TEXT NOT NULL,
              amount_toman INTEGER NOT NULL DEFAULT 0,
              receipt_file_id TEXT,
              txid TEXT,
              status TEXT NOT NULL DEFAULT 'pending',
              created_at INTEGER NOT NULL,
              approved_at INTEGER,
              rejected_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS agent_requests (
              req_id TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL,
              text TEXT NOT NULL,
              photo_file_id TEXT,
              status TEXT NOT NULL DEFAULT 'pending',
              created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_test_configs (
              test_id TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL,
              sub_id TEXT NOT NULL UNIQUE,
              created_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'created'
            );

            CREATE TABLE IF NOT EXISTS admin_events (
              event_id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'queued',
              title TEXT NOT NULL,
              payload_json TEXT NOT NULL DEFAULT '{}',
              total_count INTEGER NOT NULL DEFAULT 0,
              success_count INTEGER NOT NULL DEFAULT 0,
              failed_count INTEGER NOT NULL DEFAULT 0,
              attempt_count INTEGER NOT NULL DEFAULT 0,
              last_error TEXT,
              created_at INTEGER NOT NULL,
              started_at INTEGER,
              finished_at INTEGER,
              dismissed_at INTEGER
            );
            """
        )
        await self._ensure_columns(
            "agents",
            {
                "credit_limit_toman": "INTEGER NOT NULL DEFAULT 0",
                "credit_used_toman": "INTEGER NOT NULL DEFAULT 0",
                "daily_gb_limit": "INTEGER NOT NULL DEFAULT 0",
                "monthly_gb_limit": "INTEGER NOT NULL DEFAULT 0",
                "daily_test_limit": "INTEGER NOT NULL DEFAULT 0",
                "permissions": "TEXT NOT NULL DEFAULT '[\"buy\",\"test\"]'",
                "disabled": "INTEGER NOT NULL DEFAULT 0",
            },
        )
        await self._ensure_columns(
            "orders",
            {
                "order_id": "TEXT",
                "user_id": "INTEGER NOT NULL DEFAULT 0",
                "plan_id": "INTEGER NOT NULL DEFAULT 0",
                "gb": "INTEGER NOT NULL DEFAULT 0",
                "qty": "INTEGER NOT NULL DEFAULT 1",
                "unit_price": "INTEGER NOT NULL DEFAULT 0",
                "price": "INTEGER NOT NULL DEFAULT 0",
                "discount_code": "TEXT",
                "discount_amount": "INTEGER NOT NULL DEFAULT 0",
                "final_price": "INTEGER NOT NULL DEFAULT 0",
                "status": "TEXT NOT NULL DEFAULT 'pending'",
                "created_at": "INTEGER NOT NULL DEFAULT 0",
                "approved_at": "INTEGER",
                "rejected_at": "INTEGER",
                "receipt_file_id": "TEXT",
                "payment_method": "TEXT NOT NULL DEFAULT 'rial'",
                "client_name": "TEXT",
                "crypto_trx_total": "INTEGER NOT NULL DEFAULT 0",
                "crypto_usdt_total": "INTEGER NOT NULL DEFAULT 0",
                "order_type": "TEXT NOT NULL DEFAULT 'purchase'",
                "target_sub_id": "TEXT",
            },
        )
        await self._backfill_legacy_orders()
        await self._ensure_columns(
            "users",
            {
                "legacy_total_spent_toman": "INTEGER NOT NULL DEFAULT 0",
                "legacy_total_gb_purchased": "INTEGER NOT NULL DEFAULT 0",
                "legacy_approved_orders": "INTEGER NOT NULL DEFAULT 0",
                "disabled": "INTEGER NOT NULL DEFAULT 0",
                "disabled_reason": "TEXT",
                "disabled_at": "INTEGER",
            },
        )
        await self._ensure_columns(
            "subscriptions",
            {
                "order_id": "TEXT",
                "panel_total_bytes": "INTEGER NOT NULL DEFAULT 0",
                "panel_used_bytes": "INTEGER NOT NULL DEFAULT 0",
                "panel_remaining_bytes": "INTEGER NOT NULL DEFAULT 0",
                "panel_up_bytes": "INTEGER NOT NULL DEFAULT 0",
                "panel_down_bytes": "INTEGER NOT NULL DEFAULT 0",
                "panel_enabled": "INTEGER",
                "panel_last_online_ms": "INTEGER NOT NULL DEFAULT 0",
                "panel_expiry_time": "INTEGER NOT NULL DEFAULT 0",
                "panel_synced_at": "INTEGER NOT NULL DEFAULT 0",
                "is_test": "INTEGER NOT NULL DEFAULT 0",
                "test_expires_at": "INTEGER NOT NULL DEFAULT 0",
                "is_infinite": "INTEGER NOT NULL DEFAULT 0",
            },
        )
        await self._ensure_columns(
            "panel_settings",
            {
                "base_url": "TEXT NOT NULL DEFAULT ''",
                "username": "TEXT NOT NULL DEFAULT ''",
                "password": "TEXT NOT NULL DEFAULT ''",
                "inbound_id": "INTEGER NOT NULL DEFAULT 0",
                "sub_link_base": "TEXT NOT NULL DEFAULT ''",
                "cookie": "TEXT NOT NULL DEFAULT ''",
                "cookie_ts": "INTEGER NOT NULL DEFAULT 0",
                "created_at": "INTEGER NOT NULL DEFAULT 0",
                "updated_at": "INTEGER NOT NULL DEFAULT 0",
            },
        )
        await self._ensure_columns(
            "wallet_topups",
            {
                "method": "TEXT NOT NULL DEFAULT 'card'",
                "amount_toman": "INTEGER NOT NULL DEFAULT 0",
                "receipt_file_id": "TEXT",
                "txid": "TEXT",
                "status": "TEXT NOT NULL DEFAULT 'pending'",
                "created_at": "INTEGER NOT NULL DEFAULT 0",
                "approved_at": "INTEGER",
                "rejected_at": "INTEGER",
            },
        )
        await self._ensure_columns(
            "agent_requests",
            {
                "photo_file_id": "TEXT",
                "status": "TEXT NOT NULL DEFAULT 'pending'",
                "created_at": "INTEGER NOT NULL DEFAULT 0",
            },
        )
        await self._ensure_columns(
            "admin_events",
            {
                "kind": "TEXT NOT NULL DEFAULT ''",
                "status": "TEXT NOT NULL DEFAULT 'queued'",
                "title": "TEXT NOT NULL DEFAULT ''",
                "payload_json": "TEXT NOT NULL DEFAULT '{}'",
                "total_count": "INTEGER NOT NULL DEFAULT 0",
                "success_count": "INTEGER NOT NULL DEFAULT 0",
                "failed_count": "INTEGER NOT NULL DEFAULT 0",
                "attempt_count": "INTEGER NOT NULL DEFAULT 0",
                "last_error": "TEXT",
                "created_at": "INTEGER NOT NULL DEFAULT 0",
                "started_at": "INTEGER",
                "finished_at": "INTEGER",
                "dismissed_at": "INTEGER",
            },
        )
        await self._ensure_columns(
            "agent_test_configs",
            {
                "user_id": "INTEGER NOT NULL DEFAULT 0",
                "sub_id": "TEXT NOT NULL DEFAULT ''",
                "created_at": "INTEGER NOT NULL DEFAULT 0",
                "expires_at": "INTEGER NOT NULL DEFAULT 0",
                "status": "TEXT NOT NULL DEFAULT 'created'",
            },
        )
        await self.conn.execute("INSERT OR IGNORE INTO panel_settings(id) VALUES(1)")
        await self.conn.execute(
            "UPDATE panel_settings SET created_at=COALESCE(NULLIF(created_at,0),?), updated_at=COALESCE(NULLIF(updated_at,0),?) WHERE id=1",
            (now_ts(), now_ts()),
        )
        await self.apply_env_overrides()   # env seeds first → wins over code defaults on fresh DB
        await self._seed_settings()         # INSERT OR IGNORE → fills remaining gaps
        await self._seed_stats()
        await self._ensure_indexes()
        await self.normalize_agent_access_levels()
        # init_schema already guarantees the full admin schema, so per-request
        # schema repair can be skipped for the lifetime of this process.
        self._admin_schema_ready = True

    @staticmethod
    def normalize_agent_access_value(value: Any) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"open", "agentaccess.open", "agentaccess_open", "agent_access.open"} or raw.endswith(".open"):
            return "open"
        return "closed"

    async def normalize_agent_access_levels(self) -> None:
        # Legacy-data repair only; the app already writes normalized values.
        # Run once per process instead of on every admin read.
        if self._access_levels_normalized:
            return
        await self.conn.execute(
            """
            UPDATE agents
            SET access_level='open'
            WHERE lower(COALESCE(access_level,'')) IN ('open','agentaccess.open','agentaccess_open','agent_access.open')
               OR lower(COALESCE(access_level,'')) LIKE '%.open'
            """
        )
        await self.conn.execute(
            """
            UPDATE agents
            SET access_level='closed'
            WHERE lower(COALESCE(access_level,'')) IN ('closed','agentaccess.closed','agentaccess_closed','agent_access.closed')
               OR lower(COALESCE(access_level,'')) LIKE '%.closed'
               OR COALESCE(access_level,'')=''
            """
        )
        self._access_levels_normalized = True

    async def _ensure_columns(self, table: str, columns: dict[str, str]) -> None:
        rows = await self.fetchall(f"PRAGMA table_info({table})")
        existing = {row["name"] for row in rows}
        for name, ddl in columns.items():
            if name not in existing:
                await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    async def _table_columns(self, table: str) -> set[str]:
        rows = await self.fetchall(f"PRAGMA table_info({table})")
        return {str(row["name"]) for row in rows}

    async def _ensure_indexes(self) -> None:
        """Create the indexes the admin read paths depend on.

        Without these, the correlated subqueries in admin_list_orders and the
        per-user aggregate in admin_list_users degrade to full table scans —
        which is exactly why the dashboard crawled as the user count grew.
        Idempotent (IF NOT EXISTS); runs once at startup.
        """
        await self.conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_orders_status_created ON orders(status, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_orders_created        ON orders(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_orders_user           ON orders(user_id);
            CREATE INDEX IF NOT EXISTS idx_orders_type_status    ON orders(order_type, status);
            CREATE INDEX IF NOT EXISTS idx_orders_target_sub     ON orders(target_sub_id);
            CREATE INDEX IF NOT EXISTS idx_subs_order            ON subscriptions(order_id);
            CREATE INDEX IF NOT EXISTS idx_subs_subid            ON subscriptions(sub_id);
            CREATE INDEX IF NOT EXISTS idx_subs_user             ON subscriptions(user_id);
            CREATE INDEX IF NOT EXISTS idx_subs_user_test        ON subscriptions(user_id, is_test);
            CREATE INDEX IF NOT EXISTS idx_topups_status_created ON wallet_topups(status, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_topups_user           ON wallet_topups(user_id);
            CREATE INDEX IF NOT EXISTS idx_agentreq_status       ON agent_requests(status, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_agentreq_user         ON agent_requests(user_id);
            CREATE INDEX IF NOT EXISTS idx_users_joined          ON users(joined_at DESC);
            CREATE INDEX IF NOT EXISTS idx_agent_ledger_user     ON agent_ledger(user_id);
            CREATE INDEX IF NOT EXISTS idx_test_configs_user     ON agent_test_configs(user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_admin_events_status   ON admin_events(status, created_at DESC);
            """
        )

    @staticmethod
    def _order_column_expr(columns: set[str], column: str, default_sql: str, alias: str = "o") -> str:
        if column not in columns:
            return default_sql
        return f"{alias}.{column}" if alias else column

    @classmethod
    def _order_select_list(cls, columns: set[str], alias: str = "o") -> str:
        defaults = {
            "order_id": "''",
            "user_id": "0",
            "plan_id": "0",
            "gb": "0",
            "qty": "1",
            "unit_price": "0",
            "price": "0",
            "discount_code": "NULL",
            "discount_amount": "0",
            "final_price": "0",
            "status": "'pending'",
            "created_at": "0",
            "approved_at": "NULL",
            "rejected_at": "NULL",
            "receipt_file_id": "NULL",
            "payment_method": "'rial'",
            "crypto_trx_total": "0",
            "crypto_usdt_total": "0",
            "order_type": "'purchase'",
            "target_sub_id": "NULL",
            "client_name": "NULL",
        }
        parts = [
            f"{cls._order_column_expr(columns, column, default, alias)} AS {column}"
            for column, default in defaults.items()
        ]
        return ",\n              ".join(parts)

    async def ensure_admin_runtime_schema(self) -> None:
        """Repair schema drift before admin read queries.

        Some deployments point the admin app at a legacy DB that was created
        before this async DAL existed. If the process starts against such a DB
        and a previous migration was interrupted or skipped, admin SELECTs must
        not be the first place we discover the missing columns.

        Idempotent and guarded so it only does real work once per process,
        rather than on every admin read as it did before.
        """
        if self._admin_schema_ready:
            return
        async with self._schema_lock:
            if self._admin_schema_ready:
                return
            try:
                await self._ensure_columns(
                    "agents",
                    {
                        "credit_limit_toman": "INTEGER NOT NULL DEFAULT 0",
                        "credit_used_toman": "INTEGER NOT NULL DEFAULT 0",
                        "daily_gb_limit": "INTEGER NOT NULL DEFAULT 0",
                        "monthly_gb_limit": "INTEGER NOT NULL DEFAULT 0",
                        "daily_test_limit": "INTEGER NOT NULL DEFAULT 0",
                        "permissions": "TEXT NOT NULL DEFAULT '[\"buy\",\"test\"]'",
                        "disabled": "INTEGER NOT NULL DEFAULT 0",
                    },
                )
                await self._ensure_columns(
                    "subscriptions",
                    {
                        "is_test": "INTEGER NOT NULL DEFAULT 0",
                        "test_expires_at": "INTEGER NOT NULL DEFAULT 0",
                    },
                )
                await self._ensure_columns(
                    "users",
                    {
                        "legacy_total_spent_toman": "INTEGER NOT NULL DEFAULT 0",
                        "legacy_total_gb_purchased": "INTEGER NOT NULL DEFAULT 0",
                        "legacy_approved_orders": "INTEGER NOT NULL DEFAULT 0",
                    },
                )
                await self._ensure_columns(
                    "orders",
                    {
                        "order_id": "TEXT",
                        "user_id": "INTEGER NOT NULL DEFAULT 0",
                        "plan_id": "INTEGER NOT NULL DEFAULT 0",
                        "gb": "INTEGER NOT NULL DEFAULT 0",
                        "qty": "INTEGER NOT NULL DEFAULT 1",
                        "unit_price": "INTEGER NOT NULL DEFAULT 0",
                        "price": "INTEGER NOT NULL DEFAULT 0",
                        "discount_code": "TEXT",
                        "discount_amount": "INTEGER NOT NULL DEFAULT 0",
                        "final_price": "INTEGER NOT NULL DEFAULT 0",
                        "status": "TEXT NOT NULL DEFAULT 'pending'",
                        "created_at": "INTEGER NOT NULL DEFAULT 0",
                        "approved_at": "INTEGER",
                        "rejected_at": "INTEGER",
                        "receipt_file_id": "TEXT",
                        "payment_method": "TEXT NOT NULL DEFAULT 'rial'",
                        "crypto_trx_total": "INTEGER NOT NULL DEFAULT 0",
                        "crypto_usdt_total": "INTEGER NOT NULL DEFAULT 0",
                        "order_type": "TEXT NOT NULL DEFAULT 'purchase'",
                        "target_sub_id": "TEXT",
                        "client_name": "TEXT",
                    },
                )
                await self._backfill_legacy_orders()
                self._admin_schema_ready = True
            except sqlite3.OperationalError:
                # Admin read queries below are built dynamically, so they can
                # still render a safe empty/degraded view even if a legacy
                # database cannot be altered in-place. Do not flag ready so a
                # later call can retry the repair.
                return

    async def _backfill_legacy_orders(self) -> None:
        stamp = now_ts()
        await self.conn.execute(
            """
            UPDATE orders
            SET created_at=COALESCE(NULLIF(created_at,0), NULLIF(approved_at,0), NULLIF(rejected_at,0), ?)
            WHERE created_at IS NULL OR created_at=0
            """,
            (stamp,),
        )
        await self.conn.execute(
            "UPDATE orders SET final_price=price WHERE COALESCE(final_price,0)=0 AND COALESCE(price,0)>0"
        )
        await self.conn.execute(
            "UPDATE orders SET price=final_price WHERE COALESCE(price,0)=0 AND COALESCE(final_price,0)>0"
        )
        await self.conn.execute(
            """
            UPDATE orders
            SET unit_price=CAST(final_price / MAX(1, gb * qty) AS INTEGER)
            WHERE COALESCE(unit_price,0)=0
              AND COALESCE(final_price,0)>0
              AND COALESCE(gb,0)>0
              AND COALESCE(qty,0)>0
            """
        )
        await self.conn.execute("UPDATE orders SET status='pending' WHERE COALESCE(status,'')=''")
        await self.conn.execute("UPDATE orders SET order_type='purchase' WHERE COALESCE(order_type,'')=''")
        await self.conn.execute(
            """
            UPDATE orders
            SET order_type='renewal'
            WHERE COALESCE(order_type,'purchase')='purchase'
              AND (COALESCE(plan_id,0)<0 OR COALESCE(order_id,'') LIKE '%-renew-%')
            """
        )

    async def _seed_stats(self) -> None:
        for key in (
            "approved_total_amount",
            "approved_count",
            "rejected_count",
            "approved_subscriptions_count",
            "approved_total_gb",
            "approved_total_usdt",
        ):
            await self.conn.execute(
                "INSERT OR IGNORE INTO stats(key,value) VALUES(?,0)",
                (key,),
            )

    async def _seed_settings(self) -> None:
        defaults = {
            "price_per_gb": "200000",
            "card_number": "",
            "card_name": "",
            "crypto_address": "TRC20 Address Not Set",
            "minimum_purchase_gb": "1",
            "backup_enabled": "1",
            "backup_interval_days": "1",
            "backup_interval_value": "20",
            "backup_interval_unit": "minutes",
            "backup_include_bot": "1",
            "backup_include_xui": "1",
            "backup_xui_timeout_seconds": "180",
            "backup_last_run_ts": "0",
            "backup_last_status": "never",
            "backup_last_file": "",
            "backup_last_error": "",
            "backup_send_to_telegram": "1",
            "backup_telegram_chat_id": "",
            "backup_defaults_v2_applied": "0",
            "admin_user_ids": "",
            "default_agent_credit_limit_toman": "0",
            "default_agent_price_per_gb": "0",
            "sales_status": "open",
            "sales_status_updated_at": "0",
            "sales_status_updated_by": "",
            "ui_mode": "modern",
            "infinite_enabled": "0",
            "free_test_enabled": "1",
            "infinite_cap_gb": "100",
            "infinite_duration_days": "30",
            "infinite_price": "0",
            "price_tiers": "",
            "broadcast_rate_per_second": "25",
            "broadcast_concurrency": "16",
            # Primary panel on/off (lets the admin stop selling from the main
            # 3x-ui panel, e.g. when only the second panel should be sold).
            "panel_enabled": "1",
            # Per-panel package lists (JSON). When non-empty, the bot sells these
            # packages for that panel instead of the per-GB flow.
            "panel_packages": "",
            "panel2_packages": "",
            # ── Second 3x-ui panel (optional, stored entirely in settings KV so no
            #    schema change). Sold as a dedicated buy option at its own price. ──
            "panel2_enabled": "0",
            "panel2_label": "سرور اختصاصی",
            "panel2_base_url": "",
            "panel2_username": "",
            "panel2_password": "",
            "panel2_inbound_id": "0",
            "panel2_sub_link_base": "",
            "panel2_use_proxy": "",
            "panel2_proxy_url": "",
            "panel2_price_per_gb": "7000",
            "panel2_cookie": "",
            # ── PasarGuard backend (optional). Sells navid-style packages
            #    (pg_packages), NOT per-GB. Stored in KV so no schema change. ──
            "primary_backend": "xui",      # xui | pasarguard (main buy flow source)
            "pg_enabled": "0",
            "pg_label": "سرور اختصاصی",
            "pg_base_url": "",
            "pg_username": "",
            "pg_password": "",
            "pg_group": "Tsco-Bot",
            "pg_verify_tls": "1",
            "pg_default_days": "30",
            "pg_packages": "",
        }
        await self.conn.executemany(
            "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
            defaults.items(),
        )
        marker = await self.fetchone("SELECT value FROM settings WHERE key='backup_defaults_v2_applied'")
        if str(marker["value"] if marker else "0") != "1":
            await self.conn.executemany(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                {
                    "backup_enabled": "1",
                    "backup_interval_value": "20",
                    "backup_interval_unit": "minutes",
                    "backup_interval_days": "1",
                    "backup_include_bot": "1",
                    "backup_include_xui": "1",
                    "backup_xui_timeout_seconds": "180",
                    "backup_send_to_telegram": "1",
                    "backup_telegram_chat_id": "",
                    "backup_defaults_v2_applied": "1",
                }.items(),
            )

    async def apply_env_overrides(self) -> None:
        """Apply .env values to the settings table.

        BUSINESS settings (card_number, price_per_gb, crypto_address, support_id):
          - Written with INSERT OR IGNORE → only seed on first run.
          - Admin panel edits persist through every subsequent restart.
          - Empty env vars are silently ignored.

        INFRASTRUCTURE settings (admin_user_ids, panel credentials):
          - Written with ON CONFLICT DO UPDATE → env is always authoritative.
          - Ops team manages these; empty values are ignored.
        """
        # ── Business settings: seed-only, admin panel owns them after first run ──
        seed_values: dict[str, str] = {}
        for env_key, db_key in BUSINESS_ENV_TO_SETTING.items():
            val = os.getenv(env_key, "").strip()
            if val:                         # never apply empty strings
                seed_values[db_key] = val
        if seed_values:
            await self.conn.executemany(
                "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
                seed_values.items(),
            )

        # ── Infrastructure settings: env is always authoritative (if non-empty) ──
        infra_values: dict[str, str] = {}
        for env_key, db_key in INFRA_ENV_TO_SETTING.items():
            val = os.getenv(env_key, "").strip()
            if val:
                infra_values[db_key] = val
        if infra_values:
            await self.conn.executemany(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                infra_values.items(),
            )

        panel_env_keys = tuple(PANEL_ENV_TO_COLUMN)
        if not any(key in os.environ and os.getenv(key, "").strip() for key in panel_env_keys):
            return

        current = await self.get_panel_settings()
        base_url = self._env_or_current("PANEL_BASE_URL", current, "base_url")
        username = self._env_or_current("PANEL_USERNAME", current, "username")
        password = os.getenv("PANEL_PASSWORD") if os.getenv("PANEL_PASSWORD", "").strip() else (str(current["password"] or "") if current else "")
        inbound_raw = os.getenv("PANEL_INBOUND_ID", "").strip()
        inbound_id = self._safe_int(inbound_raw, int(current["inbound_id"] or 0) if current else 0)
        sub_link_base = self._env_or_current("SUB_LINK_BASE", current, "sub_link_base")
        cookie = str(current["cookie"] or "") if current else ""
        cookie_ts = int(current["cookie_ts"] or 0) if current else 0
        await self.upsert_panel_settings(
            base_url=base_url,
            username=username,
            password=password,
            inbound_id=inbound_id,
            sub_link_base=sub_link_base,
            cookie=cookie,
            cookie_ts=cookie_ts,
        )

    @staticmethod
    def _env_or_current(env_key: str, current: aiosqlite.Row | None, column: str) -> str:
        value = os.getenv(env_key, "").strip()
        if value:
            return value
        return str(current[column] or "") if current else ""

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(str(value).strip())
        except Exception:
            return int(default)

    async def fetchone(self, query: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(query, tuple(params)) as cur:
            return await cur.fetchone()

    async def fetchall(self, query: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(query, tuple(params)) as cur:
            return await cur.fetchall()

    async def execute(self, query: str, params: Iterable[Any] = ()) -> aiosqlite.Cursor:
        return await self.conn.execute(query, tuple(params))

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._tx_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except Exception:
                await self.conn.execute("ROLLBACK")
                raise
            else:
                await self.conn.execute("COMMIT")

    async def get_setting(self, key: str, default: str = "") -> str:
        row = await self.fetchone("SELECT value FROM settings WHERE key=?", (key,))
        return str(row["value"]) if row else default

    async def set_setting(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    async def get_payment_cards(self) -> list[dict[str, str]]:
        """Configured payment cards as [{number, name}, ...].

        Falls back to the legacy single card (card_number/card_name) when no
        multi-card list is set, so older configs keep working.
        """
        raw = await self.get_setting("payment_cards", "")
        cards: list[dict[str, str]] = []
        try:
            for item in json.loads(raw or "[]"):
                number = str((item or {}).get("number", "")).strip()
                if number:
                    cards.append({"number": number, "name": str((item or {}).get("name", "")).strip()})
        except Exception:
            cards = []
        if not cards:
            number = (await self.get_setting("card_number", "")).strip()
            if number:
                cards.append({"number": number, "name": (await self.get_setting("card_name", "")).strip()})
        return cards

    async def next_payment_card(self) -> dict[str, str]:
        """Pick the next card in round-robin order (persisted index), so deposits
        spread evenly across the configured cards."""
        cards = await self.get_payment_cards()
        if not cards:
            return {"number": "", "name": ""}
        if len(cards) == 1:
            return cards[0]
        async with self.transaction() as conn:
            row = await self.fetchone("SELECT value FROM settings WHERE key='payment_card_rr'")
            idx = int(row["value"]) if row and str(row["value"]).isdigit() else 0
            card = cards[idx % len(cards)]
            await conn.execute(
                "INSERT INTO settings(key,value) VALUES('payment_card_rr',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str((idx + 1) % len(cards)),),
            )
        return card

    async def get_admin_user_ids(self, fallback: int | None = None) -> list[int]:
        raw = await self.get_setting("admin_user_ids", "")
        ids: list[int] = []
        for part in str(raw or "").replace("\n", ",").split(","):
            value = part.strip()
            if value.isdigit():
                item = int(value)
                if item not in ids:
                    ids.append(item)
        if not ids and fallback:
            ids.append(int(fallback))
        return ids

    async def get_panel_settings(self) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM panel_settings WHERE id=1")

    async def upsert_panel_settings(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        inbound_id: int,
        sub_link_base: str = "",
        cookie: str = "",
        cookie_ts: int = 0,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO panel_settings(
              id,base_url,username,password,inbound_id,sub_link_base,cookie,cookie_ts,created_at,updated_at
            )
            VALUES(1,?,?,?,?,?,?,?,strftime('%s','now'),strftime('%s','now'))
            ON CONFLICT(id) DO UPDATE SET
              base_url=excluded.base_url,
              username=excluded.username,
              password=excluded.password,
              inbound_id=excluded.inbound_id,
              sub_link_base=excluded.sub_link_base,
              cookie=excluded.cookie,
              cookie_ts=excluded.cookie_ts,
              updated_at=strftime('%s','now')
            """,
            (
                base_url.strip().rstrip("/"),
                username.strip(),
                password,
                int(inbound_id),
                sub_link_base.strip().rstrip("/"),
                cookie.strip(),
                int(cookie_ts or 0),
            ),
        )
        await self._mirror_panel_settings_to_kv(
            base_url=base_url,
            username=username,
            password=password,
            inbound_id=inbound_id,
            sub_link_base=sub_link_base,
            cookie=cookie,
            cookie_ts=cookie_ts,
        )

    async def update_panel_cookie(self, cookie: str, cookie_ts: int) -> None:
        await self.conn.execute(
            """
            UPDATE panel_settings
            SET cookie=?, cookie_ts=?, updated_at=strftime('%s','now')
            WHERE id=1
            """,
            (cookie.strip(), int(cookie_ts or 0)),
        )
        await self.set_setting("panel_cookie", cookie.strip())
        await self.set_setting("panel_cookie_ts", str(int(cookie_ts or 0)))

    async def _mirror_panel_settings_to_kv(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        inbound_id: int,
        sub_link_base: str,
        cookie: str,
        cookie_ts: int,
    ) -> None:
        values = {
            "panel_base_url": base_url.strip().rstrip("/"),
            "panel_username": username.strip(),
            "panel_password": password,
            "panel_inbound_id": str(int(inbound_id)),
            "sub_link_base": sub_link_base.strip().rstrip("/"),
            "panel_cookie": cookie.strip(),
            "panel_cookie_ts": str(int(cookie_ts or 0)),
        }
        await self.conn.executemany(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            values.items(),
        )

    async def upsert_user(self, user_id: int, first_name: str = "", username: str = "") -> None:
        await self.conn.execute(
            """
            INSERT INTO users(user_id,first_name,username,joined_at)
            VALUES(?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
              first_name=excluded.first_name,
              username=excluded.username
            """,
            (int(user_id), first_name, username, now_ts()),
        )

    async def get_access_flags(self, user_id: int) -> dict[str, bool]:
        row = await self.fetchone(
            """
            SELECT COALESCE(u.disabled,0) AS user_disabled,
                   COALESCE(a.disabled,0) AS agent_disabled
            FROM users u
            LEFT JOIN agents a ON a.user_id=u.user_id
            WHERE u.user_id=?
            """,
            (int(user_id),),
        )
        return {
            "user_disabled": bool(row["user_disabled"]) if row else False,
            "agent_disabled": bool(row["agent_disabled"]) if row else False,
        }

    async def get_user_account_snapshot(self, user_id: int) -> dict[str, Any]:
        row = await self.fetchone(
            """
            SELECT
              u.user_id,
              u.first_name,
              u.username,
              u.joined_at,
              COALESCE(u.approved_orders,0) + COALESCE(u.legacy_approved_orders,0) AS approved_orders,
              COALESCE(u.total_spent,0) + COALESCE(u.legacy_total_spent_toman,0) AS total_spent,
              COALESCE(w.balance_toman, 0) AS wallet_balance,
              (
                SELECT COUNT(*)
                FROM users refs
                WHERE refs.referred_by = u.user_id
              ) AS referral_count
            FROM users u
            LEFT JOIN wallets w ON w.user_id = u.user_id
            WHERE u.user_id=?
            """,
            (int(user_id),),
        )
        if not row:
            return {
                "user_id": int(user_id),
                "first_name": "",
                "username": "",
                "joined_at": now_ts(),
                "approved_orders": 0,
                "total_spent": 0,
                "wallet_balance": 0,
                "referral_count": 0,
            }
        return {
            "user_id": int(row["user_id"]),
            "first_name": str(row["first_name"] or ""),
            "username": str(row["username"] or ""),
            "joined_at": int(row["joined_at"] or now_ts()),
            "approved_orders": int(row["approved_orders"] or 0),
            "total_spent": int(row["total_spent"] or 0),
            "wallet_balance": int(row["wallet_balance"] or 0),
            "referral_count": int(row["referral_count"] or 0),
        }

    async def get_wallet_balance(self, user_id: int) -> int:
        row = await self.fetchone("SELECT balance_toman FROM wallets WHERE user_id=?", (int(user_id),))
        return int(row["balance_toman"]) if row else 0

    async def set_wallet_balance(self, user_id: int, balance_toman: int) -> int:
        balance = max(0, int(balance_toman))
        async with self.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO wallets(user_id,balance_toman,updated_at)
                VALUES(?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                  balance_toman=excluded.balance_toman,
                  updated_at=excluded.updated_at
                """,
                (int(user_id), balance, now_ts()),
            )
        return balance

    async def credit_wallet(self, user_id: int, amount_toman: int) -> int:
        async with self.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO wallets(user_id,balance_toman,updated_at)
                VALUES(?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                  balance_toman=wallets.balance_toman + excluded.balance_toman,
                  updated_at=excluded.updated_at
                """,
                (int(user_id), int(amount_toman), now_ts()),
            )
            row = await self.fetchone("SELECT balance_toman FROM wallets WHERE user_id=?", (int(user_id),))
            return int(row["balance_toman"]) if row else 0

    async def try_debit_wallet(self, user_id: int, amount_toman: int) -> bool:
        async with self.transaction() as conn:
            cur = await self.try_debit_wallet_in_transaction(conn, user_id, amount_toman)
            return cur.rowcount == 1

    async def try_debit_wallet_in_transaction(self, conn: aiosqlite.Connection, user_id: int, amount_toman: int) -> aiosqlite.Cursor:
        return await conn.execute(
            """
            UPDATE wallets
            SET balance_toman=balance_toman-?, updated_at=?
            WHERE user_id=? AND balance_toman>=?
            """,
            (int(amount_toman), now_ts(), int(user_id), int(amount_toman)),
        )

    async def create_order(
        self,
        *,
        order_id: str,
        user_id: int,
        plan_id: int,
        gb: int,
        qty: int,
        unit_price: int,
        base_total: int,
        final_total: int,
        payment_method: PaymentMethod,
        discount_code: str | None = None,
        discount_amount: int = 0,
        client_name: str | None = None,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO orders
              (order_id,user_id,plan_id,gb,qty,unit_price,price,discount_code,discount_amount,
               final_price,status,created_at,payment_method,order_type,target_sub_id,client_name)
            VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?)
            """,
            (
                order_id,
                int(user_id),
                int(plan_id),
                int(gb),
                int(qty),
                int(unit_price),
                int(base_total),
                discount_code,
                int(discount_amount),
                int(final_total),
                now_ts(),
                payment_method.value,
                "renewal" if int(plan_id) < 0 else "purchase",
                None,
                (client_name or "").strip() or None,
            ),
        )

    async def approve_order(self, order_id: str) -> bool:
        async with self.transaction() as conn:
            row = await self.fetchone("SELECT * FROM orders WHERE order_id=?", (order_id,))
            if not row or row["status"] != "pending":
                return False
            await conn.execute("UPDATE orders SET status='approved', approved_at=? WHERE order_id=?", (now_ts(), order_id))
            await conn.execute(
                "UPDATE users SET approved_orders=approved_orders+1, total_spent=total_spent+? WHERE user_id=?",
                (int(row["final_price"]), int(row["user_id"])),
            )
            await conn.execute("UPDATE stats SET value=value+1 WHERE key='approved_count'")
            await conn.execute("UPDATE stats SET value=value+? WHERE key='approved_total_amount'", (int(row["final_price"]),))
            await conn.execute("UPDATE stats SET value=value+? WHERE key='approved_subscriptions_count'", (int(row["qty"]),))
            await conn.execute("UPDATE stats SET value=value+? WHERE key='approved_total_gb'", (int(row["gb"]) * int(row["qty"]),))
            return True

    async def reject_order(self, order_id: str) -> None:
        async with self.transaction() as conn:
            await conn.execute(
                "UPDATE orders SET status='rejected', rejected_at=? WHERE order_id=? AND status='pending'",
                (now_ts(), order_id),
            )
            await conn.execute("UPDATE stats SET value=value+1 WHERE key='rejected_count'")

    async def insert_subscriptions(self, rows: Iterable[Any], *, order_id: str | None = None, is_infinite: bool = False) -> None:
        await self.conn.executemany(
            """
            INSERT OR REPLACE INTO subscriptions
              (sub_id,order_id,user_id,inbound_id,gb,base_gb,qty,created_at,client_uuid,client_email,renewed_count,is_infinite)
            VALUES (?,?,?,?,?,?,?,?,?,?,0,?)
            """,
            [
                (
                    r.sub_id,
                    order_id,
                    int(r.user_id),
                    int(r.inbound_id),
                    int(r.gb),
                    int(r.gb),
                    1,
                    now_ts(),
                    r.client_uuid,
                    r.client_email,
                    1 if is_infinite else 0,
                )
                for r in rows
            ],
        )

    async def insert_test_subscription(self, row: Any, *, test_id: str, expires_at: int, total_bytes: int) -> None:
        async with self.transaction() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO subscriptions(
                  sub_id,order_id,user_id,inbound_id,gb,base_gb,qty,created_at,
                  client_uuid,client_email,renewed_count,panel_total_bytes,
                  panel_remaining_bytes,panel_enabled,panel_expiry_time,panel_synced_at,
                  is_test,test_expires_at
                )
                VALUES(?,NULL,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?)
                """,
                (
                    row.sub_id,
                    int(row.user_id),
                    int(row.inbound_id),
                    0,
                    0,
                    1,
                    now_ts(),
                    row.client_uuid,
                    row.client_email,
                    int(total_bytes),
                    int(total_bytes),
                    1,
                    int(expires_at) * 1000,
                    now_ts(),
                    1,
                    int(expires_at),
                ),
            )
            await conn.execute(
                """
                INSERT INTO agent_test_configs(test_id,user_id,sub_id,created_at,expires_at,status)
                VALUES(?,?,?,?,?,'created')
                ON CONFLICT(test_id) DO UPDATE SET
                  sub_id=excluded.sub_id,
                  expires_at=excluded.expires_at,
                  status='created'
                """,
                (str(test_id), int(row.user_id), row.sub_id, now_ts(), int(expires_at)),
            )

    async def delete_subscriptions(self, sub_ids: Iterable[str]) -> None:
        ids = [str(sub_id).strip() for sub_id in sub_ids if str(sub_id).strip()]
        if not ids:
            return
        await self.conn.executemany(
            "DELETE FROM subscriptions WHERE sub_id=?",
            [(sub_id,) for sub_id in ids],
        )

    async def get_agent(self, user_id: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM agents WHERE user_id=?", (int(user_id),))

    async def get_agent_test_usage_today(self, user_id: int) -> dict[str, int]:
        await self.ensure_admin_runtime_schema()
        start_ts, end_ts = iran_day_bounds_ts()
        row = await self.fetchone(
            """
            SELECT COUNT(*) AS used
            FROM agent_test_configs
            WHERE user_id=?
              AND created_at>=?
              AND created_at<?
              AND status IN ('pending','created','active')
            """,
            (int(user_id), start_ts, end_ts),
        )
        agent = await self.get_agent(user_id)
        return {
            "used": int(row["used"] if row else 0),
            "limit": int(agent["daily_test_limit"] or 0) if agent and "daily_test_limit" in agent.keys() else 0,
        }

    async def get_active_subscriptions(self, user_id: int) -> list[dict[str, Any]]:
        rows = await self.fetchall(
            """
            SELECT sub_id, gb, qty, created_at, client_email,
                   panel_total_bytes, panel_used_bytes, panel_remaining_bytes,
                   panel_enabled, panel_expiry_time, panel_synced_at,
                   COALESCE(is_infinite,0) AS is_infinite
            FROM subscriptions
            WHERE user_id=?
              AND COALESCE(is_test,0)=0
            ORDER BY created_at DESC
            """,
            (int(user_id),),
        )
        return [
            {
                "sub_id": str(row["sub_id"] or ""),
                "gb": int(row["gb"] or 0),
                "qty": int(row["qty"] or 1),
                "created_at": int(row["created_at"] or 0),
                "client_email": str(row["client_email"] or ""),
                "panel_total_bytes": int(row["panel_total_bytes"] or 0),
                "panel_used_bytes": int(row["panel_used_bytes"] or 0),
                "panel_remaining_bytes": int(row["panel_remaining_bytes"] or 0),
                "panel_enabled": row["panel_enabled"],
                "panel_expiry_time": int(row["panel_expiry_time"] or 0),
                "panel_synced_at": int(row["panel_synced_at"] or 0),
                "is_infinite": int(row["is_infinite"] or 0),
            }
            for row in rows
        ]

    async def search_renewable_subscriptions_count(self, user_id: int, query: str = "") -> int:
        clean = str(query or "").strip()
        pattern = f"%{clean}%"
        row = await self.fetchone(
            """
            SELECT COUNT(*) AS c
            FROM subscriptions
            WHERE user_id=?
              AND COALESCE(is_test,0)=0
              AND (
                ?=''
                OR sub_id LIKE ?
                OR COALESCE(client_email,'') LIKE ?
              )
            """,
            (int(user_id), clean, pattern, pattern),
        )
        return int(row["c"] if row else 0)

    async def search_renewable_subscriptions(
        self,
        user_id: int,
        query: str = "",
        page: int = 0,
        page_size: int = 5,
    ) -> list[dict[str, Any]]:
        clean = str(query or "").strip()
        pattern = f"%{clean}%"
        safe_page = max(0, int(page))
        safe_page_size = min(20, max(1, int(page_size)))
        rows = await self.fetchall(
            """
            SELECT sub_id, gb, qty, created_at, client_email,
                   panel_total_bytes, panel_used_bytes, panel_remaining_bytes,
                   panel_enabled, panel_expiry_time, panel_synced_at
            FROM subscriptions
            WHERE user_id=?
              AND COALESCE(is_test,0)=0
              AND (
                ?=''
                OR sub_id LIKE ?
                OR COALESCE(client_email,'') LIKE ?
              )
            ORDER BY
              CASE
                WHEN ?<>'' AND COALESCE(client_email,'')=? THEN 0
                WHEN ?<>'' AND sub_id=? THEN 1
                WHEN ?<>'' AND COALESCE(client_email,'') LIKE ? THEN 2
                WHEN ?<>'' AND sub_id LIKE ? THEN 3
                ELSE 4
              END,
              created_at DESC
            LIMIT ? OFFSET ?
            """,
            (
                int(user_id),
                clean,
                pattern,
                pattern,
                clean,
                clean,
                clean,
                clean,
                clean,
                f"{clean}%",
                clean,
                f"{clean}%",
                safe_page_size,
                safe_page * safe_page_size,
            ),
        )
        return [dict(row) for row in rows]

    async def get_user_subscription_count(self, user_id: int) -> int:
        row = await self.fetchone("SELECT COUNT(*) AS c FROM subscriptions WHERE user_id=?", (int(user_id),))
        return int(row["c"] if row else 0)

    async def get_user_subscription_page(self, user_id: int, page: int = 0, page_size: int = 8) -> list[dict[str, Any]]:
        safe_page_size = min(20, max(1, int(page_size)))
        safe_page = max(0, int(page))
        rows = await self.fetchall(
            """
            SELECT sub_id, gb, qty, created_at, client_email,
                   panel_total_bytes, panel_used_bytes, panel_remaining_bytes,
                   panel_enabled, panel_expiry_time, panel_synced_at,
                   COALESCE(is_test,0) AS is_test,
                   COALESCE(test_expires_at,0) AS test_expires_at,
                   COALESCE(is_infinite,0) AS is_infinite
            FROM subscriptions
            WHERE user_id=?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (int(user_id), safe_page_size, safe_page * safe_page_size),
        )
        return [dict(row) for row in rows]

    async def get_user_total_purchased_gb(self, user_id: int) -> int:
        await self.ensure_admin_runtime_schema()
        row = await self.fetchone(
            """
            SELECT
              COALESCE((
                SELECT SUM(COALESCE(gb,0) * COALESCE(qty,1))
                FROM orders
                WHERE user_id=? AND status='approved'
                  AND COALESCE(order_type,'purchase') IN ('purchase','renewal')
              ),0) + COALESCE((
                SELECT legacy_total_gb_purchased
                FROM users
                WHERE user_id=?
              ),0) AS total_gb
            """,
            (int(user_id), int(user_id)),
        )
        return int(row["total_gb"] if row else 0)

    async def get_agent_recent_purchase_summary(self, user_id: int, seconds: int = 86400) -> dict[str, int]:
        await self.ensure_admin_runtime_schema()
        cutoff = now_ts() - max(1, int(seconds))
        row = await self.fetchone(
            """
            SELECT
              COALESCE(SUM(COALESCE(final_price,0)),0) AS total_toman,
              COALESCE(SUM(COALESCE(gb,0) * COALESCE(qty,1)),0) AS total_gb,
              COUNT(*) AS order_count
            FROM orders
            WHERE user_id=?
              AND status='approved'
              AND COALESCE(created_at,0)>=?
              AND COALESCE(order_type,'purchase') IN ('purchase','renewal')
            """,
            (int(user_id), cutoff),
        )
        return {
            "total_toman": int(row["total_toman"] if row else 0),
            "total_gb": int(row["total_gb"] if row else 0),
            "order_count": int(row["order_count"] if row else 0),
        }

    async def get_subscription_for_user(self, user_id: int, sub_id: str) -> dict[str, Any] | None:
        row = await self.fetchone(
            "SELECT * FROM subscriptions WHERE user_id=? AND sub_id=? AND COALESCE(is_test,0)=0",
            (int(user_id), str(sub_id).strip()),
        )
        return dict(row) if row else None

    async def create_wallet_topup_request(
        self,
        *,
        topup_id: str,
        user_id: int,
        method: str,
        amount_toman: int,
        receipt_file_id: str | None = None,
        txid: str | None = None,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO wallet_topups(topup_id,user_id,method,amount_toman,receipt_file_id,txid,status,created_at)
            VALUES(?,?,?,?,?,?, 'pending', ?)
            """,
            (
                topup_id,
                int(user_id),
                method,
                int(amount_toman),
                receipt_file_id,
                txid,
                now_ts(),
            ),
        )

    async def create_agent_request(
        self,
        *,
        req_id: str,
        user_id: int,
        text: str,
        photo_file_id: str | None = None,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO agent_requests(req_id,user_id,text,photo_file_id,status,created_at)
            VALUES(?,?,?,?, 'pending', ?)
            """,
            (req_id, int(user_id), text, photo_file_id, now_ts()),
        )

    async def dashboard_stats(self, *, use_cache: bool = True) -> dict[str, int]:
        if use_cache and self._stats_cache is not None:
            cached_at, cached_value = self._stats_cache
            if now_ts() - cached_at < self._stats_cache_ttl:
                return dict(cached_value)
        async with self._stats_cache_lock:
            # Re-check inside the lock so concurrent dashboard hits share one scan.
            if use_cache and self._stats_cache is not None:
                cached_at, cached_value = self._stats_cache
                if now_ts() - cached_at < self._stats_cache_ttl:
                    return dict(cached_value)
            value = await self._compute_dashboard_stats()
            self._stats_cache = (now_ts(), dict(value))
            return value

    async def _compute_dashboard_stats(self) -> dict[str, int]:
        await self.ensure_admin_runtime_schema()
        await self.normalize_agent_access_levels()
        order_columns = await self._table_columns("orders")
        status_expr = self._order_column_expr(order_columns, "status", "''", "")
        final_price_expr = self._order_column_expr(order_columns, "final_price", "0", "")
        gb_expr = self._order_column_expr(order_columns, "gb", "0", "")
        qty_expr = self._order_column_expr(order_columns, "qty", "1", "")
        users = await self.fetchone("SELECT COUNT(*) AS c FROM users")
        agents = await self.fetchone("SELECT COUNT(*) AS c FROM agents")
        open_agents = await self.fetchone("SELECT COUNT(*) AS c FROM agents WHERE lower(COALESCE(access_level,''))='open' OR lower(COALESCE(access_level,'')) LIKE '%.open'")
        closed_agents = await self.fetchone("SELECT COUNT(*) AS c FROM agents WHERE NOT (lower(COALESCE(access_level,''))='open' OR lower(COALESCE(access_level,'')) LIKE '%.open')")
        revenue = await self.fetchone(f"SELECT COALESCE(SUM({final_price_expr}),0) AS s FROM orders WHERE {status_expr}='approved'")
        traffic = await self.fetchone(f"SELECT COALESCE(SUM({gb_expr} * {qty_expr}),0) AS s FROM orders WHERE {status_expr}='approved'")
        legacy_revenue = await self.fetchone("SELECT COALESCE(SUM(legacy_total_spent_toman),0) AS s FROM users")
        legacy_traffic = await self.fetchone("SELECT COALESCE(SUM(legacy_total_gb_purchased),0) AS s FROM users")
        topups = await self.fetchone("SELECT COUNT(*) AS c FROM wallet_topups WHERE status='pending'")
        agent_requests = await self.fetchone("SELECT COUNT(*) AS c FROM agent_requests WHERE status='pending'")
        return {
            "users": int(users["c"] if users else 0),
            "agents": int(agents["c"] if agents else 0),
            "open_agents": int(open_agents["c"] if open_agents else 0),
            "closed_agents": int(closed_agents["c"] if closed_agents else 0),
            "revenue": int(revenue["s"] if revenue else 0) + int(legacy_revenue["s"] if legacy_revenue else 0),
            "traffic_gb": int(traffic["s"] if traffic else 0) + int(legacy_traffic["s"] if legacy_traffic else 0),
            "pending_topups": int(topups["c"] if topups else 0),
            "pending_agent_requests": int(agent_requests["c"] if agent_requests else 0),
        }

    async def admin_list_users(self, search: str = "", filter_by: str = "all", limit: int = 300, offset: int = 0) -> list[dict[str, Any]]:
        await self.ensure_admin_runtime_schema()
        await self.normalize_agent_access_levels()
        safe_limit = max(1, min(1000, int(limit)))
        safe_offset = max(0, int(offset))
        pattern = f"%{search.strip()}%"
        # filter_by is validated by the caller; safe to embed directly
        if filter_by == "agents":
            filter_clause = "AND a.user_id IS NOT NULL"
        elif filter_by == "users":
            filter_clause = "AND a.user_id IS NULL"
        else:
            filter_clause = ""
        rows = await self.fetchall(
            f"""
            SELECT
              u.user_id, u.first_name, u.username, u.joined_at,
              COALESCE(u.approved_orders,0) + COALESCE(u.legacy_approved_orders,0) AS approved_orders,
              COALESCE(u.total_spent,0) + COALESCE(u.legacy_total_spent_toman,0) AS total_spent,
              u.disabled,
              COALESCE(w.balance_toman,0) AS wallet_balance,
              CASE
                WHEN lower(COALESCE(a.access_level,''))='open' OR lower(COALESCE(a.access_level,'')) LIKE '%.open' THEN 'open'
                WHEN a.user_id IS NULL THEN NULL
                ELSE 'closed'
              END AS access_level,
              a.credit_limit_toman, a.credit_used_toman
              , COALESCE((
                SELECT SUM(COALESCE(o.gb,0) * COALESCE(o.qty,1))
                FROM orders o
                WHERE o.user_id=u.user_id AND o.status='approved'
                  AND COALESCE(o.order_type,'purchase') IN ('purchase','renewal')
              ),0) + COALESCE(u.legacy_total_gb_purchased,0) AS total_gb_purchased
            FROM users u
            LEFT JOIN wallets w ON w.user_id=u.user_id
            LEFT JOIN agents a ON a.user_id=u.user_id
            WHERE (?='' OR CAST(u.user_id AS TEXT) LIKE ? OR COALESCE(u.first_name,'') LIKE ? OR COALESCE(u.username,'') LIKE ?)
            {filter_clause}
            ORDER BY u.joined_at DESC
            LIMIT ? OFFSET ?
            """,
            (search.strip(), pattern, pattern, pattern, safe_limit, safe_offset),
        )
        return [dict(row) for row in rows]

    async def admin_user_detail(self, user_id: int) -> dict[str, Any] | None:
        await self.ensure_admin_runtime_schema()
        await self.normalize_agent_access_levels()
        test_start, test_end = iran_day_bounds_ts()
        row = await self.fetchone(
            """
            SELECT
              u.user_id, u.first_name, u.username, u.joined_at,
              COALESCE(u.approved_orders,0) + COALESCE(u.legacy_approved_orders,0) AS approved_orders,
              COALESCE(u.total_spent,0) + COALESCE(u.legacy_total_spent_toman,0) AS total_spent,
              COALESCE(u.disabled,0) AS user_disabled,
              u.disabled_reason, u.disabled_at,
              COALESCE(w.balance_toman,0) AS wallet_balance,
              a.price_per_gb,
              CASE
                WHEN lower(COALESCE(a.access_level,''))='open' OR lower(COALESCE(a.access_level,'')) LIKE '%.open' THEN 'open'
                WHEN a.user_id IS NULL THEN NULL
                ELSE 'closed'
              END AS access_level,
              a.credit_limit_toman, a.credit_used_toman,
              a.daily_gb_limit, a.monthly_gb_limit, a.daily_test_limit, a.disabled,
              COALESCE((
                SELECT COUNT(*)
                FROM agent_test_configs t
                WHERE t.user_id=u.user_id
                  AND t.created_at>=?
                  AND t.created_at<?
                  AND t.status IN ('pending','created','active')
              ),0) AS daily_test_used,
              COALESCE((
                SELECT SUM(COALESCE(o.gb,0) * COALESCE(o.qty,1))
                FROM orders o
                WHERE o.user_id=u.user_id AND o.status='approved'
                  AND COALESCE(o.order_type,'purchase') IN ('purchase','renewal')
              ),0) + COALESCE(u.legacy_total_gb_purchased,0) AS total_gb_purchased
            FROM users u
            LEFT JOIN wallets w ON w.user_id=u.user_id
            LEFT JOIN agents a ON a.user_id=u.user_id
            WHERE u.user_id=?
            """,
            (test_start, test_end, int(user_id)),
        )
        return dict(row) if row else None

    async def admin_user_orders(self, user_id: int) -> list[dict[str, Any]]:
        return await self.admin_user_orders_for_period(user_id, "all")

    async def admin_user_orders_for_period(self, user_id: int, period: str = "all") -> list[dict[str, Any]]:
        await self.ensure_admin_runtime_schema()
        order_columns = await self._table_columns("orders")
        order_select = self._order_select_list(order_columns)
        order_id_expr = "orders_view.order_id"
        user_id_expr = "orders_view.user_id"
        created_expr = "orders_view.created_at"
        cutoff = self._period_cutoff_ts(period)
        rows = await self.fetchall(
            f"""
            WITH orders_view AS (
              SELECT
                {order_select}
              FROM orders o
            )
            SELECT
              orders_view.*,
              (
                SELECT s.client_email
                FROM subscriptions s
                WHERE (COALESCE(orders_view.target_sub_id,'')<>'' AND s.sub_id=orders_view.target_sub_id)
                   OR (COALESCE(orders_view.target_sub_id,'')='' AND s.order_id={order_id_expr})
                ORDER BY s.created_at ASC
                LIMIT 1
              ) AS order_subscription_name,
              (
                SELECT s.sub_id
                FROM subscriptions s
                WHERE (COALESCE(orders_view.target_sub_id,'')<>'' AND s.sub_id=orders_view.target_sub_id)
                   OR (COALESCE(orders_view.target_sub_id,'')='' AND s.order_id={order_id_expr})
                ORDER BY s.created_at ASC
                LIMIT 1
              ) AS order_subscription_id,
              (
                SELECT COUNT(*)
                FROM subscriptions s
                WHERE (COALESCE(orders_view.target_sub_id,'')<>'' AND s.sub_id=orders_view.target_sub_id)
                   OR (COALESCE(orders_view.target_sub_id,'')='' AND s.order_id={order_id_expr})
              ) AS order_subscription_count,
              NULL AS fallback_subscription_name,
              '' AS fallback_subscription_id,
              0 AS fallback_subscription_count
            FROM orders_view
            WHERE {user_id_expr}=?
              AND (?=0 OR COALESCE({created_expr},0)>=?)
            ORDER BY COALESCE({created_expr},0) DESC
            LIMIT 100
            """,
            (int(user_id), cutoff, cutoff),
        )
        return [self._decorate_order_row(dict(row)) for row in rows]

    async def admin_user_subscriptions(self, user_id: int, page: int = 1, page_size: int = 30) -> list[dict[str, Any]]:
        safe_page = max(1, int(page))
        safe_page_size = min(100, max(1, int(page_size)))
        rows = await self.fetchall(
            """
            SELECT *
            FROM subscriptions
            WHERE user_id=?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (int(user_id), safe_page_size, (safe_page - 1) * safe_page_size),
        )
        return [dict(row) for row in rows]

    async def admin_user_subscriptions_count(self, user_id: int) -> int:
        row = await self.fetchone("SELECT COUNT(*) AS c FROM subscriptions WHERE user_id=?", (int(user_id),))
        return int(row["c"] if row else 0)

    async def update_subscription_panel_snapshot(self, detail: Any) -> None:
        await self.conn.execute(
            """
            UPDATE subscriptions
            SET client_uuid=?,
                client_email=?,
                inbound_id=?,
                gb=?,
                panel_total_bytes=?,
                panel_used_bytes=?,
                panel_remaining_bytes=?,
                panel_up_bytes=?,
                panel_down_bytes=?,
                panel_enabled=?,
                panel_last_online_ms=?,
                panel_expiry_time=?,
                panel_synced_at=?
            WHERE sub_id=?
            """,
            (
                str(detail.client_uuid or ""),
                str(detail.email or ""),
                int(detail.inbound_id or 0),
                int(max(0, round(int(detail.total_bytes or 0) / (1024**3)))),
                int(detail.total_bytes or 0),
                int(detail.used_bytes or 0),
                int(detail.remaining_bytes or 0),
                int(detail.up_bytes or 0),
                int(detail.down_bytes or 0),
                1 if detail.enabled else 0,
                int(detail.last_online_ms or 0),
                int(detail.expiry_time or 0),
                now_ts(),
                str(detail.sub_id),
            ),
        )

    async def admin_subscription_detail(self, sub_id: str) -> dict[str, Any] | None:
        await self.ensure_admin_runtime_schema()
        row = await self.fetchone(
            """
            SELECT s.*, u.first_name, u.username, o.order_id, o.final_price, o.payment_method, o.status AS order_status
            FROM subscriptions s
            LEFT JOIN users u ON u.user_id=s.user_id
            LEFT JOIN orders o ON o.order_id=s.order_id
            WHERE s.sub_id=?
            LIMIT 1
            """,
            (str(sub_id).strip(),),
        )
        if row and row["order_id"]:
            return dict(row)
        row = await self.fetchone(
            """
            SELECT s.*, u.first_name, u.username, o.order_id, o.final_price, o.payment_method, o.status AS order_status
            FROM subscriptions s
            LEFT JOIN users u ON u.user_id=s.user_id
            LEFT JOIN orders o ON o.user_id=s.user_id AND COALESCE(o.created_at,0)<=s.created_at
            WHERE s.sub_id=?
            ORDER BY COALESCE(o.created_at,0) DESC
            LIMIT 1
            """,
            (str(sub_id).strip(),),
        )
        return dict(row) if row else None

    async def admin_search_subscriptions(self, search: str = "", page: int = 1, page_size: int = 30) -> list[dict[str, Any]]:
        q = search.strip()
        pattern = f"%{q}%"
        safe_page = max(1, int(page))
        safe_page_size = min(100, max(1, int(page_size)))
        rows = await self.fetchall(
            """
            SELECT s.*, u.first_name, u.username
            FROM subscriptions s
            LEFT JOIN users u ON u.user_id=s.user_id
            WHERE ?='' OR s.sub_id LIKE ? OR COALESCE(s.client_email,'') LIKE ? OR CAST(s.user_id AS TEXT) LIKE ?
            ORDER BY s.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (q, pattern, pattern, pattern, safe_page_size, (safe_page - 1) * safe_page_size),
        )
        return [dict(row) for row in rows]

    async def admin_search_subscriptions_count(self, search: str = "") -> int:
        q = search.strip()
        pattern = f"%{q}%"
        row = await self.fetchone(
            """
            SELECT COUNT(*) AS c
            FROM subscriptions s
            WHERE ?='' OR s.sub_id LIKE ? OR COALESCE(s.client_email,'') LIKE ? OR CAST(s.user_id AS TEXT) LIKE ?
            """,
            (q, pattern, pattern, pattern),
        )
        return int(row["c"] if row else 0)

    async def admin_user_subscription_ids(self, user_id: int) -> list[str]:
        rows = await self.fetchall(
            "SELECT sub_id FROM subscriptions WHERE user_id=? ORDER BY created_at DESC",
            (int(user_id),),
        )
        return [str(row["sub_id"]) for row in rows if str(row["sub_id"] or "").strip()]

    async def mark_subscription_enabled(self, sub_id: str, enabled: bool) -> None:
        await self.conn.execute(
            "UPDATE subscriptions SET panel_enabled=?, panel_synced_at=? WHERE sub_id=?",
            (1 if enabled else 0, now_ts(), str(sub_id)),
        )

    async def admin_broadcast_targets(self, audience: str) -> list[dict[str, Any]]:
        normalized = str(audience or "all").strip().lower()
        rows = await self.fetchall(
            """
            SELECT u.user_id, u.first_name, u.username,
                   CASE WHEN a.user_id IS NULL THEN 0 ELSE 1 END AS is_agent
            FROM users u
            LEFT JOIN agents a ON a.user_id=u.user_id
            WHERE COALESCE(u.disabled,0)=0
              AND (
                ?='all'
                OR (?='agents' AND a.user_id IS NOT NULL)
                OR (?='customers' AND a.user_id IS NULL)
              )
            ORDER BY u.joined_at DESC
            """,
            (normalized, normalized, normalized),
        )
        return [dict(row) for row in rows]

    async def create_admin_event(
        self,
        *,
        kind: str,
        title: str,
        payload: dict[str, Any],
        total_count: int = 0,
    ) -> str:
        event_id = f"evt-{now_ts()}-{uuid.uuid4().hex[:10]}"
        await self.conn.execute(
            """
            INSERT INTO admin_events(
              event_id,kind,status,title,payload_json,total_count,
              success_count,failed_count,attempt_count,created_at
            )
            VALUES(?,?,?,?,?,?,0,0,0,?)
            """,
            (
                event_id,
                str(kind),
                "queued",
                str(title),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                max(0, int(total_count)),
                now_ts(),
            ),
        )
        return event_id

    async def claim_next_admin_event(self) -> dict[str, Any] | None:
        async with self.transaction() as conn:
            row = await self.fetchone(
                """
                SELECT *
                FROM admin_events
                WHERE dismissed_at IS NULL
                  AND status IN ('queued','retrying')
                ORDER BY created_at ASC
                LIMIT 1
                """
            )
            if not row:
                return None
            await conn.execute(
                """
                UPDATE admin_events
                SET status='running',
                    started_at=COALESCE(started_at,?),
                    attempt_count=attempt_count+1,
                    last_error=NULL
                WHERE event_id=? AND status IN ('queued','retrying')
                """,
                (now_ts(), row["event_id"]),
            )
            claimed = await self.fetchone("SELECT * FROM admin_events WHERE event_id=?", (row["event_id"],))
            return dict(claimed) if claimed else None

    async def recover_stale_admin_events(self, timeout_seconds: int = 900) -> int:
        cutoff = now_ts() - max(60, int(timeout_seconds))
        cur = await self.conn.execute(
            """
            UPDATE admin_events
            SET status='retrying',
                last_error=COALESCE(last_error,'') || CASE WHEN COALESCE(last_error,'')='' THEN '' ELSE char(10) END || 'recovered stale running event'
            WHERE dismissed_at IS NULL
              AND status='running'
              AND COALESCE(started_at,0)>0
              AND started_at<?
            """,
            (cutoff,),
        )
        return max(0, cur.rowcount or 0)

    async def update_admin_event_progress(
        self,
        event_id: str,
        *,
        success_count: int | None = None,
        failed_count: int | None = None,
        total_count: int | None = None,
        last_error: str | None = None,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        if success_count is not None:
            sets.append("success_count=?")
            params.append(max(0, int(success_count)))
        if failed_count is not None:
            sets.append("failed_count=?")
            params.append(max(0, int(failed_count)))
        if total_count is not None:
            sets.append("total_count=?")
            params.append(max(0, int(total_count)))
        if last_error is not None:
            sets.append("last_error=?")
            params.append(str(last_error)[:1000])
        if not sets:
            return
        params.append(str(event_id))
        await self.conn.execute(f"UPDATE admin_events SET {', '.join(sets)} WHERE event_id=?", tuple(params))

    async def finish_admin_event(self, event_id: str, status: str, last_error: str = "") -> None:
        normalized = status if status in {"completed", "completed_with_errors", "failed", "retrying"} else "failed"
        await self.conn.execute(
            """
            UPDATE admin_events
            SET status=?,
                finished_at=CASE WHEN ? IN ('completed','completed_with_errors','failed') THEN ? ELSE finished_at END,
                last_error=?
            WHERE event_id=?
            """,
            (normalized, normalized, now_ts(), last_error[:1000] or None, str(event_id)),
        )

    async def list_admin_events(self, status_filter: str = "active", limit: int = 80) -> list[dict[str, Any]]:
        normalized = str(status_filter or "active").strip().lower()
        if normalized == "completed":
            where = "dismissed_at IS NULL AND status IN ('completed','completed_with_errors','failed')"
        elif normalized == "all":
            where = "dismissed_at IS NULL"
        else:
            where = "dismissed_at IS NULL AND status IN ('queued','running','retrying')"
        rows = await self.fetchall(
            f"""
            SELECT *
            FROM admin_events
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        )
        return [dict(row) for row in rows]

    async def dismiss_admin_event(self, event_id: str) -> None:
        await self.conn.execute(
            "UPDATE admin_events SET dismissed_at=? WHERE event_id=?",
            (now_ts(), str(event_id)),
        )

    async def admin_user_topups(self, user_id: int, period: str = "all") -> list[dict[str, Any]]:
        cutoff = self._period_cutoff_ts(period)
        rows = await self.fetchall(
            """
            SELECT *
            FROM wallet_topups
            WHERE user_id=?
              AND (?=0 OR COALESCE(created_at,0)>=?)
            ORDER BY created_at DESC
            LIMIT 100
            """,
            (int(user_id), cutoff, cutoff),
        )
        return [dict(row) for row in rows]

    async def admin_user_topups_total(self, user_id: int, period: str = "all") -> int:
        cutoff = self._period_cutoff_ts(period)
        row = await self.fetchone(
            """
            SELECT COALESCE(SUM(amount_toman),0) AS total
            FROM wallet_topups
            WHERE user_id=?
              AND status='approved'
              AND (?=0 OR COALESCE(created_at,0)>=?)
            """,
            (int(user_id), cutoff, cutoff),
        )
        return int(row["total"] if row else 0)

    async def admin_user_agent_ledger(self, user_id: int) -> list[dict[str, Any]]:
        rows = await self.fetchall(
            "SELECT * FROM agent_ledger WHERE user_id=? ORDER BY created_at DESC LIMIT 100",
            (int(user_id),),
        )
        return [dict(row) for row in rows]

    async def set_user_disabled(self, user_id: int, disabled: bool, reason: str = "") -> None:
        async with self.transaction() as conn:
            await conn.execute(
                "UPDATE users SET disabled=?, disabled_reason=?, disabled_at=? WHERE user_id=?",
                (1 if disabled else 0, reason.strip() or None, now_ts() if disabled else None, int(user_id)),
            )

    async def get_wallet_topup(self, topup_id: str) -> dict[str, Any] | None:
        row = await self.fetchone("SELECT * FROM wallet_topups WHERE topup_id=?", (topup_id,))
        return dict(row) if row else None

    async def admin_list_topups(self, status: str = "pending") -> list[dict[str, Any]]:
        rows = await self.fetchall(
            """
            SELECT wt.*, u.first_name, u.username
            FROM wallet_topups wt
            LEFT JOIN users u ON u.user_id=wt.user_id
            WHERE ?='all' OR wt.status=?
            ORDER BY wt.created_at DESC
            LIMIT 300
            """,
            (status, status),
        )
        return [dict(row) for row in rows]

    async def approve_wallet_topup(self, topup_id: str):
        """Approve a pending top-up. For OPEN (credit) agents the payment first
        settles their outstanding credit debt (credit_used) — since they buy on
        credit, not from the wallet — freeing credit to purchase again; any
        excess goes to the wallet. Everyone else is credited to the wallet as
        before. Returns a result dict on success, False otherwise."""
        async with self.transaction() as conn:
            row = await self.fetchone("SELECT * FROM wallet_topups WHERE topup_id=?", (topup_id,))
            if not row or row["status"] != "pending":
                return False
            user_id = int(row["user_id"])
            amount = int(row["amount_toman"])
            agent = await self.fetchone(
                "SELECT access_level, credit_used_toman FROM agents WHERE user_id=?", (user_id,)
            )
            is_open = bool(agent) and self.normalize_agent_access_value(agent["access_level"]) == "open"
            settled = 0
            to_wallet = amount
            if is_open:
                debt = max(0, int(agent["credit_used_toman"] or 0))
                settled = min(amount, debt)
                if settled > 0:
                    await conn.execute(
                        "UPDATE agents SET credit_used_toman=max(0, credit_used_toman-?) WHERE user_id=?",
                        (settled, user_id),
                    )
                    await conn.execute(
                        "INSERT OR IGNORE INTO agent_ledger(user_id,amount_toman,kind,ref_id,created_at) VALUES(?,?,?,?,?)",
                        (user_id, -settled, "credit_repay", f"topup:{topup_id}", now_ts()),
                    )
                to_wallet = amount - settled
            if to_wallet > 0:
                await conn.execute(
                    """
                    INSERT INTO wallets(user_id,balance_toman,updated_at)
                    VALUES(?,?,?)
                    ON CONFLICT(user_id) DO UPDATE SET
                      balance_toman=wallets.balance_toman+excluded.balance_toman,
                      updated_at=excluded.updated_at
                    """,
                    (user_id, int(to_wallet), now_ts()),
                )
            await conn.execute(
                "UPDATE wallet_topups SET status='approved', approved_at=? WHERE topup_id=?",
                (now_ts(), topup_id),
            )
            return {"ok": True, "amount": amount, "settled": settled, "to_wallet": to_wallet, "open_agent": is_open}

    async def reject_wallet_topup(self, topup_id: str) -> bool:
        async with self.transaction() as conn:
            row = await self.fetchone("SELECT status FROM wallet_topups WHERE topup_id=?", (topup_id,))
            if not row or row["status"] != "pending":
                return False
            await conn.execute(
                "UPDATE wallet_topups SET status='rejected', rejected_at=? WHERE topup_id=?",
                (now_ts(), topup_id),
            )
            return True

    async def admin_list_agent_requests(self, status: str = "pending") -> list[dict[str, Any]]:
        rows = await self.fetchall(
            """
            SELECT ar.*, u.first_name, u.username
            FROM agent_requests ar
            LEFT JOIN users u ON u.user_id=ar.user_id
            WHERE ?='all' OR ar.status=?
            ORDER BY ar.created_at DESC
            LIMIT 300
            """,
            (status, status),
        )
        return [dict(row) for row in rows]

    async def get_agent_request(self, req_id: str) -> dict[str, Any] | None:
        row = await self.fetchone("SELECT * FROM agent_requests WHERE req_id=?", (str(req_id),))
        return dict(row) if row else None

    async def update_agent_request_status(self, req_id: str, status: str) -> bool:
        async with self.transaction() as conn:
            cur = await conn.execute(
                "UPDATE agent_requests SET status=? WHERE req_id=? AND status='pending'",
                (status, req_id),
            )
            return cur.rowcount == 1

    async def approve_agent_request_as_agent(
        self,
        *,
        req_id: str,
        access_level: AgentAccess | str,
        credit_limit_toman: int = 0,
        price_per_gb: int = 0,
        daily_test_limit: int = 0,
        created_by: int = 0,
    ) -> dict[str, Any] | None:
        access_value = self.normalize_agent_access_value(access_level)
        async with self.transaction() as conn:
            row = await self.fetchone("SELECT * FROM agent_requests WHERE req_id=?", (str(req_id),))
            if not row or row["status"] != "pending":
                return None
            user_id = int(row["user_id"])
            await conn.execute(
                """
                INSERT INTO agents(
                  user_id,price_per_gb,access_level,created_at,created_by,credit_limit_toman,
                  credit_used_toman,daily_gb_limit,monthly_gb_limit,daily_test_limit,permissions,disabled
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,0)
                ON CONFLICT(user_id) DO UPDATE SET
                  price_per_gb=excluded.price_per_gb,
                  access_level=excluded.access_level,
                  credit_limit_toman=excluded.credit_limit_toman,
                  daily_gb_limit=excluded.daily_gb_limit,
                  monthly_gb_limit=excluded.monthly_gb_limit,
                  daily_test_limit=excluded.daily_test_limit,
                  permissions=excluded.permissions,
                  disabled=0
                """,
                (
                    user_id,
                    max(0, int(price_per_gb)),
                    access_value,
                    now_ts(),
                    int(created_by),
                    max(0, int(credit_limit_toman)),
                    0,
                    0,
                    0,
                    max(0, int(daily_test_limit)),
                    '["buy","test"]',
                ),
            )
            await conn.execute(
                "UPDATE agent_requests SET status='approved' WHERE req_id=?",
                (str(req_id),),
            )
            result = dict(row)
            result.update(
                {
                    "access_level": access_value,
                    "credit_limit_toman": max(0, int(credit_limit_toman)),
                    "price_per_gb": max(0, int(price_per_gb)),
                    "daily_test_limit": max(0, int(daily_test_limit)),
                }
            )
            return result

    async def admin_list_settings(self) -> list[dict[str, str]]:
        rows = await self.fetchall("SELECT key,value FROM settings ORDER BY key")
        return [{"key": str(row["key"]), "value": str(row["value"])} for row in rows]

    async def admin_update_settings(self, values: dict[str, str]) -> None:
        async with self.transaction() as conn:
            await conn.executemany(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [(str(k), str(v)) for k, v in values.items()],
            )

    async def admin_list_orders(self, search: str = "", period: str = "all", limit: int = 300, offset: int = 0) -> list[dict[str, Any]]:
        await self.ensure_admin_runtime_schema()
        safe_limit = max(1, min(1000, int(limit)))
        safe_offset = max(0, int(offset))
        order_columns = await self._table_columns("orders")
        order_select = self._order_select_list(order_columns)
        order_id_expr = "orders_view.order_id"
        user_id_expr = "orders_view.user_id"
        client_name_expr = "orders_view.client_name"
        created_expr = "orders_view.created_at"
        q = search.strip()
        pattern = f"%{q}%"
        cutoff = self._period_cutoff_ts(period)
        rows = await self.fetchall(
            f"""
            WITH orders_view AS (
              SELECT
                {order_select}
              FROM orders o
            )
            SELECT
              orders_view.*,
              u.first_name,
              u.username,
              (
                SELECT s.client_email
                FROM subscriptions s
                WHERE (COALESCE(orders_view.target_sub_id,'')<>'' AND s.sub_id=orders_view.target_sub_id)
                   OR (COALESCE(orders_view.target_sub_id,'')='' AND s.order_id={order_id_expr})
                ORDER BY s.created_at ASC
                LIMIT 1
              ) AS subscription_name,
              (
                SELECT s.sub_id
                FROM subscriptions s
                WHERE (COALESCE(orders_view.target_sub_id,'')<>'' AND s.sub_id=orders_view.target_sub_id)
                   OR (COALESCE(orders_view.target_sub_id,'')='' AND s.order_id={order_id_expr})
                ORDER BY s.created_at ASC
                LIMIT 1
              ) AS subscription_id,
              (
                SELECT COUNT(*)
                FROM subscriptions s
                WHERE (COALESCE(orders_view.target_sub_id,'')<>'' AND s.sub_id=orders_view.target_sub_id)
                   OR (COALESCE(orders_view.target_sub_id,'')='' AND s.order_id={order_id_expr})
              ) AS subscription_count,
              NULL AS fallback_subscription_name,
              '' AS fallback_subscription_id,
              0 AS fallback_subscription_count
            FROM orders_view
            LEFT JOIN users u ON u.user_id={user_id_expr}
            WHERE (?=0 OR COALESCE({created_expr},0)>=?)
              AND (
                ?=''
                OR {order_id_expr} LIKE ?
                OR CAST({user_id_expr} AS TEXT) LIKE ?
                OR COALESCE({client_name_expr},'') LIKE ?
                OR COALESCE(u.first_name,'') LIKE ?
                OR COALESCE(u.username,'') LIKE ?
                OR COALESCE(orders_view.target_sub_id,'') LIKE ?
                OR EXISTS (
                    SELECT 1
                    FROM subscriptions s
                    WHERE (
                        s.order_id={order_id_expr}
                        OR s.sub_id=orders_view.target_sub_id
                      )
                      AND (s.sub_id LIKE ? OR COALESCE(s.client_email,'') LIKE ?)
                )
              )
            ORDER BY COALESCE({created_expr},0) DESC
            LIMIT ? OFFSET ?
            """,
            (cutoff, cutoff, q, pattern, pattern, pattern, pattern, pattern, pattern, pattern, pattern, safe_limit, safe_offset),
        )
        return [self._decorate_order_row(dict(row)) for row in rows]

    @staticmethod
    def _period_cutoff_ts(period: str) -> int:
        seconds = {
            "24h": 86400,
            "7d": 7 * 86400,
            "30d": 30 * 86400,
        }.get(str(period or "all"), 0)
        return now_ts() - seconds if seconds else 0

    @staticmethod
    def _decorate_order_row(row: dict[str, Any]) -> dict[str, Any]:
        order_type = str(row.get("order_type") or "").strip().lower()
        if not order_type:
            order_type = "renewal" if int(row.get("plan_id") or 0) < 0 or "-renew-" in str(row.get("order_id") or "") else "purchase"
        row["order_type"] = "renewal" if order_type == "renewal" else "purchase"
        row["order_type_label"] = "تمدید" if row["order_type"] == "renewal" else "خرید جدید"
        primary_name = row.get("subscription_name") or row.get("order_subscription_name")
        primary_id = row.get("subscription_id") or row.get("order_subscription_id")
        primary_count = row.get("subscription_count") or row.get("order_subscription_count") or 0
        fallback_name = row.get("fallback_subscription_name")
        fallback_id = row.get("fallback_subscription_id")
        fallback_count = row.get("fallback_subscription_count") or 0
        row["subscription_name"] = primary_name or fallback_name or row.get("client_name")
        row["subscription_id"] = primary_id or fallback_id or ""
        row["subscription_count"] = int(primary_count or fallback_count or 0)
        return row

    async def delete_load_test_artifacts(self, user_id: int = 990000001) -> dict[str, int]:
        patterns = ("load-test:%",)
        async with self.transaction() as conn:
            sub_rows = await self.fetchall(
                "SELECT sub_id FROM subscriptions WHERE user_id=? OR COALESCE(client_email,'') LIKE 'lt_%'",
                (int(user_id),),
            )
            order_rows = await self.fetchall(
                "SELECT order_id FROM orders WHERE user_id=? OR COALESCE(client_name,'') LIKE 'lt_%'",
                (int(user_id),),
            )
            sub_ids = [str(row["sub_id"]) for row in sub_rows]
            order_ids = [str(row["order_id"]) for row in order_rows]
            await conn.execute("DELETE FROM subscriptions WHERE user_id=? OR COALESCE(client_email,'') LIKE 'lt_%'", (int(user_id),))
            await conn.execute("DELETE FROM orders WHERE user_id=? OR COALESCE(client_name,'') LIKE 'lt_%'", (int(user_id),))
            idem_deleted = 0
            for pattern in patterns:
                cur = await conn.execute("DELETE FROM idempotency_keys WHERE user_id=? OR key LIKE ?", (int(user_id), pattern))
                idem_deleted += max(0, cur.rowcount or 0)
            await conn.execute("DELETE FROM agent_ledger WHERE user_id=?", (int(user_id),))
            if order_ids:
                placeholders = ",".join("?" for _ in order_ids)
                await conn.execute(f"DELETE FROM agent_ledger WHERE ref_id IN ({placeholders})", tuple(order_ids))
            await conn.execute("DELETE FROM wallets WHERE user_id=?", (int(user_id),))
            await conn.execute("DELETE FROM users WHERE user_id=? AND (username LIKE 'loadtest_%' OR first_name='LoadTest')", (int(user_id),))
            approved_amount = await self.fetchone("SELECT COALESCE(SUM(final_price),0) AS v FROM orders WHERE status='approved'")
            approved_count = await self.fetchone("SELECT COUNT(*) AS v FROM orders WHERE status='approved'")
            rejected_count = await self.fetchone("SELECT COUNT(*) AS v FROM orders WHERE status='rejected'")
            approved_subs = await self.fetchone("SELECT COALESCE(SUM(qty),0) AS v FROM orders WHERE status='approved'")
            approved_gb = await self.fetchone("SELECT COALESCE(SUM(gb * qty),0) AS v FROM orders WHERE status='approved'")
            await conn.executemany(
                "INSERT INTO stats(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [
                    ("approved_total_amount", int(approved_amount["v"] if approved_amount else 0)),
                    ("approved_count", int(approved_count["v"] if approved_count else 0)),
                    ("rejected_count", int(rejected_count["v"] if rejected_count else 0)),
                    ("approved_subscriptions_count", int(approved_subs["v"] if approved_subs else 0)),
                    ("approved_total_gb", int(approved_gb["v"] if approved_gb else 0)),
                ],
            )
            return {"subscriptions": len(sub_ids), "orders": len(order_ids), "idempotency": idem_deleted}

    async def upsert_agent(
        self,
        *,
        user_id: int,
        price_per_gb: int,
        created_by: int,
        access_level: AgentAccess = AgentAccess.CLOSED,
        credit_limit_toman: int = 0,
        daily_gb_limit: int = 0,
        monthly_gb_limit: int = 0,
        daily_test_limit: int = 0,
        permissions: str = '["buy","test"]',
        disabled: bool = False,
        credit_used_toman: int | None = None,
    ) -> None:
        access_value = self.normalize_agent_access_value(access_level)
        used_value = -1 if credit_used_toman is None else max(0, int(credit_used_toman))
        await self.conn.execute(
            """
            INSERT INTO agents(
              user_id,price_per_gb,access_level,created_at,created_by,credit_limit_toman,
              credit_used_toman,daily_gb_limit,monthly_gb_limit,daily_test_limit,permissions,disabled
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
              price_per_gb=excluded.price_per_gb,
              access_level=excluded.access_level,
              credit_limit_toman=excluded.credit_limit_toman,
              credit_used_toman=CASE
                WHEN excluded.credit_used_toman < 0 THEN agents.credit_used_toman
                ELSE excluded.credit_used_toman
              END,
              daily_gb_limit=excluded.daily_gb_limit,
              monthly_gb_limit=excluded.monthly_gb_limit,
              daily_test_limit=excluded.daily_test_limit,
              permissions=excluded.permissions,
              disabled=excluded.disabled
            """,
            (
                int(user_id),
                int(price_per_gb),
                access_value,
                now_ts(),
                int(created_by),
                int(credit_limit_toman),
                used_value,
                int(daily_gb_limit),
                int(monthly_gb_limit),
                int(daily_test_limit),
                permissions,
                int(disabled),
            ),
        )

    async def create_native_backup(self, destination: Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)

        def _backup() -> Path:
            tmp = destination.with_suffix(destination.suffix + ".tmp")
            if tmp.exists():
                tmp.unlink()
            src = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, timeout=30)
            dst = sqlite3.connect(tmp, timeout=30)
            try:
                src.backup(dst, pages=512, sleep=0.02)
                result = dst.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0] != "ok":
                    raise RuntimeError(f"backup integrity_check failed: {result}")
            finally:
                dst.close()
                src.close()
            shutil.move(str(tmp), str(destination))
            return destination

        return await asyncio.to_thread(_backup)
