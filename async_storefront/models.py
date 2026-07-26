from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

# Subscriptions created on the PasarGuard backend are tagged with this sentinel
# inbound_id so the bot can tell them apart from 3x-ui subs (schema-free marker).
# It lives here — the dependency-free module — so db.py and provisioning.py can
# both use it without an import cycle.
PG_INBOUND_SENTINEL = -100


class AgentAccess(str ,Enum):
    OPEN = "open"
    CLOSED = "closed"


class PaymentMethod(str ,Enum):
    RIAL = "rial"
    WALLET = "wallet"
    AGENT_WALLET = "agent_wallet"
    AGENT_OPEN = "agent_open"
    TEST_RIAL = "test_rial"
    CRYPTO = "crypto"


@dataclass(frozen=True)
class AgentPolicy:
    user_id: int
    access_level: AgentAccess
    price_per_gb: int
    credit_limit_toman: int
    credit_used_toman: int
    daily_gb_limit: int
    monthly_gb_limit: int
    daily_test_limit: int
    permissions: frozenset[str]
    disabled: bool

    @classmethod
    def from_row(cls, row: Any) -> "AgentPolicy":
        import json

        raw_permissions = row["permissions"] if "permissions" in row.keys() else '["buy","test"]'
        try:
            permissions = frozenset(str(x) for x in json.loads(raw_permissions or "[]"))
        except Exception:
            permissions = frozenset({"buy", "test"})

        access = str(row["access_level"] or AgentAccess.CLOSED).lower()
        return cls(
            user_id=int(row["user_id"]),
            access_level=AgentAccess.OPEN if access == AgentAccess.OPEN else AgentAccess.CLOSED,
            price_per_gb=int(row["price_per_gb"] or 0),
            credit_limit_toman=int(row["credit_limit_toman"] or 0),
            credit_used_toman=int(row["credit_used_toman"] or 0),
            daily_gb_limit=int(row["daily_gb_limit"] or 0),
            monthly_gb_limit=int(row["monthly_gb_limit"] or 0),
            daily_test_limit=int(row["daily_test_limit"] or 0) if "daily_test_limit" in row.keys() else 0,
            permissions=permissions,
            disabled=bool(row["disabled"] if "disabled" in row.keys() else 0),
        )


@dataclass(frozen=True)
class PanelClientPayload:
    user_id: int
    sub_id: str
    sub_link: str
    inbound_id: int
    client_uuid: str
    client_email: str
    gb: int


@dataclass(frozen=True)
class SubscriptionDetail:
    sub_id: str
    sub_link: str
    inbound_id: int
    client_uuid: str
    email: str
    enabled: bool
    total_bytes: int
    used_bytes: int
    remaining_bytes: int
    up_bytes: int
    down_bytes: int
    last_online_ms: int
    expiry_time: int
    tg_id: str
    reset: int
    comment: str
    created_at_ms: int
    updated_at_ms: int
