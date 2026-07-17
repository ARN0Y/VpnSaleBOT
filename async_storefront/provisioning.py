from __future__ import annotations

import json
import logging
import secrets

from .agent import AgentService
from .db import AsyncDatabase
from .models import PaymentMethod, PanelClientPayload
from .panel import PanelClient
from .util import now_ts, gb_to_bytes, sanitize_client_name

LOG = logging.getLogger(__name__)

TEST_CONFIG_BYTES = 200 * 1024 * 1024
TEST_CONFIG_TTL_SECONDS = 10 * 60          # agent daily test config
FREE_TEST_TTL_SECONDS = 24 * 60 * 60       # one-time free test for regular users (1 day)

# Subscriptions provisioned on the PasarGuard backend are tagged with this
# sentinel inbound id so the rest of the app can tell them apart from 3x-ui
# subs with no DB schema change.
PG_INBOUND_SENTINEL = -100


def parse_packages(raw) -> list[dict]:
    """Parse a panel's package list (stored as JSON in settings). Each item:
    {kind: 'volume'|'unlimited', title, gb, days, price, agent_price}. Invalid
    rows are dropped so a malformed entry can never crash the buy flow."""
    try:
        data = json.loads(raw or "[]")
    except Exception:
        return []
    out: list[dict] = []
    for item in data if isinstance(data, list) else []:
        try:
            kind = "unlimited" if str((item or {}).get("kind")) == "unlimited" else "volume"
            title = str((item or {}).get("title") or "").strip()
            gb = max(0, int(float((item or {}).get("gb") or (item or {}).get("cap_gb") or 0)))
            days = max(0, int(float((item or {}).get("days") or 0)))
            price = max(0, int(float((item or {}).get("price") or 0)))
            agent_price = max(0, int(float((item or {}).get("agent_price") or 0)))
        except (TypeError, ValueError):
            continue
        if not title or price <= 0:
            continue
        if kind == "volume" and gb <= 0:
            continue
        out.append({"kind": kind, "title": title, "gb": gb, "days": days, "price": price, "agent_price": agent_price})
    return out


def package_price(pkg: dict, agent) -> int:
    """Price a package for a given audience — fully explicit, no per-GB leakage.

    Agents pay the package's ``agent_price`` when it is set (> 0); otherwise
    everyone (users and agents) pays the package's fixed ``price``.
    """
    if agent:
        agent_price = int(pkg.get("agent_price") or 0)
        if agent_price > 0:
            return agent_price
    return int(pkg["price"])


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

    async def _reserve_wallet_payment(self, conn, *, user_id: int, amount_toman: int, agent_row=None) -> PaymentMethod:
        """Reserve money for any checkout by debiting the buyer's wallet.

        Agent credit/open-access is intentionally not supported on this branch:
        representatives keep their pricing/limits, but every paid action must be
        backed by wallet balance. The returned payment method is only for
        reporting; rollback always refunds the same wallet.
        """
        amount = int(amount_toman)
        if amount <= 0:
            raise ValueError("مبلغ فاکتور معتبر نیست.")
        debit = await self.db.try_debit_wallet_in_transaction(conn, user_id, amount)
        if debit.rowcount != 1:
            raise ValueError("موجودی کیف پول شما کافی نیست. لطفا حساب خود را شارژ کنید.")
        return PaymentMethod.AGENT_WALLET if agent_row else PaymentMethod.WALLET

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

            payment_method = await self._reserve_wallet_payment(
                conn,
                user_id=user_id,
                amount_toman=final_total,
                agent_row=agent,
            )

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
            provisions = await self.panel.add_subscriptions(user_id=user_id, gb=requested_gb, qty=requested_qty, preferred_name=client_name)
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
            payment_method = await self._reserve_wallet_payment(
                conn,
                user_id=user_id,
                amount_toman=final_total,
                agent_row=agent,
            )
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
            await self.db.credit_wallet(user_id, final_total)
            await self.db.reject_order(order_id)
            await self.db.execute("UPDATE idempotency_keys SET status='failed' WHERE key=?", (idem,))
            raise

    async def process_agent_test_config(
        self, *, user_id: int, pg_client=None, group_ids=None, idempotency_key: str | None = None
    ) -> str:
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
                # Create the agent test on the PasarGuard backend (same as real
                # sales when PasarGuard is primary). on_hold: the 10-minute window
                # starts on the user's FIRST connect, not at creation — mirrors
                # the free-test flow so the config doesn't expire before use.
                username = f"test{int(user_id)}_{secrets.token_hex(4)}"
                resp = await pg_client.create_user(
                    username=username,
                    group_ids=list(group_ids or []),
                    data_limit_bytes=TEST_CONFIG_BYTES,
                    on_hold_duration_seconds=TEST_CONFIG_TTL_SECONDS,
                    note=f"tg:{int(user_id)} agent-test",
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

    async def process_free_test(
        self, *, user_id: int, pg_client=None, group_ids=None, idempotency_key: str | None = None
    ) -> str:
        """One-time free 200MB / 1-day test for a REGULAR user. Created on the
        primary backend (PasarGuard when pg_client is given, otherwise 3x-ui).
        Enforced one-per-user via agent_test_configs."""
        test_id = f"ftest-{int(user_id)}-{now_ts()}-{secrets.token_hex(3)}"
        idem = idempotency_key or test_id
        expires_at = now_ts() + FREE_TEST_TTL_SECONDS
        async with self.db.transaction() as conn:
            if await self.db.fetchone("SELECT 1 FROM idempotency_keys WHERE key=?", (idem,)):
                raise RuntimeError("duplicate test config request")
            used = await self.db.fetchone(
                "SELECT COUNT(*) AS c FROM agent_test_configs WHERE user_id=? AND status IN ('pending','created','active')",
                (int(user_id),),
            )
            if int((used["c"] if used else 0) or 0) > 0:
                raise ValueError("شما قبلاً تست رایگان دریافت کرده‌اید. هر کاربر فقط یک‌بار می‌تواند تست رایگان بگیرد.")
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
                # creation — otherwise the test often expires before the user
                # even imports/connects (made it look broken in the panel).
                resp = await pg_client.create_user(
                    username=username,
                    group_ids=list(group_ids or []),
                    data_limit_bytes=TEST_CONFIG_BYTES,
                    on_hold_duration_seconds=FREE_TEST_TTL_SECONDS,
                    note=f"tg:{int(user_id)} free-test",
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
                    user_id=user_id, total_bytes=TEST_CONFIG_BYTES, ttl_seconds=FREE_TEST_TTL_SECONDS
                )
            await self.db.insert_test_subscription(
                provision, test_id=test_id, expires_at=expires_at, total_bytes=TEST_CONFIG_BYTES
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

            payment_method = await self._reserve_wallet_payment(
                conn,
                user_id=user_id,
                amount_toman=final_total,
                agent_row=agent,
            )

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
            detail = await self.panel.renew_subscription(clean_sub_id, requested_gb)
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
        idempotency_key: str | None = None,
    ) -> str:
        """Renew a PasarGuard service by adding volume to the existing user.

        Mirrors process_renewal's money rules exactly (wallet-only, in-transaction
        price re-validation, full refund on failure), but adds the purchased GB to
        the PasarGuard user's data_limit and reactivates it instead of touching the
        3x-ui panel. The user keeps the same sub link."""
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
            raise ValueError("این سرویس روی سرور اختصاصی نیست.")

        agent = await self.db.get_agent(user_id)
        effective_unit_price = int(unit_price)
        if agent and int(agent["price_per_gb"] or 0) > 0:
            effective_unit_price = int(agent["price_per_gb"])
        if effective_unit_price <= 0:
            raise ValueError("تعرفه حساب شما معتبر نیست. لطفاً با پشتیبانی تماس بگیرید.")
        expected_total = requested_gb * effective_unit_price
        if int(final_total) != expected_total:
            raise ValueError("مبلغ فاکتور تمدید معتبر نیست.")

        order_id = f"{user_id}-pgrenew-{now_ts()}-{secrets.token_hex(3)}"
        idem = idempotency_key or order_id
        client_name = str(subscription.get("client_email") or clean_sub_id)

        async with self.db.transaction() as conn:
            existing = await self.db.fetchone("SELECT order_id,status FROM idempotency_keys WHERE key=?", (idem,))
            if existing:
                raise RuntimeError(f"duplicate renewal request: {existing['status']}")
            agent_row = await self.db.fetchone("SELECT * FROM agents WHERE user_id=?", (int(user_id),))
            tx_unit_price = int(unit_price)
            if agent_row and int(agent_row["price_per_gb"] or 0) > 0:
                tx_unit_price = int(agent_row["price_per_gb"])
            if tx_unit_price <= 0:
                raise ValueError("تعرفه حساب شما معتبر نیست. لطفاً با پشتیبانی تماس بگیرید.")
            if int(final_total) != requested_gb * tx_unit_price or int(effective_unit_price) != tx_unit_price:
                raise ValueError("تعرفه حساب شما تغییر کرده است. لطفاً تمدید را دوباره ثبت کنید.")
            effective_unit_price = tx_unit_price
            payment_method = await self._reserve_wallet_payment(
                conn, user_id=user_id, amount_toman=final_total, agent_row=agent_row
            )
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
            pg_user = await pg_client.get_user(clean_sub_id)
            if not pg_user:
                raise RuntimeError("PasarGuard user not found for renewal")
            current_limit = int(pg_user.get("data_limit") or 0)
            fields: dict = {"status": "active"}
            # Only capped (volume) users get more volume; an unlimited user
            # (data_limit 0) stays unlimited — just reactivate it.
            if current_limit > 0:
                fields["data_limit"] = current_limit + gb_to_bytes(requested_gb)
            resp = await pg_client.modify_user(clean_sub_id, fields)
            sub_url = str((resp or {}).get("subscription_url") or subscription.get("sub_link") or "")
            await self.db.execute(
                "UPDATE subscriptions SET gb=COALESCE(gb,0)+?, renewed_count=renewed_count+1, last_renewed_at=? WHERE sub_id=?",
                (requested_gb, now_ts(), clean_sub_id),
            )
            approved = await self.db.approve_order(order_id)
            if not approved:
                raise RuntimeError("pg renewal order approval failed after panel update")
            await self.db.execute("UPDATE idempotency_keys SET status='approved' WHERE key=?", (idem,))
            return sub_url
        except Exception:
            await self.db.credit_wallet(user_id, final_total)
            await self.db.reject_order(order_id)
            await self.db.execute("UPDATE idempotency_keys SET status='failed' WHERE key=?", (idem,))
            raise

    async def process_pg_same_renewal(
        self,
        *,
        pg_client,
        user_id: int,
        sub_id: str,
        price: int,
        gb: int,
        days: int,
        idempotency_key: str | None = None,
    ) -> str:
        """Re-buy the SAME PasarGuard package the user originally purchased, at
        the original package ``price``. Adds the package's ``gb`` volume and
        re-arms the package validity (on_hold ``days`` — countdown restarts on the
        user's next connect, exactly like a fresh package). Wallet-only, price
        re-checked in-transaction, full refund on any failure."""
        clean_sub_id = str(sub_id or "").strip()
        if not clean_sub_id:
            raise ValueError("اشتراک انتخاب‌شده معتبر نیست.")
        final_total = int(price)
        if final_total <= 0:
            raise ValueError("قیمت این سرویس مشخص نیست؛ لطفاً با پشتیبانی تماس بگیرید.")
        add_gb = max(0, int(gb))
        add_days = max(0, int(days))

        subscription = await self.db.get_subscription_for_user(user_id, clean_sub_id)
        if not subscription:
            raise ValueError("اشتراک انتخاب‌شده پیدا نشد.")
        if int(subscription.get("inbound_id") or 0) != PG_INBOUND_SENTINEL:
            raise ValueError("این سرویس روی سرور اختصاصی نیست.")

        order_id = f"{user_id}-pgsame-{now_ts()}-{secrets.token_hex(3)}"
        idem = idempotency_key or order_id
        client_name = str(subscription.get("client_email") or clean_sub_id)

        async with self.db.transaction() as conn:
            existing = await self.db.fetchone("SELECT order_id,status FROM idempotency_keys WHERE key=?", (idem,))
            if existing:
                raise RuntimeError(f"duplicate renewal request: {existing['status']}")
            agent_row = await self.db.fetchone("SELECT * FROM agents WHERE user_id=?", (int(user_id),))
            payment_method = await self._reserve_wallet_payment(
                conn, user_id=user_id, amount_toman=final_total, agent_row=agent_row
            )
            await conn.execute(
                """
                INSERT INTO orders
                  (order_id,user_id,plan_id,gb,qty,unit_price,price,discount_amount,
                   final_price,status,created_at,payment_method,order_type,target_sub_id,client_name)
                VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?)
                """,
                (
                    order_id, int(user_id), -1, int(add_gb), 1, int(final_total),
                    int(final_total), 0, int(final_total), now_ts(),
                    payment_method.value, "renewal", clean_sub_id, client_name,
                ),
            )
            await conn.execute(
                "INSERT INTO idempotency_keys(key,user_id,order_id,status,created_at) VALUES(?,?,?,?,?)",
                (idem, int(user_id), order_id, "reserved", now_ts()),
            )

        try:
            pg_user = await pg_client.get_user(clean_sub_id)
            if not pg_user:
                raise RuntimeError("PasarGuard user not found for renewal")
            current_limit = int(pg_user.get("data_limit") or 0)
            fields: dict = {}
            # Add the package's volume (capped users only; unlimited stays unlimited).
            if add_gb > 0 and current_limit > 0:
                fields["data_limit"] = current_limit + gb_to_bytes(add_gb)
            if add_days > 0:
                # Re-arm the package: fresh validity window that starts on the
                # user's next connect (same on_hold model as a new purchase).
                fields["status"] = "on_hold"
                fields["on_hold_expire_duration"] = add_days * 86400
                fields["on_hold_timeout"] = None
                fields["expire"] = None
            else:
                fields["status"] = "active"
            resp = await pg_client.modify_user(clean_sub_id, fields)
            sub_url = str((resp or {}).get("subscription_url") or subscription.get("sub_link") or "")
            await self.db.execute(
                "UPDATE subscriptions SET gb=COALESCE(gb,0)+?, renewed_count=renewed_count+1, last_renewed_at=? WHERE sub_id=?",
                (int(add_gb), now_ts(), clean_sub_id),
            )
            approved = await self.db.approve_order(order_id)
            if not approved:
                raise RuntimeError("pg same-service renewal approval failed after panel update")
            await self.db.execute("UPDATE idempotency_keys SET status='approved' WHERE key=?", (idem,))
            return sub_url
        except Exception:
            await self.db.credit_wallet(user_id, final_total)
            await self.db.reject_order(order_id)
            await self.db.execute("UPDATE idempotency_keys SET status='failed' WHERE key=?", (idem,))
            raise

    async def process_package_purchase(
        self,
        *,
        user_id: int,
        pkg: dict,
        client_name: str = "",
        idempotency_key: str | None = None,
    ) -> list[str]:
        """Buy a per-panel package (volume or fair-usage 'unlimited') on THIS
        provisioning instance's panel. Charges the audience-correct price, sets
        the package's duration as the config expiry, and (for unlimited) caps
        traffic at the admin's hidden fair-usage limit. Returns the sub link(s).
        """
        kind = "unlimited" if str(pkg.get("kind")) == "unlimited" else "volume"
        days = max(0, int(pkg.get("days") or 0))
        cap_gb = max(0, int(pkg.get("gb") or 0))  # volume = volume; unlimited = hidden cap (0 = truly unlimited)
        if kind == "volume" and cap_gb <= 0:
            raise ValueError("حجم این بسته نامعتبر است.")

        agent = await self.db.get_agent(user_id)
        final_total = package_price(pkg, agent)
        if final_total <= 0:
            raise ValueError("قیمت این بسته نامعتبر است.")

        order_id = f"{user_id}-pkg-{now_ts()}-{secrets.token_hex(3)}"
        idem = idempotency_key or order_id
        payment_method = PaymentMethod.WALLET
        async with self.db.transaction() as conn:
            existing = await self.db.fetchone("SELECT order_id,status FROM idempotency_keys WHERE key=?", (idem,))
            if existing:
                raise RuntimeError(f"duplicate purchase request: {existing['status']}")
            agent_row = await self.db.fetchone("SELECT * FROM agents WHERE user_id=?", (int(user_id),))
            tx_final_total = package_price(pkg, agent_row)
            if int(final_total) != int(tx_final_total):
                raise ValueError("تعرفه حساب شما تغییر کرده است. لطفاً خرید را دوباره ثبت کنید.")
            final_total = int(tx_final_total)
            payment_method = await self._reserve_wallet_payment(
                conn,
                user_id=user_id,
                amount_toman=final_total,
                agent_row=agent_row,
            )
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
                    payment_method.value, ("infinite" if kind == "unlimited" else "purchase"), None,
                    ((client_name or str(pkg.get("title") or "")).strip()[:64] or None),
                ),
            )
            await conn.execute(
                "INSERT INTO idempotency_keys(key,user_id,order_id,status,created_at) VALUES(?,?,?,?,?)",
                (idem, int(user_id), order_id, "reserved", now_ts()),
            )

        provisions: list = []
        try:
            expiry_ms = (now_ts() + days * 86400) * 1000 if days > 0 else 0
            provisions = await self.panel.add_subscriptions(user_id=user_id, gb=cap_gb, qty=1, preferred_name=client_name, expiry_ms=expiry_ms)
            await self.db.insert_subscriptions(provisions, order_id=order_id, is_infinite=(kind == "unlimited"))
            approved = await self.db.approve_order(order_id)
            if not approved:
                raise RuntimeError("package order approval failed after provisioning")
            await self.db.execute("UPDATE idempotency_keys SET status='approved' WHERE key=?", (idem,))
            return [p.sub_link for p in provisions]
        except Exception:
            await self.db.delete_subscriptions([p.sub_id for p in provisions])
            for provision in provisions:
                try:
                    await self.panel.delete_subscription(provision.sub_id)
                except Exception:
                    pass
            await self.db.credit_wallet(user_id, final_total)
            await self.db.reject_order(order_id)
            await self.db.execute("UPDATE idempotency_keys SET status='failed' WHERE key=?", (idem,))
            raise

    async def process_pg_package_purchase(
        self,
        *,
        pg_client,
        group_ids,
        user_id: int,
        pkg: dict,
        days: int = 0,
        client_name: str = "",
        idempotency_key: str | None = None,
    ) -> list[str]:
        """Buy a PasarGuard package (volume or fair-usage 'unlimited') at navid's
        package price. Mirrors process_package_purchase's money rules but creates
        the account on the PasarGuard backend; PG subs are tagged with
        PG_INBOUND_SENTINEL. Fully rolled back on any failure."""
        kind = "unlimited" if str(pkg.get("kind")) == "unlimited" else "volume"
        cap_gb = max(0, int(pkg.get("gb") or 0))  # volume = volume; unlimited = hidden cap (0 = truly unlimited)
        if kind == "volume" and cap_gb <= 0:
            raise ValueError("حجم این بسته نامعتبر است.")
        pkg_days = max(0, int(days or pkg.get("days") or 0))

        agent = await self.db.get_agent(user_id)
        final_total = package_price(pkg, agent)
        if final_total <= 0:
            raise ValueError("قیمت این بسته نامعتبر است.")

        order_id = f"{user_id}-pgpkg-{now_ts()}-{secrets.token_hex(3)}"
        idem = idempotency_key or order_id
        payment_method = PaymentMethod.WALLET
        async with self.db.transaction() as conn:
            existing = await self.db.fetchone("SELECT order_id,status FROM idempotency_keys WHERE key=?", (idem,))
            if existing:
                raise RuntimeError(f"duplicate purchase request: {existing['status']}")
            agent_row = await self.db.fetchone("SELECT * FROM agents WHERE user_id=?", (int(user_id),))
            tx_final_total = package_price(pkg, agent_row)
            if int(final_total) != int(tx_final_total):
                raise ValueError("تعرفه حساب شما تغییر کرده است. لطفاً خرید را دوباره ثبت کنید.")
            final_total = int(tx_final_total)
            payment_method = await self._reserve_wallet_payment(
                conn,
                user_id=user_id,
                amount_toman=final_total,
                agent_row=agent_row,
            )
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
                    payment_method.value, ("infinite" if kind == "unlimited" else "purchase"), None,
                    ((client_name or str(pkg.get("title") or "")).strip()[:64] or None),
                ),
            )
            await conn.execute(
                "INSERT INTO idempotency_keys(key,user_id,order_id,status,created_at) VALUES(?,?,?,?,?)",
                (idem, int(user_id), order_id, "reserved", now_ts()),
            )

        created: list[str] = []
        rows: list[PanelClientPayload] = []
        try:
            # The validity window starts on FIRST connect (on_hold), so e.g. a
            # 30-day package counts 30 days from when the buyer first uses it —
            # not from creation. 0 days = no time limit (active, no expiry).
            on_hold_seconds = pkg_days * 86400 if pkg_days > 0 else 0
            data_bytes = gb_to_bytes(cap_gb) if cap_gb > 0 else 0  # 0 = unlimited on PasarGuard
            base = "".join(c for c in sanitize_client_name(client_name) if c.isalnum() or c == "_")
            base = base[:18].strip("_") or f"u{int(user_id)}"
            username = f"{base}_{secrets.token_hex(4)}"
            resp = await pg_client.create_user(
                username=username,
                group_ids=list(group_ids),
                data_limit_bytes=data_bytes,
                on_hold_duration_seconds=on_hold_seconds,
                note=f"tg:{int(user_id)}",
            )
            created.append(username)
            # Enforce the fair-usage cap: if the panel didn't apply the requested
            # data_limit on create (so an "unlimited" package would otherwise be
            # truly unlimited), correct it with a follow-up modify so the real
            # hidden volume is always applied on PasarGuard.
            if data_bytes > 0 and int((resp or {}).get("data_limit") or 0) != data_bytes:
                try:
                    fixed = await pg_client.modify_user(username, {"data_limit": data_bytes})
                    if isinstance(fixed, dict) and fixed.get("subscription_url"):
                        resp = fixed
                except Exception:
                    LOG.exception("failed to enforce PG data_limit for %s", username)
            sub_url = str((resp or {}).get("subscription_url") or "")
            if not sub_url:
                raise RuntimeError("PasarGuard did not return a subscription_url")
            rows.append(
                PanelClientPayload(
                    user_id=int(user_id),
                    sub_id=username,
                    sub_link=sub_url,
                    inbound_id=PG_INBOUND_SENTINEL,
                    client_uuid="",
                    client_email=username,
                    gb=cap_gb,
                )
            )
            await self.db.insert_subscriptions(rows, order_id=order_id, is_infinite=(kind == "unlimited"))
            approved = await self.db.approve_order(order_id)
            if not approved:
                raise RuntimeError("pg package order approval failed after provisioning")
            await self.db.execute("UPDATE idempotency_keys SET status='approved' WHERE key=?", (idem,))
            return [r.sub_link for r in rows]
        except Exception:
            await self.db.delete_subscriptions([r.sub_id for r in rows])
            for uname in created:
                try:
                    await pg_client.delete_user(uname)
                except Exception:
                    pass
            await self.db.credit_wallet(user_id, final_total)
            await self.db.reject_order(order_id)
            await self.db.execute("UPDATE idempotency_keys SET status='failed' WHERE key=?", (idem,))
            raise
