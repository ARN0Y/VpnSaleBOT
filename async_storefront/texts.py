"""Every message the bot sends, in one editable registry.

The operator owns the wording. Each entry below is a default; an override saved
from the panel replaces it, and clearing the override brings the default back.
Nothing here is compiled into a flow, so changing a message never means a
deploy.

Two rules make this safe to hand to a non-programmer:

  * A template that names a placeholder the bot does not provide renders as
    itself rather than raising. A typo in a message must never stop a purchase.
  * The registry declares which placeholders each message may use, so the panel
    can show them, insert them, and warn about ones that will not be filled.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SETTING_PREFIX = "text_"


@dataclass(frozen=True)
class Message:
    key: str
    label: str                       # what the operator sees in the panel
    group: str                       # which section of the panel it belongs to
    default: str
    placeholders: tuple[str, ...] = ()
    note: str = ""
    multiline: bool = True


class _Defaulting(dict):
    """Leaves unknown placeholders exactly as they were written."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


GROUPS: dict[str, str] = {
    "menu": "منو و خوش‌آمد",
    "buy": "خرید سرویس",
    "wallet": "کیف پول",
    "reseller": "نمایندگی",
    "support": "پشتیبانی و اطلاعات",
}

MESSAGES: tuple[Message, ...] = (
    Message(
        key="welcome",
        label="پیام خوش‌آمد (/start)",
        group="menu",
        placeholders=("name", "balance", "support"),
        note="{name} نام کاربر، {balance} موجودی کیف پول، {support} آیدی پشتیبانی.",
        default=(
            "سلام {name} 👋\n"
            "به فروشگاه ما خوش آمدید.\n\n"
            "💎 موجودی کیف پول شما: <b>{balance}</b> تومان\n\n"
            "برای شروع از دکمه‌های پایین استفاده کنید."
        ),
    ),
    Message(
        key="main_menu",
        label="متن منوی اصلی",
        group="menu",
        placeholders=("name", "balance"),
        default=(
            "🏠 <b>منوی اصلی</b>\n\n"
            "💎 موجودی: <b>{balance}</b> تومان\n\n"
            "یکی از گزینه‌های زیر را انتخاب کنید:"
        ),
    ),
    Message(
        key="reseller_hub",
        label="متن بخش نمایندگی",
        group="reseller",
        default=(
            "💎 <b>بخش نمایندگی</b>\n"
            "<code>─────────────────────</code>\n"
            "اگر می‌خواهید خودتان فروشنده باشید، از اینجا شروع کنید:\n\n"
            "🌐 <b>پنل نمایندگی</b> — پنل اختصاصی با قیمت عمده؛ خودتان کاربر "
            "بسازید، حجم بدهید و مدیریت کنید.\n\n"
            "🤝 <b>درخواست نمایندگی</b> — خرید با تعرفه‌ی نمایندگی از همین ربات.\n\n"
            "یکی از گزینه‌های زیر را انتخاب کنید:"
        ),
    ),
    Message(
        key="reseller_packages",
        label="متن فهرست بسته‌های پنل",
        group="reseller",
        placeholders=("balance",),
        default=(
            "🌐 <b>خرید پنل نمایندگی</b>\n"
            "<code>─────────────────────</code>\n"
            "💎 موجودی کیف پول: <b>{balance}</b> تومان\n\n"
            "یک بسته را انتخاب کنید. هزینه از کیف پول شما کسر می‌شود و پنل "
            "بلافاصله ساخته و مشخصات ورود ارسال می‌شود."
        ),
    ),
    Message(
        key="support",
        label="متن تماس با پشتیبانی",
        group="support",
        placeholders=("support_id",),
        default=(
            "🛟 <b>پشتیبانی</b>\n\n"
            "برای هر سوال یا مشکلی می‌توانید با ما در تماس باشید:\n"
            "{support_id}\n\n"
            "پاسخگویی در سریع‌ترین زمان ممکن."
        ),
    ),
    Message(
        key="sales_closed",
        label="پیام بسته بودن فروش",
        group="buy",
        default=(
            "🔒 <b>فروش موقتاً بسته است.</b>\n\n"
            "لطفاً بعداً دوباره تلاش کنید یا با پشتیبانی در تماس باشید."
        ),
    ),
)

BY_KEY: dict[str, Message] = {m.key: m for m in MESSAGES}


def setting_key(key: str) -> str:
    return f"{SETTING_PREFIX}{key}"


def default_for(key: str) -> str:
    message = BY_KEY.get(key)
    return message.default if message else ""


def fill(template: str, **values: object) -> str:
    """Substitute placeholders, leaving unknown ones untouched."""
    try:
        return str(template).format_map(_Defaulting(
            {k: ("" if v is None else v) for k, v in values.items()}
        ))
    except Exception:
        # A stray brace in hand-written text is not worth failing a purchase
        # over; the operator sees their own text instead of an error.
        return str(template)


async def raw(db, key: str) -> str:
    """The operator's override, or "" when they have not written one."""
    return str(await db.get_setting(setting_key(key), "") or "")


async def render(db, key: str, **values: object) -> str:
    override = await raw(db, key)
    return fill(override.strip() or default_for(key), **values)


async def save(db, key: str, value: str) -> None:
    """Store an override. Empty text restores the default."""
    if key not in BY_KEY:
        raise ValueError(f"unknown message: {key}")
    await db.set_setting(setting_key(key), str(value or "").strip())


async def overview(db) -> list[dict]:
    """Every message with its default, its override and its placeholders —
    everything the panel needs to render the editor."""
    out: list[dict] = []
    for message in MESSAGES:
        override = await raw(db, message.key)
        out.append({
            "key": message.key,
            "label": message.label,
            "group": message.group,
            "group_label": GROUPS.get(message.group, message.group),
            "default": message.default,
            "value": override,
            "customised": bool(override.strip()),
            "placeholders": list(message.placeholders),
            "note": message.note,
            "multiline": message.multiline,
        })
    return out


def unknown_placeholders(key: str, template: str) -> list[str]:
    """Placeholders in `template` the bot will not fill for this message."""
    import re

    allowed = set(BY_KEY[key].placeholders) if key in BY_KEY else set()
    found = {m.group(1) for m in re.finditer(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", str(template or ""))}
    return sorted(found - allowed)

# ── the bot's own buttons ──
# The labels on the persistent keyboard. Same idea as the messages: a default,
# an override, and clearing the override brings the default back. The routing
# index is rebuilt from whatever is current, so a renamed button keeps working
# and the old label keeps working too for anyone whose keyboard has not
# refreshed yet.


@dataclass(frozen=True)
class Button:
    action: str
    label: str                       # what the operator sees in the panel
    default: str


BUTTONS: tuple[Button, ...] = (
    Button("buy", "خرید سرویس", "⚡ خرید سرویس پرسرعت"),
    Button("renew", "تمدید سرویس", "🔄 تمدید سرویس"),
    Button("subs", "سرویس‌های من", "📦 سرویس‌های من"),
    Button("account", "حساب کاربری", "🪪 حساب کاربری"),
    Button("wallet", "کیف پول", "💎 کیف پول من"),
    Button("support", "پشتیبانی", "🛟 تماس با پشتیبانی"),
    Button("test_config", "تست رایگان", "🆓 دریافت تست رایگان"),
    Button("agent_request", "بخش نمایندگی", "💎 بخش نمایندگی"),
)
BUTTON_BY_ACTION: dict[str, Button] = {b.action: b for b in BUTTONS}


def button_setting_key(action: str) -> str:
    return f"btn_{action}_label"


async def button_overview(db) -> list[dict]:
    out: list[dict] = []
    for button in BUTTONS:
        override = str(await db.get_setting(button_setting_key(button.action), "") or "").strip()
        out.append({
            "action": button.action,
            "label": button.label,
            "default": button.default,
            "value": override,
            "customised": bool(override),
        })
    return out


async def save_button(db, action: str, label: str) -> None:
    if action not in BUTTON_BY_ACTION:
        raise ValueError(f"unknown button: {action}")
    await db.set_setting(button_setting_key(action), str(label or "").strip())
