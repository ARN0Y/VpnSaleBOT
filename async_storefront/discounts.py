"""Discount codes: what a code is, and whether this buyer may use it now.

Everything here is pure — no database, no Telegram. The rules a code carries
(who, when, how many times, on which plans, between which amounts) are checked
in one place so the bot's quote, the invoice the buyer confirms, and the amount
actually charged inside the purchase transaction can never disagree. The money
path re-runs these same checks under the transaction; this module is what both
sides call.

Every rejection carries the Persian sentence the buyer should read, because a
code that silently does nothing is indistinguishable from a broken bot.
"""
from __future__ import annotations

import json
import re
from typing import Any

# ── the shape of a code ────────────────────────────────────────────────────

KIND_PERCENT = "percent"
KIND_FIXED = "fixed"
KINDS = (KIND_PERCENT, KIND_FIXED)

# Who may redeem it.
AUDIENCE_ALL = "all"
AUDIENCE_USERS = "users"      # ordinary customers only, never agents
AUDIENCE_AGENTS = "agents"    # agents only
AUDIENCE_NEW = "new"          # nobody who already has an approved order
AUDIENCES = (AUDIENCE_ALL, AUDIENCE_USERS, AUDIENCE_AGENTS, AUDIENCE_NEW)

# What kind of order it may be spent on.
APPLIES_ALL = "all"
APPLIES_PURCHASE = "purchase"
APPLIES_RENEWAL = "renewal"
APPLIES = (APPLIES_ALL, APPLIES_PURCHASE, APPLIES_RENEWAL)

CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_\-]{1,31}$")
MAX_CODE_LEN = 32


class DiscountError(ValueError):
    """A code that cannot be used, with the reason the buyer should see."""


def normalize_code(raw: Any) -> str:
    """Codes are matched case-insensitively; storage is upper-case.

    Persian keyboards produce Arabic-Indic digits and a zero-width joiner that
    look identical to what the admin typed, so those are folded first — a buyer
    who copies the code out of a channel post must not be told it is wrong.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    fold = {ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")}
    fold.update({ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")})
    fold.update({0x200C: None, 0x200F: None, 0x200E: None, 0xFEFF: None})
    text = text.translate(fold)
    return re.sub(r"\s+", "", text).upper()[:MAX_CODE_LEN]


def _int(value: Any, default: int = 0, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        out = int(float(str(value).strip()))
    except (TypeError, ValueError):
        out = int(default)
    if minimum is not None:
        out = max(minimum, out)
    if maximum is not None:
        out = min(maximum, out)
    return out


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "on", "yes"}


def _str(value: Any, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text


def _id_list(value: Any, *, numeric: bool = False, limit: int = 500) -> list:
    """A JSON list, a comma/newline separated string, or nothing."""
    items: list = []
    if isinstance(value, (list, tuple)):
        items = list(value)
    elif isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                items = list(parsed) if isinstance(parsed, list) else []
            except Exception:
                items = []
        elif text:
            items = re.split(r"[,\n\r\t ]+", text)
    out: list = []
    for item in items:
        if numeric:
            n = _int(item, 0, minimum=0)
            if n > 0 and n not in out:
                out.append(n)
        else:
            s = _str(item)
            if s and s not in out:
                out.append(s)
        if len(out) >= limit:
            break
    return out


def normalize(raw: dict) -> dict:
    """A stored/edited code, with every field forced into range.

    Normalising on the way in means the bot never has to defend against a
    percent of 900 or an end date before the start date.
    """
    raw = raw if isinstance(raw, dict) else {}
    kind = _str(raw.get("kind"), KIND_PERCENT).lower()
    if kind not in KINDS:
        kind = KIND_PERCENT
    audience = _str(raw.get("audience"), AUDIENCE_ALL).lower()
    if audience not in AUDIENCES:
        audience = AUDIENCE_ALL
    applies_to = _str(raw.get("applies_to"), APPLIES_ALL).lower()
    if applies_to not in APPLIES:
        applies_to = APPLIES_ALL

    value = (
        _int(raw.get("value"), 0, minimum=0, maximum=100)
        if kind == KIND_PERCENT
        else _int(raw.get("value"), 0, minimum=0)
    )
    starts_at = _int(raw.get("starts_at"), 0, minimum=0)
    ends_at = _int(raw.get("ends_at"), 0, minimum=0)
    min_order = _int(raw.get("min_order_toman"), 0, minimum=0)
    max_order = _int(raw.get("max_order_toman"), 0, minimum=0)

    return {
        "code": normalize_code(raw.get("code")),
        "title": _str(raw.get("title"))[:80],
        "kind": kind,
        "value": value,
        # A percentage without a ceiling is how a 20% code costs you a million
        # toman on one large order. 0 means the admin accepted that.
        "max_discount_toman": _int(raw.get("max_discount_toman"), 0, minimum=0),
        "min_order_toman": min_order,
        "max_order_toman": max_order,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "max_uses": _int(raw.get("max_uses"), 0, minimum=0),
        "max_uses_per_user": _int(raw.get("max_uses_per_user"), 1, minimum=0),
        "audience": audience,
        "applies_to": applies_to,
        "user_ids": _id_list(raw.get("user_ids"), numeric=True),
        "plan_ids": _id_list(raw.get("plan_ids")),
        "category_ids": _id_list(raw.get("category_ids")),
        "enabled": _bool(raw.get("enabled"), True),
        "note": _str(raw.get("note"))[:500],
    }


def validate(code: dict) -> list[str]:
    """Problems that would make a saved code useless or dangerous. Empty = fine."""
    problems: list[str] = []
    if not code.get("code"):
        problems.append("کد تخفیف خالی است.")
    elif not CODE_RE.match(str(code["code"])):
        problems.append("کد فقط می‌تواند شامل حروف انگلیسی، عدد، خط تیره و زیرخط باشد (۲ تا ۳۲ کاراکتر).")
    if code.get("kind") == KIND_PERCENT:
        if not (1 <= int(code.get("value") or 0) <= 100):
            problems.append("درصد تخفیف باید بین ۱ تا ۱۰۰ باشد.")
    else:
        if int(code.get("value") or 0) <= 0:
            problems.append("مبلغ تخفیف باید بزرگ‌تر از صفر باشد.")
    starts, ends = int(code.get("starts_at") or 0), int(code.get("ends_at") or 0)
    if starts and ends and ends <= starts:
        problems.append("تاریخ پایان باید بعد از تاریخ شروع باشد.")
    lo, hi = int(code.get("min_order_toman") or 0), int(code.get("max_order_toman") or 0)
    if lo and hi and hi < lo:
        problems.append("حداکثر مبلغ سفارش نمی‌تواند کمتر از حداقل آن باشد.")
    if code.get("kind") == KIND_FIXED and lo and int(code["value"]) > lo:
        # Not fatal — the discount is capped at the order total anyway — but it
        # means the advertised amount is never actually given in full.
        problems.append("مبلغ تخفیف از حداقل مبلغ سفارش بیشتر است؛ تخفیف کامل اعمال نمی‌شود.")
    if code.get("audience") == AUDIENCE_AGENTS and code.get("user_ids"):
        problems.append("هم «فقط نماینده‌ها» انتخاب شده و هم فهرست کاربران مشخص است؛ یکی را انتخاب کنید.")
    return problems


def amount_for(code: dict, base_total: int) -> int:
    """How much this code takes off ``base_total`` — never more than the total."""
    base = max(0, int(base_total))
    if base <= 0:
        return 0
    if str(code.get("kind")) == KIND_PERCENT:
        percent = _int(code.get("value"), 0, minimum=0, maximum=100)
        # Integer floor: the shop rounds in its own favour by at most one toman,
        # and the buyer is never charged a fraction.
        raw = (base * percent) // 100
    else:
        raw = _int(code.get("value"), 0, minimum=0)
    cap = _int(code.get("max_discount_toman"), 0, minimum=0)
    if cap > 0:
        raw = min(raw, cap)
    return max(0, min(raw, base))


def window_label(code: dict) -> str:
    starts, ends = int(code.get("starts_at") or 0), int(code.get("ends_at") or 0)
    if not starts and not ends:
        return "بدون محدودیت زمانی"
    if starts and ends:
        return "بازه‌ی مشخص"
    return "تا تاریخ مشخص" if ends else "از تاریخ مشخص"


def summary(code: dict) -> str:
    """One line describing the code, for the admin list and the bot."""
    if str(code.get("kind")) == KIND_PERCENT:
        head = f"{int(code.get('value') or 0)}٪"
        cap = int(code.get("max_discount_toman") or 0)
        if cap > 0:
            head += f" (حداکثر {cap:,} تومان)"
    else:
        head = f"{int(code.get('value') or 0):,} تومان"
    return head


def check(
    code: dict,
    *,
    now: int,
    base_total: int,
    user_id: int,
    is_agent: bool,
    order_kind: str,
    plan_id: str = "",
    category_id: str = "",
    used_by_user: int = 0,
    has_approved_order: bool = False,
) -> int:
    """Raise ``DiscountError`` unless this buyer may use this code right now.

    Returns the discount amount when everything passes. The caller must have
    already loaded ``used_by_user`` (this buyer's redemptions of this code) and
    the code's own ``used_count``; both are re-read inside the purchase
    transaction, because the gap between quoting and confirming is exactly where
    a shared code gets spent past its limit.
    """
    if not code:
        raise DiscountError("کد تخفیف پیدا نشد.")
    if not _bool(code.get("enabled"), True):
        raise DiscountError("این کد تخفیف غیرفعال است.")

    starts = int(code.get("starts_at") or 0)
    ends = int(code.get("ends_at") or 0)
    if starts and now < starts:
        raise DiscountError("این کد هنوز فعال نشده است.")
    if ends and now >= ends:
        raise DiscountError("مهلت استفاده از این کد تمام شده است.")

    max_uses = int(code.get("max_uses") or 0)
    if max_uses and int(code.get("used_count") or 0) >= max_uses:
        raise DiscountError("ظرفیت استفاده از این کد تکمیل شده است.")

    per_user = int(code.get("max_uses_per_user") or 0)
    if per_user and int(used_by_user or 0) >= per_user:
        raise DiscountError(
            "شما قبلاً از این کد استفاده کرده‌اید."
            if per_user == 1
            else f"سقف استفاده‌ی شما از این کد ({per_user} بار) پر شده است."
        )

    audience = str(code.get("audience") or AUDIENCE_ALL)
    if audience == AUDIENCE_USERS and is_agent:
        raise DiscountError("این کد برای نمایندگان قابل استفاده نیست.")
    if audience == AUDIENCE_AGENTS and not is_agent:
        raise DiscountError("این کد فقط برای نمایندگان است.")
    if audience == AUDIENCE_NEW and has_approved_order:
        raise DiscountError("این کد فقط برای اولین خرید است.")

    allowed_users = code.get("user_ids") or []
    if allowed_users and int(user_id) not in [int(u) for u in allowed_users]:
        raise DiscountError("این کد برای حساب شما فعال نیست.")

    applies_to = str(code.get("applies_to") or APPLIES_ALL)
    kind = str(order_kind or APPLIES_PURCHASE)
    if applies_to == APPLIES_PURCHASE and kind != APPLIES_PURCHASE:
        raise DiscountError("این کد فقط برای خرید سرویس جدید است.")
    if applies_to == APPLIES_RENEWAL and kind != APPLIES_RENEWAL:
        raise DiscountError("این کد فقط برای تمدید سرویس است.")

    plans = code.get("plan_ids") or []
    if plans and str(plan_id or "") not in [str(p) for p in plans]:
        raise DiscountError("این کد برای پلن انتخابی معتبر نیست.")
    categories = code.get("category_ids") or []
    if categories and str(category_id or "") not in [str(c) for c in categories]:
        raise DiscountError("این کد برای این دسته از سرویس‌ها معتبر نیست.")

    base = max(0, int(base_total))
    low = int(code.get("min_order_toman") or 0)
    high = int(code.get("max_order_toman") or 0)
    if low and base < low:
        raise DiscountError(f"این کد برای سفارش‌های بالای {low:,} تومان است.")
    if high and base > high:
        raise DiscountError(f"این کد برای سفارش‌های تا {high:,} تومان است.")

    amount = amount_for(code, base)
    if amount <= 0:
        raise DiscountError("این کد روی مبلغ فعلی تخفیفی ندارد.")
    return amount
