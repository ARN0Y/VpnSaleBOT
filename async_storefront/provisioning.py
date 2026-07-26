from __future__ import annotations

import json
import logging
import secrets

from .agent import AgentService
from .db import AsyncDatabase
from .models import PG_INBOUND_SENTINEL, PaymentMethod, PanelClientPayload
from .panel import PanelClient
from .pasarguard import _iso_to_epoch
from .util import gb_to_bytes, now_ts, sanitize_client_name

LOG = logging.getLogger(__name__)

TEST_CONFIG_BYTES = 200 * 1024 * 1024
TEST_CONFIG_TTL_SECONDS = 10 * 60

__all__ = ["ProvisioningService", "PG_INBOUND_SENTINEL", "TEST_CONFIG_BYTES", "TEST_CONFIG_TTL_SECONDS"]


def _safe_positive_int(value, default: int = 1) -> int:
    try:
        return max(1, int(str(value).strip()))
    except Exception:
        return max(1, int(default))


class ProvisioningService:
    def __init__(self, db: AsyncDatabase, panel: PanelClient, agents: AgentService):
        self.db = db
        self.panel = panel
        self.agents = agents

    async def minimum_purchase_gb(self) -> int:
        return _safe_positive_int(await self.db.get_setting("minimum_purchase_gb", "1"), 1)

    async def buy_with_wallet(
        self,
        *,
        user_id: int,
        plan_id: int,
        gb: int,
        qty: int,
        unit_price: int,
        final_total: int,
        client_name: str = "",
        idempotency_key: str | None = None,
    ) -> list[str]:
        return await self.process_checkout(
            user_id=user_id,
            plan_id=plan_id,
            gb=gb,
            qty=qty,
            unit_price=unit_price,
            final_total=final_total,
            client_name=client_name,
            idempotency_key=idempotency_key,
        )

    async def process_checkout(
        self,
        *,
        user_id: int,
        plan_id: int,
        gb: int,
        qty: int,
        unit_price: int,
        final_total: int,
        client_name: str = "",
        idempotency_key: str | None = None,
    ) -> list[str]:
        requested_gb = int(gb)
        requested_qty = int(qty)
        if requested_gb <= 0:
            raise ValueError("حجم خرید معتبر نیست.")
        if requested_qty <= 0:
            raise ValueError("تعداد خرید معتبر نیست.")

        agent = await self.db.get_agent(user_id)
        effective_unit_price = int(unit_price)
        if agent and int(agent["price_per_gb"] or 0) > 0:
            effective_unit_price = int(agent["price_per_gb"])
        if effective_unit_price <= 0:
            raise ValueError("تعرفه حساب شما معتبر نیست. لطفاً با پشتیبانی تماس بگیرید.")

        min_gb = await self.minimum_purchase_gb()
        if requested_gb < min_gb:
            raise ValueError(f"حداقل حجم مجاز برای خرید {min_gb} گیگ است.")

        expected_total = requested_gb * requested_qty * effective_unit_price
        if int(final_total) != expected_total:
            raise ValueError("مبلغ فاکتور معتبر نیست.")

        order_id = f"{user_id}-{plan_id}-{now_ts()}-{secrets.token_hex(3)}"
        idem = idempotency_key or order_id
        payment_method = PaymentMethod.WALLET
        async with self.db.transaction() as conn:
            existing = await self.db.fetchone("SELECT order_id,status FROM idempotency_keys WHERE key=?", (idem,))
            if existing:
                raise RuntimeError(f"duplicate purchase request: {existing['status']}")

            agent = await self.db.fetchone("SELECT * FROM agents WHERE user_id=?", (int(user_id),))
            tx_effective_unit_price = int(unit_price)
            if agent and int(agent["price_per_gb"] or 0) > 0:
                tx_effective_unit_price = int(agent["price_per_gb"])
            if tx_effective_unit_price <= 0:
                raise ValueError("تعرفه حساب شما معتبر نیست. لطفاً با پشتیبانی تماس بگیرید.")
            tx_expected_total = requested_gb * requested_qty * tx_effective_unit_price
            if int(final_total) != tx_expected_total or int(effective_unit_price) != tx_effective_unit_price:
                raise ValueError("تعرفه حساب شما تغییر کرده است. لطفاً خرید را دوباره ثبت کنید.")
            effective_unit_price = tx_effective_unit_price

            if agent and self.db.normalize_agent_access_value(agent["access_level"]) == "open":
                payment_method = PaymentMethod.AGENT_OPEN
                credit_update = await conn.execute(
                    """
                    UPDATE agents
                    SET credit_used_toman=credit_used_toman+?
                    WHERE user_id=?
                      AND (credit_limit_toman<=0 OR credit_used_toman + ? <= credit_limit_toman)
                    """,
                    (int(final_total), int(user_id), int(final_total)),
                )
                if credit_update.rowcount != 1:
                    raise ValueError("سقف اعتبار شما کافی نیست. لطفا بدهی خود را تسویه کنید.")
                await conn.execute(
                    "INSERT OR IGNORE INTO agent_ledger(user_id,amount_toman,kind,ref_id,created_at) VALUES(?,?,?,?,?)",
                    (int(user_id), int(final_total), "credit_reserve", order_id, now_ts()),
                )
            else:
                debit = await self.db.try_debit_wallet_in_transaction(conn, user_id, final_total)
                if debit.rowcount != 1:
                    raise ValueError("موجودی کیف پول شما کافی نیست. لطفا حساب خود را شارژ کنید.")

            await conn.execute(
                """
                INSERT INTO orders
                  (order_id,user_id,plan_id,gb,qty,unit_price,price,discount_amount,
                   final_price,status,created_at,payment_method,order_type,target_sub_id,client_name)
                VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?)
                """,
                (
                    order_id,
                    int(user_id),
                    int(plan_id),
                    requested_gb,
                    requested_qty,
                    int(effective_unit_price),
                    int(effective_unit_price) * requested_gb * requested_qty,
                    0,
                    int(final_total),
                    now_ts(),
                    payment_method.value,
                    "purchase",
                    None,
                    (client_name or "").strip() or None,
                ),
            )
            await conn.execute(
                "INSERT INTO idempotency_keys(key,user_id,order_id,status,created_at) VALUES(?,?,?,?,?)",
                (idem, int(user_id), order_id, "reserved", now_ts()),
            )

        provisions = []
        try:
            # Every volume purchase gets a fixed validity window (default 30 days)
            # instead of an unlimited-time config.
            duration_days = _safe_positive_int(await self.db.get_setting("purchase_duration_days", "30"), 30)
            expiry_ms = (now_ts() + duration_days * 86400) * 1000 if duration_days > 0 else 0
            provisions = await self.panel.add_subscriptions(
                user_id=user_id, gb=requested_gb, qty=requested_qty,
                preferred_name=client_name, expiry_ms=expiry_ms,
            )
            await self.db.insert_subscriptions(provisions, order_id=order_id)
            approved = await self.db.approve_order(order_id)
            if not approved:
                raise RuntimeError("order approval failed after provisioning")
            await self.db.execute("UPDATE idempotency_keys SET status='approved' WHERE key=?", (idem,))
            return [p.sub_link for p in provisions]
        except Exception:
            await self.db.delete_subscriptions([provision.sub_id for provision in provisions])
            for provision in provisions:
                try:
                    await self.panel.delete_subscription(provision.sub_id)
                except Exception:
                    pass
            if payment_method == PaymentMethod.AGENT_OPEN:
                async with self.db.transaction() as conn:
                    await conn.execute(
                        "UPDATE agents SET credit_used_toman=max(0, credit_used_toman-?) WHERE user_id=?",
                        (int(final_total), int(user_id)),
                    )
                    await conn.execute(
                        "INSERT OR IGNORE INTO agent_ledger(user_id,amount_toman,kind,ref_id,created_at) VALUES(?,?,?,?,?)",
                        (int(user_id), -int(final_total), "credit_refund", f"{order_id}:refund", now_ts()),
                    )
            else:
                await self.db.credit_wallet(user_id, final_total)
            await self.db.reject_order(order_id)
            await self.db.execute("UPDATE idempotency_keys SET status='failed' WHERE key=?", (idem,))
            raise

    async def buy_as_agent(
        self,
        *,
        user_id: int,
        gb: int,
        qty: int,
        client_name: str = "",
        idempotency_key: str | None = None,
    ) -> list[str]:
        agent = await self.db.get_agent(user_id)
        if not agent:
            raise PermissionError("user is not an agent")
        unit_price = int(agent["price_per_gb"] or 0)
        if unit_price <= 0:
            unit_price = int(await self.db.get_setting("price_per_gb", "200000") or "200000")
        return await self.process_checkout(
            user_id=user_id,
            gb=gb,
            qty=qty,
            plan_id=0,
            unit_price=unit_price,
            final_total=int(gb) * int(qty) * unit_price,
            client_name=client_name,
            idempotency_key=idempotency_key,
        )

    async def infinite_package(self) -> dict:
        """Admin-configured fair-usage 'unlimited' package settings."""
        enabled = str(await self.db.get_setting("infinite_enabled", "0") or "0").strip().lower() in {"1", "true", "on", "yes"}
        return {
            "enabled": enabled,
            "cap_gb": _safe_positive_int(await self.db.get_setting("infinite_cap_gb", "100"), 100),
            "duration_days": _safe_positive_int(await self.db.get_setting("infinite_duration_days", "30"), 30),
            "price": max(0, int(str(await self.db.get_setting("infinite_price", "0") or "0").strip() or "0")),
        }

    async def process_infinite_purchase(self, *, user_id: int, client_name: str = "", idempotency_key: str | None = None) -> list[str]:
        """Buy a fair-usage 'unlimited' config: a config capped at cap_gb traffic
        and duration_days, charged at the admin's custom price. xray enforces the
        cap (auto-stops at it). Users may buy several. Returns the sub_link(s) of
        the created config(s) — the bot then delivers raw config URIs, not the sub
        link itself.
        """
        pkg = await self.infinite_package()
        if not pkg["enabled"]:
            raise ValueError("بسته‌ی بی‌نهایت در حال حاضر فعال نیست.")
        if pkg["price"] <= 0:
            raise ValueError("قیمت بسته‌ی بی‌نهایت توسط مدیریت تنظیم نشده است.")
        cap_gb = int(pkg["cap_gb"])
        final_total = int(pkg["price"])

        order_id = f"{user_id}-inf-{now_ts()}-{secrets.token_hex(3)}"
        idem = idempotency_key or order_id
        payment_method = PaymentMethod.WALLET
        async with self.db.transaction() as conn:
            existing = await self.db.fetchone("SELECT order_id,status FROM idempotency_keys WHERE key=?", (idem,))
            if existing:
                raise RuntimeError(f"duplicate purchase request: {existing['status']}")
            agent = await self.db.fetchone("SELECT * FROM agents WHERE user_id=?", (int(user_id),))
            if agent and self.db.normalize_agent_access_value(agent["access_level"]) == "open":
                payment_method = PaymentMethod.AGENT_OPEN
                credit_update = await conn.execute(
                    """
                    UPDATE agents SET credit_used_toman=credit_used_toman+?
                    WHERE user_id=? AND (credit_limit_toman<=0 OR credit_used_toman + ? <= credit_limit_toman)
                    """,
                    (int(final_total), int(user_id), int(final_total)),
                )
                if credit_update.rowcount != 1:
                    raise ValueError("سقف اعتبار شما کافی نیست. لطفا بدهی خود را تسویه کنید.")
                await conn.execute(
                    "INSERT OR IGNORE INTO agent_ledger(user_id,amount_toman,kind,ref_id,created_at) VALUES(?,?,?,?,?)",
                    (int(user_id), int(final_total), "credit_reserve", order_id, now_ts()),
                )
            else:
                debit = await self.db.try_debit_wallet_in_transaction(conn, user_id, final_total)
                if debit.rowcount != 1:
                    raise ValueError("موجودی کیف پول شما کافی نیست. لطفا حساب خود را شارژ کنید.")
            await conn.execute(
                """
                INSERT INTO orders
                  (order_id,user_id,plan_id,gb,qty,unit_price,price,discount_amount,
                   final_price,status,created_at,payment_method,order_type,target_sub_id,client_name)
                VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?)
                """,
                (
                    order_id, int(user_id), 0, int(cap_gb), 1, int(final_total),
                    int(final_total), 0, int(final_total), now_ts(),
                    payment_method.value, "infinite", None, (client_name or "").strip() or None,
                ),
            )
            await conn.execute(
                "INSERT INTO idempotency_keys(key,user_id,order_id,status,created_at) VALUES(?,?,?,?,?)",
                (idem, int(user_id), order_id, "reserved", now_ts()),
            )

        provisions = []
        try:
            expiry_ms = (now_ts() + int(pkg["duration_days"]) * 86400) * 1000
            provisions = await self.panel.add_subscriptions(
                user_id=user_id, gb=cap_gb, qty=1, preferred_name=client_name, expiry_ms=expiry_ms
            )
            await self.db.insert_subscriptions(provisions, order_id=order_id, is_infinite=True)
            approved = await self.db.approve_order(order_id)
            if not approved:
                raise RuntimeError("infinite order approval failed after provisioning")
            await self.db.execute("UPDATE idempotency_keys SET status='approved' WHERE key=?", (idem,))
            return [p.sub_link for p in provisions]
        except Exception:
            await self.db.delete_subscriptions([p.sub_id for p in provisions])
            for provision in provisions:
                try:
                    await self.panel.delete_subscription(provision.sub_id)
                except Exception:
                    pass
            if payment_method == PaymentMethod.AGENT_OPEN:
                async with self.db.transaction() as conn:
                    await conn.execute(
                        "UPDATE agents SET credit_used_toman=max(0, credit_used_toman-?) WHERE user_id=?",
                        (int(final_total), int(user_id)),
                    )
                    await conn.execute(
                        "INSERT OR IGNORE INTO agent_ledger(user_id,amount_toman,kind,ref_id,created_at) VALUES(?,?,?,?,?)",
                        (int(user_id), -int(final_total), "credit_refund", f"{order_id}:refund", now_ts()),
                    )
            else:
                await self.db.credit_wallet(user_id, final_total)
            await self.db.reject_order(order_id)
            await self.db.execute("UPDATE idempotency_keys SET status='failed' WHERE key=?", (idem,))
            raise

    async def process_agent_test_config(
        self, *, user_id: int, pg_client=None, group_ids=None, idempotency_key: str | None = None
    ) -> str:
        """Daily test config for an agent, created on the PRIMARY backend.

        When ``pg_client`` is given the test lives on PasarGuard, otherwise on
        3x-ui. (Without this the test always hit 3x-ui and broke on PasarGuard-
        primary deployments.)"""
        test_id = f"test-{int(user_id)}-{now_ts()}-{secrets.token_hex(3)}"
        idem = idempotency_key or test_id
        expires_at = now_ts() + TEST_CONFIG_TTL_SECONDS
        async with self.db.transaction() as conn:
            existing = await self.db.fetchone("SELECT order_id,status FROM idempotency_keys WHERE key=?", (idem,))
            if existing:
                raise RuntimeError(f"duplicate test config request: {existing['status']}")
            agent = await self.db.fetchone("SELECT * FROM agents WHERE user_id=?", (int(user_id),))
            if not agent:
                raise ValueError("این قابلیت فقط برای نماینده‌ها فعال است.")
            if int(agent["disabled"] or 0):
                raise ValueError("دسترسی نمایندگی شما غیرفعال است.")
            try:
                permissions = {str(item) for item in json.loads(agent["permissions"] or "[]")}
            except Exception:
                permissions = {"buy", "test"}
            if "test" not in permissions:
                raise ValueError("مجوز دریافت کانفیگ تست برای حساب شما فعال نیست.")
            daily_limit = int(agent["daily_test_limit"] or 0) if "daily_test_limit" in agent.keys() else 0
            if daily_limit <= 0:
                raise ValueError("سهمیه روزانه کانفیگ تست برای حساب شما تنظیم نشده است.")
            from .util import iran_day_bounds_ts

            day_start, day_end = iran_day_bounds_ts()
            used_row = await self.db.fetchone(
                """
                SELECT COUNT(*) AS used
                FROM agent_test_configs
                WHERE user_id=?
                  AND created_at>=?
                  AND created_at<?
                  AND status IN ('pending','created','active')
                """,
                (int(user_id), day_start, day_end),
            )
            if int(used_row["used"] if used_row else 0) >= daily_limit:
                raise ValueError("سهمیه کانفیگ تست امروز شما تمام شده است.")
            await conn.execute(
                "INSERT INTO idempotency_keys(key,user_id,order_id,status,created_at) VALUES(?,?,?,?,?)",
                (idem, int(user_id), test_id, "reserved", now_ts()),
            )
            await conn.execute(
                "INSERT INTO agent_test_configs(test_id,user_id,sub_id,created_at,expires_at,status) VALUES(?,?,?,?,?,'pending')",
                (test_id, int(user_id), f"pending:{test_id}", now_ts(), expires_at),
            )

        try:
            if pg_client is not None:
                username = f"test{int(user_id)}_{secrets.token_hex(4)}"
                # on_hold: the test window starts on FIRST connect, not at
                # creation — otherwise a 10-minute test usually expires before
                # the buyer even imports the link (looks broken in the panel).
                resp = await pg_client.create_user(
                    username=username,
                    group_ids=list(group_ids or []),
                    data_limit_bytes=TEST_CONFIG_BYTES,
                    on_hold_duration_seconds=TEST_CONFIG_TTL_SECONDS,
                    note=f"tg:{int(user_id)} test",
                )
                sub_url = str((resp or {}).get("subscription_url") or "")
                if not sub_url:
                    raise RuntimeError("PasarGuard did not return a subscription_url")
                provision = PanelClientPayload(
                    user_id=int(user_id), sub_id=username, sub_link=sub_url,
                    inbound_id=PG_INBOUND_SENTINEL, client_uuid="", client_email=username, gb=0,
                )
            else:
                provision = await self.panel.add_test_subscription(
                    user_id=user_id,
                    total_bytes=TEST_CONFIG_BYTES,
                    ttl_seconds=TEST_CONFIG_TTL_SECONDS,
                )
            await self.db.insert_test_subscription(
                provision,
                test_id=test_id,
                expires_at=expires_at,
                total_bytes=TEST_CONFIG_BYTES,
            )
            await self.db.execute("UPDATE idempotency_keys SET status='approved' WHERE key=?", (idem,))
            return provision.sub_link
        except Exception:
            await self.db.execute("UPDATE agent_test_configs SET status='failed' WHERE test_id=?", (test_id,))
            await self.db.execute("UPDATE idempotency_keys SET status='failed' WHERE key=?", (idem,))
            raise

    async def process_renewal(
        self,
        *,
        user_id: int,
        sub_id: str,
        gb: int,
        unit_price: int,
        final_total: int,
        idempotency_key: str | None = None,
    ) -> str:
        clean_sub_id = str(sub_id or "").strip()
        if not clean_sub_id:
            raise ValueError("اشتراک انتخاب‌شده معتبر نیست.")
        requested_gb = int(gb)
        if requested_gb <= 0:
            raise ValueError("حجم تمدید معتبر نیست.")
        min_gb = await self.minimum_purchase_gb()
        if requested_gb < min_gb:
            raise ValueError(f"حداقل حجم مجاز برای تمدید {min_gb} گیگ است.")

        subscription = await self.db.get_subscription_for_user(user_id, clean_sub_id)
        if not subscription:
            raise ValueError("اشتراک انتخاب‌شده پیدا نشد.")

        agent = await self.db.get_agent(user_id)
        effective_unit_price = int(unit_price)
        if agent and int(agent["price_per_gb"] or 0) > 0:
            effective_unit_price = int(agent["price_per_gb"])
        if effective_unit_price <= 0:
            raise ValueError("تعرفه حساب شما معتبر نیست. لطفاً با پشتیبانی تماس بگیرید.")

        expected_total = requested_gb * effective_unit_price
        if int(final_total) != expected_total:
            raise ValueError("مبلغ فاکتور تمدید معتبر نیست.")

        order_id = f"{user_id}-renew-{now_ts()}-{secrets.token_hex(3)}"
        idem = idempotency_key or order_id
        payment_method = PaymentMethod.WALLET
        client_name = str(subscription.get("client_email") or clean_sub_id)

        async with self.db.transaction() as conn:
            existing = await self.db.fetchone("SELECT order_id,status FROM idempotency_keys WHERE key=?", (idem,))
            if existing:
                raise RuntimeError(f"duplicate renewal request: {existing['status']}")

            subscription = await self.db.fetchone(
                "SELECT * FROM subscriptions WHERE user_id=? AND sub_id=?",
                (int(user_id), clean_sub_id),
            )
            if not subscription:
                raise ValueError("اشتراک انتخاب‌شده پیدا نشد.")

            agent = await self.db.fetchone("SELECT * FROM agents WHERE user_id=?", (int(user_id),))
            tx_effective_unit_price = int(unit_price)
            if agent and int(agent["price_per_gb"] or 0) > 0:
                tx_effective_unit_price = int(agent["price_per_gb"])
            if tx_effective_unit_price <= 0:
                raise ValueError("تعرفه حساب شما معتبر نیست. لطفاً با پشتیبانی تماس بگیرید.")
            tx_expected_total = requested_gb * tx_effective_unit_price
            if int(final_total) != tx_expected_total or int(effective_unit_price) != tx_effective_unit_price:
                raise ValueError("تعرفه حساب شما تغییر کرده است. لطفاً تمدید را دوباره ثبت کنید.")
            effective_unit_price = tx_effective_unit_price

            if agent and self.db.normalize_agent_access_value(agent["access_level"]) == "open":
                payment_method = PaymentMethod.AGENT_OPEN
                credit_update = await conn.execute(
                    """
                    UPDATE agents
                    SET credit_used_toman=credit_used_toman+?
                    WHERE user_id=?
                      AND (credit_limit_toman<=0 OR credit_used_toman + ? <= credit_limit_toman)
                    """,
                    (int(final_total), int(user_id), int(final_total)),
                )
                if credit_update.rowcount != 1:
                    raise ValueError("سقف اعتبار شما کافی نیست. لطفا بدهی خود را تسویه کنید.")
                await conn.execute(
                    "INSERT OR IGNORE INTO agent_ledger(user_id,amount_toman,kind,ref_id,created_at) VALUES(?,?,?,?,?)",
                    (int(user_id), int(final_total), "renewal_credit_reserve", order_id, now_ts()),
                )
            else:
                debit = await self.db.try_debit_wallet_in_transaction(conn, user_id, final_total)
                if debit.rowcount != 1:
                    raise ValueError("موجودی کیف پول شما کافی نیست. لطفا حساب خود را شارژ کنید.")

            await conn.execute(
                """
                INSERT INTO orders
                  (order_id,user_id,plan_id,gb,qty,unit_price,price,discount_amount,
                   final_price,status,created_at,payment_method,order_type,target_sub_id,client_name)
                VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?)
                """,
                (
                    order_id,
                    int(user_id),
                    -1,
                    requested_gb,
                    1,
                    int(effective_unit_price),
                    int(effective_unit_price) * requested_gb,
                    0,
                    int(final_total),
                    now_ts(),
                    payment_method.value,
                    "renewal",
                    clean_sub_id,
                    client_name,
                ),
            )
            await conn.execute(
                "INSERT INTO idempotency_keys(key,user_id,order_id,status,created_at) VALUES(?,?,?,?,?)",
                (idem, int(user_id), order_id, "reserved", now_ts()),
            )

        try:
            # A renewal grants a fresh validity window (same duration as a new
            # purchase, default 30 days) so renewing never leaves the config
            # expiring on its original date.
            duration_days = _safe_positive_int(await self.db.get_setting("purchase_duration_days", "30"), 30)
            renew_expiry_ms = (now_ts() + duration_days * 86400) * 1000 if duration_days > 0 else None
            detail = await self.panel.renew_subscription(clean_sub_id, requested_gb, set_expiry_ms=renew_expiry_ms)
            await self.db.update_subscription_panel_snapshot(detail)
            await self.db.execute(
                "UPDATE subscriptions SET renewed_count=renewed_count+1,last_renewed_at=? WHERE sub_id=?",
                (now_ts(), clean_sub_id),
            )
            approved = await self.db.approve_order(order_id)
            if not approved:
                raise RuntimeError("renewal order approval failed after panel update")
            await self.db.execute("UPDATE idempotency_keys SET status='approved' WHERE key=?", (idem,))
            return detail.sub_link
        except Exception:
            if payment_method == PaymentMethod.AGENT_OPEN:
                async with self.db.transaction() as conn:
                    await conn.execute(
                        "UPDATE agents SET credit_used_toman=max(0, credit_used_toman-?) WHERE user_id=?",
                        (int(final_total), int(user_id)),
                    )
                    await conn.execute(
                        "INSERT OR IGNORE INTO agent_ledger(user_id,amount_toman,kind,ref_id,created_at) VALUES(?,?,?,?,?)",
                        (int(user_id), -int(final_total), "renewal_credit_refund", f"{order_id}:refund", now_ts()),
                    )
            else:
                await self.db.credit_wallet(user_id, final_total)
            await self.db.reject_order(order_id)
            await self.db.execute("UPDATE idempotency_keys SET status='failed' WHERE key=?", (idem,))
            raise

    # ───────────────────────── PasarGuard backend ─────────────────────────
    async def _sync_pg_subscription_row(self, sub_id: str, pg_user: dict | None) -> None:
        """Mirror a PasarGuard user's live numbers into the local subscriptions
        row so «اشتراک‌های من» can show volume/remaining/expiry without a second
        panel round-trip. Never raises (a snapshot is a nicety, not the truth)."""
        if not pg_user:
            return
        try:
            total = int(pg_user.get("data_limit") or 0)
            used = int(pg_user.get("used_traffic") or 0)
            remaining = max(0, total - used) if total > 0 else 0
            expiry_ms = int(_iso_to_epoch(pg_user.get("expire")) * 1000)
            enabled = 1 if str(pg_user.get("status") or "active") in {"active", "on_hold"} else 0
            await self.db.execute(
                """
                UPDATE subscriptions
                SET panel_total_bytes=?, panel_used_bytes=?, panel_remaining_bytes=?,
                    panel_enabled=?, panel_expiry_time=?, panel_synced_at=?
                WHERE sub_id=?
                """,
                (total, used, remaining, enabled, expiry_ms, now_ts(), str(sub_id)),
            )
        except Exception:
            LOG.debug("PasarGuard snapshot sync failed sub_id=%s", sub_id, exc_info=True)

    async def process_pg_checkout(
        self,
        *,
        pg_client,
        group_ids: list[int],
        user_id: int,
        gb: int,
        qty: int,
        unit_price: int,
        final_total: int,
        days: int = 0,
        client_name: str = "",
        idempotency_key: str | None = None,
    ) -> list[str]:
        """Buy service on the PasarGuard backend at ElsaVPN's per-GB price.

        Charges the wallet/agent-credit (same money rules as the 3x-ui flow),
        creates ``qty`` PasarGuard users (data_limit = gb, expire = now+days —
        the same validity window a 3x-ui purchase gets), records them in the
        subscriptions table tagged with PG_INBOUND_SENTINEL and returns their
        subscription URLs. Fully rolled back on any failure.
        """
        requested_gb = int(gb)
        requested_qty = int(qty)
        if requested_gb <= 0:
            raise ValueError("حجم خرید معتبر نیست.")
        if requested_qty <= 0:
            raise ValueError("تعداد خرید معتبر نیست.")

        agent = await self.db.get_agent(user_id)
        effective_unit_price = int(unit_price)
        if agent and int(agent["price_per_gb"] or 0) > 0:
            effective_unit_price = int(agent["price_per_gb"])
        if effective_unit_price <= 0:
            raise ValueError("تعرفه حساب شما معتبر نیست. لطفاً با پشتیبانی تماس بگیرید.")
        min_gb = await self.minimum_purchase_gb()
        if requested_gb < min_gb:
            raise ValueError(f"حداقل حجم مجاز برای خرید {min_gb} گیگ است.")
        if int(final_total) != requested_gb * requested_qty * effective_unit_price:
            raise ValueError("مبلغ فاکتور معتبر نیست.")

        order_id = f"{user_id}-pg-{now_ts()}-{secrets.token_hex(3)}"
        idem = idempotency_key or order_id
        payment_method = PaymentMethod.WALLET
        async with self.db.transaction() as conn:
            existing = await self.db.fetchone("SELECT order_id,status FROM idempotency_keys WHERE key=?", (idem,))
            if existing:
                raise RuntimeError(f"duplicate purchase request: {existing['status']}")
            agent_row = await self.db.fetchone("SELECT * FROM agents WHERE user_id=?", (int(user_id),))
            tx_unit = int(unit_price)
            if agent_row and int(agent_row["price_per_gb"] or 0) > 0:
                tx_unit = int(agent_row["price_per_gb"])
            if tx_unit <= 0 or int(final_total) != requested_gb * requested_qty * tx_unit or tx_unit != effective_unit_price:
                raise ValueError("تعرفه حساب شما تغییر کرده است. لطفاً خرید را دوباره ثبت کنید.")
            if agent_row and self.db.normalize_agent_access_value(agent_row["access_level"]) == "open":
                payment_method = PaymentMethod.AGENT_OPEN
                credit_update = await conn.execute(
                    """
                    UPDATE agents SET credit_used_toman=credit_used_toman+?
                    WHERE user_id=? AND (credit_limit_toman<=0 OR credit_used_toman + ? <= credit_limit_toman)
                    """,
                    (int(final_total), int(user_id), int(final_total)),
                )
                if credit_update.rowcount != 1:
                    raise ValueError("سقف اعتبار شما کافی نیست. لطفا بدهی خود را تسویه کنید.")
                await conn.execute(
                    "INSERT OR IGNORE INTO agent_ledger(user_id,amount_toman,kind,ref_id,created_at) VALUES(?,?,?,?,?)",
                    (int(user_id), int(final_total), "credit_reserve", order_id, now_ts()),
                )
            else:
                debit = await self.db.try_debit_wallet_in_transaction(conn, user_id, final_total)
                if debit.rowcount != 1:
                    raise ValueError("موجودی کیف پول شما کافی نیست. لطفا حساب خود را شارژ کنید.")
            await conn.execute(
                """
                INSERT INTO orders
                  (order_id,user_id,plan_id,gb,qty,unit_price,price,discount_amount,
                   final_price,status,created_at,payment_method,order_type,target_sub_id,client_name)
                VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?)
                """,
                (
                    order_id, int(user_id), 0, requested_gb, requested_qty, int(effective_unit_price),
                    int(effective_unit_price) * requested_gb * requested_qty, 0, int(final_total), now_ts(),
                    payment_method.value, "purchase", None, (client_name or "").strip() or None,
                ),
            )
            await conn.execute(
                "INSERT INTO idempotency_keys(key,user_id,order_id,status,created_at) VALUES(?,?,?,?,?)",
                (idem, int(user_id), order_id, "reserved", now_ts()),
            )

        created: list[str] = []
        rows: list[PanelClientPayload] = []
        try:
            expire_ts = (now_ts() + int(days) * 86400) if int(days) > 0 else 0
            data_bytes = gb_to_bytes(requested_gb)
            # PasarGuard usernames are strict (letters/digits/underscore); strip
            # anything else so a custom name can never cause a 422 on create.
            base = "".join(c for c in sanitize_client_name(client_name) if c.isalnum() or c == "_")
            base = base[:18].strip("_") or f"u{int(user_id)}"
            for _ in range(requested_qty):
                username = f"{base}_{secrets.token_hex(4)}"
                resp = await pg_client.create_user(
                    username=username,
                    group_ids=list(group_ids),
                    data_limit_bytes=data_bytes,
                    expire=expire_ts,
                    note=f"tg:{int(user_id)}",
                )
                created.append(username)
                sub_url = str((resp or {}).get("subscription_url") or "")
                rows.append(
                    PanelClientPayload(
                        user_id=int(user_id),
                        sub_id=username,
                        sub_link=sub_url,
                        inbound_id=PG_INBOUND_SENTINEL,
                        client_uuid="",
                        client_email=username,
                        gb=requested_gb,
                    )
                )
            await self.db.insert_subscriptions(rows, order_id=order_id)
            approved = await self.db.approve_order(order_id)
            if not approved:
                raise RuntimeError("PasarGuard order approval failed after provisioning")
            await self.db.execute("UPDATE idempotency_keys SET status='approved' WHERE key=?", (idem,))
            # Seed the local snapshot so the first «اشتراک‌های من» view is accurate.
            for row in rows:
                await self.db.execute(
                    """
                    UPDATE subscriptions
                    SET panel_total_bytes=?, panel_used_bytes=0, panel_remaining_bytes=?,
                        panel_enabled=1, panel_expiry_time=?, panel_synced_at=?
                    WHERE sub_id=?
                    """,
                    (data_bytes, data_bytes, expire_ts * 1000, now_ts(), row.sub_id),
                )
            return [r.sub_link for r in rows]
        except Exception:
            for username in created:
                try:
                    await pg_client.delete_user(username)
                except Exception:
                    LOG.exception("PasarGuard rollback: could not delete user %s", username)
            await self.db.delete_subscriptions(created)
            if payment_method == PaymentMethod.AGENT_OPEN:
                async with self.db.transaction() as conn:
                    await conn.execute(
                        "UPDATE agents SET credit_used_toman=max(0, credit_used_toman-?) WHERE user_id=?",
                        (int(final_total), int(user_id)),
                    )
                    await conn.execute(
                        "INSERT OR IGNORE INTO agent_ledger(user_id,amount_toman,kind,ref_id,created_at) VALUES(?,?,?,?,?)",
                        (int(user_id), -int(final_total), "credit_refund", f"{order_id}:refund", now_ts()),
                    )
            else:
                await self.db.credit_wallet(user_id, final_total)
            await self.db.reject_order(order_id)
            await self.db.execute("UPDATE idempotency_keys SET status='failed' WHERE key=?", (idem,))
            raise

    async def process_pg_renewal(
        self,
        *,
        pg_client,
        user_id: int,
        sub_id: str,
        gb: int,
        unit_price: int,
        final_total: int,
        days: int = 0,
        idempotency_key: str | None = None,
    ) -> str:
        """Renew a PasarGuard service by ADDING volume (the same per-GB model as
        the 3x-ui renewal) and refreshing its validity window.

        The panel user's ``data_limit`` grows by the purchased GB and ``expire``
        is pushed to now+days, so a depleted/expired service becomes usable
        again. Money rules mirror ``process_renewal`` exactly (wallet or
        open-agent credit, re-validated inside the transaction, fully refunded
        when the panel call fails).
        """
        clean_sub_id = str(sub_id or "").strip()
        if not clean_sub_id:
            raise ValueError("اشتراک انتخاب‌شده معتبر نیست.")
        requested_gb = int(gb)
        if requested_gb <= 0:
            raise ValueError("حجم تمدید معتبر نیست.")
        min_gb = await self.minimum_purchase_gb()
        if requested_gb < min_gb:
            raise ValueError(f"حداقل حجم مجاز برای تمدید {min_gb} گیگ است.")

        subscription = await self.db.get_subscription_for_user(user_id, clean_sub_id)
        if not subscription:
            raise ValueError("اشتراک انتخاب‌شده پیدا نشد.")
        if int(subscription.get("inbound_id") or 0) != PG_INBOUND_SENTINEL:
            raise ValueError("این سرویس روی سرور دیگری است و از این مسیر تمدید نمی‌شود.")

        agent = await self.db.get_agent(user_id)
        effective_unit_price = int(unit_price)
        if agent and int(agent["price_per_gb"] or 0) > 0:
            effective_unit_price = int(agent["price_per_gb"])
        if effective_unit_price <= 0:
            raise ValueError("تعرفه حساب شما معتبر نیست. لطفاً با پشتیبانی تماس بگیرید.")
        if int(final_total) != requested_gb * effective_unit_price:
            raise ValueError("مبلغ فاکتور تمدید معتبر نیست.")

        # Read the live panel state BEFORE charging: renewing a service that no
        # longer exists on the panel must fail without taking the user's money.
        pg_user = await pg_client.get_user(clean_sub_id)
        if not pg_user:
            raise ValueError("این سرویس روی پنل پیدا نشد. لطفاً با پشتیبانی تماس بگیرید.")
        current_limit = int(pg_user.get("data_limit") or 0)

        order_id = f"{user_id}-pgrenew-{now_ts()}-{secrets.token_hex(3)}"
        idem = idempotency_key or order_id
        payment_method = PaymentMethod.WALLET
        client_name = str(subscription.get("client_email") or clean_sub_id)

        async with self.db.transaction() as conn:
            existing = await self.db.fetchone("SELECT order_id,status FROM idempotency_keys WHERE key=?", (idem,))
            if existing:
                raise RuntimeError(f"duplicate renewal request: {existing['status']}")
            row = await self.db.fetchone(
                "SELECT * FROM subscriptions WHERE user_id=? AND sub_id=?",
                (int(user_id), clean_sub_id),
            )
            if not row:
                raise ValueError("اشتراک انتخاب‌شده پیدا نشد.")
            agent_row = await self.db.fetchone("SELECT * FROM agents WHERE user_id=?", (int(user_id),))
            tx_unit = int(unit_price)
            if agent_row and int(agent_row["price_per_gb"] or 0) > 0:
                tx_unit = int(agent_row["price_per_gb"])
            if tx_unit <= 0 or int(final_total) != requested_gb * tx_unit or tx_unit != effective_unit_price:
                raise ValueError("تعرفه حساب شما تغییر کرده است. لطفاً تمدید را دوباره ثبت کنید.")
            if agent_row and self.db.normalize_agent_access_value(agent_row["access_level"]) == "open":
                payment_method = PaymentMethod.AGENT_OPEN
                credit_update = await conn.execute(
                    """
                    UPDATE agents SET credit_used_toman=credit_used_toman+?
                    WHERE user_id=? AND (credit_limit_toman<=0 OR credit_used_toman + ? <= credit_limit_toman)
                    """,
                    (int(final_total), int(user_id), int(final_total)),
                )
                if credit_update.rowcount != 1:
                    raise ValueError("سقف اعتبار شما کافی نیست. لطفا بدهی خود را تسویه کنید.")
                await conn.execute(
                    "INSERT OR IGNORE INTO agent_ledger(user_id,amount_toman,kind,ref_id,created_at) VALUES(?,?,?,?,?)",
                    (int(user_id), int(final_total), "renewal_credit_reserve", order_id, now_ts()),
                )
            else:
                debit = await self.db.try_debit_wallet_in_transaction(conn, user_id, final_total)
                if debit.rowcount != 1:
                    raise ValueError("موجودی کیف پول شما کافی نیست. لطفا حساب خود را شارژ کنید.")
            await conn.execute(
                """
                INSERT INTO orders
                  (order_id,user_id,plan_id,gb,qty,unit_price,price,discount_amount,
                   final_price,status,created_at,payment_method,order_type,target_sub_id,client_name)
                VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?)
                """,
                (
                    order_id, int(user_id), -1, requested_gb, 1, int(effective_unit_price),
                    int(effective_unit_price) * requested_gb, 0, int(final_total), now_ts(),
                    payment_method.value, "renewal", clean_sub_id, client_name,
                ),
            )
            await conn.execute(
                "INSERT INTO idempotency_keys(key,user_id,order_id,status,created_at) VALUES(?,?,?,?,?)",
                (idem, int(user_id), order_id, "reserved", now_ts()),
            )

        try:
            fields: dict = {"status": "active"}
            # A service with data_limit=0 is unlimited on the panel — adding volume
            # to it would silently DOWNGRADE it to a capped one, so leave it alone.
            if current_limit > 0:
                fields["data_limit"] = current_limit + gb_to_bytes(requested_gb)
            if int(days) > 0:
                fields["expire"] = pg_client._iso(now_ts() + int(days) * 86400)
            updated = await pg_client.modify_user(clean_sub_id, fields)
            await self.db.execute(
                "UPDATE subscriptions SET gb=gb+?, renewed_count=renewed_count+1, last_renewed_at=? WHERE sub_id=?",
                (requested_gb, now_ts(), clean_sub_id),
            )
            await self._sync_pg_subscription_row(clean_sub_id, updated if isinstance(updated, dict) else pg_user)
            approved = await self.db.approve_order(order_id)
            if not approved:
                raise RuntimeError("renewal order approval failed after panel update")
            await self.db.execute("UPDATE idempotency_keys SET status='approved' WHERE key=?", (idem,))
            return str((updated or {}).get("subscription_url") or pg_user.get("subscription_url") or "")
        except Exception:
            if payment_method == PaymentMethod.AGENT_OPEN:
                async with self.db.transaction() as conn:
                    await conn.execute(
                        "UPDATE agents SET credit_used_toman=max(0, credit_used_toman-?) WHERE user_id=?",
                        (int(final_total), int(user_id)),
                    )
                    await conn.execute(
                        "INSERT OR IGNORE INTO agent_ledger(user_id,amount_toman,kind,ref_id,created_at) VALUES(?,?,?,?,?)",
                        (int(user_id), -int(final_total), "renewal_credit_refund", f"{order_id}:refund", now_ts()),
                    )
            else:
                await self.db.credit_wallet(user_id, final_total)
            await self.db.reject_order(order_id)
            await self.db.execute("UPDATE idempotency_keys SET status='failed' WHERE key=?", (idem,))
            raise
