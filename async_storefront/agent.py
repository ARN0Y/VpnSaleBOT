from __future__ import annotations

import json
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from .db import AsyncDatabase
from .models import AgentAccess, AgentPolicy, PaymentMethod
from .util import iran_day_bounds_ts

IRAN_TZ = ZoneInfo("Asia/Tehran")


class AgentService:
    def __init__(self, db: AsyncDatabase):
        self.db = db

    async def get_policy(self, user_id: int) -> AgentPolicy | None:
        row = await self.db.get_agent(user_id)
        return AgentPolicy.from_row(row) if row else None

    async def configure_agent(
        self,
        *,
        user_id: int,
        price_per_gb: int,
        admin_id: int,
        access_level: AgentAccess,
        credit_limit_toman: int = 0,
        daily_gb_limit: int = 0,
        monthly_gb_limit: int = 0,
        daily_test_limit: int = 0,
        permissions: set[str] | None = None,
        disabled: bool = False,
    ) -> None:
        await self.db.upsert_agent(
            user_id=user_id,
            price_per_gb=price_per_gb,
            created_by=admin_id,
            access_level=AgentAccess.CLOSED,
            credit_limit_toman=0,
            daily_gb_limit=daily_gb_limit,
            monthly_gb_limit=monthly_gb_limit,
            daily_test_limit=daily_test_limit,
            permissions=json.dumps(sorted(permissions or {"buy", "test"})),
            disabled=disabled,
        )

    async def authorize_purchase(self, *, user_id: int, gb: int, qty: int, ref_id: str) -> tuple[PaymentMethod, int]:
        policy = await self.get_policy(user_id)
        if not policy:
            raise PermissionError("user is not an agent")
        if policy.disabled or "buy" not in policy.permissions:
            raise PermissionError("agent is disabled or lacks buy permission")

        total_gb = int(gb) * int(qty)
        await self._enforce_volume_limits(policy, total_gb)
        amount = int(gb) * int(qty) * int(policy.price_per_gb)

        if not await self.db.try_debit_wallet(user_id, amount):
            raise PermissionError("agent wallet balance is insufficient")
        return PaymentMethod.AGENT_WALLET, amount

    async def refund_authorization(self, *, user_id: int, amount_toman: int, payment_method: PaymentMethod, ref_id: str) -> None:
        if payment_method in {PaymentMethod.AGENT_WALLET, PaymentMethod.AGENT_OPEN}:
            await self.db.credit_wallet(user_id, amount_toman)
            return

    async def _enforce_volume_limits(self, policy: AgentPolicy, requested_gb: int) -> None:
        if policy.daily_gb_limit <= 0 and policy.monthly_gb_limit <= 0:
            return
        day_start, _ = iran_day_bounds_ts()
        rows = await self.db.fetchall(
            """
            SELECT COALESCE(SUM(gb * qty),0) AS gb_sum
            FROM orders
            WHERE user_id=? AND status='approved' AND created_at>=?
            """,
            (policy.user_id, day_start),
        )
        today_gb = int(rows[0]["gb_sum"] or 0)
        if policy.daily_gb_limit > 0 and today_gb + requested_gb > policy.daily_gb_limit:
            raise PermissionError("agent daily GB limit exceeded")
        if policy.monthly_gb_limit > 0:
            now = datetime.now(IRAN_TZ)
            month_start = int(datetime.combine(now.date().replace(day=1), dt_time.min, tzinfo=IRAN_TZ).timestamp())
            rows = await self.db.fetchall(
                """
                SELECT COALESCE(SUM(gb * qty),0) AS gb_sum
                FROM orders
                WHERE user_id=? AND status='approved' AND created_at>=?
                """,
                (policy.user_id, month_start),
            )
            month_gb = int(rows[0]["gb_sum"] or 0)
            if month_gb + requested_gb > policy.monthly_gb_limit:
                raise PermissionError("agent monthly GB limit exceeded")
