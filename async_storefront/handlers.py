from __future__ import annotations

import asyncio
import contextlib
import html
import json
import logging
import re
import secrets
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .config import Settings
from . import catalog
from .db import AsyncDatabase
from .athena import AthenaClient, connection_lines
from .pasarguard import PasarGuardClient, _iso_to_epoch as _pg_iso_to_epoch
from .provisioning import PG_INBOUND_SENTINEL, ProvisioningService
from .qr import QRService
from .util import now_ms, now_ts

try:
    import jdatetime
except Exception:
    jdatetime = None


LOG = logging.getLogger(__name__)

BUY_GB, BUY_CUSTOM_GB, BUY_QTY, BUY_NAME_MODE, BUY_NAME_INPUT, BUY_CONFIRM = range(6)
# Catalog buy flow. Its own range so it can never collide with the older
# per-GB states while both exist.
CAT_SELECT, CAT_QTY, CAT_NAME_MODE, CAT_NAME_INPUT, CAT_CONFIRM = range(80, 85)
CATALOG_MAX_QTY = 20
TOPUP_AMOUNT, TOPUP_CUSTOM_AMOUNT, TOPUP_AMOUNT_CONFIRM, TOPUP_C2C_PHOTO, TOPUP_CRYPTO_TXID = range(10, 15)
AGENT_TEXT, AGENT_CONFIRM = range(30, 32)
RENEW_SELECT, RENEW_SEARCH, RENEW_GB, RENEW_CUSTOM_GB, RENEW_CONFIRM = range(40, 45)

FLOW_PROMPT_KEY = "_flow_prompt_message_id"
HOME_MESSAGE_KEY = "_home_message_id"
FLOW_STATE_KEYS = {"checkout", "topup", "agent_request", "renewal", FLOW_PROMPT_KEY}

BTN_BUY = "❄️ خرید سرویس پرسرعت"
BTN_RENEW = "🧊 تمدید سرویس"
BTN_SUBS = "🔹 سرویس‌های من"
BTN_ACCOUNT = "🪪 حساب کاربری"
BTN_WALLET = "💎 کیف پول من"
BTN_TARIFFS = "🏷 تعرفه‌ها"
BTN_SUPPORT = "🛟 تماس با پشتیبانی"
BTN_TEST_CONFIG = "🆓 دریافت تست رایگان"
BTN_AGENT_REQ = "🤝 درخواست نمایندگی"
BTN_INFINITE = "♾️ بسته‌ی بی‌نهایت"

_ALL_NAV_BTNS = frozenset({BTN_BUY, BTN_RENEW, BTN_SUBS, BTN_ACCOUNT, BTN_WALLET, BTN_TARIFFS, BTN_SUPPORT, BTN_TEST_CONFIG, BTN_AGENT_REQ, BTN_INFINITE})
_nav_filter = filters.Text(list(_ALL_NAV_BTNS))

WELCOME_TEXT = (
    "❄️ <b>به ElsaVPN خوش آمدید</b>\n"
    "<i>اینترنت آزاد، پایدار و پرسرعت — هر لحظه، همه‌جا.</i>\n\n"
    "⚡️ سرعت بالا و اتصال بی‌وقفه\n"
    "🛡 امنیت کامل و حفظ حریم خصوصی\n"
    "🎯 تحویل آنی سرویس و پشتیبانی واقعی\n\n"
    "🛟 آیدی پشتیبانی: {support}\n\n"
    "برای شروع، یکی از گزینه‌های زیر را انتخاب کنید 👇"
)


async def render_welcome(db: AsyncDatabase) -> str:
    """Welcome text with the support id injected from settings (single source
    of truth — keeps the welcome's support handle in sync with the support
    section and the admin-configured value)."""
    support = (await db.get_setting("support_id", "") or "").strip()
    return WELCOME_TEXT.replace("{support}", html.escape(support) if support else "—")

BAN_TEXT = (
    "⛔️ <b>دسترسی شما به ربات محدود شده است.</b>\n\n"
    "اگر فکر می‌کنید این محدودیت اشتباهی ثبت شده، لطفاً با پشتیبانی تماس بگیرید."
)

AGENT_REQUEST_TEXT = (
    "🤝 <b>درخواست نمایندگی</b>\n\n"
    "برای بررسی درخواست، لطفاً در یک پیام کامل خودتان را معرفی کنید:\n\n"
    "• سابقه فروش و تجربه قبلی\n"
    "• حجم خرید ماهانه تقریبی\n"
    "• نوع همکاری مدنظر\n"
    "• توضیحات تکمیلی\n\n"
    "می‌توانید حداکثر یک عکس همراه کپشن ارسال کنید. آلبوم یا چند عکس همزمان پذیرفته نمی‌شود."
)

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{2,32}$")
GB_BUTTON_FACTORS = (1, 2, 3, 4)
DEFAULT_CUSTOM_MAX_GB = 100


def _positive_int(value, default: int = 1) -> int:
    try:
        return max(1, int(str(value).strip()))
    except Exception:
        return max(1, int(default))


async def minimum_purchase_gb(db: AsyncDatabase) -> int:
    return _positive_int(await db.get_setting("minimum_purchase_gb", "1"), 1)


def max_custom_gb(min_gb: int) -> int:
    return max(DEFAULT_CUSTOM_MAX_GB, int(min_gb) * max(GB_BUTTON_FACTORS))


async def gb_choice_keyboard(db: AsyncDatabase, user_id: int, prefix: str, cancel_data: str, min_gb: int = 1) -> InlineKeyboardMarkup:
    minimum = _positive_int(min_gb, 1)
    agent = await db.get_agent(user_id)
    buttons = []
    for factor in GB_BUTTON_FACTORS:
        gb = minimum * factor
        unit = await unit_price_for_gb(db, user_id, gb, agent)
        buttons.append(
            InlineKeyboardButton(f"{gb} گیگ • {gb * unit:,} ت", callback_data=f"{prefix}:gb:{gb}")
        )
    return InlineKeyboardMarkup(
        [
            buttons[:2],
            buttons[2:],
            [InlineKeyboardButton("✍️ حجم دلخواه", callback_data=f"{prefix}:gb:custom")],
            [InlineKeyboardButton("❌ انصراف", callback_data=cancel_data)],
        ]
    )


def gb_choice_prompt(title: str, min_gb: int) -> str:
    return (
        f"{title}\n\n"
        f"📦 حداقل حجم مجاز: <b>{min_gb}</b> گیگ\n"
        "یکی از حجم‌های پیشنهادی را انتخاب کنید یا برای عدد دقیق‌تر <b>حجم دلخواه</b> را بزنید."
    )


def custom_gb_prompt(title: str, min_gb: int) -> str:
    return (
        f"✍️ <b>{title}</b>\n\n"
        f"عدد حجم را از <code>{min_gb}</code> گیگ به بالا ارسال کنید.\n"
        f"بازه مجاز: <code>{min_gb}</code> تا <code>{max_custom_gb(min_gb)}</code> گیگ\n"
        "<i>مثال: 15</i>"
    )


def invalid_gb_text(min_gb: int, *, renewal: bool = False) -> str:
    action = "تمدید" if renewal else "خرید"
    return (
        "⚠️ <b>حجم واردشده معتبر نیست.</b>\n\n"
        f"حداقل حجم مجاز برای {action}: <b>{min_gb}</b> گیگ است.\n"
        f"لطفاً یک عدد صحیح بین <code>{min_gb}</code> تا <code>{max_custom_gb(min_gb)}</code> گیگ ارسال کنید."
    )


async def infinite_enabled(db: AsyncDatabase) -> bool:
    return str(await db.get_setting("infinite_enabled", "0") or "0").strip().lower() in {"1", "true", "on", "yes"}


# ───────────────────────── PasarGuard backend ─────────────────────────
async def pg_configured(db: AsyncDatabase) -> bool:
    enabled = str(await db.get_setting("pg_enabled", "0") or "0").strip().lower() in {"1", "true", "on", "yes"}
    if not enabled:
        return False
    return bool((await db.get_setting("pg_base_url", "")).strip()) and bool((await db.get_setting("pg_username", "")).strip())


async def pg_is_primary(db: AsyncDatabase) -> bool:
    """True when the main 'buy' flow should sell from PasarGuard instead of 3x-ui."""
    backend = str(await db.get_setting("primary_backend", "xui") or "xui").strip().lower()
    return backend == "pasarguard" and await pg_configured(db)


async def pg_unit_price_override(db: AsyncDatabase) -> int:
    """When PasarGuard is the primary backend, its own per-GB price
    (``pg_price_per_gb``) overrides the shop's flat/tiered price for regular
    users. ``0`` means "use ElsaVPN's normal pricing". Agents with their own flat
    rate are unaffected (that check runs before this one)."""
    if not await pg_is_primary(db):
        return 0
    try:
        return max(0, int(float(await db.get_setting("pg_price_per_gb", "0") or "0")))
    except (TypeError, ValueError):
        return 0


async def pg_purchase_days(db: AsyncDatabase) -> int:
    """Validity window (days) for a PasarGuard purchase/renewal.

    ``pg_default_days`` overrides it when set; empty/0 falls back to the shop's
    own ``purchase_duration_days`` so PasarGuard services expire on exactly the
    same schedule as 3x-ui ones (ElsaVPN's 30-day rule) with one setting to
    change instead of two."""
    raw = str(await db.get_setting("pg_default_days", "") or "").strip()
    if raw:
        try:
            return max(0, int(float(raw)))
        except (TypeError, ValueError):
            pass
    try:
        return max(0, int(float(await db.get_setting("purchase_duration_days", "30") or "30")))
    except (TypeError, ValueError):
        return 30


async def get_pg_client(context: ContextTypes.DEFAULT_TYPE):
    """Build (and cache) a PasarGuardClient from settings; rebuild it when the
    panel address/username/TLS changes so admin edits apply without a restart."""
    app = context.application
    db: AsyncDatabase = app.bot_data["db"]
    if not await pg_configured(db):
        return None
    base = (await db.get_setting("pg_base_url", "")).strip().rstrip("/")
    user = (await db.get_setting("pg_username", "")).strip()
    pwd = (await db.get_setting("pg_password", "")).strip()
    verify = str(await db.get_setting("pg_verify_tls", "1") or "1").strip().lower() not in {"0", "false", "off", "no"}
    key = f"{base}|{user}|{int(verify)}"
    client = app.bot_data.get("pg_client")
    if client is not None and app.bot_data.get("pg_client_key") == key:
        return client
    if client is not None:
        with contextlib.suppress(Exception):
            await client.close()
    try:
        client = PasarGuardClient(base_url=base, username=user, password=pwd, verify_tls=verify)
    except Exception:
        LOG.exception("failed to build PasarGuard client")
        return None
    app.bot_data["pg_client"] = client
    app.bot_data["pg_client_key"] = key
    return client


async def athena_configured(db: AsyncDatabase) -> bool:
    enabled = str(await db.get_setting("athena_enabled", "0") or "0").strip().lower() in {"1", "true", "on", "yes"}
    if not enabled:
        return False
    return bool((await db.get_setting("athena_base_url", "")).strip()) and bool(
        (await db.get_setting("athena_api_key", "")).strip()
    )


async def get_athena_client(context: ContextTypes.DEFAULT_TYPE):
    """Build (and cache) an AthenaClient from settings; rebuild it when the
    address, key or TLS setting changes so admin edits apply without a restart."""
    app = context.application
    db: AsyncDatabase = app.bot_data["db"]
    if not await athena_configured(db):
        return None
    base = (await db.get_setting("athena_base_url", "")).strip()
    api_key = (await db.get_setting("athena_api_key", "")).strip()
    verify = str(await db.get_setting("athena_verify_tls", "1") or "1").strip().lower() not in {"0", "false", "off", "no"}
    key = f"{base}|{api_key[:16]}|{int(verify)}"
    client = app.bot_data.get("athena_client")
    if client is not None and app.bot_data.get("athena_client_key") == key:
        return client
    if client is not None:
        with contextlib.suppress(Exception):
            await client.close()
    try:
        client = AthenaClient(base_url=base, api_key=api_key, verify_tls=verify)
    except Exception:
        LOG.exception("failed to build Athena client")
        return None
    app.bot_data["athena_client"] = client
    app.bot_data["athena_client_key"] = key
    return client


async def pg_group_ids(db: AsyncDatabase, pg_client) -> list[int]:
    """Resolve the configured PasarGuard group name to its panel id(s)."""
    group = str(await db.get_setting("pg_group", "") or "").strip()
    if not group:
        return []
    return await pg_client.resolve_group_ids([group])


def is_pg_subscription(sub: dict) -> bool:
    return int(sub.get("inbound_id") or 0) == PG_INBOUND_SENTINEL


async def test_config_available(user_id: int, db: AsyncDatabase, provisioning=None) -> bool:
    """True when this user can still take a free test config.

    Regular users get one for life, agents with the "test" permission get a daily
    quota — so the button is hidden once the allowance is used up rather than
    offered and then refused.
    """
    if provisioning is None:
        return False
    try:
        quota = await provisioning.test_config_quota(user_id)
    except Exception:
        LOG.debug("test_config_quota failed user_id=%s", user_id, exc_info=True)
        return False
    return bool(quota["enabled"] and quota["remaining"] > 0)


async def main_menu_keyboard(user_id: int, db: AsyncDatabase, provisioning=None) -> InlineKeyboardMarkup:
    agent = await db.get_agent(user_id)
    has_test = await test_config_available(user_id, db, provisioning)
    rows = [
        [InlineKeyboardButton("❄️ خرید سرویس پرسرعت", callback_data="menu:buy")],
        [
            InlineKeyboardButton("🔹 سرویس‌های من", callback_data="menu:subs"),
            InlineKeyboardButton("🧊 تمدید سرویس", callback_data="menu:renew"),
        ],
        [
            InlineKeyboardButton("💎 کیف پول من", callback_data="menu:wallet"),
            InlineKeyboardButton("🪪 حساب کاربری", callback_data="menu:account"),
        ],
    ]
    if await infinite_enabled(db):
        rows.insert(1, [InlineKeyboardButton("♾️ بسته‌ی بی‌نهایت", callback_data="menu:infinite")])
    last_row: list[InlineKeyboardButton] = []
    if has_test:
        last_row.append(InlineKeyboardButton("🆓 دریافت تست رایگان", callback_data="menu:test_config"))
    if not (agent and not int(agent["disabled"] or 0)):
        last_row.append(InlineKeyboardButton("🤝 درخواست نمایندگی", callback_data="menu:agent_request"))
    last_row.append(InlineKeyboardButton("🏷 تعرفه‌ها", callback_data="menu:tariffs"))
    # Telegram renders at most 3 buttons per row comfortably in RTL; split if needed.
    for chunk in (last_row[:2], last_row[2:]):
        if chunk:
            rows.append(chunk)
    rows.append([InlineKeyboardButton("🛟 تماس با پشتیبانی", callback_data="menu:support")])
    return InlineKeyboardMarkup(rows)


def back_keyboard(label: str = "🏠 بازگشت به منو") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="menu:main")]])


def wallet_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💳 کارت به کارت", callback_data="wallet:c2c")],
            [InlineKeyboardButton("🪙 تتر (USDT)", callback_data="wallet:crypto")],
            [InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")],
        ]
    )


def config_name_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎲 نام رندوم", callback_data="buy:name:random")],
            [InlineKeyboardButton("✍️ نام دلخواه", callback_data="buy:name:custom")],
            [InlineKeyboardButton("❌ انصراف", callback_data="buy:cancel")],
        ]
    )


def buy_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ تایید و خرید", callback_data="buy:confirm"), InlineKeyboardButton("❌ انصراف", callback_data="buy:cancel")]]
    )


def renew_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ تایید تمدید", callback_data="renew:confirm"), InlineKeyboardButton("❌ انصراف", callback_data="renew:cancel")]]
    )


def topup_amount_keyboard(method: str, unit_price: int) -> InlineKeyboardMarkup:
    factors = [1, 2, 3, 5, 10, 20]
    rows: list[list[InlineKeyboardButton]] = []
    for idx in range(0, len(factors), 3):
        row = []
        for factor in factors[idx : idx + 3]:
            amount = int(unit_price) * factor
            row.append(InlineKeyboardButton(f"{factor}GB | {amount:,}", callback_data=f"topup:amount:{method}:{amount}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("✍️ مبلغ دلخواه", callback_data=f"topup:custom:{method}")])
    rows.append([InlineKeyboardButton("❌ انصراف", callback_data="topup:cancel")])
    return InlineKeyboardMarkup(rows)


def topup_amount_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ تایید مبلغ", callback_data="topup:amount_confirm"), InlineKeyboardButton("🔁 تغییر مبلغ", callback_data="topup:amount_back")]]
    )


def admin_decision_keyboard(prefix: str, ref_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ تایید", callback_data=f"{prefix}:approve:{ref_id}"), InlineKeyboardButton("❌ رد", callback_data=f"{prefix}:reject:{ref_id}")]]
    )


def agent_admin_decision_keyboard(ref_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ تایید باز", callback_data=f"agent_admin:approve_open:{ref_id}")],
            [InlineKeyboardButton("✅ تایید نیازمند پرداخت", callback_data=f"agent_admin:approve_closed:{ref_id}")],
            [InlineKeyboardButton("❌ رد", callback_data=f"agent_admin:reject:{ref_id}")],
        ]
    )


def qty_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("۱ عدد", callback_data="buy:qty:1"),
                InlineKeyboardButton("۲ عدد", callback_data="buy:qty:2"),
                InlineKeyboardButton("۳ عدد", callback_data="buy:qty:3"),
            ],
            [
                InlineKeyboardButton("۵ عدد", callback_data="buy:qty:5"),
                InlineKeyboardButton("۱۰ عدد", callback_data="buy:qty:10"),
                InlineKeyboardButton("✍️ دلخواه", callback_data="buy:qty:custom"),
            ],
            [InlineKeyboardButton("❌ انصراف", callback_data="buy:cancel")],
        ]
    )


def agent_admin_credit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("۵ میلیون", callback_data="agent_admin:cl:5000000"),
                InlineKeyboardButton("۱۰ میلیون", callback_data="agent_admin:cl:10000000"),
            ],
            [
                InlineKeyboardButton("۲۰ میلیون", callback_data="agent_admin:cl:20000000"),
                InlineKeyboardButton("۵۰ میلیون", callback_data="agent_admin:cl:50000000"),
            ],
            [InlineKeyboardButton("✍️ مبلغ سفارشی", callback_data="agent_admin:cl:custom")],
            [InlineKeyboardButton("🔁 پیش‌فرض سیستم", callback_data="agent_admin:cl:def")],
            [InlineKeyboardButton("❌ لغو تایید", callback_data="agent_admin:cancel")],
        ]
    )


def agent_admin_price_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("۱۵۰ هزار/گیگ", callback_data="agent_admin:pg:150000"),
                InlineKeyboardButton("۱۸۰ هزار/گیگ", callback_data="agent_admin:pg:180000"),
            ],
            [
                InlineKeyboardButton("۲۰۰ هزار/گیگ", callback_data="agent_admin:pg:200000"),
                InlineKeyboardButton("۲۵۰ هزار/گیگ", callback_data="agent_admin:pg:250000"),
            ],
            [InlineKeyboardButton("✍️ قیمت سفارشی", callback_data="agent_admin:pg:custom")],
            [InlineKeyboardButton("🔁 پیش‌فرض سیستم", callback_data="agent_admin:pg:def")],
            [InlineKeyboardButton("❌ لغو تایید", callback_data="agent_admin:cancel")],
        ]
    )


def agent_admin_daily_test_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("۱ کانفیگ/روز", callback_data="agent_admin:dt:1"),
                InlineKeyboardButton("۳ کانفیگ/روز", callback_data="agent_admin:dt:3"),
            ],
            [
                InlineKeyboardButton("۵ کانفیگ/روز", callback_data="agent_admin:dt:5"),
                InlineKeyboardButton("۱۰ کانفیگ/روز", callback_data="agent_admin:dt:10"),
            ],
            [InlineKeyboardButton("✍️ تعداد سفارشی", callback_data="agent_admin:dt:custom")],
            [InlineKeyboardButton("🔁 پیش‌فرض سیستم", callback_data="agent_admin:dt:def")],
            [InlineKeyboardButton("❌ لغو تایید", callback_data="agent_admin:cancel")],
        ]
    )


def agent_admin_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ تایید نهایی", callback_data="agent_admin:ok"),
                InlineKeyboardButton("❌ لغو", callback_data="agent_admin:cancel"),
            ]
        ]
    )


def main_reply_keyboard(*, is_agent: bool = False, has_test: bool = False, has_infinite: bool = False) -> ReplyKeyboardMarkup:
    # Hero "buy" button on top (full width), then balanced icy pairs below.
    rows = [
        [KeyboardButton(BTN_BUY)],
        [KeyboardButton(BTN_SUBS), KeyboardButton(BTN_RENEW)],
        [KeyboardButton(BTN_WALLET), KeyboardButton(BTN_ACCOUNT)],
    ]
    if has_infinite:
        rows.insert(1, [KeyboardButton(BTN_INFINITE)])
    fourth = []
    if has_test:
        fourth.append(KeyboardButton(BTN_TEST_CONFIG))
    if not is_agent:
        fourth.append(KeyboardButton(BTN_AGENT_REQ))
    fourth.append(KeyboardButton(BTN_TARIFFS))
    rows.append(fourth)
    rows.append([KeyboardButton(BTN_SUPPORT)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def clear_flow_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in FLOW_STATE_KEYS:
        context.user_data.pop(key, None)


async def send_text(target, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> Message:
    return await target.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def send_chat_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    return await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML,
    )


async def edit_text(query, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except BadRequest as exc:
        if "Message is not modified" in str(exc):
            return
        raise


async def _answer_query(query, text: str | None = None, *, show_alert: bool = False) -> None:
    try:
        await query.answer(text=text, show_alert=show_alert)
    except BadRequest:
        pass


async def remove_keyboard(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=int(message_id), reply_markup=None)
    except BadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
    except Exception:
        LOG.debug("failed to remove keyboard chat_id=%s message_id=%s", chat_id, message_id, exc_info=True)


async def retire_clicked_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query and query.message:
        await remove_keyboard(context, update.effective_chat.id, query.message.message_id)


async def new_flow_card(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    query = update.callback_query
    old_flow_id = context.user_data.get(FLOW_PROMPT_KEY)
    clicked_id = query.message.message_id if query and query.message else None
    if old_flow_id and old_flow_id != clicked_id:
        await remove_keyboard(context, update.effective_chat.id, old_flow_id)
    if query:
        await _answer_query(query)
        await retire_clicked_keyboard(update, context)
    else:
        pass
    message = await send_chat_message(update, context, text, reply_markup)
    context.user_data[FLOW_PROMPT_KEY] = message.message_id
    return message


async def edit_flow_query(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    query = update.callback_query
    await _answer_query(query)
    await edit_text(query, text, reply_markup)
    if query.message:
        context.user_data[FLOW_PROMPT_KEY] = query.message.message_id


async def send_flow_prompt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    chat_id = update.effective_chat.id
    message_id = context.user_data.get(FLOW_PROMPT_KEY)
    if message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(message_id),
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
            )
            return
        except BadRequest as exc:
            if "Message is not modified" in str(exc):
                return
        except Exception:
            LOG.exception("failed to edit flow prompt chat_id=%s message_id=%s", chat_id, message_id)
    message = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    context.user_data[FLOW_PROMPT_KEY] = message.message_id


async def ensure_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return
    db: AsyncDatabase = context.application.bot_data["db"]
    user = update.effective_user
    await db.upsert_user(user.id, user.first_name or "", user.username or "")
    flags = await db.get_access_flags(user.id)
    if flags["user_disabled"] or flags["agent_disabled"]:
        if update.callback_query:
            await update.callback_query.answer("دسترسی شما محدود شده است.", show_alert=True)
            await context.bot.send_message(chat_id=update.effective_chat.id, text=BAN_TEXT, parse_mode=ParseMode.HTML)
        elif update.effective_message:
            await send_text(update.effective_message, BAN_TEXT)
        raise ApplicationHandlerStop


async def menu_for_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    db: AsyncDatabase = context.application.bot_data["db"]
    return await main_menu_keyboard(
        update.effective_user.id, db, context.application.bot_data.get("provisioning")
    )


async def _build_reply_keyboard(user_id: int, db: AsyncDatabase, provisioning=None) -> ReplyKeyboardMarkup:
    agent = await db.get_agent(user_id)
    is_agent = bool(agent and not int(agent["disabled"] or 0))
    has_test = await test_config_available(user_id, db, provisioning)
    has_infinite = await infinite_enabled(db)
    return main_reply_keyboard(is_agent=is_agent, has_test=has_test, has_infinite=has_infinite)


async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update, context)
    chat_id = update.effective_chat.id
    old_home_id = context.user_data.get(HOME_MESSAGE_KEY)
    old_flow_id = context.user_data.get(FLOW_PROMPT_KEY)
    clear_flow_state(context)
    db: AsyncDatabase = context.application.bot_data["db"]
    welcome = await render_welcome(db)
    if update.callback_query:
        keyboard = await menu_for_user(update, context)
        query = update.callback_query
        await _answer_query(query)
        msg = query.message
        if msg and not msg.photo and not msg.document and not msg.video and not msg.audio:
            try:
                await query.edit_message_text(welcome, reply_markup=keyboard, parse_mode=ParseMode.HTML)
                new_id = msg.message_id
                context.user_data[HOME_MESSAGE_KEY] = new_id
                if old_home_id and old_home_id != new_id:
                    await remove_keyboard(context, chat_id, old_home_id)
                if old_flow_id and old_flow_id != new_id:
                    await remove_keyboard(context, chat_id, old_flow_id)
                return
            except BadRequest as exc:
                if "Message is not modified" in str(exc):
                    context.user_data[HOME_MESSAGE_KEY] = msg.message_id
                    return
        await retire_clicked_keyboard(update, context)
        await remove_keyboard(context, chat_id, old_home_id)
        await remove_keyboard(context, chat_id, old_flow_id)
        message = await send_chat_message(update, context, welcome, keyboard)
        context.user_data[HOME_MESSAGE_KEY] = message.message_id
        return
    # Text-triggered: send reply keyboard (persists at bottom of chat)
    await remove_keyboard(context, chat_id, old_home_id)
    await remove_keyboard(context, chat_id, old_flow_id)
    reply_kb = await _build_reply_keyboard(
        update.effective_user.id, db, context.application.bot_data.get("provisioning")
    )
    message = await context.bot.send_message(
        chat_id=chat_id,
        text=welcome,
        reply_markup=reply_kb,
        parse_mode=ParseMode.HTML,
    )
    context.user_data[HOME_MESSAGE_KEY] = message.message_id


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_main_menu(update, context)


async def end_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await send_main_menu(update, context)
    return ConversationHandler.END


async def get_price_tiers(db: AsyncDatabase) -> list[dict]:
    """Admin-configured volume-based pricing brackets, sorted ascending by
    ``min_gb``. Each entry is ``{"min_gb": int, "price_per_gb": int}``. The
    applicable price for a given volume is the tier with the greatest
    ``min_gb`` that does not exceed the requested gigabytes."""
    raw = str(await db.get_setting("price_tiers", "") or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    tiers: list[dict] = []
    if isinstance(data, list):
        for item in data:
            try:
                mg = int(float((item or {}).get("min_gb")))
                pp = int(float((item or {}).get("price_per_gb")))
            except (TypeError, ValueError):
                continue
            if mg >= 0 and pp > 0:
                tiers.append({"min_gb": mg, "price_per_gb": pp})
    tiers.sort(key=lambda x: x["min_gb"])
    return tiers


async def unit_price_for_gb(db: AsyncDatabase, user_id: int, gb: int, agent=None) -> int:
    """Per-gigabyte price for a specific volume.

    Agents with a custom flat price always keep it. Otherwise, if the admin has
    configured volume tiers, the matching bracket's price is used; if no tier
    matches (volume below the smallest bracket), the smallest bracket applies.
    Falls back to the flat ``price_per_gb`` setting when no tiers exist."""
    if agent is None:
        agent = await db.get_agent(user_id)
    agent_price = int(agent["price_per_gb"] or 0) if agent else 0
    if agent_price > 0:
        return agent_price
    pg_unit = await pg_unit_price_override(db)
    if pg_unit > 0:
        return pg_unit
    tiers = await get_price_tiers(db)
    if tiers:
        chosen = tiers[0]["price_per_gb"]
        for tier in tiers:
            if int(gb) >= tier["min_gb"]:
                chosen = tier["price_per_gb"]
            else:
                break
        return int(chosen)
    return int(await db.get_setting("price_per_gb", "200000") or "200000")


async def effective_unit_price(db: AsyncDatabase, user_id: int, agent=None) -> int:
    """Baseline per-gigabyte price (agent flat price, or the lowest configured
    tier / flat price). Used for top-up suggestions and the tariff summary where
    no specific purchase volume is known yet."""
    if agent is None:
        agent = await db.get_agent(user_id)
    agent_price = int(agent["price_per_gb"] or 0) if agent else 0
    if agent_price > 0:
        return agent_price
    pg_unit = await pg_unit_price_override(db)
    if pg_unit > 0:
        return pg_unit
    tiers = await get_price_tiers(db)
    if tiers:
        return int(tiers[0]["price_per_gb"])
    return int(await db.get_setting("price_per_gb", "200000") or "200000")


async def sales_is_open(db: AsyncDatabase, *, is_agent: bool = False) -> bool:
    """Whether buying/renewing is currently open for this audience.

    Sales can be opened/closed independently for agents (نماینده) and normal
    users. The per-audience setting falls back to the legacy master
    ``sales_status`` when it has never been set, so existing deployments keep
    their current behavior.
    """
    master = str(await db.get_setting("sales_status", "open") or "open").strip().lower()
    key = "sales_status_agent" if is_agent else "sales_status_user"
    value = str(await db.get_setting(key, master) or master).strip().lower()
    return value != "closed"


async def audience_sales_is_open(db: AsyncDatabase, user_id: int) -> bool:
    return await sales_is_open(db, is_agent=await db.get_agent(user_id) is not None)


async def show_sales_closed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await new_flow_card(
        update,
        context,
        "🔒 <b>فروش سرویس موقتاً بسته است.</b>\n\n"
        "در حال حاضر خرید جدید و تمدید اشتراک انجام نمی‌شود.\n"
        "به محض باز شدن فروش، از طریق ربات اطلاع‌رسانی خواهد شد.",
        back_keyboard(),
    )


async def admin_ids(context: ContextTypes.DEFAULT_TYPE) -> list[int]:
    db: AsyncDatabase = context.application.bot_data["db"]
    settings: Settings = context.application.bot_data["settings"]
    return await db.get_admin_user_ids(settings.admin_id)


async def is_bot_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.effective_user:
        return False
    return int(update.effective_user.id) in await admin_ids(context)


async def notify_admins(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    text: str,
    photo_file_id: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> int:
    sent = 0
    for admin_id in await admin_ids(context):
        try:
            if photo_file_id:
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=photo_file_id,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
            else:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
            sent += 1
        except Exception:
            LOG.exception("failed to notify admin admin_id=%s", admin_id)
    return sent


# ─────────────────────── Sales catalog flow (categories → plans) ───────────────────────
# The buy button sells CATALOG PLANS. A plan carries its own target, so which
# panel and which group a purchase lands on is a property of the plan, not of
# the button that opened it — that is what lets one shop sell PasarGuard,
# 3x-ui and Athena side by side.
#
# The volume step only appears for a variable plan, and the quantity step is
# kept from elsa's older flow: agents routinely buy several at once, and
# dropping it would quietly remove something people use.

CAT_STATE_KEY = "cat_buy"


def _cat_state(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault(CAT_STATE_KEY, {})


async def _cat_expired(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """A button whose flow state is gone (bot restarted, or a very old message)."""
    clear_flow_state(context)
    context.user_data.pop(CAT_STATE_KEY, None)
    message = "⚠️ این دکمه منقضی شده است. لطفاً دوباره از منو شروع کنید."
    if update.callback_query:
        await edit_text(update.callback_query, message, back_keyboard())
    else:
        await send_flow_prompt(update, context, message, back_keyboard())
    return ConversationHandler.END


async def _agent_unit_price(db: AsyncDatabase, user_id: int) -> tuple[bool, int]:
    """(is_agent, their own per-GB rate). Elsa contracts rates per agent."""
    agent = await db.get_agent(user_id)
    if not agent:
        return False, 0
    return True, int(agent["price_per_gb"] or 0)


async def _plan_price(db: AsyncDatabase, user_id: int, plan: dict, gb: int | None) -> int:
    is_agent, own_rate = await _agent_unit_price(db, user_id)
    return catalog.price_for(plan, gb=gb, is_agent=is_agent, agent_unit_price=own_rate)


def _entry_gb(plan: dict) -> int | None:
    """The volume a plan is quoted at before the buyer picks one."""
    if str((plan.get("volume") or {}).get("mode")) != catalog.VOLUME_VARIABLE:
        return None
    choices = catalog.gb_choices(plan)
    return choices[0] if choices else 0


def _plan_lines(plan: dict, gb: int | None) -> str:
    volume = catalog.volume_label(plan, gb)
    icon = "♾️" if volume == "نامحدود" else "📦"
    note = str((plan.get("display") or {}).get("note") or "").strip()
    lines = [
        f"{icon} حجم: <b>{html.escape(volume)}</b>",
        f"⏳ مدت اعتبار: <b>{html.escape(catalog.duration_label(plan))}</b>",
    ]
    if note:
        lines.append(f"ℹ️ {html.escape(note)}")
    return "\n".join(lines)


async def show_catalog_root(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: AsyncDatabase = context.application.bot_data["db"]
    data = await catalog.load_catalog(db)
    categories = catalog.visible_categories(data)
    if not categories:
        await new_flow_card(update, context, "🛒 در حال حاضر پلنی برای فروش تعریف نشده است.", back_keyboard())
        return ConversationHandler.END
    if len(categories) == 1:
        return await show_category_plans(update, context, categories[0]["id"])

    rows = []
    for cat in categories:
        label = f"{str(cat.get('emoji') or '').strip()} {cat['title']}".strip()
        count = len(catalog.plans_in_category(data, cat["id"]))
        rows.append([InlineKeyboardButton(f"{label}  ({count})", callback_data=f"cat:{cat['id']}")])
    rows.append([InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")])
    await new_flow_card(
        update, context,
        "🛒 <b>خرید سرویس</b>\n"
        "<code>─────────────────────</code>\n"
        "ابتدا دسته‌ی مورد نظر را انتخاب کنید 👇",
        InlineKeyboardMarkup(rows),
    )
    return CAT_SELECT


async def show_category_plans(update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: str) -> int:
    db: AsyncDatabase = context.application.bot_data["db"]
    data = await catalog.load_catalog(db)
    cat = catalog.find_category(data, category_id)
    plans = catalog.plans_in_category(data, category_id)
    # A disabled category must not be reachable through a stale button either.
    if not cat or not cat.get("enabled") or not plans:
        await new_flow_card(update, context, "🛒 در این دسته پلنی موجود نیست.", back_keyboard())
        return ConversationHandler.END

    rows = []
    for plan in plans:
        gb = _entry_gb(plan)
        price = await _plan_price(db, update.effective_user.id, plan, gb)
        badge = str((plan.get("display") or {}).get("badge") or "").strip()
        prefix = "از " if gb is not None else ""
        label = f"{badge + ' ' if badge else ''}{plan['title']} — {prefix}{price:,} ت"
        rows.append([InlineKeyboardButton(label, callback_data=f"cpk:sel:{plan['id']}")])
    if len(catalog.visible_categories(data)) > 1:
        rows.append([InlineKeyboardButton("↩️ دسته‌های دیگر", callback_data="cat:back")])
    rows.append([InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")])

    heading = f"{str(cat.get('emoji') or '').strip()} {cat['title']}".strip()
    desc = str(cat.get("description") or "").strip()
    text = (
        f"🛒 <b>{html.escape(heading)}</b>\n"
        "<code>─────────────────────</code>\n"
        + (f"{html.escape(desc)}\n\n" if desc else "")
        + "یکی از پلن‌های زیر را انتخاب کنید 👇"
    )
    _cat_state(context)["category_id"] = category_id
    if update.callback_query:
        await edit_flow_query(update, context, text, InlineKeyboardMarkup(rows))
    else:
        await new_flow_card(update, context, text, InlineKeyboardMarkup(rows))
    return CAT_SELECT


async def catalog_category_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _answer_query(query)
    category_id = (query.data or "").split(":", 1)[1]
    if category_id == "back":
        return await show_catalog_root(update, context)
    return await show_category_plans(update, context, category_id)


async def catalog_plan_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _answer_query(query)
    db: AsyncDatabase = context.application.bot_data["db"]
    plan_id = (query.data or "").split(":", 2)[-1]
    data = await catalog.load_catalog(db)
    plan = catalog.find_plan(data, plan_id)
    if not catalog.plan_is_sellable(data, plan):
        clear_flow_state(context)
        await edit_text(query, "⚠️ این پلن دیگر در دسترس نیست. لطفاً دوباره از منو انتخاب کنید.", back_keyboard())
        return ConversationHandler.END

    state = _cat_state(context)
    state.update(plan_id=plan_id, gb=None, qty=1, client_name="", awaiting_name=False)
    state.setdefault("idem", f"cat-{update.effective_user.id}-{secrets.token_hex(8)}")
    if str((plan.get("volume") or {}).get("mode")) == catalog.VOLUME_VARIABLE:
        return await show_plan_volumes(update, context, plan)
    return await show_plan_qty(update, context, plan)


async def show_plan_volumes(update: Update, context: ContextTypes.DEFAULT_TYPE, plan: dict) -> int:
    db: AsyncDatabase = context.application.bot_data["db"]
    choices = catalog.gb_choices(plan)
    if not choices:
        clear_flow_state(context)
        await edit_text(update.callback_query, "⚠️ این پلن پیکربندی کاملی ندارد. با پشتیبانی تماس بگیرید.", back_keyboard())
        return ConversationHandler.END
    rows, row = [], []
    for gb in choices:
        price = await _plan_price(db, update.effective_user.id, plan, gb)
        row.append(InlineKeyboardButton(f"{gb} گیگ — {price:,} ت", callback_data=f"cpk:gb:{plan['id']}:{gb}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    back = str(plan.get("category_id") or "")
    rows.append([InlineKeyboardButton("↩️ بازگشت", callback_data=f"cat:{back}" if back else "menu:main")])
    await edit_flow_query(
        update, context,
        f"📦 <b>{html.escape(str(plan['title']))}</b>\n"
        "<code>─────────────────────</code>\n"
        f"⏳ مدت اعتبار: <b>{html.escape(catalog.duration_label(plan))}</b>\n\n"
        "چه مقدار حجم می‌خواهید؟ 👇",
        InlineKeyboardMarkup(rows),
    )
    return CAT_SELECT


async def catalog_volume_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _answer_query(query)
    db: AsyncDatabase = context.application.bot_data["db"]
    try:
        _, _, plan_id, gb_s = (query.data or "").split(":", 3)
        gb = int(gb_s)
    except Exception:
        return await _cat_expired(update, context)
    data = await catalog.load_catalog(db)
    plan = catalog.find_plan(data, plan_id)
    if not catalog.plan_is_sellable(data, plan):
        clear_flow_state(context)
        await edit_text(query, "⚠️ این پلن دیگر در دسترس نیست.", back_keyboard())
        return ConversationHandler.END
    # Never trust the callback: only volumes this plan actually offers.
    if gb not in catalog.gb_choices(plan):
        clear_flow_state(context)
        await edit_text(query, "⚠️ این حجم برای این پلن معتبر نیست.", back_keyboard())
        return ConversationHandler.END
    state = _cat_state(context)
    state.update(plan_id=plan_id, gb=gb)
    state.setdefault("idem", f"cat-{update.effective_user.id}-{secrets.token_hex(8)}")
    return await show_plan_qty(update, context, plan)


async def show_plan_qty(update: Update, context: ContextTypes.DEFAULT_TYPE, plan: dict) -> int:
    """Quantity step — kept from elsa's older flow; agents buy in batches."""
    state = _cat_state(context)
    gb = state.get("gb")
    rows = [[
        InlineKeyboardButton(f"{n} عدد", callback_data=f"cpk:q:{n}") for n in (1, 2, 3)
    ], [
        InlineKeyboardButton(f"{n} عدد", callback_data=f"cpk:q:{n}") for n in (5, 10)
    ], [InlineKeyboardButton("✍️ تعداد دلخواه", callback_data="cpk:q:custom")],
       [InlineKeyboardButton("❌ انصراف", callback_data="buy:cancel")]]
    text = (
        f"🔢 <b>تعداد</b>\n"
        "<code>─────────────────────</code>\n"
        f"🎁 پلن: <b>{html.escape(str(plan['title']))}</b>\n"
        f"{_plan_lines(plan, gb)}\n"
        "<code>─────────────────────</code>\n"
        "چند عدد از این سرویس می‌خواهید؟"
    )
    if update.callback_query:
        await edit_flow_query(update, context, text, InlineKeyboardMarkup(rows))
    else:
        await send_flow_prompt(update, context, text, InlineKeyboardMarkup(rows))
    return CAT_QTY


async def catalog_qty_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _answer_query(query)
    raw = (query.data or "").split(":", 2)[-1]
    if raw == "custom":
        await edit_flow_query(
            update, context,
            "✍️ <b>تعداد دلخواه</b>\n\nعدد تعداد سرویس را ارسال کنید.",
            InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="buy:cancel")]]),
        )
        return CAT_QTY
    try:
        qty = int(raw)
    except (TypeError, ValueError):
        return await _cat_expired(update, context)
    return await _set_qty_and_ask_name(update, context, qty)


async def catalog_qty_typed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.effective_message.text or "").strip()
    if not text.isdigit() or not (1 <= int(text) <= CATALOG_MAX_QTY):
        await send_flow_prompt(
            update, context,
            f"⚠️ <b>تعداد معتبر نیست.</b>\n\n🔢 عددی بین ۱ و {CATALOG_MAX_QTY} بفرستید.",
            InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="buy:cancel")]]),
        )
        return CAT_QTY
    return await _set_qty_and_ask_name(update, context, int(text))


async def _set_qty_and_ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE, qty: int) -> int:
    state = _cat_state(context)
    if not state.get("plan_id"):
        return await _cat_expired(update, context)
    state["qty"] = max(1, min(CATALOG_MAX_QTY, int(qty)))
    state["awaiting_name"] = False
    rows = [
        [InlineKeyboardButton("🎲 نام رندوم", callback_data="cpk:name:random")],
        [InlineKeyboardButton("✍️ نام دلخواه", callback_data="cpk:name:custom")],
        [InlineKeyboardButton("❌ انصراف", callback_data="buy:cancel")],
    ]
    text = (
        f"✅ تعداد <b>{state['qty']}</b> سرویس ثبت شد.\n\n"
        "🪪 <b>نام کانفیگ</b>\n"
        "می‌توانید نام را خودتان مشخص کنید یا اجازه بدهید ربات نام رندوم بسازد."
    )
    if update.callback_query:
        await edit_flow_query(update, context, text, InlineKeyboardMarkup(rows))
    else:
        await send_flow_prompt(update, context, text, InlineKeyboardMarkup(rows))
    return CAT_NAME_MODE


async def catalog_name_random(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _answer_query(update.callback_query)
    state = _cat_state(context)
    if not state.get("plan_id"):
        return await _cat_expired(update, context)
    state["client_name"] = ""
    state["awaiting_name"] = False
    return await build_catalog_invoice(update, context)


async def catalog_name_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _answer_query(update.callback_query)
    state = _cat_state(context)
    if not state.get("plan_id"):
        return await _cat_expired(update, context)
    state["awaiting_name"] = True
    await edit_flow_query(
        update, context,
        "✍️ <b>نام دلخواه کانفیگ</b>\n\n"
        "یک نام کوتاه انگلیسی/عددی بفرستید.\nمثال: <code>ali-office</code>",
        InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="buy:cancel")]]),
    )
    return CAT_NAME_INPUT


async def catalog_name_typed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.effective_message.text or "").strip()
    if not NAME_RE.match(name):
        await send_flow_prompt(
            update, context,
            "⚠️ نام معتبر نیست.\n\nاز ۲ تا ۳۲ کاراکتر انگلیسی/عددی و علامت‌های <code>- _ .</code> استفاده کنید.",
            InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="buy:cancel")]]),
        )
        return CAT_NAME_INPUT
    state = _cat_state(context)
    state["client_name"] = name
    state["awaiting_name"] = False
    return await build_catalog_invoice(update, context)


async def build_catalog_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: AsyncDatabase = context.application.bot_data["db"]
    state = _cat_state(context)
    plan_id = str(state.get("plan_id") or "")
    if not plan_id:
        return await _cat_expired(update, context)
    data = await catalog.load_catalog(db)
    plan = catalog.find_plan(data, plan_id)
    if not catalog.plan_is_sellable(data, plan):
        clear_flow_state(context)
        message = "⚠️ این پلن دیگر در دسترس نیست. لطفاً دوباره از منو انتخاب کنید."
        if update.callback_query:
            await edit_text(update.callback_query, message, back_keyboard())
        else:
            await send_flow_prompt(update, context, message, back_keyboard())
        return ConversationHandler.END

    gb = state.get("gb")
    qty = max(1, int(state.get("qty") or 1))
    unit = await _plan_price(db, update.effective_user.id, plan, gb)
    total = unit * qty
    name = str(state.get("client_name") or "").strip()
    text = (
        "🧾 <b>تایید خرید</b>\n"
        "<code>─────────────────────</code>\n"
        f"🎁 پلن: <b>{html.escape(str(plan['title']))}</b>\n"
        f"{_plan_lines(plan, gb)}\n"
        f"🔢 تعداد: <b>{qty}</b>\n"
        f"🪪 نام کانفیگ: <b>{html.escape(name) if name else '🎲 رندوم'}</b>\n"
        "<code>─────────────────────</code>\n"
        + (f"💰 قیمت هر سرویس: <b>{unit:,}</b> تومان\n" if qty > 1 else "")
        + f"💰 مبلغ قابل پرداخت: <b>{total:,}</b> تومان\n\n"
        "✅ با تایید، سرویس فوری ساخته و تحویل داده می‌شود."
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تایید و خرید", callback_data="cpk:ok"),
        InlineKeyboardButton("❌ انصراف", callback_data="buy:cancel"),
    ]])
    if update.callback_query:
        await edit_flow_query(update, context, text, keyboard)
    else:
        await send_flow_prompt(update, context, text, keyboard)
    return CAT_CONFIRM


async def catalog_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _answer_query(query)
    db: AsyncDatabase = context.application.bot_data["db"]
    provisioning: ProvisioningService = context.application.bot_data["provisioning"]
    qr: QRService = context.application.bot_data["qr"]
    state = _cat_state(context)
    plan_id = str(state.get("plan_id") or "")
    if not plan_id:
        return await _cat_expired(update, context)

    if not await audience_sales_is_open(db, update.effective_user.id):
        clear_flow_state(context)
        await edit_text(query, "🔒 <b>فروش سرویس موقتاً بسته است.</b>", back_keyboard())
        return ConversationHandler.END

    data = await catalog.load_catalog(db)
    plan = catalog.find_plan(data, plan_id)
    if not catalog.plan_is_sellable(data, plan):
        clear_flow_state(context)
        await edit_text(query, "⚠️ این پلن دیگر در دسترس نیست. لطفاً دوباره انتخاب کنید.", back_keyboard())
        return ConversationHandler.END
    problems = catalog.validate_plan(plan)
    if problems:
        clear_flow_state(context)
        LOG.warning("plan %s is not sellable: %s", plan_id, problems)
        await edit_text(query, "⚠️ این پلن پیکربندی کاملی ندارد. لطفاً با پشتیبانی تماس بگیرید.", back_keyboard())
        return ConversationHandler.END

    gb = state.get("gb")
    variable = str((plan.get("volume") or {}).get("mode")) == catalog.VOLUME_VARIABLE
    if variable:
        if gb is None or int(gb) not in catalog.gb_choices(plan):
            clear_flow_state(context)
            await edit_text(query, "⚠️ این حجم برای این پلن معتبر نیست.", back_keyboard())
            return ConversationHandler.END
    else:
        gb = None
    qty = max(1, min(CATALOG_MAX_QTY, int(state.get("qty") or 1)))
    unit = await _plan_price(db, update.effective_user.id, plan, gb)
    real_gb = catalog.provisioned_gb(plan, gb)

    target = plan.get("target") or {}
    kind = str(target.get("kind") or catalog.TARGET_PASARGUARD)
    pg_client = athena_client = panel_client = None
    group_ids: list[int] = []
    if kind == catalog.TARGET_PASARGUARD:
        pg_client = await get_pg_client(context)
        if pg_client is None:
            clear_flow_state(context)
            await edit_text(query, "🌐 این سرور در حال حاضر در دسترس نیست.", back_keyboard())
            return ConversationHandler.END
        group = str(target.get("group") or "").strip()
        group_ids = await pg_client.resolve_group_ids([group]) if group else []
        if not group_ids:
            clear_flow_state(context)
            LOG.warning("plan %s targets unknown PasarGuard group %r", plan_id, group)
            await edit_text(query, "گروه سرور پیدا نشد؛ لطفاً با پشتیبانی تماس بگیرید.", back_keyboard())
            return ConversationHandler.END
    elif kind == catalog.TARGET_ATHENA:
        athena_client = await get_athena_client(context)
        if athena_client is None:
            clear_flow_state(context)
            await edit_text(query, "🌐 این سرور در حال حاضر در دسترس نیست.", back_keyboard())
            return ConversationHandler.END
    else:
        panel_client = context.application.bot_data.get("panel")
        if panel_client is None:
            clear_flow_state(context)
            await edit_text(query, "🌐 این سرور در حال حاضر در دسترس نیست.", back_keyboard())
            return ConversationHandler.END

    await edit_flow_query(update, context, "⏳ <b>در حال ساخت سرویس...</b>\n\nلطفاً چند لحظه صبر کنید.")
    try:
        deliveries = await provisioning.process_catalog_purchase(
            user_id=update.effective_user.id,
            plan=plan,
            gb=real_gb,
            qty=qty,
            unit_total=unit,
            client_name=str(state.get("client_name") or ""),
            pg_client=pg_client,
            group_ids=group_ids,
            athena_client=athena_client,
            panel=panel_client,
            idempotency_key=str(state.get("idem") or query.id),
        )
    except ValueError as exc:
        clear_flow_state(context)
        await edit_text(
            query,
            f"⚠️ <b>خرید انجام نشد.</b>\n\n{html.escape(str(exc))}",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 شارژ کیف پول", callback_data="menu:wallet")],
                [InlineKeyboardButton("بازگشت به منو", callback_data="menu:main")],
            ]),
        )
        return ConversationHandler.END
    except Exception as exc:
        if "duplicate purchase request" in str(exc):
            await _answer_query(query, "این خرید در حال پردازش است…")
            return ConversationHandler.END
        LOG.exception("catalog purchase failed user_id=%s", update.effective_user.id)
        clear_flow_state(context)
        await edit_text(query, f"❌ خطا در ساخت سرویس:\n{html.escape(str(exc))}", back_keyboard())
        return ConversationHandler.END

    await edit_text(query, "✅ <b>سرویس شما ساخته شد.</b>\n\nمشخصات اتصال در پیام بعدی ارسال می‌شود.")
    await deliver_catalog_services(update, context, plan=plan, gb=gb, deliveries=deliveries, qr=qr)
    clear_flow_state(context)
    context.user_data.pop(CAT_STATE_KEY, None)
    return ConversationHandler.END


async def deliver_catalog_services(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    plan: dict,
    gb: int | None,
    deliveries: list[dict],
    qr: QRService,
) -> None:
    """Hand over what each backend actually gives a customer.

    PasarGuard and 3x-ui give a subscription link; Athena gives a username, a
    password and per-protocol endpoints. Sending the wrong shape is the whole
    reason this is one function rather than a format string at the call site.
    """
    title = html.escape(str(plan.get("title") or ""))
    volume = html.escape(catalog.volume_label(plan, gb))
    duration = html.escape(catalog.duration_label(plan))
    last = len(deliveries) - 1
    for index, item in enumerate(deliveries):
        markup = back_keyboard() if index == last else None
        if item.get("backend") == catalog.TARGET_ATHENA:
            lines = connection_lines(item)
            caption = (
                f"✅ <b>{title}</b>\n"
                "<i>سرویس شما فعال شد 🌟</i>\n"
                "<code>─────────────────────</code>\n"
                f"📦 حجم: <b>{volume}</b>\n"
                f"⏳ مدت اعتبار: <b>{duration}</b>\n"
                "<code>─────────────────────</code>\n"
                f"👤 نام کاربری: <code>{html.escape(str(item.get('username') or ''))}</code>\n"
                f"🔑 رمز عبور: <code>{html.escape(str(item.get('password') or ''))}</code>\n"
            )
            if lines:
                caption += "\n🌐 <b>آدرس سرور:</b>\n" + "\n".join(
                    f"• <code>{html.escape(line)}</code>" for line in lines
                )
            sub_link = str(item.get("sub_link") or "")
            if sub_link:
                caption += f"\n\n🔗 <b>لینک اشتراک:</b>\n<code>{html.escape(sub_link)}</code>"
            caption += "\n\n📲 در اپلیکیشن L2TP/SSTP همین مشخصات را وارد کنید."
            if sub_link:
                png = await qr.png(sub_link)
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id, photo=BytesIO(png),
                    caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup,
                )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id, text=caption,
                    parse_mode=ParseMode.HTML, reply_markup=markup,
                )
            continue

        sub_link = str(item.get("sub_link") or "")
        caption = (
            f"✅ <b>{title}</b>\n"
            "<i>سرویس شما فعال شد 🌟</i>\n"
            "<code>─────────────────────</code>\n"
            f"📦 حجم: <b>{volume}</b>\n"
            f"⏳ مدت اعتبار: <b>{duration}</b>\n"
            "<code>─────────────────────</code>\n"
            f"🔗 <b>لینک اشتراک شما:</b>\n<code>{html.escape(sub_link)}</code>\n\n"
            "📲 این لینک را در اپلیکیشن خود وارد کنید یا QR را اسکن کنید."
        )
        if sub_link:
            png = await qr.png(sub_link)
            await context.bot.send_photo(
                chat_id=update.effective_chat.id, photo=BytesIO(png),
                caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup,
            )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id, text=caption,
                parse_mode=ParseMode.HTML, reply_markup=markup,
            )


async def buy_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Main buy button — opens the sales catalog.

    Which panel a purchase lands on is a property of the plan, so this no longer
    decides anything about backends: the catalog already knows. When no plan has
    been defined yet it falls back to the old per-GB flow so a shop that has not
    been migrated still sells.
    """
    await ensure_user(update, context)
    db: AsyncDatabase = context.application.bot_data["db"]
    if not await audience_sales_is_open(db, update.effective_user.id):
        await show_sales_closed(update, context)
        return ConversationHandler.END
    await remove_keyboard(context, update.effective_chat.id, context.user_data.get(FLOW_PROMPT_KEY))
    clear_flow_state(context)
    context.user_data.pop(CAT_STATE_KEY, None)

    data = await catalog.load_catalog(db)
    if catalog.visible_categories(data):
        return await show_catalog_root(update, context)

    min_gb = await minimum_purchase_gb(db)
    context.user_data["checkout"] = {}
    await new_flow_card(
        update,
        context,
        gb_choice_prompt("🛒 <b>خرید سرویس جدید</b>  •  مرحله ۱ از ۴", min_gb),
        await gb_choice_keyboard(db, update.effective_user.id, "buy", "buy:cancel", min_gb),
    )
    return BUY_GB


async def buy_gb_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: AsyncDatabase = context.application.bot_data["db"]
    min_gb = await minimum_purchase_gb(db)
    await send_flow_prompt(
        update,
        context,
        "📦 لطفاً حجم را از دکمه‌های همین کارت انتخاب کنید.\n\n"
        f"حداقل حجم مجاز: <b>{min_gb}</b> گیگ. برای وارد کردن عدد، گزینه <b>حجم دلخواه</b> را بزنید.",
        await gb_choice_keyboard(db, update.effective_user.id, "buy", "buy:cancel", min_gb),
    )
    return BUY_GB


async def buy_gb_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _answer_query(query)
    db: AsyncDatabase = context.application.bot_data["db"]
    min_gb = await minimum_purchase_gb(db)
    gb = int(query.data.rsplit(":", 1)[1])
    if gb < min_gb:
        await edit_text(query, invalid_gb_text(min_gb), await gb_choice_keyboard(db, update.effective_user.id, "buy", "buy:cancel", min_gb))
        context.user_data[FLOW_PROMPT_KEY] = query.message.message_id
        return BUY_GB
    context.user_data.setdefault("checkout", {})["gb"] = gb
    await edit_text(
        query,
        f"✅ حجم <b>{gb} گیگ</b> انتخاب شد.\n\n"
        "🛒 <b>مرحله ۲ از ۴ – تعداد اشتراک</b>\n\n"
        "چند اشتراک می‌خواهید؟",
        qty_keyboard(),
    )
    context.user_data[FLOW_PROMPT_KEY] = query.message.message_id
    return BUY_QTY


async def buy_custom_gb_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: AsyncDatabase = context.application.bot_data["db"]
    min_gb = await minimum_purchase_gb(db)
    await edit_flow_query(
        update,
        context,
        custom_gb_prompt("حجم دلخواه", min_gb),
        InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="buy:cancel")]]),
    )
    return BUY_CUSTOM_GB


async def buy_custom_gb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.effective_message.text or "").strip()
    db: AsyncDatabase = context.application.bot_data["db"]
    min_gb = await minimum_purchase_gb(db)
    max_gb = max_custom_gb(min_gb)
    if not text.isdigit() or not (min_gb <= int(text) <= max_gb):
        await send_flow_prompt(update, context, invalid_gb_text(min_gb))
        return BUY_CUSTOM_GB
    gb = int(text)
    context.user_data.setdefault("checkout", {})["gb"] = gb
    await send_flow_prompt(
        update,
        context,
        f"✅ حجم <b>{gb} گیگ</b> ثبت شد.\n\n"
        "🛒 <b>مرحله ۲ از ۴ – تعداد اشتراک</b>\n\n"
        "چند اشتراک می‌خواهید؟",
        qty_keyboard(),
    )
    return BUY_QTY


async def buy_qty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.effective_message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await send_flow_prompt(update, context, "⚠️ <b>تعداد معتبر نیست.</b>\n\n🔢 لطفاً یک عدد مثبت ارسال کنید.", qty_keyboard())
        return BUY_QTY
    context.user_data.setdefault("checkout", {})["qty"] = int(text)
    gb = int(context.user_data.get("checkout", {}).get("gb") or 0)
    await send_flow_prompt(
        update,
        context,
        f"✅ تعداد <b>{int(text)}</b> اشتراک {gb} گیگ ثبت شد.\n\n"
        "🛒 <b>مرحله ۳ از ۴ – نام کانفیگ</b>\n\n"
        "می‌توانید نام کانفیگ را خودتان مشخص کنید یا اجازه بدهید ربات نام رندوم بسازد.",
        config_name_keyboard(),
    )
    return BUY_NAME_MODE


async def buy_qty_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _answer_query(query)
    qty = int(query.data.rsplit(":", 1)[1])
    gb = int(context.user_data.get("checkout", {}).get("gb") or 0)
    context.user_data.setdefault("checkout", {})["qty"] = qty
    await edit_text(
        query,
        f"✅ تعداد <b>{qty}</b> اشتراک {gb} گیگ ثبت شد.\n\n"
        "🛒 <b>مرحله ۳ از ۴ – نام کانفیگ</b>\n\n"
        "می‌توانید نام کانفیگ را خودتان مشخص کنید یا اجازه بدهید ربات نام رندوم بسازد.",
        config_name_keyboard(),
    )
    context.user_data[FLOW_PROMPT_KEY] = query.message.message_id
    return BUY_NAME_MODE


async def buy_qty_custom_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await edit_flow_query(
        update,
        context,
        "✍️ <b>تعداد دلخواه</b>\n\nعدد تعداد اشتراک را ارسال کنید.",
        InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="buy:cancel")]]),
    )
    return BUY_QTY


async def build_buy_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: AsyncDatabase = context.application.bot_data["db"]
    checkout = context.user_data.setdefault("checkout", {})
    agent = await db.get_agent(update.effective_user.id)
    gb = int(checkout["gb"])
    qty = int(checkout["qty"])
    unit_price = await unit_price_for_gb(db, update.effective_user.id, gb, agent)
    min_gb = await minimum_purchase_gb(db)
    if gb < min_gb:
        checkout.pop("gb", None)
        checkout.pop("qty", None)
        await send_flow_prompt(
            update,
            context,
            invalid_gb_text(min_gb),
            await gb_choice_keyboard(db, update.effective_user.id, "buy", "buy:cancel", min_gb),
        )
        return BUY_GB
    total = gb * qty * unit_price
    method_label = (
        "دسترسی باز نمایندگی (ثبت روی اعتبار)"
        if agent and db.normalize_agent_access_value(agent["access_level"]) == "open"
        else "کسر از کیف پول"
    )
    client_name = str(checkout.get("client_name") or "").strip()
    # Stable idempotency token per built invoice: a double-tap on "confirm"
    # (possible because updates run concurrently) reuses the same key so the
    # purchase can never be charged/provisioned twice.
    checkout["idem"] = f"buy-{update.effective_user.id}-{secrets.token_hex(8)}"
    checkout.update(unit_price=unit_price, total=total, method_label=method_label)
    await send_flow_prompt(
        update,
        context,
        "🛒 <b>مرحله ۴ از ۴ – تایید نهایی</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 حجم هر اشتراک: <b>{gb}</b> گیگ\n"
        f"🔢 تعداد: <b>{qty}</b>\n"
        f"💵 قیمت هر گیگ: <b>{unit_price:,}</b> تومان\n"
        f"💰 مبلغ کل: <b>{total:,}</b> تومان\n"
        f"🪪 نام کانفیگ: <b>{html.escape(client_name) if client_name else '🎲 رندوم'}</b>\n"
        f"💳 روش پرداخت: <b>{method_label}</b>\n\n"
        "در صورت تایید، سرویس فوری ساخته می‌شود.",
        buy_confirm_keyboard(),
    )
    return BUY_CONFIRM


async def buy_name_random(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _answer_query(update.callback_query)
    context.user_data.setdefault("checkout", {})["client_name"] = ""
    return await build_buy_invoice(update, context)


async def buy_name_custom_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await edit_flow_query(
        update,
        context,
        "✍️ <b>نام دلخواه کانفیگ</b>\n\n"
        "یک نام کوتاه انگلیسی/عددی بفرستید.\n"
        "مثال: <code>ali-office</code>",
    )
    return BUY_NAME_INPUT


async def buy_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.effective_message.text or "").strip()
    if not NAME_RE.match(name):
        await send_flow_prompt(
            update,
            context,
            "⚠️ نام معتبر نیست.\n\n"
            "از ۲ تا ۳۲ کاراکتر انگلیسی/عددی و علامت‌های <code>- _ .</code> استفاده کنید.",
        )
        return BUY_NAME_INPUT
    context.user_data.setdefault("checkout", {})["client_name"] = name
    return await build_buy_invoice(update, context)


async def buy_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _answer_query(update.callback_query, "خرید لغو شد.")
    clear_flow_state(context)
    await edit_text(update.callback_query, "❌ <b>خرید لغو شد.</b>\n\nهر وقت آماده بودید دوباره شروع کنید.", back_keyboard())
    return ConversationHandler.END


async def buy_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    checkout = context.user_data.get("checkout") or {}
    gb = int(checkout.get("gb") or 0)
    qty = int(checkout.get("qty") or 0)
    unit_price = int(checkout.get("unit_price") or 0)
    total = int(checkout.get("total") or 0)
    client_name = str(checkout.get("client_name") or "")
    method_label = str(checkout.get("method_label") or "کسر از کیف پول")
    if gb <= 0 or qty <= 0 or unit_price <= 0:
        clear_flow_state(context)
        await edit_text(query, "⚠️ اطلاعات خرید کامل نیست. لطفاً دوباره شروع کنید.", back_keyboard())
        return ConversationHandler.END

    db: AsyncDatabase = context.application.bot_data["db"]
    min_gb = await minimum_purchase_gb(db)
    if gb < min_gb:
        checkout.pop("gb", None)
        checkout.pop("qty", None)
        await edit_text(
            query,
            invalid_gb_text(min_gb),
            await gb_choice_keyboard(db, update.effective_user.id, "buy", "buy:cancel", min_gb),
        )
        context.user_data[FLOW_PROMPT_KEY] = query.message.message_id
        return BUY_GB
    if not await audience_sales_is_open(db, update.effective_user.id):
        clear_flow_state(context)
        await edit_text(
            query,
            "🔒 <b>فروش سرویس موقتاً بسته است.</b>\n\n"
            "این خرید ثبت نشد و هیچ مبلغی کسر نشده است.",
            back_keyboard(),
        )
        return ConversationHandler.END

    provisioning: ProvisioningService = context.application.bot_data["provisioning"]
    qr: QRService = context.application.bot_data["qr"]
    await edit_flow_query(update, context, "⏳ <b>در حال ساخت سرویس...</b>\n\nلطفاً چند لحظه صبر کنید.")
    try:
        if await pg_is_primary(db):
            pg_client = await get_pg_client(context)
            if pg_client is None:
                raise ValueError("اتصال به سرور برقرار نشد. لطفاً با پشتیبانی تماس بگیرید.")
            group_ids = await pg_group_ids(db, pg_client)
            if not group_ids:
                raise ValueError("گروه سرور پیدا نشد؛ لطفاً با پشتیبانی تماس بگیرید.")
            links = await provisioning.process_pg_checkout(
                pg_client=pg_client,
                group_ids=group_ids,
                user_id=update.effective_user.id,
                gb=gb,
                qty=qty,
                unit_price=unit_price,
                final_total=total,
                days=await pg_purchase_days(db),
                client_name=client_name,
                idempotency_key=str(checkout.get("idem") or query.id),
            )
        else:
            links = await provisioning.process_checkout(
                user_id=update.effective_user.id,
                plan_id=0,
                gb=gb,
                qty=qty,
                unit_price=unit_price,
                final_total=total,
                client_name=client_name,
                idempotency_key=str(checkout.get("idem") or query.id),
            )
    except ValueError as exc:
        clear_flow_state(context)
        await edit_text(
            query,
            f"⚠️ <b>خرید انجام نشد.</b>\n\n{html.escape(str(exc))}",
            InlineKeyboardMarkup([[InlineKeyboardButton("💳 شارژ کیف پول", callback_data="menu:wallet")], [InlineKeyboardButton("بازگشت به منو", callback_data="menu:main")]]),
        )
        return ConversationHandler.END
    except Exception as exc:
        if "duplicate purchase request" in str(exc):
            # A concurrent double-tap on confirm: the first request is already
            # being processed, so silently ignore this one (no second charge).
            await _answer_query(query, "این خرید در حال پردازش است…")
            return ConversationHandler.END
        LOG.exception("provisioning failed user_id=%s", update.effective_user.id)
        clear_flow_state(context)
        await edit_text(query, f"❌ خطا در ساخت سرویس:\n{html.escape(str(exc))}", back_keyboard())
        return ConversationHandler.END

    await edit_text(query, "❄️ <b>سرویس شما با موفقیت ساخته شد.</b>\n\nلینک اتصال و QR Code در پیام بعدی ارسال می‌شود.")
    for idx, sub_link in enumerate(links):
        safe_link = html.escape(sub_link)
        caption = (
            "✅ <b>پرداخت با موفقیت انجام شد!</b>\n"
            "<i>از خرید شما سپاسگزاریم 🌟</i>\n\n"
            f"📦 حجم هر اشتراک: <b>{gb}</b> گیگ × <b>{qty}</b> عدد\n"
            f"💰 مبلغ پرداختی: <b>{total:,}</b> تومان\n"
            f"💳 روش پرداخت: {html.escape(method_label)}\n\n"
            "🔗 <b>لینک اشتراک شما:</b>\n"
            f"<code>{safe_link}</code>\n\n"
            "برای اتصال، لینک بالا را در اپلیکیشن خود وارد کنید یا QR را اسکن کنید."
        )
        is_last = idx == len(links) - 1
        png = await qr.png(sub_link)
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=BytesIO(png),
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard() if is_last else None,
        )
    return ConversationHandler.END


async def infinite_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update, context)
    if update.callback_query:
        await update.callback_query.answer()
    db: AsyncDatabase = context.application.bot_data["db"]
    provisioning: ProvisioningService = context.application.bot_data["provisioning"]
    pkg = await provisioning.infinite_package()
    if not pkg["enabled"]:
        await new_flow_card(update, context, "♾️ <b>بسته‌ی بی‌نهایت</b>\n\nاین بسته در حال حاضر فعال نیست.", back_keyboard())
        return
    text = (
        "♾️ <b>بسته‌ی بی‌نهایت</b>\n\n"
        "🌊 ترافیک نامحدود\n"
        f"⏳ مدت اعتبار: <b>{pkg['duration_days']:,}</b> روز\n"
        f"💰 قیمت: <b>{pkg['price']:,}</b> تومان\n\n"
        "✅ پس از خرید، <b>لینک اشتراک</b> برایتان ارسال می‌شود.\n"
        "✅ امکان خرید چند بسته وجود دارد."
    )
    # Fresh idempotency token per shown offer → a double-tap on buy cannot
    # charge/provision the package twice.
    context.user_data["infinite_idem"] = f"inf-{update.effective_user.id}-{secrets.token_hex(8)}"
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ خرید و دریافت لینک اشتراک", callback_data="infinite:buy")],
            [InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")],
        ]
    )
    await new_flow_card(update, context, text, kb)


async def infinite_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    db: AsyncDatabase = context.application.bot_data["db"]
    if not await audience_sales_is_open(db, update.effective_user.id):
        await edit_text(query, "🔒 <b>فروش سرویس موقتاً بسته است.</b>", back_keyboard())
        return
    provisioning: ProvisioningService = context.application.bot_data["provisioning"]
    qr: QRService = context.application.bot_data["qr"]
    # The package must come from whichever panel is currently primary — same rule
    # as a normal purchase and an agent test config.
    pg_client = None
    group_ids: list[int] = []
    if await pg_is_primary(db):
        pg_client = await get_pg_client(context)
        if pg_client is None:
            await edit_text(query, "❌ اتصال به سرور برقرار نشد. لطفاً با پشتیبانی تماس بگیرید.", back_keyboard())
            return
        group_ids = await pg_group_ids(db, pg_client)
        if not group_ids:
            await edit_text(query, "❌ سرور به‌درستی پیکربندی نشده است. لطفاً با پشتیبانی تماس بگیرید.", back_keyboard())
            return
    await edit_flow_query(update, context, "⏳ <b>در حال ساخت بسته‌ی بی‌نهایت...</b>\n\nلطفاً چند لحظه صبر کنید.")
    try:
        links = await provisioning.process_infinite_purchase(
            user_id=update.effective_user.id,
            pg_client=pg_client,
            group_ids=group_ids,
            idempotency_key=str(context.user_data.get("infinite_idem") or query.id),
        )
    except ValueError as exc:
        await edit_text(
            query,
            f"⚠️ <b>خرید انجام نشد.</b>\n\n{html.escape(str(exc))}",
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("💳 شارژ کیف پول", callback_data="menu:wallet")], [InlineKeyboardButton("بازگشت به منو", callback_data="menu:main")]]
            ),
        )
        return
    except Exception as exc:
        if "duplicate purchase request" in str(exc):
            await _answer_query(query, "این خرید در حال پردازش است…")
            return
        LOG.exception("infinite purchase failed user_id=%s", update.effective_user.id)
        await edit_text(query, f"❌ خطا در ساخت بسته:\n{html.escape(str(exc))}", back_keyboard())
        return

    links = [str(link) for link in links if link]
    if not links:
        await edit_text(
            query,
            "✅ بسته ساخته شد، اما دریافت لینک اشتراک کمی طول کشید.\n"
            "از بخش «اشتراک‌های من» می‌توانید سرویس را ببینید یا با پشتیبانی تماس بگیرید.",
            back_keyboard(),
        )
        return
    pkg = await provisioning.infinite_package()
    await edit_text(query, "♾️ <b>بسته‌ی بی‌نهایت ساخته شد.</b>\n\nلینک اشتراک و QR Code در پیام بعدی ارسال می‌شود.")
    for idx, sub_link in enumerate(links):
        caption = (
            "✅ <b>بسته‌ی بی‌نهایت فعال شد!</b>\n"
            "<i>از خرید شما سپاسگزاریم 🌟</i>\n\n"
            "🌊 ترافیک نامحدود\n"
            f"⏳ مدت اعتبار: <b>{int(pkg['duration_days']):,}</b> روز\n"
            f"💰 مبلغ پرداختی: <b>{int(pkg['price']):,}</b> تومان\n\n"
            "🔗 <b>لینک اشتراک شما:</b>\n"
            f"<code>{html.escape(sub_link)}</code>\n\n"
            "برای اتصال، لینک بالا را در اپلیکیشن خود وارد کنید یا QR را اسکن کنید."
        )
        png = await qr.png(sub_link)
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=BytesIO(png),
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard() if idx == len(links) - 1 else None,
        )


def format_join_date(ts: int) -> str:
    dt = datetime.fromtimestamp(int(ts), ZoneInfo("Asia/Tehran"))
    if jdatetime:
        return jdatetime.datetime.fromgregorian(datetime=dt).strftime("%Y/%m/%d")
    return dt.strftime("%Y/%m/%d")


async def account_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update, context)
    query = update.callback_query
    if query:
        await query.answer()
    db: AsyncDatabase = context.application.bot_data["db"]
    user = update.effective_user
    snapshot = await db.get_user_account_snapshot(user.id)
    total_gb = await db.get_user_total_purchased_gb(user.id)
    agent = await db.get_agent(user.id)
    provisioning: ProvisioningService = context.application.bot_data["provisioning"]
    quota = await provisioning.test_config_quota(user.id)
    if not quota["enabled"]:
        test_line = ""
    elif quota["scope"] == "daily":
        test_line = f"\n🧪 کانفیگ تست امروز: {quota['used']:,} / {quota['limit']:,}"
    else:
        test_line = f"\n🧪 کانفیگ تست: {quota['used']:,} / {quota['limit']:,}"
    agent_extra_lines = ""
    if agent:
        recent = await db.get_agent_recent_purchase_summary(user.id)
        agent_extra_lines = (
            f"\n🧾 خرید ۲۴ ساعت اخیر: {recent['total_toman']:,} تومان"
            f"\n📦 گیگ ۲۴ ساعت اخیر: {recent['total_gb']:,} گیگ"
            f"\n✅ سفارش ۲۴ ساعت اخیر: {recent['order_count']:,}"
        )
    if not agent:
        access_level = "کاربر عادی"
        credit_lines = ""
    elif db.normalize_agent_access_value(agent["access_level"]) == "open":
        access_level = "نماینده (دسترسی باز)"
        credit_limit = int(agent["credit_limit_toman"] or 0)
        credit_used = int(agent["credit_used_toman"] or 0)
        if credit_limit <= 0:
            # سقف صفر = نامحدود (هم‌راستا با منطق خرید)
            credit_lines = (
                f"\n💳 سقف اعتبار: نامحدود"
                f"\n📉 اعتبار مصرف‌شده: {credit_used:,} تومان"
            )
        else:
            credit_left = max(0, credit_limit - credit_used)
            credit_lines = (
                f"\n💳 سقف اعتبار: {credit_limit:,} تومان"
                f"\n📉 اعتبار مصرف‌شده: {credit_used:,} تومان"
                f"\n✅ اعتبار باقی‌مانده: {credit_left:,} تومان"
            )
    else:
        access_level = "نماینده (نیاز به پرداخت)"
        credit_limit = int(agent["credit_limit_toman"] or 0)
        credit_used = int(agent["credit_used_toman"] or 0)
        credit_left = max(0, credit_limit - credit_used)
        credit_lines = (
            f"\n💳 سقف اعتبار: {credit_limit:,} تومان"
            f"\n📉 اعتبار مصرف‌شده: {credit_used:,} تومان"
            f"\n✅ اعتبار باقی‌مانده: {credit_left:,} تومان"
        )
    if agent:
        credit_lines += agent_extra_lines
    credit_lines += test_line
    username = (user.username or snapshot["username"] or "").strip().lstrip("@")
    username_line = f"@{html.escape(username)}" if username else "ندارد"
    text = (
        "👤 <b>اطلاعات حساب شما</b>\n\n"
        f"🆔 یوزرآیدی: <code>{user.id}</code>\n"
        f"👤 نام: {html.escape(user.first_name or snapshot['first_name'] or '')}\n"
        f"📛 یوزرنیم: {username_line}\n"
        f"🔐 سطح دسترسی: <b>{access_level}</b>\n"
        f"📅 تاریخ عضویت: {format_join_date(snapshot['joined_at'])}\n"
        f"✅ سفارش‌های تاییدشده: {snapshot['approved_orders']}\n"
        f"👥 زیرمجموعه‌ها: {snapshot['referral_count']}\n"
        f"📦 کل حجم خریداری‌شده: {total_gb:,} گیگ\n"
        f"💰 کل هزینه ریالی: {snapshot['total_spent']:,} تومان\n"
        f"👛 موجودی کیف پول: {snapshot['wallet_balance']:,} تومان"
        f"{credit_lines}"
    )
    await new_flow_card(update, context, text, back_keyboard())


def user_identity_label(user) -> str:
    first_name = html.escape(user.first_name or "بدون نام")
    username_value = (user.username or "").strip().lstrip("@")
    username = f"@{html.escape(username_value)}" if username_value else "بدون یوزرنیم"
    return f"{first_name} | {username} | <code>{user.id}</code>"


def subscription_status_label(sub: dict) -> str:
    enabled = sub.get("panel_enabled")
    is_infinite = int(sub.get("is_infinite") or 0) == 1
    total_bytes = int(sub.get("panel_total_bytes") or 0)
    remaining = int(sub.get("panel_remaining_bytes") or 0)
    # Infinite configs are auto-disabled by the panel once the cap is reached;
    # treat a synced & depleted infinite config as disabled even if the panel
    # hasn't flipped the enable flag yet.
    if is_infinite and total_bytes > 0 and remaining <= 0:
        return "غیرفعال (پایان حجم)"
    if enabled is not None and int(enabled) == 0:
        return "غیرفعال (پایان حجم)" if is_infinite else "غیرفعال"
    if enabled is None:
        return "نامشخص"
    return "فعال"


def subscription_remaining_label(sub: dict) -> str:
    remaining = int(sub.get("panel_remaining_bytes") or 0)
    if remaining <= 0:
        return "نامشخص"
    return f"{remaining / (1024**3):.2f} گیگ"


def subscription_expiry_label(sub: dict) -> str:
    """Human remaining-time from the panel snapshot (panel_expiry_time is ms;
    0 = no time limit)."""
    exp_ms = int(sub.get("panel_expiry_time") or 0)
    if exp_ms <= 0:
        return "نامحدود"
    remaining_ms = exp_ms - now_ms()
    if remaining_ms <= 0:
        return "منقضی"
    days = remaining_ms // 86_400_000
    if days >= 1:
        return f"{int(days)} روز"
    hours = remaining_ms // 3_600_000
    return f"{int(hours)} ساعت" if hours >= 1 else "کمتر از ۱ ساعت"


async def _sync_subscriptions_snapshot(panel, db: AsyncDatabase, subs: list[dict]) -> None:
    """Refresh the panel snapshot (status / remaining / expiry) for the visible
    subs so 'my configs' is accurate. find_subscription reuses ONE cached
    inbounds fetch, and recently-synced rows are skipped — so this is cheap.
    Updates each row dict in place; never raises."""
    if panel is None:
        return
    now_s = now_ts()
    # PasarGuard subs live on a different backend — never look them up in 3x-ui
    # (they are refreshed by _sync_pg_snapshot instead).
    stale = [
        s for s in subs
        if now_s - int(s.get("panel_synced_at") or 0) > 60
        and str(s.get("sub_id") or "").strip()
        and not is_pg_subscription(s)
    ]
    if not stale:
        return

    async def _one(s: dict) -> None:
        try:
            detail = await panel.find_subscription(str(s.get("sub_id") or ""))
            if not detail:
                return
            await db.update_subscription_panel_snapshot(detail)
            s["panel_total_bytes"] = int(detail.total_bytes or 0)
            s["panel_remaining_bytes"] = int(detail.remaining_bytes or 0)
            s["panel_enabled"] = 1 if detail.enabled else 0
            s["panel_expiry_time"] = int(detail.expiry_time or 0)
            s["panel_synced_at"] = now_s
        except Exception:
            LOG.debug("my-subs snapshot sync failed sub_id=%s", s.get("sub_id"), exc_info=True)

    await asyncio.gather(*[_one(s) for s in stale])


async def _sync_pg_snapshot(context: ContextTypes.DEFAULT_TYPE, subs: list[dict]) -> None:
    """Refresh the visible PasarGuard subs from their own panel so «اشتراک‌های من»
    shows live volume/remaining/expiry for them too. Updates the row dicts in
    place and never raises."""
    db: AsyncDatabase = context.application.bot_data["db"]
    now_s = now_ts()
    stale = [
        s for s in subs
        if is_pg_subscription(s)
        and str(s.get("sub_id") or "").strip()
        and now_s - int(s.get("panel_synced_at") or 0) > 60
    ]
    if not stale or not await pg_configured(db):
        return
    client = await get_pg_client(context)
    if client is None:
        return
    provisioning: ProvisioningService = context.application.bot_data["provisioning"]

    async def _one(s: dict) -> None:
        try:
            pg_user = await client.get_user(str(s.get("sub_id") or ""))
            if not pg_user:
                return
            await provisioning._sync_pg_subscription_row(str(s.get("sub_id")), pg_user)
            total = int(pg_user.get("data_limit") or 0)
            used = int(pg_user.get("used_traffic") or 0)
            s["panel_total_bytes"] = total
            s["panel_used_bytes"] = used
            s["panel_remaining_bytes"] = max(0, total - used) if total > 0 else 0
            s["panel_enabled"] = 1 if str(pg_user.get("status") or "active") in {"active", "on_hold"} else 0
            s["panel_expiry_time"] = int(_pg_iso_to_epoch(pg_user.get("expire")) * 1000)
            s["panel_synced_at"] = now_s
            s["_pg_status"] = str(pg_user.get("status") or "")
        except Exception:
            LOG.debug("PasarGuard my-subs sync failed sub_id=%s", s.get("sub_id"), exc_info=True)

    await asyncio.gather(*[_one(s) for s in stale])


def pg_status_label(sub: dict) -> str:
    """Human status for a PasarGuard service (falls back to the shared 3x-ui
    style label when the panel status is unknown)."""
    return {
        "active": "فعال",
        "on_hold": "در انتظار اولین اتصال",
        "limited": "اتمام حجم",
        "expired": "منقضی",
        "disabled": "غیرفعال",
    }.get(str(sub.get("_pg_status") or ""), subscription_status_label(sub))


async def render_my_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int, *, new_card: bool) -> None:
    db: AsyncDatabase = context.application.bot_data["db"]
    page_size = 8
    total = await db.get_user_subscription_count(update.effective_user.id)
    if total <= 0:
        await new_flow_card(update, context, "📭 شما هنوز هیچ اشتراکی ندارید.", back_keyboard())
        return

    total_pages = max(1, (total + page_size - 1) // page_size)
    safe_page = min(max(0, int(page)), total_pages - 1)
    subs = await db.get_user_subscription_page(update.effective_user.id, safe_page, page_size)
    # Refresh the visible page from the panels so status/remaining/expiry are live.
    await _sync_subscriptions_snapshot(context.application.bot_data.get("panel"), db, subs)
    await _sync_pg_snapshot(context, subs)
    lines = [
        "📋 <b>اشتراک‌های من</b>",
        f"صفحه <b>{safe_page + 1}</b> از <b>{total_pages}</b> | مجموع: <b>{total}</b>",
        "",
    ]
    pg_buttons: list[InlineKeyboardButton] = []
    for idx, sub in enumerate(subs, start=safe_page * page_size + 1):
        name = html.escape(str(sub.get("client_email") or "بدون نام"))
        sub_id = html.escape(str(sub.get("sub_id") or ""))
        is_test = int(sub.get("is_test") or 0) == 1
        is_infinite = int(sub.get("is_infinite") or 0) == 1
        is_pg = is_pg_subscription(sub)
        # The infinite check comes FIRST: an infinite package is sold as
        # "unlimited", so its cap must stay hidden on either backend.
        if is_infinite:
            sub_enabled = sub.get("panel_enabled")
            total_b = int(sub.get("panel_total_bytes") or 0)
            remain_b = int(sub.get("panel_remaining_bytes") or 0)
            depleted = (sub_enabled is not None and int(sub_enabled) == 0) or (total_b > 0 and remain_b <= 0)
            status = "غیرفعال (پایان حجم)" if depleted else "فعال"
            lines.append(
                f"{idx}. <b>{name}</b> | ♾️ بی‌نهایت\n"
                f"   🆔 <code>{sub_id}</code>\n"
                f"   ♾️ بسته‌ی بی‌نهایت | ⏳ زمان باقی‌مانده: {subscription_expiry_label(sub)} | وضعیت: {status}"
            )
            if depleted:
                lines.append("   ♾️ این کانفیگ به پایان حجم خود رسیده و غیرفعال شده است.")
            if is_pg:
                # PasarGuard links are token-based and fetched live, so offer the
                # re-delivery button for these too.
                raw_name = str(sub.get("client_email") or sub.get("sub_id") or "")
                pg_buttons.append(
                    InlineKeyboardButton(f"🔗 لینک {raw_name[:18]}", callback_data=f"pgsub:link:{sub.get('sub_id')}")
                )
            continue
        if is_pg:
            # PasarGuard service: the connection link is token-based and fetched
            # live, so we show the live numbers plus a button to (re)deliver it.
            gb_label = f"{int(sub.get('gb') or 0)} گیگ" if not is_test else f"{int(sub.get('panel_total_bytes') or 0) / (1024 ** 2):.0f} MB"
            lines.append(
                f"{idx}. <b>{name}</b> | 🌐 سرور اختصاصی{' | 🧪 تست' if is_test else ''}\n"
                f"   📦 حجم: {gb_label} | باقی‌مانده: {subscription_remaining_label(sub)}\n"
                f"   ⏳ زمان باقی‌مانده: {subscription_expiry_label(sub)} | وضعیت: {pg_status_label(sub)}"
            )
            raw_name = str(sub.get("client_email") or sub.get("sub_id") or "")
            pg_buttons.append(
                InlineKeyboardButton(f"🔗 لینک {raw_name[:18]}", callback_data=f"pgsub:link:{sub.get('sub_id')}")
            )
            continue

        if is_test:
            total_bytes = int(sub.get("panel_total_bytes") or 0)
            volume_label = f"{total_bytes / (1024 ** 2):.0f} MB"
            type_label = " | 🧪 تست"
        else:
            volume_label = f"{int(sub.get('gb') or 0)} گیگ"
            type_label = ""
        lines.append(
            f"{idx}. <b>{name}</b>{type_label}\n"
            f"   🆔 <code>{sub_id}</code>\n"
            f"   📦 حجم: {int(sub.get('gb') or 0)} گیگ | باقی‌مانده: {subscription_remaining_label(sub)}\n"
            f"   ⏳ زمان باقی‌مانده: {subscription_expiry_label(sub)} | وضعیت: {subscription_status_label(sub)}"
        )

        if is_test:
            lines.append(f"   🧪 نوع: تست | حجم واقعی: {volume_label}")

    nav: list[InlineKeyboardButton] = []
    if safe_page > 0:
        nav.append(InlineKeyboardButton("قبلی", callback_data=f"subs:page:{safe_page - 1}"))
    if safe_page < total_pages - 1:
        nav.append(InlineKeyboardButton("بعدی", callback_data=f"subs:page:{safe_page + 1}"))
    rows = []
    for pg_button in pg_buttons:
        rows.append([pg_button])
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("بازگشت به منو", callback_data="menu:main")])
    markup = InlineKeyboardMarkup(rows)
    if new_card:
        await new_flow_card(update, context, "\n\n".join(lines), markup)
    else:
        await edit_flow_query(update, context, "\n\n".join(lines), markup)


async def my_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update, context)
    if update.callback_query:
        await update.callback_query.answer()
    await render_my_subscriptions(update, context, 0, new_card=True)


async def my_subscriptions_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update, context)
    page = int(update.callback_query.data.rsplit(":", 1)[1])
    await render_my_subscriptions(update, context, page, new_card=False)


async def pgsub_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-deliver a PasarGuard service's subscription link (fetched live by
    username). Ownership is enforced via get_subscription_for_user."""
    query = update.callback_query
    await query.answer()
    await ensure_user(update, context)
    sub_id = query.data.split(":", 2)[2]
    db: AsyncDatabase = context.application.bot_data["db"]
    sub = await db.get_subscription_for_user(update.effective_user.id, sub_id)
    if not sub or not is_pg_subscription(sub):
        await _answer_query(query, "این سرویس پیدا نشد.", show_alert=True)
        return
    client = await get_pg_client(context)
    if client is None:
        await _answer_query(query, "سرور در حال حاضر در دسترس نیست.", show_alert=True)
        return
    try:
        pg_user = await client.get_user(sub_id)
    except Exception:
        LOG.exception("pgsub_link get_user failed sub_id=%s", sub_id)
        await _answer_query(query, "خطا در دریافت لینک. بعداً تلاش کنید.", show_alert=True)
        return
    sub_url = str((pg_user or {}).get("subscription_url") or "")
    if not sub_url:
        await _answer_query(query, "لینک این سرویس یافت نشد. با پشتیبانی تماس بگیرید.", show_alert=True)
        return
    qr: QRService = context.application.bot_data["qr"]
    png = await qr.png(sub_url)
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=BytesIO(png),
        caption=(
            "🌐 <b>سرور اختصاصی</b>\n\n"
            "🔗 <b>لینک اشتراک شما:</b>\n"
            f"<code>{html.escape(sub_url)}</code>\n\n"
            "📲 این لینک را در اپلیکیشن خود وارد کنید یا QR را اسکن کنید."
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=back_keyboard(),
    )


def renewal_label(sub: dict) -> str:
    name = str(sub.get("client_email") or "بدون نام")
    gb = int(sub.get("gb") or 0)
    remaining = int(sub.get("panel_remaining_bytes") or 0)
    if remaining > 0:
        remain_gb = round(remaining / (1024**3), 2)
        return f"{name} | {gb}GB | مانده {remain_gb}GB"
    return f"{name} | {gb}GB"


async def render_renewal_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int, *, new_card: bool = False) -> int:
    db: AsyncDatabase = context.application.bot_data["db"]
    renewal = context.user_data.setdefault("renewal", {})
    query = str(renewal.get("query") or "").strip()
    page_size = 5
    total = await db.search_renewable_subscriptions_count(update.effective_user.id, query)
    if total <= 0 and not query:
        await new_flow_card(update, context, "📭 هنوز اشتراکی برای تمدید ندارید.", back_keyboard())
        return ConversationHandler.END

    if total <= 0:
        rows = [
            [InlineKeyboardButton("🔎 جستجوی جدید", callback_data="renew:search")],
            [InlineKeyboardButton("پاک کردن جستجو", callback_data="renew:clear_search")],
            [InlineKeyboardButton("بازگشت به منو", callback_data="menu:main")],
        ]
        text = (
            "🔁 <b>تمدید اشتراک</b>\n\n"
            f"برای جستجوی <code>{html.escape(query)}</code> هیچ کانفیگی پیدا نشد.\n"
            "نام کانفیگ یا بخشی از شناسه اشتراک را دقیق‌تر وارد کنید."
        )
        if new_card:
            await new_flow_card(update, context, text, InlineKeyboardMarkup(rows))
        elif update.callback_query:
            await edit_flow_query(update, context, text, InlineKeyboardMarkup(rows))
        else:
            await send_flow_prompt(update, context, text, InlineKeyboardMarkup(rows))
        return RENEW_SELECT

    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(0, int(page)), total_pages - 1)
    renewal["page"] = page
    page_rows = await db.search_renewable_subscriptions(update.effective_user.id, query, page, page_size)
    rows: list[list[InlineKeyboardButton]] = []
    for sub in page_rows:
        sub_id = str(sub["sub_id"])
        rows.append([InlineKeyboardButton(renewal_label(sub), callback_data=f"renew:sub:{sub_id}")])
    rows.append([InlineKeyboardButton("🔎 جستجوی کانفیگ", callback_data="renew:search")])
    if query:
        rows.append([InlineKeyboardButton("پاک کردن جستجو", callback_data="renew:clear_search")])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("قبلی", callback_data=f"renew:page:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("بعدی", callback_data=f"renew:page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("بازگشت به منو", callback_data="menu:main")])
    text = (
        "🔁 <b>تمدید اشتراک</b>\n\n"
        "اشتراکی که می‌خواهید حجمش افزایش پیدا کند را انتخاب کنید.\n"
        f"صفحه <b>{page + 1}</b> از <b>{total_pages}</b>"
    )
    if query:
        text += f"\n🔎 جستجو: <code>{html.escape(query)}</code> | نتیجه: <b>{total}</b>"
    else:
        text += f"\nمجموع قابل تمدید: <b>{total}</b>"
    if new_card:
        await new_flow_card(update, context, text, InlineKeyboardMarkup(rows))
    elif update.callback_query:
        await edit_flow_query(update, context, text, InlineKeyboardMarkup(rows))
    else:
        await send_flow_prompt(update, context, text, InlineKeyboardMarkup(rows))
    return RENEW_SELECT


async def renew_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await ensure_user(update, context)
    db: AsyncDatabase = context.application.bot_data["db"]
    if not await audience_sales_is_open(db, update.effective_user.id):
        await show_sales_closed(update, context)
        return ConversationHandler.END
    await remove_keyboard(context, update.effective_chat.id, context.user_data.get(FLOW_PROMPT_KEY))
    clear_flow_state(context)
    context.user_data["renewal"] = {}
    return await render_renewal_list(update, context, 0, new_card=True)


async def renew_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    page = int(update.callback_query.data.rsplit(":", 1)[1])
    return await render_renewal_list(update, context, page)


async def renew_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await edit_flow_query(
        update,
        context,
        "🔎 <b>جستجوی سریع کانفیگ برای تمدید</b>\n\n"
        "نام کانفیگ یا چند کاراکتر از شناسه اشتراک را ارسال کنید.\n"
        "مثال: <code>ali</code> یا <code>Xq4A</code>",
        InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("بازگشت به لیست", callback_data="renew:page:0")],
                [InlineKeyboardButton("انصراف", callback_data="renew:cancel")],
            ]
        ),
    )
    return RENEW_SEARCH


async def renew_search_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = (update.effective_message.text or "").strip()
    if len(query) < 2:
        await send_flow_prompt(
            update,
            context,
            "🔎 <b>جستجوی سریع کانفیگ</b>\n\n"
            "برای جستجوی دقیق‌تر حداقل ۲ کاراکتر از نام کانفیگ یا شناسه اشتراک را وارد کنید.",
            InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("بازگشت به لیست", callback_data="renew:page:0")],
                    [InlineKeyboardButton("انصراف", callback_data="renew:cancel")],
                ]
            ),
        )
        return RENEW_SEARCH
    context.user_data.setdefault("renewal", {})["query"] = query[:64]
    return await render_renewal_list(update, context, 0, new_card=False)


async def renew_clear_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("renewal", {}).pop("query", None)
    return await render_renewal_list(update, context, 0)


async def renew_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    sub_id = query.data.split(":", 2)[2]
    db: AsyncDatabase = context.application.bot_data["db"]
    sub = await db.get_subscription_for_user(update.effective_user.id, sub_id)
    if not sub:
        await edit_text(query, "⚠️ این اشتراک پیدا نشد. لطفاً دوباره از لیست انتخاب کنید.", back_keyboard())
        return ConversationHandler.END
    name = str(sub.get("client_email") or sub_id)
    if int(sub.get("is_infinite") or 0) == 1:
        # An infinite package has a fixed price and its own cap — it is re-bought,
        # not topped up per GB. (Guards a stale callback: the list already hides them.)
        await edit_text(
            query,
            "♾️ <b>این سرویس بسته‌ی بی‌نهایت است.</b>\n\n"
            "تمدید حجمی برای آن انجام نمی‌شود؛ می‌توانید از منو یک بسته‌ی بی‌نهایت جدید تهیه کنید.",
            back_keyboard(),
        )
        return ConversationHandler.END
    is_pg = is_pg_subscription(sub)
    if is_pg and not await pg_configured(db):
        await edit_text(
            query,
            "🌐 <b>این سرویس روی سرور اختصاصی است.</b>\n\n"
            "تمدید آن در حال حاضر ممکن نیست؛ لطفاً با پشتیبانی در ارتباط باشید.",
            back_keyboard(),
        )
        return ConversationHandler.END
    min_gb = await minimum_purchase_gb(db)
    context.user_data["renewal"] = {"sub_id": sub_id, "client_name": name, "is_pg": is_pg}
    await edit_text(
        query,
        "🔁 <b>تمدید اشتراک</b>\n\n"
        f"کانفیگ انتخابی: <b>{html.escape(name)}</b>{' | 🌐 سرور اختصاصی' if is_pg else ''}\n"
        f"شناسه: <code>{html.escape(sub_id)}</code>\n\n"
        f"چه مقدار حجم اضافه شود؟\nحداقل حجم مجاز: <b>{min_gb}</b> گیگ",
        await gb_choice_keyboard(db, update.effective_user.id, "renew", "renew:cancel", min_gb),
    )
    context.user_data[FLOW_PROMPT_KEY] = query.message.message_id
    return RENEW_GB


async def renew_gb_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: AsyncDatabase = context.application.bot_data["db"]
    min_gb = await minimum_purchase_gb(db)
    await send_flow_prompt(
        update,
        context,
        "📦 لطفاً حجم تمدید را از دکمه‌ها انتخاب کنید.\n\n"
        f"حداقل حجم مجاز: <b>{min_gb}</b> گیگ. برای عدد دلخواه، گزینه <b>حجم دلخواه</b> را بزنید.",
        await gb_choice_keyboard(db, update.effective_user.id, "renew", "renew:cancel", min_gb),
    )
    return RENEW_GB


async def build_renew_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: AsyncDatabase = context.application.bot_data["db"]
    renewal = context.user_data.setdefault("renewal", {})
    agent = await db.get_agent(update.effective_user.id)
    gb = int(renewal.get("gb") or 0)
    unit_price = await unit_price_for_gb(db, update.effective_user.id, gb, agent)
    min_gb = await minimum_purchase_gb(db)
    if gb < min_gb:
        renewal.pop("gb", None)
        await send_flow_prompt(
            update,
            context,
            invalid_gb_text(min_gb, renewal=True),
            await gb_choice_keyboard(db, update.effective_user.id, "renew", "renew:cancel", min_gb),
        )
        return RENEW_GB
    total = gb * unit_price
    method_label = (
        "دسترسی باز نمایندگی (ثبت روی اعتبار)"
        if agent and db.normalize_agent_access_value(agent["access_level"]) == "open"
        else "کسر از کیف پول"
    )
    renewal["idem"] = f"renew-{update.effective_user.id}-{secrets.token_hex(8)}"
    renewal.update(unit_price=unit_price, total=total, method_label=method_label)
    await send_flow_prompt(
        update,
        context,
        "🧾 <b>تایید تمدید اشتراک</b>\n\n"
        f"🪪 کانفیگ: <b>{html.escape(str(renewal.get('client_name') or 'بدون نام'))}</b>\n"
        f"📦 حجم افزایشی: <b>{gb}</b> گیگ\n"
        f"💵 قیمت هر گیگ: <b>{unit_price:,}</b> تومان\n"
        f"💰 مبلغ کل: <b>{total:,}</b> تومان\n"
        f"💳 روش پرداخت: <b>{method_label}</b>\n\n"
        "در صورت تایید، حجم به اشتراک انتخاب‌شده اضافه می‌شود.",
        renew_confirm_keyboard(),
    )
    return RENEW_CONFIRM


async def renew_gb_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    db: AsyncDatabase = context.application.bot_data["db"]
    min_gb = await minimum_purchase_gb(db)
    gb = int(query.data.rsplit(":", 1)[1])
    if gb < min_gb:
        await edit_text(query, invalid_gb_text(min_gb, renewal=True), await gb_choice_keyboard(db, update.effective_user.id, "renew", "renew:cancel", min_gb))
        context.user_data[FLOW_PROMPT_KEY] = query.message.message_id
        return RENEW_GB
    context.user_data.setdefault("renewal", {})["gb"] = gb
    return await build_renew_invoice(update, context)


async def renew_custom_gb_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: AsyncDatabase = context.application.bot_data["db"]
    min_gb = await minimum_purchase_gb(db)
    await edit_flow_query(
        update,
        context,
        custom_gb_prompt("حجم دلخواه تمدید", min_gb),
        InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="renew:cancel")]]),
    )
    return RENEW_CUSTOM_GB


async def renew_custom_gb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.effective_message.text or "").strip()
    db: AsyncDatabase = context.application.bot_data["db"]
    min_gb = await minimum_purchase_gb(db)
    max_gb = max_custom_gb(min_gb)
    if not text.isdigit() or not (min_gb <= int(text) <= max_gb):
        await send_flow_prompt(update, context, invalid_gb_text(min_gb, renewal=True))
        return RENEW_CUSTOM_GB
    context.user_data.setdefault("renewal", {})["gb"] = int(text)
    return await build_renew_invoice(update, context)


async def renew_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    clear_flow_state(context)
    await edit_text(update.callback_query, "❌ تمدید اشتراک لغو شد.", back_keyboard())
    return ConversationHandler.END


async def renew_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    renewal = context.user_data.get("renewal") or {}
    sub_id = str(renewal.get("sub_id") or "")
    gb = int(renewal.get("gb") or 0)
    unit_price = int(renewal.get("unit_price") or 0)
    total = int(renewal.get("total") or 0)
    method_label = str(renewal.get("method_label") or "کسر از کیف پول")
    if not sub_id or gb <= 0 or unit_price <= 0:
        clear_flow_state(context)
        await edit_text(query, "⚠️ اطلاعات تمدید کامل نیست. لطفاً دوباره شروع کنید.", back_keyboard())
        return ConversationHandler.END

    db: AsyncDatabase = context.application.bot_data["db"]
    min_gb = await minimum_purchase_gb(db)
    if gb < min_gb:
        renewal.pop("gb", None)
        await edit_text(
            query,
            invalid_gb_text(min_gb, renewal=True),
            await gb_choice_keyboard(db, update.effective_user.id, "renew", "renew:cancel", min_gb),
        )
        context.user_data[FLOW_PROMPT_KEY] = query.message.message_id
        return RENEW_GB
    if not await audience_sales_is_open(db, update.effective_user.id):
        clear_flow_state(context)
        await edit_text(
            query,
            "🔒 <b>فروش سرویس موقتاً بسته است.</b>\n\n"
            "این تمدید ثبت نشد و هیچ مبلغی کسر نشده است.",
            back_keyboard(),
        )
        return ConversationHandler.END

    provisioning: ProvisioningService = context.application.bot_data["provisioning"]
    await edit_flow_query(update, context, "⏳ <b>در حال تمدید اشتراک...</b>\n\nلطفاً چند لحظه صبر کنید.")
    try:
        if renewal.get("is_pg"):
            pg_client = await get_pg_client(context)
            if pg_client is None:
                raise ValueError("اتصال به سرور برقرار نشد. لطفاً با پشتیبانی تماس بگیرید.")
            sub_link = await provisioning.process_pg_renewal(
                pg_client=pg_client,
                user_id=update.effective_user.id,
                sub_id=sub_id,
                gb=gb,
                unit_price=unit_price,
                final_total=total,
                days=await pg_purchase_days(db),
                idempotency_key=str(renewal.get("idem") or query.id),
            )
        else:
            sub_link = await provisioning.process_renewal(
                user_id=update.effective_user.id,
                sub_id=sub_id,
                gb=gb,
                unit_price=unit_price,
                final_total=total,
                idempotency_key=str(renewal.get("idem") or query.id),
            )
    except ValueError as exc:
        clear_flow_state(context)
        await edit_text(
            query,
            f"⚠️ <b>تمدید انجام نشد.</b>\n\n{html.escape(str(exc))}",
            InlineKeyboardMarkup([[InlineKeyboardButton("💳 شارژ کیف پول", callback_data="menu:wallet")], [InlineKeyboardButton("بازگشت به منو", callback_data="menu:main")]]),
        )
        return ConversationHandler.END
    except Exception as exc:
        if "duplicate renewal request" in str(exc):
            await _answer_query(query, "این تمدید در حال پردازش است…")
            return ConversationHandler.END
        LOG.exception("renewal failed user_id=%s sub_id=%s", update.effective_user.id, sub_id)
        clear_flow_state(context)
        await edit_text(query, f"❌ خطا در تمدید اشتراک:\n{html.escape(str(exc))}", back_keyboard())
        return ConversationHandler.END

    await edit_text(
        query,
        "✅ <b>تمدید با موفقیت انجام شد.</b>\n\n"
        f"📦 حجم اضافه‌شده: <b>{gb}</b> گیگ\n"
        f"💰 مبلغ ثبت‌شده: <b>{total:,}</b> تومان\n"
        f"🟢 روش ثبت: {html.escape(method_label)}\n\n"
        "🔗 لینک اشتراک شما:\n"
        f"<code>{html.escape(sub_link)}</code>",
        back_keyboard(),
    )
    return ConversationHandler.END


async def tariffs_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update, context)
    if update.callback_query:
        await update.callback_query.answer()
    db: AsyncDatabase = context.application.bot_data["db"]
    agent = await db.get_agent(update.effective_user.id)
    agent_price = int(agent["price_per_gb"] or 0) if agent else 0
    pg_unit = await pg_unit_price_override(db)
    tiers = await get_price_tiers(db)
    # A PasarGuard flat rate replaces the tier table — showing stale brackets
    # that nobody is charged from would be misleading.
    if agent_price <= 0 and pg_unit <= 0 and tiers:
        lines = [
            "❄️ <b>تعرفه پلکانی سرویس‌ها</b>",
            "هر چه بیشتر بخرید، هر گیگ ارزان‌تر! 📉",
            "",
            "┏━━━━━━━━━━━━━━━━━━",
        ]
        for idx, tier in enumerate(tiers):
            nxt = tiers[idx + 1]["min_gb"] if idx + 1 < len(tiers) else None
            if nxt is not None:
                rng = f"{tier['min_gb']} تا {nxt - 1} گیگ"
            else:
                rng = f"{tier['min_gb']} گیگ به بالا"
            lines.append(f"┃ 📦 <b>{rng}</b>")
            lines.append(f"┃     هر گیگ: <b>{tier['price_per_gb']:,}</b> تومان")
            if idx < len(tiers) - 1:
                lines.append("┃")
        lines.append("┗━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("💳 مبلغ نهایی = حجم انتخابی × قیمت همان پله")
        await new_flow_card(update, context, "\n".join(lines), back_keyboard())
        return
    unit_price = await effective_unit_price(db, update.effective_user.id, agent)
    # Volume purchases carry a validity window (default 30 days) — the tariff
    # card used to promise "no time limit", which stopped being true.
    duration_days = await pg_purchase_days(db) if await pg_is_primary(db) else _positive_int(
        await db.get_setting("purchase_duration_days", "30"), 30
    )
    validity_line = (
        f"<i>پرداخت بر اساس حجم مصرفی — اعتبار زمانی هر سرویس {duration_days} روز است.</i>"
        if duration_days > 0
        else "<i>پرداخت بر اساس حجم مصرفی — بدون محدودیت زمانی.</i>"
    )
    await new_flow_card(
        update,
        context,
        "🏷 <b>تعرفه سرویس‌ها</b>\n\n"
        f"💎 قیمت هر گیگابایت برای شما: <b>{unit_price:,}</b> تومان\n\n"
        f"{validity_line}",
        back_keyboard(),
    )


async def support_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update, context)
    if update.callback_query:
        await update.callback_query.answer()
    db: AsyncDatabase = context.application.bot_data["db"]
    support_id = await db.get_setting("support_id", "@Admin")
    await new_flow_card(
        update,
        context,
        "🛟 <b>پشتیبانی ElsaVPN</b>\n\n"
        f"برای ارتباط مستقیم با تیم پشتیبانی به آیدی زیر پیام دهید:\n👈 {html.escape(support_id)}\n\n"
        "<i>پاسخگوی شما هستیم — سریع و دقیق.</i>",
        back_keyboard(),
    )


async def agent_test_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update, context)
    query = update.callback_query
    if query:
        await query.answer()
    db: AsyncDatabase = context.application.bot_data["db"]
    provisioning: ProvisioningService = context.application.bot_data["provisioning"]
    qr: QRService = context.application.bot_data["qr"]
    quota = await provisioning.test_config_quota(update.effective_user.id)
    if not quota["enabled"]:
        await new_flow_card(
            update, context,
            "🆓 <b>کانفیگ تست</b>\n\nدریافت کانفیگ تست در حال حاضر غیرفعال است.",
            back_keyboard(),
        )
        return
    if quota["remaining"] <= 0:
        await new_flow_card(
            update, context,
            "🆓 <b>کانفیگ تست</b>\n\n"
            + (
                "سهمیه کانفیگ تست امروز شما تمام شده است. فردا دوباره تلاش کنید."
                if quota["scope"] == "daily"
                else "شما قبلاً کانفیگ تست خود را دریافت کرده‌اید و امکان دریافت مجدد وجود ندارد."
            ),
            back_keyboard(),
        )
        return
    await new_flow_card(
        update,
        context,
        "⏳ <b>در حال ساخت کانفیگ تست...</b>\n\n"
        f"حجم تست {quota['mb']} مگابایت و اعتبار زمانی آن {quota['minutes']} دقیقه است.",
    )
    idem_key = query.id if query else f"test-{update.effective_user.id}-{update.effective_message.message_id}"
    # The test config must be created on whichever panel is actually selling —
    # otherwise it silently hits 3x-ui on a PasarGuard-primary deployment.
    pg_client = None
    group_ids: list[int] = []
    if await pg_is_primary(db):
        pg_client = await get_pg_client(context)
        if pg_client is not None:
            group_ids = await pg_group_ids(db, pg_client)
            if not group_ids:
                await send_flow_prompt(
                    update,
                    context,
                    "⚠️ <b>کانفیگ تست ساخته نشد.</b>\n\nگروه سرور پیدا نشد؛ لطفاً با پشتیبانی تماس بگیرید.",
                    back_keyboard(),
                )
                return
    try:
        sub_link = await provisioning.process_test_config(
            user_id=update.effective_user.id,
            pg_client=pg_client,
            group_ids=group_ids,
            idempotency_key=idem_key,
        )
    except ValueError as exc:
        await send_flow_prompt(
            update,
            context,
            f"⚠️ <b>کانفیگ تست ساخته نشد.</b>\n\n{html.escape(str(exc))}",
            back_keyboard(),
        )
        return
    except Exception as exc:
        LOG.exception("agent test config failed user_id=%s", update.effective_user.id)
        await send_flow_prompt(
            update,
            context,
            f"❌ <b>خطا در ساخت کانفیگ تست.</b>\n\n<code>{html.escape(str(exc))}</code>",
            back_keyboard(),
        )
        return

    left = max(0, int(quota["remaining"]) - 1)
    if quota["scope"] == "daily":
        left_line = f"🎟 سهمیه امروز شما: <b>{left}</b> عدد باقی‌مانده\n"
    else:
        left_line = "" if left > 0 else "🎟 این تنها کانفیگ تست شما بود.\n"
        if left > 0:
            left_line = f"🎟 <b>{left}</b> کانفیگ تست دیگر می‌توانید دریافت کنید.\n"
    await send_flow_prompt(
        update,
        context,
        "✅ <b>کانفیگ تست آماده شد.</b>\n\n"
        f"📦 حجم: <b>{quota['mb']}</b> مگابایت\n"
        f"⏱ اعتبار: <b>{quota['minutes']}</b> دقیقه\n"
        f"{left_line}\n"
        "لینک و QR Code در پیام بعدی ارسال می‌شود.",
        back_keyboard(),
    )
    png = await qr.png(sub_link)
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=BytesIO(png),
        caption=(
            "🧪 <b>کانفیگ تست</b>\n\n"
            f"📦 حجم: <b>{quota['mb']}</b> مگابایت\n"
            f"⏱ اعتبار: <b>{quota['minutes']}</b> دقیقه\n\n"
            f"🔗 <b>لینک اشتراک:</b>\n<code>{html.escape(sub_link)}</code>\n\n"
            "این لینک را در اپلیکیشن خود وارد کنید یا QR را اسکن کنید."
        ),
        parse_mode=ParseMode.HTML,
    )


async def wallet_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ensure_user(update, context)
    if update.callback_query:
        await update.callback_query.answer()
    db: AsyncDatabase = context.application.bot_data["db"]
    wallet = await db.get_wallet_balance(update.effective_user.id)
    unit_price = await effective_unit_price(db, update.effective_user.id)
    await new_flow_card(
        update,
        context,
        "💳 <b>کیف پول شما</b>\n\n"
        f"موجودی فعلی: <b>{wallet:,}</b> تومان\n"
        f"مبنای شارژ پیشنهادی: <b>{unit_price:,}</b> تومان",
        wallet_keyboard(),
    )


async def _topup_uses_tiered_pricing(db: AsyncDatabase, user_id: int) -> bool:
    """True when volume tiers are active for this user (so a single per-GB
    suggestion is meaningless and the top-up should ask for a free amount)."""
    agent = await db.get_agent(user_id)
    agent_price = int(agent["price_per_gb"] or 0) if agent else 0
    if agent_price > 0:
        return False
    return bool(await get_price_tiers(db))


async def topup_start(update: Update, context: ContextTypes.DEFAULT_TYPE, method: str) -> int:
    await ensure_user(update, context)
    db: AsyncDatabase = context.application.bot_data["db"]
    unit_price = await effective_unit_price(db, update.effective_user.id)
    await remove_keyboard(context, update.effective_chat.id, context.user_data.get(FLOW_PROMPT_KEY))
    clear_flow_state(context)
    tier_mode = await _topup_uses_tiered_pricing(db, update.effective_user.id)
    context.user_data["topup"] = {"method": method, "unit_price": unit_price, "tier_mode": tier_mode}
    method_label = "کارت به کارت" if method == "card" else "رمزارز (تتر)"
    if tier_mode:
        # Tiered pricing → no fixed per-GB amount to suggest; ask for a free
        # amount in Toman and let the user type it.
        await new_flow_card(
            update,
            context,
            f"💳 <b>شارژ کیف پول - {method_label}</b>\n\n"
            "💰 مبلغ مورد نظر را به تومان وارد کنید.\n"
            "<i>مثال: 200000 تومان</i>\n\n"
            "فقط عدد ارسال کنید 👇",
            InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="topup:cancel")]]),
        )
        return TOPUP_CUSTOM_AMOUNT
    await new_flow_card(
        update,
        context,
        f"💳 <b>شارژ کیف پول - {method_label}</b>\n\n"
        "ابتدا مبلغ شارژ را انتخاب کنید. مبلغ‌ها بر اساس تعرفه فعال حساب شما ساخته شده‌اند.",
        topup_amount_keyboard(method, unit_price),
    )
    return TOPUP_AMOUNT


async def topup_c2c_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await topup_start(update, context, "card")


async def topup_crypto_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await topup_start(update, context, "crypto")


async def topup_amount_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _, _, method, amount_text = query.data.split(":", 3)
    amount = int(amount_text)
    context.user_data.setdefault("topup", {})["method"] = method
    context.user_data.setdefault("topup", {})["amount"] = amount
    await edit_text(
        query,
        "🧾 <b>تایید مبلغ شارژ</b>\n\n"
        f"مبلغ انتخابی: <b>{amount:,}</b> تومان\n\n"
        "پس از تایید مبلغ، اطلاعات پرداخت نمایش داده می‌شود.",
        topup_amount_confirm_keyboard(),
    )
    context.user_data[FLOW_PROMPT_KEY] = query.message.message_id
    return TOPUP_AMOUNT_CONFIRM


async def topup_custom_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _, _, method = query.data.split(":", 2)
    context.user_data.setdefault("topup", {})["method"] = method
    await edit_text(query, "✍️ <b>مبلغ دلخواه</b>\n\nمبلغ شارژ را به تومان و فقط عددی ارسال کنید.")
    context.user_data[FLOW_PROMPT_KEY] = query.message.message_id
    return TOPUP_CUSTOM_AMOUNT


async def topup_custom_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.effective_message.text or "").replace(",", "").strip()
    if not text.isdigit() or int(text) <= 0:
        await send_flow_prompt(update, context, "⚠️ مبلغ معتبر نیست.\n\nمبلغ شارژ را به تومان و فقط عددی ارسال کنید.")
        return TOPUP_CUSTOM_AMOUNT
    amount = int(text)
    context.user_data.setdefault("topup", {})["amount"] = amount
    await send_flow_prompt(
        update,
        context,
        "🧾 <b>تایید مبلغ شارژ</b>\n\n"
        f"مبلغ واردشده: <b>{amount:,}</b> تومان\n\n"
        "پس از تایید مبلغ، اطلاعات پرداخت نمایش داده می‌شود.",
        topup_amount_confirm_keyboard(),
    )
    return TOPUP_AMOUNT_CONFIRM


async def topup_amount_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = context.user_data.get("topup") or {}
    method = str(data.get("method") or "card")
    if data.get("tier_mode"):
        # Tiered pricing → re-ask for a free amount instead of fixed suggestions.
        await edit_flow_query(
            update,
            context,
            "💰 مبلغ مورد نظر را به تومان وارد کنید.\n"
            "<i>مثال: 200000 تومان</i>\n\n"
            "فقط عدد ارسال کنید 👇",
            InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="topup:cancel")]]),
        )
        return TOPUP_CUSTOM_AMOUNT
    unit_price = int(data.get("unit_price") or 0)
    if unit_price <= 0:
        unit_price = await effective_unit_price(context.application.bot_data["db"], update.effective_user.id)
    await edit_flow_query(
        update,
        context,
        "💳 <b>انتخاب مبلغ شارژ</b>\n\nمبلغ‌ها بر اساس تعرفه فعال حساب شما ساخته شده‌اند.",
        topup_amount_keyboard(method, unit_price),
    )
    return TOPUP_AMOUNT


async def topup_amount_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = context.user_data.get("topup") or {}
    method = str(data.get("method") or "")
    amount = int(data.get("amount") or 0)
    if not method or amount <= 0:
        await edit_text(query, "⚠️ مبلغ شارژ مشخص نیست. لطفاً دوباره شروع کنید.", back_keyboard())
        return ConversationHandler.END

    db: AsyncDatabase = context.application.bot_data["db"]
    if method == "card":
        card = await db.next_payment_card()
        card_number = card.get("number", "")
        card_name = card.get("name", "")
        await edit_text(
            query,
            "💳 <b>پرداخت کارت به کارت</b>\n\n"
            f"مبلغ قابل پرداخت: <b>{amount:,}</b> تومان\n\n"
            f"شماره کارت:\n<code>{html.escape(card_number)}</code>\n"
            f"به نام: <b>{html.escape(card_name)}</b>\n\n"
            "پس از واریز، فقط <b>عکس رسید</b> را ارسال کنید. رسید شما مستقیم در صف تایید مدیریت قرار می‌گیرد.",
            InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="topup:cancel")]]),
        )
        context.user_data[FLOW_PROMPT_KEY] = query.message.message_id
        return TOPUP_C2C_PHOTO

    address = await db.get_setting("crypto_address", "TRC20 Address Not Set")
    await edit_text(
        query,
        "🪙 <b>پرداخت با تتر</b>\n\n"
        f"معادل تومانی ثبت‌شده: <b>{amount:,}</b> تومان\n\n"
        f"آدرس پرداخت:\n<code>{html.escape(address)}</code>\n\n"
        "پس از انتقال، TXID یا Hash تراکنش را ارسال کنید.",
        InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="topup:cancel")]]),
    )
    context.user_data[FLOW_PROMPT_KEY] = query.message.message_id
    return TOPUP_CRYPTO_TXID


async def notify_admin_topup(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict) -> str:
    db: AsyncDatabase = context.application.bot_data["db"]
    method = str(data["method"])
    amount = int(data["amount"])
    topup_id = f"topup-{update.effective_user.id}-{secrets.token_hex(6)}"
    await db.create_wallet_topup_request(
        topup_id=topup_id,
        user_id=update.effective_user.id,
        method=method,
        amount_toman=amount,
        receipt_file_id=data.get("receipt_file_id"),
        txid=data.get("txid"),
    )
    admin_text = (
        "💳 <b>درخواست شارژ کیف پول</b>\n\n"
        f"شناسه: <code>{topup_id}</code>\n"
        f"کاربر: {user_identity_label(update.effective_user)}\n"
        f"روش: <b>{'کارت به کارت' if method == 'card' else 'تتر'}</b>\n"
        f"مبلغ: <b>{amount:,}</b> تومان"
    )
    if data.get("txid"):
        admin_text += f"\nTXID: <code>{html.escape(data['txid'])}</code>"
    sent = await notify_admins(
        context,
        text=admin_text,
        photo_file_id=data.get("receipt_file_id"),
        reply_markup=admin_decision_keyboard("topup_admin", topup_id),
    )
    if sent == 0:
        LOG.error("failed to notify all admins about wallet topup topup_id=%s", topup_id)
    return topup_id


async def topup_c2c_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.effective_message
    if not msg.photo:
        await send_flow_prompt(update, context, "⚠️ لطفاً فقط <b>عکس رسید</b> را ارسال کنید.")
        return TOPUP_C2C_PHOTO
    data = context.user_data.setdefault("topup", {})
    data["receipt_file_id"] = msg.photo[-1].file_id
    await notify_admin_topup(update, context, data)
    await send_chat_message(
        update,
        context,
        "✅ <b>رسید واریزی شما با موفقیت ثبت شد.</b>\n\n"
        "درخواست شما در صف بررسی مدیریت قرار گرفت.\n"
        "⚠️ کیف پول هنوز شارژ نشده و فقط بعد از تایید ادمین شارژ خواهد شد.",
        back_keyboard(),
    )
    return ConversationHandler.END


async def topup_crypto_txid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    txid = (update.effective_message.text or "").strip()
    if len(txid) < 8:
        await send_flow_prompt(update, context, "⚠️ TXID معتبر ارسال کنید.")
        return TOPUP_CRYPTO_TXID
    data = context.user_data.setdefault("topup", {})
    data["txid"] = txid
    await notify_admin_topup(update, context, data)
    await send_chat_message(
        update,
        context,
        "✅ <b>تراکنش شما با موفقیت ثبت شد.</b>\n\n"
        "درخواست شارژ در صف بررسی مدیریت قرار گرفت.\n"
        "⚠️ کیف پول هنوز شارژ نشده و فقط بعد از تایید ادمین شارژ خواهد شد.",
        back_keyboard(),
    )
    return ConversationHandler.END


async def topup_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    clear_flow_state(context)
    await edit_text(update.callback_query, "❌ درخواست شارژ لغو شد.", back_keyboard())
    return ConversationHandler.END


async def agent_request_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await ensure_user(update, context)
    await remove_keyboard(context, update.effective_chat.id, context.user_data.get(FLOW_PROMPT_KEY))
    clear_flow_state(context)
    context.user_data["agent_request"] = {}
    await new_flow_card(update, context, AGENT_REQUEST_TEXT, back_keyboard())
    return AGENT_TEXT


async def agent_request_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.effective_message
    if getattr(msg, "media_group_id", None):
        await send_flow_prompt(update, context, "⚠️ آلبوم یا چند عکس همزمان پذیرفته نمی‌شود. لطفاً یک پیام کامل ارسال کنید.")
        return AGENT_TEXT
    text = (msg.caption or msg.text or "").strip()
    photo_file_id = msg.photo[-1].file_id if msg.photo else None
    if not text:
        await send_flow_prompt(update, context, "لطفاً متن معرفی را ارسال کنید.")
        return AGENT_TEXT
    context.user_data["agent_request"] = {"text": text, "photo_file_id": photo_file_id}
    await send_flow_prompt(
        update,
        context,
        f"🧾 <b>پیش‌نمایش درخواست نمایندگی</b>\n\n{html.escape(text)}\n\nارسال برای مدیریت؟",
        InlineKeyboardMarkup(
            [[InlineKeyboardButton("✅ ارسال برای مدیریت", callback_data="agent:confirm"), InlineKeyboardButton("❌ انصراف", callback_data="agent:cancel")]]
        ),
    )
    return AGENT_CONFIRM


async def agent_request_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    clear_flow_state(context)
    await edit_text(update.callback_query, "❌ درخواست نمایندگی لغو شد.", back_keyboard())
    return ConversationHandler.END


async def agent_request_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = context.user_data.get("agent_request") or {}
    text = str(data.get("text") or "")
    if not text:
        clear_flow_state(context)
        await edit_text(query, "⚠️ متن درخواست پیدا نشد. لطفاً دوباره تلاش کنید.", back_keyboard())
        return ConversationHandler.END
    db: AsyncDatabase = context.application.bot_data["db"]
    req_id = f"agent-{update.effective_user.id}-{secrets.token_hex(5)}"
    await db.create_agent_request(req_id=req_id, user_id=update.effective_user.id, text=text, photo_file_id=data.get("photo_file_id"))
    admin_text = (
        "🤝 <b>درخواست نمایندگی</b>\n\n"
        f"شناسه: <code>{req_id}</code>\n"
        f"کاربر: {user_identity_label(update.effective_user)}\n\n"
        f"{html.escape(text)}"
    )
    sent = await notify_admins(
        context,
        text=admin_text,
        photo_file_id=data.get("photo_file_id"),
        reply_markup=agent_admin_decision_keyboard(req_id),
    )
    if sent == 0:
        LOG.error("failed to send agent request to all admins req_id=%s", req_id)
        clear_flow_state(context)
        await edit_text(query, "خطا در ارتباط با سرور مدیریت. لطفا بعدا تلاش کنید.", back_keyboard())
        return ConversationHandler.END
    await edit_text(query, "✅ درخواست شما برای مدیریت ارسال شد.", back_keyboard())
    return ConversationHandler.END


async def topup_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    if not await is_bot_admin(update, context):
        await query.answer("شما دسترسی مدیریت ندارید.", show_alert=True)
        return
    _, action, topup_id = query.data.split(":", 2)
    db: AsyncDatabase = context.application.bot_data["db"]
    topup = await db.get_wallet_topup(topup_id)
    if not topup:
        await query.answer("درخواست شارژ پیدا نشد.", show_alert=True)
        return

    changed = False
    if action == "approve":
        if int(topup["user_id"]) == int(update.effective_user.id):
            await query.answer("نمی‌توانید شارژ کیف پول خودتان را تایید کنید.", show_alert=True)
            return
        changed = await db.approve_wallet_topup(topup_id)
        if changed:
            await context.bot.send_message(
                chat_id=int(topup["user_id"]),
                text=(
                    "🎉 <b>شارژ کیف پول شما تایید شد.</b>\n\n"
                    f"مبلغ شارژشده: <b>{int(topup['amount_toman']):,}</b> تومان"
                ),
                parse_mode=ParseMode.HTML,
            )
    elif action == "reject":
        changed = await db.reject_wallet_topup(topup_id)
        if changed:
            await context.bot.send_message(
                chat_id=int(topup["user_id"]),
                text=(
                    "❌ <b>درخواست شارژ کیف پول شما رد شد.</b>\n\n"
                    f"مبلغ: <b>{int(topup['amount_toman']):,}</b> تومان\n"
                    "برای پیگیری، با پشتیبانی پیام بدهید."
                ),
                parse_mode=ParseMode.HTML,
            )
    else:
        await query.answer("عملیات نامعتبر است.", show_alert=True)
        return

    await query.answer("ثبت شد." if changed else "این درخواست قبلاً بررسی شده است.", show_alert=not changed)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        LOG.debug("failed to clear topup admin keyboard topup_id=%s", topup_id, exc_info=True)


async def agent_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles approve_open / approve_closed / reject on the original request message."""
    query = update.callback_query
    if not query:
        return
    if not await is_bot_admin(update, context):
        await query.answer("شما دسترسی مدیریت ندارید.", show_alert=True)
        return

    _, action, req_id = query.data.split(":", 2)
    db: AsyncDatabase = context.application.bot_data["db"]
    request_row = await db.get_agent_request(req_id)
    if not request_row:
        await query.answer("درخواست نمایندگی پیدا نشد.", show_alert=True)
        return
    if request_row["status"] != "pending":
        await query.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if action == "reject":
        changed = await db.update_agent_request_status(req_id, "rejected")
        if not changed:
            await query.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
            return
        await context.bot.send_message(
            chat_id=int(request_row["user_id"]),
            text=(
                "❌ <b>درخواست نمایندگی شما رد شد.</b>\n\n"
                "برای پیگیری یا ارسال توضیحات تکمیلی، با پشتیبانی در ارتباط باشید."
            ),
            parse_mode=ParseMode.HTML,
        )
        await query.answer("درخواست رد شد.")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            LOG.debug("failed to clear agent admin keyboard req_id=%s", req_id, exc_info=True)
        return

    # approve_open or approve_closed → start multi-step settings flow
    is_open = action == "approve_open"
    context.user_data["admin_agent_approval"] = {
        "req_id": req_id,
        "access_level": "open" if is_open else "closed",
        "credit_limit": None,
        "price_per_gb": None,
    }
    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    if is_open:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                f"⚙️ <b>تنظیم نماینده – دسترسی باز</b>\n"
                f"درخواست: <code>{req_id}</code>\n\n"
                "📊 <b>سقف اعتبار</b> نماینده را انتخاب کنید:"
            ),
            reply_markup=agent_admin_credit_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    else:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                f"⚙️ <b>تنظیم نماینده – نیازمند پرداخت</b>\n"
                f"درخواست: <code>{req_id}</code>\n\n"
                "💵 <b>قیمت هر گیگابایت</b> را انتخاب کنید:"
            ),
            reply_markup=agent_admin_price_keyboard(),
            parse_mode=ParseMode.HTML,
        )


async def agent_admin_set_credit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Step 2 for open agents: admin selects credit limit."""
    query = update.callback_query
    if not await is_bot_admin(update, context):
        await query.answer("دسترسی ندارید.", show_alert=True)
        return

    approval = context.user_data.get("admin_agent_approval")
    if not approval:
        await query.answer("جلسه تایید نماینده منقضی شده است. درخواست را از نو باز کنید.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    amount_str = query.data.split(":")[-1]
    db: AsyncDatabase = context.application.bot_data["db"]
    if amount_str == "def":
        amount = int(await db.get_setting("default_agent_credit_limit_toman", "0") or "0")
    else:
        amount = int(amount_str)

    approval["credit_limit"] = amount
    await query.answer()
    await query.edit_message_text(
        f"✅ سقف اعتبار: <b>{amount:,}</b> تومان ثبت شد.\n\n"
        "💵 <b>قیمت هر گیگابایت</b> را انتخاب کنید:",
        reply_markup=agent_admin_price_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def agent_admin_set_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Step 3: admin selects price per GB, then asks for daily test limit."""
    query = update.callback_query
    if not await is_bot_admin(update, context):
        await query.answer("دسترسی ندارید.", show_alert=True)
        return

    approval = context.user_data.get("admin_agent_approval")
    if not approval:
        await query.answer("جلسه تایید نماینده منقضی شده است.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    price_str = query.data.split(":")[-1]
    db: AsyncDatabase = context.application.bot_data["db"]
    if price_str == "def":
        price = int(await db.get_setting("default_agent_price_per_gb", "0") or "0")
    else:
        price = int(price_str)

    approval["price_per_gb"] = price
    await query.answer()
    await query.edit_message_text(
        f"✅ قیمت هر گیگ: <b>{price:,}</b> تومان ثبت شد.\n\n"
        "🧪 <b>سهمیه کانفیگ تست روزانه</b> را انتخاب کنید:",
        reply_markup=agent_admin_daily_test_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def agent_admin_set_daily_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Step 4: admin selects daily test limit, then shows confirmation summary."""
    query = update.callback_query
    if not await is_bot_admin(update, context):
        await query.answer("دسترسی ندارید.", show_alert=True)
        return

    approval = context.user_data.get("admin_agent_approval")
    if not approval:
        await query.answer("جلسه تایید نماینده منقضی شده است.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    limit_str = query.data.split(":")[-1]
    if limit_str == "def":
        limit = 0
    else:
        limit = int(limit_str)

    approval["daily_test_limit"] = limit
    access_level = approval["access_level"]
    credit_limit = int(approval.get("credit_limit") or 0)
    price_per_gb = int(approval.get("price_per_gb") or 0)
    req_id = approval["req_id"]

    label_access = "باز (اعتباری)" if access_level == "open" else "بسته (نیازمند پرداخت)"
    summary = (
        f"📋 <b>خلاصه تنظیمات نماینده</b>\n\n"
        f"درخواست: <code>{req_id}</code>\n"
        f"نوع دسترسی: <b>{label_access}</b>\n"
    )
    if access_level == "open":
        summary += f"سقف اعتبار: <b>{credit_limit:,}</b> تومان\n"
    summary += f"قیمت هر گیگ: <b>{price_per_gb:,}</b> تومان\n"
    summary += f"کانفیگ تست روزانه: <b>{limit if limit > 0 else 'تنظیم نشده'}</b>\n\n"
    summary += "آیا تایید نهایی می‌کنید؟"

    await query.answer()
    await query.edit_message_text(
        summary,
        reply_markup=agent_admin_confirm_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def agent_admin_final_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Final step: execute agent approval with chosen settings."""
    query = update.callback_query
    if not await is_bot_admin(update, context):
        await query.answer("دسترسی ندارید.", show_alert=True)
        return

    approval = context.user_data.pop("admin_agent_approval", None)
    if not approval:
        await query.answer("جلسه منقضی شد. درخواست را دوباره باز کنید.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    req_id = approval["req_id"]
    access_level = approval["access_level"]
    credit_limit = int(approval.get("credit_limit") or 0)
    price_per_gb = int(approval.get("price_per_gb") or 0)
    daily_test_limit = int(approval.get("daily_test_limit") or 0)

    db: AsyncDatabase = context.application.bot_data["db"]
    result = await db.approve_agent_request_as_agent(
        req_id=req_id,
        access_level=access_level,
        credit_limit_toman=credit_limit if access_level == "open" else 0,
        price_per_gb=price_per_gb,
        daily_test_limit=daily_test_limit,
        created_by=update.effective_user.id,
    )
    if not result:
        await query.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    label = "نماینده با دسترسی باز" if access_level == "open" else "نماینده نیازمند پرداخت"
    msg = (
        "✅ <b>درخواست نمایندگی شما تایید شد.</b>\n\n"
        f"سطح جدید شما: <b>{label}</b>\n"
    )
    if access_level == "open":
        msg += f"سقف اعتبار: <b>{credit_limit:,}</b> تومان\n"
    if price_per_gb > 0:
        msg += f"قیمت هر گیگ: <b>{price_per_gb:,}</b> تومان\n"
    # 0 = follow the shop-wide default rather than "no tests".
    effective_test_limit = daily_test_limit or int(
        await db.get_setting("default_agent_daily_test_limit", "5") or "5"
    )
    if effective_test_limit > 0:
        msg += f"کانفیگ تست روزانه: <b>{effective_test_limit}</b> عدد\n"
    msg += "از این لحظه منوی ربات برای حساب شما به‌روزرسانی شده است."

    try:
        await context.bot.send_message(
            chat_id=int(result["user_id"]),
            text=msg,
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        LOG.exception("failed to notify new agent user_id=%s", result["user_id"])

    await query.answer("✅ نماینده با موفقیت ساخته شد.")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


async def agent_admin_flow_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel pending agent approval flow."""
    query = update.callback_query
    if not await is_bot_admin(update, context):
        await query.answer("دسترسی ندارید.", show_alert=True)
        return
    context.user_data.pop("admin_agent_approval", None)
    await query.answer("لغو شد.")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


async def handle_nav_btn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles reply keyboard navigation from any conversation state."""
    text = (update.effective_message.text or "").strip()
    clear_flow_state(context)
    if text == BTN_BUY:
        return await buy_start(update, context)
    if text == BTN_RENEW:
        return await renew_start(update, context)
    if text == BTN_SUBS:
        await my_subscriptions(update, context)
        return ConversationHandler.END
    if text == BTN_ACCOUNT:
        await account_info(update, context)
        return ConversationHandler.END
    if text == BTN_WALLET:
        await wallet_info(update, context)
        return ConversationHandler.END
    if text == BTN_TARIFFS:
        await tariffs_info(update, context)
        return ConversationHandler.END
    if text == BTN_SUPPORT:
        await support_info(update, context)
        return ConversationHandler.END
    if text == BTN_TEST_CONFIG:
        await agent_test_config(update, context)
        return ConversationHandler.END
    if text == BTN_INFINITE:
        await infinite_start(update, context)
        return ConversationHandler.END
    if text == BTN_AGENT_REQ:
        return await agent_request_start(update, context)
    return ConversationHandler.END


async def agent_admin_custom_credit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt admin to type a custom credit limit."""
    query = update.callback_query
    if not await is_bot_admin(update, context):
        await query.answer("دسترسی ندارید.", show_alert=True)
        return
    approval = context.user_data.get("admin_agent_approval")
    if not approval:
        await query.answer("جلسه تایید نماینده منقضی شده است.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return
    approval["awaiting_custom"] = "credit"
    await query.answer()
    await query.edit_message_text(
        "✍️ <b>مبلغ سقف اعتبار</b> را به تومان تایپ کنید:\n\nمثال: <code>30000000</code>",
        parse_mode=ParseMode.HTML,
    )


async def agent_admin_custom_price_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt admin to type a custom price per GB."""
    query = update.callback_query
    if not await is_bot_admin(update, context):
        await query.answer("دسترسی ندارید.", show_alert=True)
        return
    approval = context.user_data.get("admin_agent_approval")
    if not approval:
        await query.answer("جلسه تایید نماینده منقضی شده است.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return
    approval["awaiting_custom"] = "price"
    await query.answer()
    await query.edit_message_text(
        "✍️ <b>قیمت هر گیگابایت</b> را به تومان تایپ کنید:\n\nمثال: <code>175000</code>",
        parse_mode=ParseMode.HTML,
    )


async def agent_admin_custom_daily_test_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt admin to type a custom daily test config limit."""
    query = update.callback_query
    if not await is_bot_admin(update, context):
        await query.answer("دسترسی ندارید.", show_alert=True)
        return
    approval = context.user_data.get("admin_agent_approval")
    if not approval:
        await query.answer("جلسه تایید نماینده منقضی شده است.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return
    approval["awaiting_custom"] = "daily_test"
    await query.answer()
    await query.edit_message_text(
        "✍️ <b>تعداد کانفیگ تست روزانه</b> را تایپ کنید:\n\nمثال: <code>3</code>",
        parse_mode=ParseMode.HTML,
    )


async def admin_custom_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Group=-1: intercepts admin typed input for custom values during agent approval."""
    if not update.effective_message or not update.effective_message.text:
        return
    if not await is_bot_admin(update, context):
        return
    approval = context.user_data.get("admin_agent_approval")
    if not approval:
        return
    awaiting = approval.get("awaiting_custom")
    if not awaiting:
        return

    text = (update.effective_message.text or "").replace(",", "").strip()
    if not text.isdigit() or int(text) <= 0:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ مقدار معتبر نیست. لطفاً یک عدد مثبت ارسال کنید.",
            parse_mode=ParseMode.HTML,
        )
        raise ApplicationHandlerStop

    value = int(text)
    del approval["awaiting_custom"]

    if awaiting == "credit":
        approval["credit_limit"] = value
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                f"✅ سقف اعتبار: <b>{value:,}</b> تومان ثبت شد.\n\n"
                "💵 <b>قیمت هر گیگابایت</b> را انتخاب کنید:"
            ),
            reply_markup=agent_admin_price_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    elif awaiting == "price":
        approval["price_per_gb"] = value
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                f"✅ قیمت هر گیگ: <b>{value:,}</b> تومان ثبت شد.\n\n"
                "🧪 <b>سهمیه کانفیگ تست روزانه</b> را انتخاب کنید:"
            ),
            reply_markup=agent_admin_daily_test_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    else:  # daily_test
        approval["daily_test_limit"] = value
        access_level = approval["access_level"]
        credit_limit = int(approval.get("credit_limit") or 0)
        price_per_gb = int(approval.get("price_per_gb") or 0)
        req_id = approval["req_id"]
        label_access = "باز (اعتباری)" if access_level == "open" else "بسته (نیازمند پرداخت)"
        summary = (
            f"📋 <b>خلاصه تنظیمات نماینده</b>\n\n"
            f"درخواست: <code>{req_id}</code>\n"
            f"نوع دسترسی: <b>{label_access}</b>\n"
        )
        if access_level == "open":
            summary += f"سقف اعتبار: <b>{credit_limit:,}</b> تومان\n"
        summary += f"قیمت هر گیگ: <b>{price_per_gb:,}</b> تومان\n"
        summary += f"کانفیگ تست روزانه: <b>{value}</b> عدد\n\n"
        summary += "آیا تایید نهایی می‌کنید؟"
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=summary,
            reply_markup=agent_admin_confirm_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    raise ApplicationHandlerStop


def _common_fallbacks() -> list:
    return [
        CommandHandler("start", end_to_main),
        CallbackQueryHandler(end_to_main, pattern=r"^menu:main$"),
    ]


def build_main_conversation() -> ConversationHandler:
    """Single merged ConversationHandler for all user flows.

    _nav_filter is placed first in every text-input state so reply keyboard
    buttons always intercept before raw text handlers process them as data.
    """
    entry_points = [
        CallbackQueryHandler(buy_start, pattern=r"^menu:buy$"),
        CallbackQueryHandler(renew_start, pattern=r"^menu:renew$"),
        CallbackQueryHandler(topup_c2c_start, pattern=r"^wallet:c2c$"),
        CallbackQueryHandler(topup_crypto_start, pattern=r"^wallet:crypto$"),
        CallbackQueryHandler(agent_request_start, pattern=r"^menu:agent_request$"),
        MessageHandler(_nav_filter, handle_nav_btn),
    ]
    states = {
        CAT_SELECT: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(catalog_category_select, pattern=r"^cat:[A-Za-z0-9_\-]+$"),
            CallbackQueryHandler(catalog_volume_select, pattern=r"^cpk:gb:[A-Za-z0-9_\-]+:\d+$"),
            CallbackQueryHandler(catalog_plan_select, pattern=r"^cpk:sel:[A-Za-z0-9_\-]+$"),
            CallbackQueryHandler(buy_cancel, pattern=r"^buy:cancel$"),
        ],
        CAT_QTY: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(catalog_qty_selected, pattern=r"^cpk:q:(\d+|custom)$"),
            CallbackQueryHandler(buy_cancel, pattern=r"^buy:cancel$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, catalog_qty_typed),
        ],
        CAT_NAME_MODE: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(catalog_name_random, pattern=r"^cpk:name:random$"),
            CallbackQueryHandler(catalog_name_custom, pattern=r"^cpk:name:custom$"),
            CallbackQueryHandler(buy_cancel, pattern=r"^buy:cancel$"),
        ],
        CAT_NAME_INPUT: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(buy_cancel, pattern=r"^buy:cancel$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, catalog_name_typed),
        ],
        CAT_CONFIRM: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(catalog_confirm, pattern=r"^cpk:ok$"),
            CallbackQueryHandler(buy_cancel, pattern=r"^buy:cancel$"),
        ],
        BUY_GB: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(buy_gb_selected, pattern=r"^buy:gb:\d+$"),
            CallbackQueryHandler(buy_custom_gb_start, pattern=r"^buy:gb:custom$"),
            CallbackQueryHandler(buy_cancel, pattern=r"^buy:cancel$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, buy_gb_message),
        ],
        BUY_CUSTOM_GB: [
            MessageHandler(_nav_filter, handle_nav_btn),
            MessageHandler(filters.TEXT & ~filters.COMMAND, buy_custom_gb),
        ],
        BUY_QTY: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(buy_qty_selected, pattern=r"^buy:qty:(1|2|3|5|10)$"),
            CallbackQueryHandler(buy_qty_custom_start, pattern=r"^buy:qty:custom$"),
            CallbackQueryHandler(buy_cancel, pattern=r"^buy:cancel$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, buy_qty),
        ],
        BUY_NAME_MODE: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(buy_name_random, pattern=r"^buy:name:random$"),
            CallbackQueryHandler(buy_name_custom_start, pattern=r"^buy:name:custom$"),
            CallbackQueryHandler(buy_cancel, pattern=r"^buy:cancel$"),
        ],
        BUY_NAME_INPUT: [
            MessageHandler(_nav_filter, handle_nav_btn),
            MessageHandler(filters.TEXT & ~filters.COMMAND, buy_name_input),
        ],
        BUY_CONFIRM: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(buy_confirm, pattern=r"^buy:confirm$"),
            CallbackQueryHandler(buy_cancel, pattern=r"^buy:cancel$"),
        ],
        RENEW_SELECT: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(renew_page, pattern=r"^renew:page:\d+$"),
            CallbackQueryHandler(renew_search_start, pattern=r"^renew:search$"),
            CallbackQueryHandler(renew_clear_search, pattern=r"^renew:clear_search$"),
            CallbackQueryHandler(renew_select, pattern=r"^renew:sub:"),
        ],
        RENEW_SEARCH: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(renew_page, pattern=r"^renew:page:\d+$"),
            CallbackQueryHandler(renew_clear_search, pattern=r"^renew:clear_search$"),
            CallbackQueryHandler(renew_cancel, pattern=r"^renew:cancel$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, renew_search_text),
        ],
        RENEW_GB: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(renew_gb_selected, pattern=r"^renew:gb:\d+$"),
            CallbackQueryHandler(renew_custom_gb_start, pattern=r"^renew:gb:custom$"),
            CallbackQueryHandler(renew_cancel, pattern=r"^renew:cancel$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, renew_gb_message),
        ],
        RENEW_CUSTOM_GB: [
            MessageHandler(_nav_filter, handle_nav_btn),
            MessageHandler(filters.TEXT & ~filters.COMMAND, renew_custom_gb),
        ],
        RENEW_CONFIRM: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(renew_confirm, pattern=r"^renew:confirm$"),
            CallbackQueryHandler(renew_cancel, pattern=r"^renew:cancel$"),
        ],
        TOPUP_AMOUNT: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(topup_amount_selected, pattern=r"^topup:amount:(card|crypto):\d+$"),
            CallbackQueryHandler(topup_custom_start, pattern=r"^topup:custom:(card|crypto)$"),
            CallbackQueryHandler(topup_cancel, pattern=r"^topup:cancel$"),
        ],
        TOPUP_CUSTOM_AMOUNT: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(topup_cancel, pattern=r"^topup:cancel$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, topup_custom_amount),
        ],
        TOPUP_AMOUNT_CONFIRM: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(topup_amount_confirm, pattern=r"^topup:amount_confirm$"),
            CallbackQueryHandler(topup_amount_back, pattern=r"^topup:amount_back$"),
            CallbackQueryHandler(topup_cancel, pattern=r"^topup:cancel$"),
        ],
        TOPUP_C2C_PHOTO: [
            MessageHandler(_nav_filter, handle_nav_btn),
            MessageHandler(filters.PHOTO, topup_c2c_photo),
            MessageHandler(~filters.PHOTO & ~filters.COMMAND, topup_c2c_photo),
        ],
        TOPUP_CRYPTO_TXID: [
            MessageHandler(_nav_filter, handle_nav_btn),
            MessageHandler(filters.TEXT & ~filters.COMMAND, topup_crypto_txid),
        ],
        AGENT_TEXT: [
            MessageHandler(_nav_filter, handle_nav_btn),
            MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, agent_request_message),
        ],
        AGENT_CONFIRM: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(agent_request_confirm, pattern=r"^agent:confirm$"),
            CallbackQueryHandler(agent_request_cancel, pattern=r"^agent:cancel$"),
        ],
    }
    fallbacks = _common_fallbacks() + [
        CallbackQueryHandler(buy_cancel, pattern=r"^buy:cancel$"),
        CallbackQueryHandler(renew_cancel, pattern=r"^renew:cancel$"),
        CallbackQueryHandler(topup_cancel, pattern=r"^topup:cancel$"),
        CallbackQueryHandler(agent_request_cancel, pattern=r"^agent:cancel$"),
    ]
    return ConversationHandler(
        entry_points=entry_points,
        states=states,
        fallbacks=fallbacks,
        allow_reentry=True,
        per_message=False,
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global safety net: log any unhandled exception and reassure the user so a
    crash in one handler can never leave the bot silent or in a broken state.
    Never raises (any failure while reporting is swallowed)."""
    LOG.exception("Unhandled error while processing update", exc_info=context.error)
    try:
        if isinstance(update, Update):
            chat = update.effective_chat
            if update.callback_query:
                with contextlib.suppress(Exception):
                    await update.callback_query.answer()
            if chat is not None:
                await context.bot.send_message(
                    chat_id=chat.id,
                    text=(
                        "⚠️ <b>یک خطای غیرمنتظره رخ داد.</b>\n\n"
                        "اگر مبلغی کسر شده ولی سرویس را دریافت نکرده‌اید نگران نباشید؛ "
                        "تراکنش‌های ناقص به‌صورت خودکار برگشت می‌خورند. لطفاً دوباره تلاش کنید "
                        "یا با پشتیبانی در تماس باشید."
                    ),
                    parse_mode=ParseMode.HTML,
                )
    except Exception:
        LOG.exception("Failed to deliver the error notice to the user")


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", start))
    # Admin custom text input (group=-1 runs before ConversationHandler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_custom_text_handler), group=-1)
    # Admin callbacks (before conversation handlers so they match regardless of state)
    app.add_handler(CallbackQueryHandler(topup_admin_callback, pattern=r"^topup_admin:(approve|reject):"))
    app.add_handler(CallbackQueryHandler(agent_admin_callback, pattern=r"^agent_admin:(approve_open|approve_closed|reject):"))
    app.add_handler(CallbackQueryHandler(agent_admin_custom_credit_start, pattern=r"^agent_admin:cl:custom$"))
    app.add_handler(CallbackQueryHandler(agent_admin_custom_price_start, pattern=r"^agent_admin:pg:custom$"))
    app.add_handler(CallbackQueryHandler(agent_admin_custom_daily_test_start, pattern=r"^agent_admin:dt:custom$"))
    app.add_handler(CallbackQueryHandler(agent_admin_set_credit, pattern=r"^agent_admin:cl:"))
    app.add_handler(CallbackQueryHandler(agent_admin_set_price, pattern=r"^agent_admin:pg:"))
    app.add_handler(CallbackQueryHandler(agent_admin_set_daily_test, pattern=r"^agent_admin:dt:"))
    app.add_handler(CallbackQueryHandler(agent_admin_final_confirm, pattern=r"^agent_admin:ok$"))
    app.add_handler(CallbackQueryHandler(agent_admin_flow_cancel, pattern=r"^agent_admin:cancel$"))
    # Merged conversation handler for all user flows
    app.add_handler(build_main_conversation())
    # Standalone menu callbacks (outside conversation)
    app.add_handler(CallbackQueryHandler(send_main_menu, pattern=r"^menu:main$"))
    app.add_handler(CallbackQueryHandler(account_info, pattern=r"^menu:account$"))
    app.add_handler(CallbackQueryHandler(my_subscriptions, pattern=r"^menu:subs$"))
    app.add_handler(CallbackQueryHandler(my_subscriptions_page, pattern=r"^subs:page:\d+$"))
    app.add_handler(CallbackQueryHandler(pgsub_link, pattern=r"^pgsub:link:"))
    app.add_handler(CallbackQueryHandler(tariffs_info, pattern=r"^menu:tariffs$"))
    app.add_handler(CallbackQueryHandler(agent_test_config, pattern=r"^menu:test_config$"))
    app.add_handler(CallbackQueryHandler(infinite_start, pattern=r"^menu:infinite$"))
    app.add_handler(CallbackQueryHandler(infinite_confirm, pattern=r"^infinite:buy$"))
    app.add_handler(CallbackQueryHandler(support_info, pattern=r"^menu:support$"))
    app.add_handler(CallbackQueryHandler(wallet_info, pattern=r"^menu:wallet$"))
    # Global safety net for any unhandled exception in any handler.
    app.add_error_handler(on_error)
