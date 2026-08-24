"""Sales catalog: categories, plans, and how a plan is priced.

Replaces the old arrangement where a "package" list was buried inside each
panel's technical settings (``panel_packages`` / ``panel2_packages`` /
``pg_packages``) and every PasarGuard sale went to one global group. That made
two things impossible: selling the same panel through different groups, and
reasoning about the price list without also thinking about panel plumbing.

The split here is deliberate:

  * a **panel** is infrastructure — a URL, credentials, an inbound. It has no
    prices and no titles a buyer ever sees.
  * a **plan** is a thing you sell — title, what the buyer sees, what actually
    gets provisioned, and what it costs. It *points at* a target (a PasarGuard
    group or a 3x-ui panel), so two plans can sell the same panel through
    different groups.
  * a **category** groups plans in the bot menu (buyer picks a category, then a
    plan inside it).

Two decouplings carry the business rules:

  display vs quota   What the buyer is told ("نامحدود") is separate from what is
                     actually provisioned (an 85 GB fair-usage cap). Setting a
                     display label never changes what the panel enforces, and
                     changing the cap never changes the advertised label.

  price vs quota     Pricing is a rule, not a number, so a plan can be a flat
                     price, a base plus per-GB, or tiered per-GB brackets —
                     each with its own agent variant.

Stored as JSON in the ``catalog`` settings key. Nothing here touches the DB
schema: the .db is carried between deployments and must stay untouched.
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Any

CATALOG_SETTING = "catalog"
CATALOG_VERSION = 1

# Elsa never had package lists; its first catalog is built from the per-GB shop
# settings instead (see migrate_elsa_pricing).

TARGET_PASARGUARD = "pasarguard"
TARGET_XUI = "xui"
TARGET_ATHENA = "athena"
TARGET_KINDS = (TARGET_PASARGUARD, TARGET_XUI, TARGET_ATHENA)

PRICING_FIXED = "fixed"          # one price for the plan
PRICING_LINEAR = "linear"        # base + gb * per_gb
PRICING_TIERED = "tiered"        # per-GB rate from the bracket the gb falls in
PRICING_MODES = (PRICING_FIXED, PRICING_LINEAR, PRICING_TIERED)

VOLUME_FIXED = "fixed"           # the plan sells a set number of GB
VOLUME_VARIABLE = "variable"     # the buyer chooses how many GB
VOLUME_MODES = (VOLUME_FIXED, VOLUME_VARIABLE)


def _int(value: Any, default: int = 0, minimum: int | None = None) -> int:
    try:
        out = int(float(str(value).strip()))
    except (TypeError, ValueError):
        out = int(default)
    if minimum is not None:
        out = max(minimum, out)
    return out


def _str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "on", "yes"}


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def _slug(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().lower()).strip("_")
    return cleaned[:24] or fallback


# ───────────────────────────── pricing ─────────────────────────────

def parse_pricing(raw: Any) -> dict:
    """Normalise a pricing rule. Unknown/broken input degrades to a fixed 0,
    which `validate_plan` then reports rather than silently selling for free."""
    data = raw if isinstance(raw, dict) else {}
    mode = _str(data.get("mode"), PRICING_FIXED).lower()
    if mode not in PRICING_MODES:
        mode = PRICING_FIXED
    tiers: list[dict] = []
    for item in data.get("tiers") or []:
        if not isinstance(item, dict):
            continue
        tiers.append({
            "min_gb": _int(item.get("min_gb"), 0, minimum=0),
            "price_per_gb": _int(item.get("price_per_gb"), 0, minimum=0),
            "agent_price_per_gb": _int(item.get("agent_price_per_gb"), 0, minimum=0),
        })
    # Brackets are matched by "largest min_gb that is <= gb", so keep them sorted.
    tiers.sort(key=lambda t: t["min_gb"])
    return {
        "mode": mode,
        "price": _int(data.get("price"), 0, minimum=0),
        "agent_price": _int(data.get("agent_price"), 0, minimum=0),
        "base": _int(data.get("base"), 0, minimum=0),
        "agent_base": _int(data.get("agent_base"), 0, minimum=0),
        "per_gb": _int(data.get("per_gb"), 0, minimum=0),
        "agent_per_gb": _int(data.get("agent_per_gb"), 0, minimum=0),
        "tiers": tiers,
        # Rounding keeps computed prices tidy (e.g. to the nearest 1000 Toman).
        "round_to": _int(data.get("round_to"), 0, minimum=0),
    }


def _tier_rate(tiers: list[dict], gb: int, *, agent: bool) -> int:
    """Rate from the highest bracket whose min_gb is still <= gb."""
    rate = 0
    for tier in tiers:
        if gb >= int(tier.get("min_gb") or 0):
            candidate = int(tier.get("agent_price_per_gb") or 0) if agent else 0
            if not agent or candidate <= 0:
                candidate = int(tier.get("price_per_gb") or 0)
            rate = candidate
        else:
            break
    return max(0, rate)


def price_for(
    plan: dict,
    *,
    gb: int | None = None,
    is_agent: bool = False,
    agent_unit_price: int = 0,
) -> int:
    """Price of ``plan`` for this audience, at ``gb`` when the buyer picks volume.

    Agent values fall back to the user value when unset (0), so an admin only
    fills in the agent side where it actually differs.

    ``agent_unit_price`` is ElsaVPN's per-agent rate (``agents.price_per_gb``).
    Elsa has always priced an agent's volume purchases from that column rather
    than from a shop-wide agent price, and every agent on the panel has one, so
    it keeps winning for per-GB plans. It deliberately does NOT apply to a fixed
    price: a package costs what the package costs, which is also what elsa did
    with its infinite package before plans existed.
    """
    pricing = plan.get("pricing") or {}
    mode = _str(pricing.get("mode"), PRICING_FIXED)
    volume = plan.get("volume") or {}
    effective_gb = _int(gb if gb is not None else volume.get("gb"), 0, minimum=0)
    own_rate = _int(agent_unit_price, 0, minimum=0)

    if mode == PRICING_FIXED:
        total = _int(pricing.get("price"), 0, minimum=0)
        if is_agent:
            agent_price = _int(pricing.get("agent_price"), 0, minimum=0)
            if agent_price > 0:
                total = agent_price
    elif mode == PRICING_LINEAR:
        base = _int(pricing.get("base"), 0, minimum=0)
        per_gb = _int(pricing.get("per_gb"), 0, minimum=0)
        if is_agent:
            agent_base = _int(pricing.get("agent_base"), 0, minimum=0)
            agent_per_gb = _int(pricing.get("agent_per_gb"), 0, minimum=0)
            if agent_base > 0:
                base = agent_base
            if agent_per_gb > 0:
                per_gb = agent_per_gb
            # This agent's own contracted rate beats the plan's agent rate.
            if own_rate > 0:
                per_gb = own_rate
        total = base + per_gb * effective_gb
    else:  # tiered
        rate = _tier_rate(list(pricing.get("tiers") or []), effective_gb, agent=is_agent)
        base = _int(pricing.get("base"), 0, minimum=0)
        if is_agent:
            agent_base = _int(pricing.get("agent_base"), 0, minimum=0)
            if agent_base > 0:
                base = agent_base
            if own_rate > 0:
                rate = own_rate
        total = base + rate * effective_gb

    round_to = _int(pricing.get("round_to"), 0, minimum=0)
    if round_to > 1 and total > 0:
        # Half-UP, not Python's round() — round() is banker's rounding, so a
        # total landing exactly on .5 of a step rounded to even and disagreed
        # with the panel's preview (JS Math.round). Money must not depend on
        # which language computed it.
        total = ((int(total) + round_to // 2) // round_to) * round_to
    return max(0, int(total))


# ───────────────────────────── plans ─────────────────────────────

def parse_target(raw: Any) -> dict:
    """Where a plan is provisioned. Every plan carries its own, so two plans can
    sell from two different panels — or two groups of the same panel."""
    data = raw if isinstance(raw, dict) else {}
    kind = _str(data.get("kind"), TARGET_PASARGUARD).lower()
    if kind not in TARGET_KINDS:
        kind = TARGET_PASARGUARD
    if kind == TARGET_PASARGUARD:
        return {"kind": kind, "group": _str(data.get("group"))}
    if kind == TARGET_ATHENA:
        # node_id 0 means "the panel's default node"; outbound "" means the
        # panel's own default egress. Both are optional on purpose.
        return {
            "kind": kind,
            "node_id": _int(data.get("node_id"), 0, minimum=0),
            "outbound": _str(data.get("outbound")),
        }
    panel = _str(data.get("panel"), "1")
    return {"kind": kind, "panel": panel if panel in ("1", "2") else "1"}


def parse_plan(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    title = _str(raw.get("title"))
    if not title:
        return None
    volume = raw.get("volume") if isinstance(raw.get("volume"), dict) else {}
    vmode = _str(volume.get("mode"), VOLUME_FIXED).lower()
    if vmode not in VOLUME_MODES:
        vmode = VOLUME_FIXED
    display = raw.get("display") if isinstance(raw.get("display"), dict) else {}
    # Hiding the real quota used to be inferred from "volume_label is not empty",
    # so a purely cosmetic label silently turned a volume plan into an unlimited
    # one (changing order_type, is_infinite and the 3x-ui delivery method). It is
    # explicit now; plans written before this key existed keep their old meaning.
    if "hide_volume" in display:
        hide_volume = _bool(display.get("hide_volume"), False)
    else:
        hide_volume = bool(_str(display.get("volume_label")))
    return {
        "id": _str(raw.get("id")) or new_id("plan"),
        "category_id": _str(raw.get("category_id")),
        "title": title,
        "enabled": _bool(raw.get("enabled"), True),
        "sort": _int(raw.get("sort"), 0),
        "target": parse_target(raw.get("target")),
        "volume": {
            "mode": vmode,
            # gb 0 with mode=fixed means genuinely unlimited on the panel.
            "gb": _int(volume.get("gb"), 0, minimum=0),
            "days": _int(volume.get("days"), 0, minimum=0),
            "min_gb": _int(volume.get("min_gb"), 0, minimum=0),
            "max_gb": _int(volume.get("max_gb"), 0, minimum=0),
            "step_gb": _int(volume.get("step_gb"), 0, minimum=0),
        },
        "display": {
            # volume_label is cosmetic wording; hide_volume is what actually
            # withholds the real quota from the buyer.
            "volume_label": _str(display.get("volume_label")),
            "hide_volume": hide_volume,
            "note": _str(display.get("note")),
            "badge": _str(display.get("badge")),
        },
        "pricing": parse_pricing(raw.get("pricing")),
    }


def parse_category(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    title = _str(raw.get("title"))
    if not title:
        return None
    return {
        "id": _str(raw.get("id")) or new_id("cat"),
        "title": title,
        "emoji": _str(raw.get("emoji")),
        "description": _str(raw.get("description")),
        "enabled": _bool(raw.get("enabled"), True),
        "sort": _int(raw.get("sort"), 0),
    }


def _dedupe_ids(items: list[dict], prefix: str) -> list[dict]:
    """Give every entry a unique id.

    find_plan() resolves by id and returns the first match, so two plans sharing
    an id would both be listed while only the cheaper one could ever be bought.
    Re-id the later duplicates instead of dropping them: the operator keeps the
    plan they created, and the menu can no longer point two buttons at one plan.
    """
    seen: set[str] = set()
    for item in items:
        current = str(item.get("id") or "")
        if not current or current in seen:
            current = new_id(prefix)
            while current in seen:
                current = new_id(prefix)
            item["id"] = current
        seen.add(current)
    return items


def parse_catalog(raw: Any) -> dict:
    """Parse the stored catalog. Always returns a usable structure."""
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else (raw or {})
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    categories = _dedupe_ids([c for c in (parse_category(x) for x in data.get("categories") or []) if c], "cat")
    plans = _dedupe_ids([p for p in (parse_plan(x) for x in data.get("plans") or []) if p], "plan")
    known = {c["id"] for c in categories}
    # A plan pointing at a deleted category would vanish from the menu; park it
    # in the first category instead of losing the sale silently.
    fallback = categories[0]["id"] if categories else ""
    for plan in plans:
        if plan["category_id"] not in known:
            plan["category_id"] = fallback
    categories.sort(key=lambda c: (c["sort"], c["title"]))
    plans.sort(key=lambda p: (p["sort"], p["title"]))
    return {"version": CATALOG_VERSION, "categories": categories, "plans": plans}


def dump_catalog(catalog: dict) -> str:
    return json.dumps(
        {
            "version": CATALOG_VERSION,
            "categories": catalog.get("categories") or [],
            "plans": catalog.get("plans") or [],
        },
        ensure_ascii=False,
    )


def visible_categories(catalog: dict) -> list[dict]:
    """Categories that have at least one sellable plan."""
    plans = [p for p in catalog.get("plans") or [] if p.get("enabled")]
    live = {p["category_id"] for p in plans}
    return [c for c in catalog.get("categories") or [] if c.get("enabled") and c["id"] in live]


def plans_in_category(catalog: dict, category_id: str) -> list[dict]:
    return [
        p for p in catalog.get("plans") or []
        if p.get("enabled") and p.get("category_id") == category_id
    ]


def find_plan(catalog: dict, plan_id: str) -> dict | None:
    for plan in catalog.get("plans") or []:
        if plan.get("id") == plan_id:
            return plan
    return None


def find_category(catalog: dict, category_id: str) -> dict | None:
    for cat in catalog.get("categories") or []:
        if cat.get("id") == category_id:
            return cat
    return None


def category_is_open(catalog: dict, category_id: str) -> bool:
    """True when the category exists and is enabled."""
    cat = find_category(catalog, category_id)
    return bool(cat and cat.get("enabled"))


def plan_is_sellable(catalog: dict, plan: dict | None) -> bool:
    """Whether this plan may be sold right now.

    Disabling a CATEGORY has to stop its plans from selling too. Menus are built
    from visible_categories(), but a buyer holding an older message can still tap
    a stale button, so every step of the purchase path checks this instead of
    only plan.enabled.
    """
    if not plan or not plan.get("enabled"):
        return False
    return category_is_open(catalog, str(plan.get("category_id") or ""))


# ───────────────────────── buyer-facing labels ─────────────────────────

def volume_label(plan: dict, gb: int | None = None) -> str:
    """What the buyer is told about volume — the display override wins.

    This is the only place that decides it, so the bot cannot accidentally leak
    a fair-usage cap that the admin chose to hide.
    """
    display = plan.get("display") or {}
    override = _str(display.get("volume_label"))
    if override:
        return override
    if _bool(display.get("hide_volume"), False):
        return "نامحدود"
    volume = plan.get("volume") or {}
    # A variable plan has no volume until the buyer picks one — saying
    # "نامحدود" there would advertise a per-GB plan as unlimited.
    if gb is None and _str(volume.get("mode"), VOLUME_FIXED) == VOLUME_VARIABLE:
        return "به انتخاب شما"
    effective = _int(gb if gb is not None else volume.get("gb"), 0, minimum=0)
    if effective <= 0:
        return "نامحدود"
    return f"{effective} گیگ"


def duration_label(plan: dict) -> str:
    days = _int((plan.get("volume") or {}).get("days"), 0, minimum=0)
    return f"{days} روز" if days > 0 else "بدون محدودیت زمانی"


def provisioned_gb(plan: dict, gb: int | None = None) -> int:
    """GB actually applied on the panel — never the display label."""
    volume = plan.get("volume") or {}
    if _str(volume.get("mode"), VOLUME_FIXED) == VOLUME_VARIABLE and gb is not None:
        return _int(gb, 0, minimum=0)
    return _int(volume.get("gb"), 0, minimum=0)


def gb_choices(plan: dict, *, limit: int = 12) -> list[int]:
    """Volume options offered for a variable plan."""
    volume = plan.get("volume") or {}
    if _str(volume.get("mode"), VOLUME_FIXED) != VOLUME_VARIABLE:
        return []
    step = _int(volume.get("step_gb"), 0, minimum=0)
    if step <= 0:
        step = _int(volume.get("min_gb"), 1, minimum=1)
    if step <= 0:
        step = 1
    low = _int(volume.get("min_gb"), 0, minimum=0) or step
    high = _int(volume.get("max_gb"), 0, minimum=0)
    out: list[int] = []
    value = low
    while len(out) < limit:
        if high and value > high:
            break
        out.append(value)
        value += step
    return out


def validate_plan(plan: dict) -> list[str]:
    """Problems that would make this plan misbehave if sold. Empty = sellable."""
    problems: list[str] = []
    volume = plan.get("volume") or {}
    pricing = plan.get("pricing") or {}
    mode = _str(pricing.get("mode"), PRICING_FIXED)
    vmode = _str(volume.get("mode"), VOLUME_FIXED)
    target = plan.get("target") or {}

    if target.get("kind") == TARGET_PASARGUARD and not _str(target.get("group")):
        problems.append("گروه پنل PasarGuard انتخاب نشده است.")
    if vmode == VOLUME_VARIABLE:
        if mode == PRICING_FIXED:
            problems.append("در پلن حجم‌متغیر، قیمت ثابت معنا ندارد؛ «پایه + هر گیگ» یا «پلکانی» را انتخاب کنید.")
        if not gb_choices(plan):
            problems.append("بازه‌ی حجم برای پلن حجم‌متغیر تعریف نشده است.")
    if mode == PRICING_FIXED and _int(pricing.get("price"), 0) <= 0:
        problems.append("قیمت پلن صفر است.")
    if mode == PRICING_LINEAR and _int(pricing.get("base"), 0) <= 0 and _int(pricing.get("per_gb"), 0) <= 0:
        problems.append("قیمت پایه و نرخ هر گیگ هر دو صفر هستند.")
    if mode == PRICING_TIERED and not (pricing.get("tiers") or []):
        problems.append("هیچ پله‌ی قیمتی تعریف نشده است.")
    if vmode == VOLUME_FIXED and mode != PRICING_FIXED and _int(volume.get("gb"), 0) <= 0:
        problems.append("برای قیمت‌گذاری بر اساس حجم، حجم پلن نمی‌تواند صفر باشد.")

    # A computed price of zero is not caught by the checks above: a tiered plan
    # whose lowest bracket starts above its smallest offered volume, or a linear
    # plan with everything at zero, would advertise «از ۰ ت» and then fail deep
    # in provisioning with "قیمت این بسته نامعتبر است".
    zero_at: list[str] = []
    if vmode == VOLUME_VARIABLE:
        for choice in gb_choices(plan):
            if price_for(plan, gb=choice, is_agent=False) <= 0 or price_for(plan, gb=choice, is_agent=True) <= 0:
                zero_at.append(str(choice))
    elif price_for(plan, is_agent=False) <= 0 or price_for(plan, is_agent=True) <= 0:
        zero_at.append("")
    if zero_at:
        detail = f" (در حجم‌های {'، '.join(zero_at[:5])} گیگ)" if zero_at[0] else ""
        problems.append(f"قیمت این پلن صفر می‌شود{detail}؛ نرخ یا پله‌ها را کامل کنید.")
    return problems


# ───────────────────────── migration from legacy ─────────────────────────

def migrate_elsa_pricing(
    *,
    price_per_gb: int,
    pg_price_per_gb: int,
    price_tiers: str,
    minimum_purchase_gb: int,
    purchase_duration_days: int,
    infinite_enabled: bool,
    infinite_cap_gb: int,
    infinite_duration_days: int,
    infinite_price: int,
    primary_backend: str,
    pg_group: str,
    pg_label: str,
) -> dict:
    """Build elsa's first catalog from the per-GB shop it had before plans.

    Elsa never had package lists — it sold volume by the gigabyte plus one
    optional "infinite" package. Both become plans so nothing about what is on
    sale, or what it costs, changes on the day of the upgrade:

      • the per-GB shop  → one VARIABLE plan priced per gigabyte, keeping the
        minimum purchase as both the floor and the step (elsa's buttons were
        multiples of the minimum), and the tier table when one was configured;
      • the infinite package → one FIXED plan holding the real cap with the
        volume hidden, exactly how elsa advertised it.

    Per-agent rates are not baked in here: they live on ``agents.price_per_gb``
    and are applied at purchase time, so all five agents keep their own price.
    """
    on_pg = str(primary_backend or "").strip().lower() == TARGET_PASARGUARD
    target = (
        {"kind": TARGET_PASARGUARD, "group": pg_group}
        if on_pg
        else {"kind": TARGET_XUI, "panel": "1"}
    )
    cat_id = "cat_shop"
    title = (pg_label.strip() if on_pg and pg_label.strip() else "") or "خرید سرویس"
    categories = [{
        "id": cat_id,
        "title": title,
        "emoji": "🌐",
        "description": "",
        "enabled": True,
        "sort": 0,
    }]

    # The rate elsa actually charged: PasarGuard's own per-GB price overrode the
    # shop price whenever PasarGuard was the primary backend.
    unit = _int(pg_price_per_gb, 0, minimum=0) if on_pg else 0
    if unit <= 0:
        unit = _int(price_per_gb, 0, minimum=0)

    try:
        tiers_raw = json.loads(price_tiers or "[]")
    except Exception:
        tiers_raw = []
    tiers = []
    for item in tiers_raw if isinstance(tiers_raw, list) else []:
        if not isinstance(item, dict):
            continue
        tiers.append({
            "min_gb": _int(item.get("min_gb"), 0, minimum=0),
            "price_per_gb": _int(item.get("price_per_gb"), 0, minimum=0),
            "agent_price_per_gb": 0,
        })
    tiers = [t for t in tiers if t["price_per_gb"] > 0]

    step = _int(minimum_purchase_gb, 1, minimum=1)
    plans = [{
        "id": "plan_volume",
        "category_id": cat_id,
        "title": "خرید حجمی",
        "enabled": True,
        "sort": 0,
        "target": dict(target),
        "volume": {
            "mode": VOLUME_VARIABLE,
            "gb": 0,
            "days": _int(purchase_duration_days, 0, minimum=0),
            "min_gb": step,
            "max_gb": 0,
            "step_gb": step,
        },
        "display": {"volume_label": "", "hide_volume": False, "note": "", "badge": ""},
        "pricing": (
            {
                "mode": PRICING_TIERED,
                "price": 0, "agent_price": 0, "base": 0, "agent_base": 0,
                "per_gb": 0, "agent_per_gb": 0,
                "tiers": tiers, "round_to": 0,
            }
            if tiers
            else {
                "mode": PRICING_LINEAR,
                "price": 0, "agent_price": 0, "base": 0, "agent_base": 0,
                "per_gb": unit, "agent_per_gb": 0,
                "tiers": [], "round_to": 0,
            }
        ),
    }]

    if infinite_enabled and _int(infinite_price, 0, minimum=0) > 0:
        plans.append({
            "id": "plan_infinite",
            "category_id": cat_id,
            "title": "بسته‌ی بی‌نهایت",
            "enabled": True,
            "sort": 1,
            "target": dict(target),
            "volume": {
                "mode": VOLUME_FIXED,
                # The cap is REAL — it is what gets provisioned. The buyer is
                # only ever told "نامحدود", which is what hide_volume enforces.
                "gb": _int(infinite_cap_gb, 0, minimum=0),
                "days": _int(infinite_duration_days, 0, minimum=0),
                "min_gb": 0, "max_gb": 0, "step_gb": 0,
            },
            "display": {
                "volume_label": "نامحدود",
                "hide_volume": True,
                "note": "",
                "badge": "♾️",
            },
            "pricing": {
                "mode": PRICING_FIXED,
                "price": _int(infinite_price, 0, minimum=0),
                # Elsa charged agents the same price for this package.
                "agent_price": 0,
                "base": 0, "agent_base": 0, "per_gb": 0, "agent_per_gb": 0,
                "tiers": [], "round_to": 0,
            },
        })

    return {"version": CATALOG_VERSION, "categories": categories, "plans": plans}


def legacy_equivalent(plan: dict, gb: int | None = None) -> dict:
    """Shape a plan like an old package dict.

    Lets the existing provisioning code paths keep working unchanged while the
    catalog becomes the source of truth.
    """
    volume = plan.get("volume") or {}
    real_gb = provisioned_gb(plan, gb)
    # "unlimited" means the buyer must not be shown the quota — an explicit
    # choice now, so a cosmetic label cannot flip a volume plan.
    masked = _bool((plan.get("display") or {}).get("hide_volume"), False)
    return {
        "kind": "unlimited" if masked or real_gb <= 0 else "volume",
        "title": plan.get("title") or "",
        "gb": real_gb,
        "days": _int(volume.get("days"), 0, minimum=0),
        "price": price_for(plan, gb=gb, is_agent=False),
        "agent_price": price_for(plan, gb=gb, is_agent=True),
    }


# ───────────────────────── storage helpers ─────────────────────────

async def load_catalog(db) -> dict:
    """Read the catalog, building it from elsa's per-GB shop on first use.

    The migration runs once and is persisted, so an operator upgrading mid-day
    never sees an empty shop: what they already sell is there, priced
    identically, before they open the panel.
    """
    raw = await db.get_setting(CATALOG_SETTING, "")
    if str(raw or "").strip():
        return parse_catalog(raw)

    async def _i(key: str, default: str = "0") -> int:
        return _int(await db.get_setting(key, default), 0, minimum=0)

    async def _s(key: str, default: str = "") -> str:
        return str(await db.get_setting(key, default) or "").strip()

    catalog = migrate_elsa_pricing(
        price_per_gb=await _i("price_per_gb", "0"),
        pg_price_per_gb=await _i("pg_price_per_gb", "0"),
        price_tiers=await _s("price_tiers"),
        minimum_purchase_gb=await _i("minimum_purchase_gb", "1"),
        purchase_duration_days=await _i("purchase_duration_days", "30"),
        infinite_enabled=(await _s("infinite_enabled", "0")) == "1",
        infinite_cap_gb=await _i("infinite_cap_gb", "0"),
        infinite_duration_days=await _i("infinite_duration_days", "0"),
        infinite_price=await _i("infinite_price", "0"),
        primary_backend=await _s("primary_backend", "xui"),
        pg_group=await _s("pg_group"),
        pg_label=await _s("pg_label"),
    )
    # Nothing sellable was configured yet — leave the catalog empty rather than
    # persisting a zero-priced plan the bot would refuse anyway.
    if not any(price_for(p, gb=(gb_choices(p) or [0])[0]) > 0 for p in catalog["plans"]):
        return parse_catalog("")
    catalog = parse_catalog(dump_catalog(catalog))
    await db.set_setting(CATALOG_SETTING, dump_catalog(catalog))
    # The per-GB settings stay untouched as a rollback path.
    await db.set_setting("catalog_migrated_from_packages", "1")
    return catalog


async def save_catalog(db, catalog: dict) -> dict:
    normalised = parse_catalog(dump_catalog(catalog))
    await db.set_setting(CATALOG_SETTING, dump_catalog(normalised))
    return normalised
