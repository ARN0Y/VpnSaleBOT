from __future__ import annotations

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
from . import discounts
from .panel import PanelClient
from .pasarguard import PasarGuardClient
from .provisioning import ProvisioningService, package_price, parse_packages, PG_INBOUND_SENTINEL
from .qr import QRService
from .util import resolve_proxy_value

try:
    import jdatetime
except Exception:
    jdatetime = None


LOG = logging.getLogger(__name__)

BUY_GB, BUY_CUSTOM_GB, BUY_QTY, BUY_NAME_MODE, BUY_NAME_INPUT, BUY_CONFIRM = range(6)
TOPUP_AMOUNT, TOPUP_CUSTOM_AMOUNT, TOPUP_AMOUNT_CONFIRM, TOPUP_C2C_PHOTO, TOPUP_CRYPTO_TXID = range(10, 15)
AGENT_TEXT, AGENT_CONFIRM = range(30, 32)
RENEW_SELECT, RENEW_SEARCH, RENEW_GB, RENEW_CUSTOM_GB, RENEW_CONFIRM, RENEW_DISCOUNT = range(40, 46)
PKG_SELECT, PKG_NAME_MODE, PKG_NAME_INPUT, PKG_CONFIRM, PKG_DISCOUNT = range(60, 65)

FLOW_PROMPT_KEY = "_flow_prompt_message_id"
HOME_MESSAGE_KEY = "_home_message_id"
FLOW_STATE_KEYS = {"checkout", "pkg", "topup", "agent_request", "renewal", FLOW_PROMPT_KEY}

BTN_BUY = "⚡ خرید سرویس پرسرعت"
BTN_RENEW = "🔄 تمدید سرویس"
BTN_SUBS = "📦 سرویس‌های من"
BTN_ACCOUNT = "🪪 حساب کاربری"
BTN_WALLET = "💎 کیف پول من"
BTN_TARIFFS = "🏷 تعرفه‌ها"
BTN_SUPPORT = "🛟 تماس با پشتیبانی"
BTN_TEST_CONFIG = "🆓 دریافت تست رایگان"
BTN_AGENT_REQ = "🤝 درخواست نمایندگی"
BTN_INFINITE = "♾️ بسته‌ی بی‌نهایت"
BTN_PANEL2 = "🌐 سرور اختصاصی"

PANEL2_PRICE_DEFAULT = "7000"

# ── Editable reply-keyboard labels ────────────────────────────────────────────
# Admins can rename the bot's menu buttons from the panel. Each action has a
# default label and a settings key; the live label is the override or default.
# panel2 keeps its own (panel2_label) reply button, fixed here for routing.
NAV_ACTIONS: tuple[tuple[str, str], ...] = (
    ("buy", BTN_BUY),
    ("renew", BTN_RENEW),
    ("subs", BTN_SUBS),
    ("account", BTN_ACCOUNT),
    ("wallet", BTN_WALLET),
    ("support", BTN_SUPPORT),
    ("test_config", BTN_TEST_CONFIG),
    ("agent_request", BTN_AGENT_REQ),
)
NAV_DEFAULT_LABEL: dict[str, str] = {action: default for action, default in NAV_ACTIONS}
NAV_LABEL_SETTING: dict[str, str] = {action: f"btn_{action}_label" for action, _ in NAV_ACTIONS}

# Index consumed by the (sync) reply-button filter + router. Always seeded with
# defaults so routing works even before the first DB read, and kept current as
# keyboards are (re)built.
_NAV_TEXT_TO_ACTION: dict[str, str] = {}
_NAV_TEXTSET: set[str] = set()


def _rebuild_nav_index(labels: dict[str, str]) -> None:
    mapping: dict[str, str] = {}
    # Defaults first (fallback so a just-renamed button's old label still routes
    # for users whose persistent keyboard hasn't refreshed yet).
    for action, default in NAV_DEFAULT_LABEL.items():
        mapping[default.strip()] = action
    for action, label in labels.items():
        clean = (label or "").strip()
        if clean:
            mapping[clean] = action
    mapping[BTN_PANEL2.strip()] = "panel2"
    global _NAV_TEXT_TO_ACTION, _NAV_TEXTSET
    _NAV_TEXT_TO_ACTION = mapping
    _NAV_TEXTSET = set(mapping.keys())


_rebuild_nav_index(dict(NAV_DEFAULT_LABEL))


async def resolve_nav_labels(db: AsyncDatabase) -> dict[str, str]:
    """Current label for each nav action (admin override or default). Also
    refreshes the in-memory routing index so the reply-button filter stays in
    sync with whatever was last shown to users."""
    labels: dict[str, str] = {}
    for action, default in NAV_DEFAULT_LABEL.items():
        val = (await db.get_setting(NAV_LABEL_SETTING[action], "") or "").strip()
        labels[action] = val or default
    _rebuild_nav_index(labels)
    return labels


class _NavFilter(filters.MessageFilter):
    """Matches a reply message whose text is one of the live menu-button labels."""

    def filter(self, message) -> bool:  # sync, reads the in-memory index
        text = (message.text or "").strip()
        return bool(text) and text in _NAV_TEXTSET


_nav_filter = _NavFilter()

WELCOME_TEXT = (
    "⚡️ <b>NavidVPN</b>\n"
    "<i>نویدِ یک اینترنت آزاد و پرسرعت — هر لحظه، همه‌جا.</i>\n"
    "<code>─────────────────────</code>\n"
    "🚀 سرعت بالا و اتصال پایدار و بی‌وقفه\n"
    "👥 کانفیگ <b>۳ کاربره</b> — ایده‌آل برای خانواده و دوستان\n"
    "🇩🇪 <b>آیپی ثابت آلمان</b> — مطمئن و باکیفیت\n"
    "🛡 امنیت کامل و حفظ کامل حریم خصوصی\n"
    "🎯 تحویل آنی سرویس + پشتیبانی واقعی انسانی\n"
    "<code>─────────────────────</code>\n"
    "🛟 پشتیبانی: {support}\n\n"
    "✨ برای شروع، یکی از گزینه‌های زیر را انتخاب کنید 👇"
)


async def render_welcome(db: AsyncDatabase) -> str:
    """Welcome text with the support id injected from settings. The admin can
    override the whole message from the panel (``welcome_text``); the optional
    ``{support}`` placeholder is still filled in. Falls back to the built-in."""
    template = (await db.get_setting("welcome_text", "") or "").strip() or WELCOME_TEXT
    support = (await db.get_setting("support_id", "") or "").strip()
    return template.replace("{support}", html.escape(support) if support else "—")

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


async def gb_choice_keyboard(db: AsyncDatabase, user_id: int, prefix: str, cancel_data: str, min_gb: int = 1, *, panel2: bool = False) -> InlineKeyboardMarkup:
    minimum = _positive_int(min_gb, 1)
    agent = await db.get_agent(user_id)
    buttons = []
    for factor in GB_BUTTON_FACTORS:
        gb = minimum * factor
        unit = await buy_unit_price(db, user_id, gb, agent, panel2=panel2)
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


async def free_test_enabled(db: AsyncDatabase) -> bool:
    """One-time free test for regular (non-agent) users — toggle in settings."""
    return str(await db.get_setting("free_test_enabled", "1") or "1").strip().lower() not in {"0", "false", "off", "no"}


async def panel1_enabled(db: AsyncDatabase) -> bool:
    """Whether the primary 3x-ui panel is offered for new purchases. The admin
    can turn it off in settings (e.g. to sell only via the second panel)."""
    return str(await db.get_setting("panel_enabled", "1") or "1").strip().lower() not in {"0", "false", "off", "no"}


# ───────────────────────── Second (dedicated) 3x-ui panel ─────────────────────────
async def panel2_available(db: AsyncDatabase) -> bool:
    """True when the optional second panel is enabled and minimally configured."""
    enabled = str(await db.get_setting("panel2_enabled", "0") or "0").strip().lower() in {"1", "true", "on", "yes"}
    if not enabled:
        return False
    return bool((await db.get_setting("panel2_base_url", "")).strip())


async def panel2_label(db: AsyncDatabase) -> str:
    return (await db.get_setting("panel2_label", "") or "").strip() or "سرور اختصاصی"


async def panel2_price_per_gb(db: AsyncDatabase) -> int:
    return _positive_int(await db.get_setting("panel2_price_per_gb", PANEL2_PRICE_DEFAULT), int(PANEL2_PRICE_DEFAULT))


async def is_panel2_subscription(db: AsyncDatabase, sub: dict) -> bool:
    """Best-effort: a subscription belongs to the second panel when its inbound
    matches the configured panel2 inbound (and that inbound is distinct). Used to
    keep panel2 services out of the (panel1-only) renewal flow in v1."""
    if not await panel2_available(db):
        return False
    try:
        p2_inbound = int(str(await db.get_setting("panel2_inbound_id", "0")).strip() or "0")
        p1_inbound = int(str(await db.get_setting("panel_inbound_id", "0")).strip() or "0")
        sub_inbound = int(sub.get("inbound_id") or 0)
    except Exception:
        return False
    return p2_inbound > 0 and sub_inbound == p2_inbound and p2_inbound != p1_inbound


async def buy_unit_price(db: AsyncDatabase, user_id: int, gb: int, agent=None, *, panel2: bool = False) -> int:
    """Per-GB price for the buy flow. For the dedicated second panel an agent
    keeps their own flat rate (if set), otherwise the panel's dedicated price
    applies; regular users always pay the dedicated price."""
    if not panel2:
        return await unit_price_for_gb(db, user_id, gb, agent)
    if agent is None:
        agent = await db.get_agent(user_id)
    agent_price = int(agent["price_per_gb"] or 0) if agent else 0
    if agent_price > 0:
        return agent_price
    return await panel2_price_per_gb(db)


async def get_panel2_provisioning(context: ContextTypes.DEFAULT_TYPE) -> "ProvisioningService | None":
    """Lazily build (and cache) a ProvisioningService bound to the second panel.

    The panel's proxy is resolved from its own settings (panel2_use_proxy /
    panel2_proxy_url) at build time; changing the proxy needs a bot restart, but
    credentials/inbound are read live on every request like the primary panel.
    """
    app = context.application
    db: AsyncDatabase = app.bot_data["db"]
    if not await panel2_available(db):
        return None
    prov2 = app.bot_data.get("provisioning2")
    if prov2 is not None:
        return prov2
    settings: Settings = app.bot_data["settings"]
    try:
        proxy = resolve_proxy_value(
            await db.get_setting("panel2_proxy_url", ""),
            await db.get_setting("panel2_use_proxy", ""),
        )
        panel2 = PanelClient(
            db,
            proxy_url=proxy,
            pool_size=settings.panel_pool_size,
            timeout_seconds=settings.panel_timeout_seconds,
            kv_prefix="panel2_",
        )
    except Exception:
        # A malformed proxy URL (or any client-construction error) must not blow
        # up the buy flow — surface it as "unavailable" instead.
        LOG.exception("failed to build second-panel client")
        return None
    prov2 = ProvisioningService(db, panel2, app.bot_data["agents"])
    app.bot_data["panel2"] = panel2
    app.bot_data["provisioning2"] = prov2
    return prov2


# ───────────────────────── PasarGuard backend ─────────────────────────
async def pg_configured(db: AsyncDatabase) -> bool:
    enabled = str(await db.get_setting("pg_enabled", "0") or "0").strip().lower() in {"1", "true", "on", "yes"}
    if not enabled:
        return False
    return bool((await db.get_setting("pg_base_url", "")).strip()) and bool((await db.get_setting("pg_username", "")).strip())


async def pg_is_primary(db: AsyncDatabase) -> bool:
    """True when the main 'buy' flow should sell from PasarGuard (its packages)
    instead of the primary 3x-ui panel."""
    backend = str(await db.get_setting("primary_backend", "xui") or "xui").strip().lower()
    return backend == "pasarguard" and await pg_configured(db)


async def primary_buy_available(db: AsyncDatabase) -> bool:
    """Whether the MAIN 'buy' button should be offered, based on the chosen
    primary selling panel:
      • primary = PasarGuard → available when PasarGuard is configured.
      • primary = 3x-ui      → available when the 3x-ui panel is enabled.
    This is the single source of truth so the button never disappears just
    because 3x-ui is off while PasarGuard is the active seller."""
    backend = str(await db.get_setting("primary_backend", "xui") or "xui").strip().lower()
    if backend == "pasarguard":
        return await pg_configured(db)
    return await panel1_enabled(db)


async def get_pg_client(context: ContextTypes.DEFAULT_TYPE):
    """Build (and cache) a PasarGuardClient from settings; rebuild if the panel
    address/username/TLS changed so admin edits apply without a restart."""
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


# ───────────────────────── Sales catalog (categories → plans) ─────────────────────────
# Legacy per-panel package lists. Kept only so the one-time catalog migration can
# read them; nothing in the buy flow consults them any more.
PANEL_PKG_SETTING = {"1": "panel_packages", "2": "panel2_packages", "pg": "pg_packages"}


async def get_catalog(context: ContextTypes.DEFAULT_TYPE) -> dict:
    db: AsyncDatabase = context.application.bot_data["db"]
    return await catalog.load_catalog(db)


async def _provisioning_for_panel(context: ContextTypes.DEFAULT_TYPE, panel_key: str):
    if panel_key == "2":
        return await get_panel2_provisioning(context)
    return context.application.bot_data["provisioning"]


async def _provisioning_for_plan(context: ContextTypes.DEFAULT_TYPE, plan: dict):
    """Resolve the provisioning service for a plan's own target."""
    target = plan.get("target") or {}
    if target.get("kind") == catalog.TARGET_XUI:
        return await _provisioning_for_panel(context, str(target.get("panel") or "1"))
    return context.application.bot_data["provisioning"]


def _plan_volume_line(plan: dict, gb: int | None = None) -> str:
    label = catalog.volume_label(plan, gb)
    icon = "♾️" if label == "نامحدود" else "📦"
    return f"{icon} حجم: <b>{html.escape(label)}</b>"


def _plan_duration_line(plan: dict) -> str:
    return f"⏳ مدت اعتبار: <b>{html.escape(catalog.duration_label(plan))}</b>"


def _package_volume_label(pkg: dict) -> str:
    """Volume line for a legacy-shaped package dict (post-purchase messages and
    the PasarGuard renewal flow still pass these around)."""
    if str(pkg.get("kind")) == "unlimited":
        return "♾️ حجم: <b>نامحدود</b>"
    return f"📦 حجم: <b>{int(pkg.get('gb') or 0)}</b> گیگ"


def _package_duration_label(pkg: dict) -> str:
    days = int(pkg.get("days") or 0)
    return f"⏳ مدت اعتبار: <b>{days}</b> روز" if days > 0 else "⏳ بدون محدودیت زمانی"


def _plan_button_label(plan: dict, price: int) -> str:
    badge = str((plan.get("display") or {}).get("badge") or "").strip()
    prefix = f"{badge} " if badge else ""
    return f"{prefix}{plan['title']} — {price:,} ت"


async def show_catalog_root(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str) -> int:
    """Entry point of the buy flow: pick a category.

    With a single category there is nothing to choose, so we skip straight to its
    plans rather than making the buyer tap through a one-item menu.
    """
    data = await get_catalog(context)
    categories = catalog.visible_categories(data)
    if not categories:
        await new_flow_card(update, context, "🛒 در حال حاضر پلنی برای فروش تعریف نشده است.", back_keyboard())
        return ConversationHandler.END
    if len(categories) == 1:
        return await show_category_plans(update, context, categories[0]["id"], title)

    rows = []
    for cat in categories:
        emoji = str(cat.get("emoji") or "").strip()
        label = f"{emoji} {cat['title']}".strip()
        count = len(catalog.plans_in_category(data, cat["id"]))
        rows.append([InlineKeyboardButton(f"{label}  ({count})", callback_data=f"cat:{cat['id']}")])
    rows.append([InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")])
    text = (
        f"🛒 <b>{html.escape(title)}</b>\n"
        "<code>─────────────────────</code>\n"
        "ابتدا دسته‌ی مورد نظر را انتخاب کنید 👇"
    )
    await new_flow_card(update, context, text, InlineKeyboardMarkup(rows))
    return PKG_SELECT


async def show_category_plans(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: str, title: str = ""
) -> int:
    data = await get_catalog(context)
    cat = catalog.find_category(data, category_id)
    plans = catalog.plans_in_category(data, category_id)
    # A disabled category must not be reachable through a stale button either.
    if not cat or not cat.get("enabled") or not plans:
        await new_flow_card(update, context, "🛒 در این دسته پلنی موجود نیست.", back_keyboard())
        return ConversationHandler.END

    db: AsyncDatabase = context.application.bot_data["db"]
    agent = await db.get_agent(update.effective_user.id)
    rows = []
    for plan in plans:
        # For a variable plan the button shows the entry price, since the buyer
        # picks the volume on the next step.
        gb = None
        if str((plan.get("volume") or {}).get("mode")) == catalog.VOLUME_VARIABLE:
            choices = catalog.gb_choices(plan)
            gb = choices[0] if choices else 0
        price = catalog.price_for(plan, gb=gb, is_agent=bool(agent))
        prefix = "از " if gb is not None else ""
        rows.append([InlineKeyboardButton(
            f"{prefix}{_plan_button_label(plan, price)}", callback_data=f"pkg:sel:{plan['id']}"
        )])
    multi = len(catalog.visible_categories(data)) > 1
    if multi:
        rows.append([InlineKeyboardButton("↩️ دسته‌های دیگر", callback_data="cat:back")])
    rows.append([InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")])

    emoji = str(cat.get("emoji") or "").strip()
    heading = f"{emoji} {cat['title']}".strip() or title
    desc = str(cat.get("description") or "").strip()
    text = (
        f"🛒 <b>{html.escape(heading)}</b>\n"
        "<code>─────────────────────</code>\n"
        + (f"{html.escape(desc)}\n\n" if desc else "")
        + "یکی از پلن‌های زیر را انتخاب کنید 👇"
    )
    context.user_data["catalog_category"] = category_id
    if update.callback_query:
        await edit_flow_query(update, context, text, InlineKeyboardMarkup(rows))
    else:
        await new_flow_card(update, context, text, InlineKeyboardMarkup(rows))
    return PKG_SELECT


async def catalog_category_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _answer_query(query)
    category_id = (query.data or "").split(":", 1)[1]
    if category_id == "back":
        db: AsyncDatabase = context.application.bot_data["db"]
        labels = await resolve_nav_labels(db)
        return await show_catalog_root(update, context, labels["buy"])
    return await show_category_plans(update, context, category_id)


async def pkg_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """A plan was picked: ask for the volume first when the plan is variable."""
    query = update.callback_query
    await _answer_query(query)
    try:
        _, _, plan_id = (query.data or "").split(":", 2)
    except Exception:
        return ConversationHandler.END
    data = await get_catalog(context)
    plan = catalog.find_plan(data, plan_id)
    if not catalog.plan_is_sellable(data, plan):
        clear_flow_state(context)
        await edit_text(query, "⚠️ این پلن دیگر در دسترس نیست. لطفاً دوباره از منو انتخاب کنید.", back_keyboard())
        return ConversationHandler.END

    if str((plan.get("volume") or {}).get("mode")) == catalog.VOLUME_VARIABLE:
        return await show_plan_volumes(update, context, plan)
    return await _begin_plan_naming(update, context, plan, gb=None)


async def show_plan_volumes(update: Update, context: ContextTypes.DEFAULT_TYPE, plan: dict) -> int:
    """Volume picker for a variable plan — each button carries its own price."""
    db: AsyncDatabase = context.application.bot_data["db"]
    agent = await db.get_agent(update.effective_user.id)
    choices = catalog.gb_choices(plan)
    if not choices:
        clear_flow_state(context)
        await edit_text(update.callback_query, "⚠️ این پلن پیکربندی کاملی ندارد. با پشتیبانی تماس بگیرید.", back_keyboard())
        return ConversationHandler.END
    rows, row = [], []
    for gb in choices:
        price = catalog.price_for(plan, gb=gb, is_agent=bool(agent))
        row.append(InlineKeyboardButton(f"{gb} گیگ — {price:,} ت", callback_data=f"pkg:gb:{plan['id']}:{gb}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    back_cat = str(plan.get("category_id") or "")
    rows.append([InlineKeyboardButton("↩️ بازگشت", callback_data=f"cat:{back_cat}" if back_cat else "menu:main")])
    text = (
        f"📦 <b>{html.escape(str(plan['title']))}</b>\n"
        "<code>─────────────────────</code>\n"
        f"{_plan_duration_line(plan)}\n\n"
        "چه مقدار حجم می‌خواهید؟ 👇"
    )
    await edit_flow_query(update, context, text, InlineKeyboardMarkup(rows))
    return PKG_SELECT


async def pkg_volume_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _answer_query(query)
    try:
        _, _, plan_id, gb_s = (query.data or "").split(":", 3)
        gb = int(gb_s)
    except Exception:
        return ConversationHandler.END
    data = await get_catalog(context)
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
    return await _begin_plan_naming(update, context, plan, gb=gb)


async def _begin_plan_naming(
    update: Update, context: ContextTypes.DEFAULT_TYPE, plan: dict, *, gb: int | None
) -> int:
    db: AsyncDatabase = context.application.bot_data["db"]
    agent = await db.get_agent(update.effective_user.id)
    price = catalog.price_for(plan, gb=gb, is_agent=bool(agent))
    context.user_data["pkg"] = {
        "plan_id": plan["id"],
        "gb": gb,
        "idem": f"pkg-{update.effective_user.id}-{secrets.token_hex(8)}",
        "client_name": "",
        "awaiting_name": False,
    }
    note = str((plan.get("display") or {}).get("note") or "").strip()
    text = (
        "🛒 <b>نام کانفیگ</b>\n"
        "<code>─────────────────────</code>\n"
        f"🎁 پلن: <b>{html.escape(str(plan['title']))}</b>\n"
        f"{_plan_volume_line(plan, gb)}\n"
        f"{_plan_duration_line(plan)}\n"
        + (f"ℹ️ {html.escape(note)}\n" if note else "")
        + "<code>─────────────────────</code>\n"
        f"💰 مبلغ قابل پرداخت: <b>{price:,}</b> تومان\n\n"
        "می‌توانید نام کانفیگ را خودتان مشخص کنید یا اجازه بدهید ربات نام رندوم بسازد."
    )
    await edit_flow_query(update, context, text, package_name_keyboard(plan["id"], gb))
    return PKG_NAME_MODE


def _parse_plan_callback(raw: str, parts: int) -> tuple[str, int | None] | None:
    """Pull (plan_id, gb) out of a pkg: callback. gb is None for fixed plans."""
    try:
        fields = (raw or "").split(":", parts)
        plan_id, token = fields[parts - 1], fields[parts]
    except Exception:
        return None
    if not plan_id:
        return None
    if token == "-":
        return plan_id, None
    try:
        return plan_id, int(token)
    except (TypeError, ValueError):
        return None


def _set_pkg_context_from_name_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[str, int | None] | None:
    parsed = _parse_plan_callback(update.callback_query.data, 4)
    if parsed is None:
        return None
    # (callers show an explicit "expired button" message on None)
    plan_id, gb = parsed
    data = context.user_data.setdefault("pkg", {})
    data.update(plan_id=plan_id, gb=gb)
    data.setdefault("idem", f"pkg-{update.effective_user.id}-{secrets.token_hex(8)}")
    return plan_id, gb


# ───────────────────────── discount codes ─────────────────────────
# The code lives on the flow state (pkg / renewal) and is re-quoted every time
# an invoice is drawn. Storing the discounted amount and trusting it later is
# how a buyer ends up paying a price the shop never agreed to — the quote is a
# preview, and the purchase transaction re-checks it from scratch.


def _flow_bucket(context: ContextTypes.DEFAULT_TYPE, flow: str) -> dict:
    return context.user_data.setdefault(flow, {})


def stored_discount_code(context: ContextTypes.DEFAULT_TYPE, flow: str) -> str:
    return str((_flow_bucket(context, flow).get("discount") or {}).get("code") or "")


def stored_discount_amount(context: ContextTypes.DEFAULT_TYPE, flow: str) -> int:
    """What the invoice the buyer is looking at promised to take off."""
    return int((_flow_bucket(context, flow).get("discount") or {}).get("amount") or 0)


def clear_discount(context: ContextTypes.DEFAULT_TYPE, flow: str) -> None:
    _flow_bucket(context, flow).pop("discount", None)


async def resolve_discount(
    context: ContextTypes.DEFAULT_TYPE,
    flow: str,
    *,
    user_id: int,
    base_total: int,
    order_kind: str = "purchase",
    plan_id: str = "",
    category_id: str = "",
) -> dict:
    """Re-price the stored code against the invoice being drawn right now.

    Returns ``{"code", "amount", "final", "error"}``. A code that stopped
    applying (expired mid-flow, basket changed, allowance used up elsewhere) is
    dropped and reported, never silently kept at its old value.
    """
    base = max(0, int(base_total))
    code = stored_discount_code(context, flow)
    if not code:
        return {"code": "", "amount": 0, "final": base, "error": ""}
    db: AsyncDatabase = context.application.bot_data["db"]
    try:
        quote = await db.quote_discount(
            code,
            user_id=int(user_id),
            base_total=base,
            order_kind=order_kind,
            plan_id=plan_id,
            category_id=category_id,
        )
    except discounts.DiscountError as exc:
        clear_discount(context, flow)
        return {"code": "", "amount": 0, "final": base, "error": str(exc)}
    _flow_bucket(context, flow)["discount"] = {"code": quote["code"], "amount": int(quote["amount"])}
    return {
        "code": str(quote["code"]),
        "amount": int(quote["amount"]),
        "final": int(quote["final"]),
        "error": "",
    }


def discount_lines(state: dict, base_total: int) -> str:
    """The price block of an invoice: with a code applied, or without."""
    base = max(0, int(base_total))
    if state.get("code") and int(state.get("amount") or 0) > 0:
        amount = int(state["amount"])
        final = max(0, base - amount)
        return (
            f"💰 مبلغ سرویس: <b>{base:,}</b> تومان\n"
            f"🎟 کد <code>{html.escape(str(state['code']))}</code>: "
            f"<b>−{amount:,}</b> تومان\n"
            f"💳 مبلغ قابل پرداخت: <b>{final:,}</b> تومان"
            + ("\n🎉 این سفارش برای شما رایگان است." if final == 0 else "")
        )
    return f"💰 مبلغ قابل پرداخت: <b>{base:,}</b> تومان"


def discount_button_row(state: dict, *, prefix: str) -> list[InlineKeyboardButton]:
    if state.get("code"):
        return [InlineKeyboardButton("🎟 حذف کد تخفیف", callback_data=f"{prefix}:disc:clear")]
    return [InlineKeyboardButton("🎟 کد تخفیف دارم", callback_data=f"{prefix}:disc:add")]


async def _ask_for_code(update: Update, context: ContextTypes.DEFAULT_TYPE, *, prefix: str, note: str = "") -> None:
    await edit_flow_query(
        update,
        context,
        (f"⚠️ {html.escape(note)}\n\n" if note else "")
        + "🎟 <b>کد تخفیف</b>\n\n"
        "کد خود را ارسال کنید.\n"
        "<i>حروف بزرگ و کوچک فرقی ندارد.</i>",
        InlineKeyboardMarkup([[InlineKeyboardButton("↩️ بازگشت به فاکتور", callback_data=f"{prefix}:disc:back")],
                              [InlineKeyboardButton("❌ انصراف", callback_data=f"{prefix}:cancel")]]),
    )


async def pkg_discount_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _answer_query(update.callback_query)
    if not (context.user_data.get("pkg") or {}).get("plan_id"):
        await edit_text(update.callback_query, "⚠️ اطلاعات خرید کامل نیست. لطفاً دوباره شروع کنید.", back_keyboard())
        return ConversationHandler.END
    await _ask_for_code(update, context, prefix="buy")
    return PKG_DISCOUNT


async def pkg_discount_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _answer_query(update.callback_query)
    return await build_package_invoice(update, context)


async def pkg_discount_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _answer_query(update.callback_query, "کد تخفیف حذف شد")
    clear_discount(context, "pkg")
    return await build_package_invoice(update, context)


async def pkg_discount_typed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: AsyncDatabase = context.application.bot_data["db"]
    data = context.user_data.get("pkg") or {}
    plan_id = str(data.get("plan_id") or "")
    if not plan_id:
        await send_flow_prompt(update, context, "⚠️ اطلاعات خرید کامل نیست. لطفاً دوباره شروع کنید.", back_keyboard())
        return ConversationHandler.END
    catalog_data = await catalog.load_catalog(db)
    plan = catalog.find_plan(catalog_data, plan_id)
    if not catalog.plan_is_sellable(catalog_data, plan):
        clear_flow_state(context)
        await send_flow_prompt(update, context, "⚠️ این پلن دیگر در دسترس نیست.", back_keyboard())
        return ConversationHandler.END

    agent = await db.get_agent(update.effective_user.id)
    base = catalog.price_for(plan, gb=data.get("gb"), is_agent=bool(agent))
    code = discounts.normalize_code((update.effective_message.text or ""))
    try:
        quote = await db.quote_discount(
            code,
            user_id=update.effective_user.id,
            base_total=base,
            order_kind="purchase",
            plan_id=plan_id,
            category_id=str(plan.get("category_id") or ""),
        )
    except discounts.DiscountError as exc:
        await send_flow_prompt(
            update,
            context,
            f"⚠️ <b>{html.escape(str(exc))}</b>\n\nکد دیگری بفرستید یا به فاکتور برگردید.",
            InlineKeyboardMarkup([[InlineKeyboardButton("↩️ بازگشت به فاکتور", callback_data="buy:disc:back")],
                                  [InlineKeyboardButton("❌ انصراف", callback_data="buy:cancel")]]),
        )
        return PKG_DISCOUNT
    context.user_data.setdefault("pkg", {})["discount"] = {
        "code": quote["code"], "amount": int(quote["amount"])
    }
    return await build_package_invoice(update, context)


async def renew_discount_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _answer_query(update.callback_query)
    if not (context.user_data.get("renewal") or {}).get("sub_id"):
        await edit_text(update.callback_query, "⚠️ اطلاعات تمدید کامل نیست. لطفاً دوباره شروع کنید.", back_keyboard())
        return ConversationHandler.END
    await _ask_for_code(update, context, prefix="renew")
    return RENEW_DISCOUNT


async def renew_discount_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _answer_query(update.callback_query)
    return await _redraw_renew_invoice(update, context)


async def renew_discount_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _answer_query(update.callback_query, "کد تخفیف حذف شد")
    clear_discount(context, "renewal")
    return await _redraw_renew_invoice(update, context)


async def renew_discount_typed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: AsyncDatabase = context.application.bot_data["db"]
    renewal = context.user_data.get("renewal") or {}
    base = _renewal_base_total(renewal)
    if base <= 0:
        await send_flow_prompt(update, context, "⚠️ اطلاعات تمدید کامل نیست. لطفاً دوباره شروع کنید.", back_keyboard())
        return ConversationHandler.END
    code = discounts.normalize_code((update.effective_message.text or ""))
    try:
        quote = await db.quote_discount(
            code, user_id=update.effective_user.id, base_total=base, order_kind="renewal",
        )
    except discounts.DiscountError as exc:
        await send_flow_prompt(
            update,
            context,
            f"⚠️ <b>{html.escape(str(exc))}</b>\n\nکد دیگری بفرستید یا به فاکتور برگردید.",
            InlineKeyboardMarkup([[InlineKeyboardButton("↩️ بازگشت به فاکتور", callback_data="renew:disc:back")],
                                  [InlineKeyboardButton("❌ انصراف", callback_data="renew:cancel")]]),
        )
        return RENEW_DISCOUNT
    renewal["discount"] = {"code": quote["code"], "amount": int(quote["amount"])}
    return await _redraw_renew_invoice(update, context)


def _renewal_base_total(renewal: dict) -> int:
    """The renewal price before any discount, for either renewal shape."""
    if isinstance(renewal.get("pkg"), dict):
        return max(0, int(renewal.get("plan_price") or 0))
    return max(0, int(renewal.get("total") or 0))


async def _redraw_renew_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Re-render whichever renewal invoice the buyer is looking at."""
    renewal = context.user_data.setdefault("renewal", {})
    if isinstance(renewal.get("pkg"), dict):
        return await _render_renew_plan_invoice(update, context)
    return await _render_renew_volume_invoice(update, context)


async def _render_renew_plan_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Invoice for renewing onto a package (PasarGuard plan renewal)."""
    db: AsyncDatabase = context.application.bot_data["db"]
    renewal = context.user_data.setdefault("renewal", {})
    pkg = renewal.get("pkg") if isinstance(renewal.get("pkg"), dict) else None
    if not pkg:
        clear_flow_state(context)
        await _renew_fail(update, context, "⚠️ اطلاعات تمدید ناقص است. لطفاً دوباره شروع کنید.")
        return ConversationHandler.END

    agent = await db.get_agent(update.effective_user.id)
    price = package_price(pkg, agent)
    renewal["plan_price"] = int(price)
    state = await resolve_discount(
        context, "renewal",
        user_id=update.effective_user.id, base_total=price, order_kind="renewal",
    )
    name = str(renewal.get("client_name") or renewal.get("sub_id") or "بدون نام")
    vol = "♾️ نامحدود (مصرف منصفانه)" if str(pkg.get("kind")) == "unlimited" else f"{int(pkg.get('gb') or 0)} گیگ"
    days = int(pkg.get("days") or 0)
    days_line = f"⏳ اعتبار: <b>{days}</b> روز (از اولین اتصال)\n" if days > 0 else ""
    text = (
        "🧾 <b>تایید تمدید سرویس</b>\n\n"
        + (f"⚠️ {html.escape(state['error'])}\n\n" if state.get("error") else "")
        + f"🪪 کانفیگ: <b>{html.escape(name)}</b>\n"
        f"🎁 پلن: <b>{html.escape(str(pkg.get('title') or '-'))}</b>\n"
        f"📦 حجم: <b>{vol}</b>\n"
        f"{days_line}"
        f"{discount_lines(state, price)}\n\n"
        "با تایید، همین کانفیگ با این پلن تمدید می‌شود (لینک اشتراک تغییر نمی‌کند)."
    )
    keyboard = renew_confirm_keyboard(discount=state)
    if update.callback_query:
        await edit_flow_query(update, context, text, keyboard)
    else:
        await send_flow_prompt(update, context, text, keyboard)
    return RENEW_CONFIRM


async def _render_renew_volume_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Invoice for adding volume to an existing 3x-ui subscription."""
    db: AsyncDatabase = context.application.bot_data["db"]
    renewal = context.user_data.setdefault("renewal", {})
    gb = int(renewal.get("gb") or 0)
    agent = await db.get_agent(update.effective_user.id)
    unit_price = await unit_price_for_gb(db, update.effective_user.id, gb, agent)
    min_gb = await minimum_purchase_gb(db)
    if gb < min_gb:
        renewal.pop("gb", None)
        await send_flow_prompt(
            update, context,
            invalid_gb_text(min_gb, renewal=True),
            await gb_choice_keyboard(db, update.effective_user.id, "renew", "renew:cancel", min_gb),
        )
        return RENEW_GB
    total = gb * unit_price
    method_label = "کیف پول نماینده" if agent else "کسر از کیف پول"
    renewal.update(unit_price=unit_price, total=total, method_label=method_label)
    renewal.setdefault("idem", f"renew-{update.effective_user.id}-{secrets.token_hex(8)}")
    state = await resolve_discount(
        context, "renewal",
        user_id=update.effective_user.id, base_total=total, order_kind="renewal",
    )
    text = (
        "🧾 <b>تایید تمدید اشتراک</b>\n\n"
        + (f"⚠️ {html.escape(state['error'])}\n\n" if state.get("error") else "")
        + f"🪪 کانفیگ: <b>{html.escape(str(renewal.get('client_name') or 'بدون نام'))}</b>\n"
        f"📦 حجم افزایشی: <b>{gb}</b> گیگ\n"
        f"💵 قیمت هر گیگ: <b>{unit_price:,}</b> تومان\n"
        f"{discount_lines(state, total)}\n"
        f"💳 روش پرداخت: <b>{method_label}</b>\n\n"
        "در صورت تایید، حجم به اشتراک انتخاب‌شده اضافه می‌شود."
    )
    keyboard = renew_confirm_keyboard(discount=state)
    if update.callback_query:
        await edit_flow_query(update, context, text, keyboard)
    else:
        await send_flow_prompt(update, context, text, keyboard)
    return RENEW_CONFIRM


async def _renew_fail(update: Update, context: ContextTypes.DEFAULT_TYPE, message: str) -> None:
    if update.callback_query:
        await edit_text(update.callback_query, message, back_keyboard())
    else:
        await send_flow_prompt(update, context, message, back_keyboard())


async def build_package_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: AsyncDatabase = context.application.bot_data["db"]
    data = context.user_data.get("pkg") or {}
    plan_id = str(data.get("plan_id") or "")
    gb = data.get("gb")

    async def _fail(message: str) -> int:
        clear_flow_state(context)
        if update.callback_query:
            await edit_text(update.callback_query, message, back_keyboard())
        else:
            await send_flow_prompt(update, context, message, back_keyboard())
        return ConversationHandler.END

    if not plan_id:
        return await _fail("⚠️ اطلاعات خرید کامل نیست. لطفاً دوباره شروع کنید.")
    catalog_data = await catalog.load_catalog(db)
    plan = catalog.find_plan(catalog_data, plan_id)
    if not catalog.plan_is_sellable(catalog_data, plan):
        return await _fail("⚠️ این پلن دیگر در دسترس نیست. لطفاً دوباره از منو انتخاب کنید.")

    agent = await db.get_agent(update.effective_user.id)
    price = catalog.price_for(plan, gb=gb, is_agent=bool(agent))
    client_name = str(data.get("client_name") or "").strip()
    data["awaiting_name"] = False
    note = str((plan.get("display") or {}).get("note") or "").strip()
    state = await resolve_discount(
        context, "pkg",
        user_id=update.effective_user.id,
        base_total=price,
        order_kind="purchase",
        plan_id=plan_id,
        category_id=str(plan.get("category_id") or ""),
    )
    text = (
        "🧾 <b>تایید خرید</b>\n"
        "<code>─────────────────────</code>\n"
        + (f"⚠️ {html.escape(state['error'])}\n" if state.get("error") else "")
        + f"🎁 پلن: <b>{html.escape(str(plan['title']))}</b>\n"
        f"{_plan_volume_line(plan, gb)}\n"
        f"{_plan_duration_line(plan)}\n"
        f"🪪 نام کانفیگ: <b>{html.escape(client_name) if client_name else '🎲 رندوم'}</b>\n"
        + (f"ℹ️ {html.escape(note)}\n" if note else "")
        + "<code>─────────────────────</code>\n"
        + f"{discount_lines(state, price)}\n\n"
        + "✅ با تایید، سرویس فوری ساخته و تحویل داده می‌شود."
    )
    keyboard = package_confirm_keyboard(plan_id, gb, state)
    if update.callback_query:
        await edit_flow_query(update, context, text, keyboard)
    else:
        await send_flow_prompt(update, context, text, keyboard)
    return PKG_CONFIRM


async def pkg_name_random(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _answer_query(update.callback_query)
    if _set_pkg_context_from_name_callback(update, context) is None:
        await edit_text(update.callback_query, "⚠️ عملیات نامعتبر است. لطفاً دوباره شروع کنید.", back_keyboard())
        return ConversationHandler.END
    context.user_data.setdefault("pkg", {})["client_name"] = ""
    context.user_data.setdefault("pkg", {})["awaiting_name"] = False
    return await build_package_invoice(update, context)


async def pkg_name_custom_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if _set_pkg_context_from_name_callback(update, context) is None:
        await edit_text(update.callback_query, "⚠️ عملیات نامعتبر است. لطفاً دوباره شروع کنید.", back_keyboard())
        return ConversationHandler.END
    context.user_data.setdefault("pkg", {})["awaiting_name"] = True
    await edit_flow_query(
        update,
        context,
        "✍️ <b>نام دلخواه کانفیگ</b>\n\n"
        "یک نام کوتاه انگلیسی/عددی بفرستید.\n"
        "مثال: <code>ali-office</code>",
        InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="buy:cancel")]]),
    )
    return PKG_NAME_INPUT


async def pkg_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.effective_message.text or "").strip()
    if not NAME_RE.match(name):
        await send_flow_prompt(
            update,
            context,
            "⚠️ نام معتبر نیست.\n\n"
            "از ۲ تا ۳۲ کاراکتر انگلیسی/عددی و علامت‌های <code>- _ .</code> استفاده کنید.",
            InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="buy:cancel")]]),
        )
        return PKG_NAME_INPUT
    context.user_data.setdefault("pkg", {})["client_name"] = name
    context.user_data.setdefault("pkg", {})["awaiting_name"] = False
    return await build_package_invoice(update, context)


async def pkg_name_input_standalone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.user_data.get("pkg") or {}
    if not data.get("awaiting_name"):
        return
    await pkg_name_input(update, context)


async def pkg_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    db: AsyncDatabase = context.application.bot_data["db"]
    # "pkg:ok:<plan_id>:<gb>" — three splits, so plan_id is field 2.
    parsed = _parse_plan_callback(query.data, 3)
    if parsed is None:
        clear_flow_state(context)
        await edit_text(query, "⚠️ این دکمه منقضی شده است. لطفاً دوباره از منو شروع کنید.", back_keyboard())
        return ConversationHandler.END
    plan_id, gb = parsed
    if not await audience_sales_is_open(db, update.effective_user.id):
        clear_flow_state(context)
        await edit_text(query, "🔒 <b>فروش سرویس موقتاً بسته است.</b>", back_keyboard())
        return ConversationHandler.END

    catalog_data = await catalog.load_catalog(db)
    plan = catalog.find_plan(catalog_data, plan_id)
    if not catalog.plan_is_sellable(catalog_data, plan):
        clear_flow_state(context)
        await edit_text(query, "⚠️ این پلن دیگر در دسترس نیست. لطفاً دوباره انتخاب کنید.", back_keyboard())
        return ConversationHandler.END
    # Re-validate the volume against the plan itself: the price is recomputed
    # from it below, so a tampered callback must not buy 500 GB at the 5 GB price.
    if str((plan.get("volume") or {}).get("mode")) == catalog.VOLUME_VARIABLE:
        if gb is None or gb not in catalog.gb_choices(plan):
            clear_flow_state(context)
            await edit_text(query, "⚠️ این حجم برای این پلن معتبر نیست.", back_keyboard())
            return ConversationHandler.END
    else:
        gb = None
    problems = catalog.validate_plan(plan)
    if problems:
        clear_flow_state(context)
        LOG.warning("plan %s is not sellable: %s", plan_id, problems)
        await edit_text(query, "⚠️ این پلن پیکربندی کاملی ندارد. لطفاً با پشتیبانی تماس بگیرید.", back_keyboard())
        return ConversationHandler.END

    target = plan.get("target") or {}
    panel_key = "pg" if target.get("kind") == catalog.TARGET_PASARGUARD else str(target.get("panel") or "1")
    if panel_key == "1" and not await panel1_enabled(db):
        clear_flow_state(context)
        await edit_text(query, "🌐 فروش از این سرور موقتاً غیرفعال است.", back_keyboard())
        return ConversationHandler.END

    # The provisioning layer still speaks the package dict; the catalog is the
    # source of truth for what it contains — including the REAL quota behind a
    # masked "نامحدود" label and the price for this exact audience and volume.
    pkg = catalog.legacy_equivalent(plan, gb)
    qr: QRService = context.application.bot_data["qr"]
    data = context.user_data.get("pkg") or {}
    idem = str(data.get("idem") or query.id)
    client_name = str(data.get("client_name") or "").strip()
    # Only the CODE travels to the money path; the amount is recomputed there.
    discount_code = stored_discount_code(context, "pkg")
    discount_amount = stored_discount_amount(context, "pkg")
    await edit_flow_query(update, context, "⏳ <b>در حال ساخت سرویس...</b>\n\nلطفاً چند لحظه صبر کنید.")
    try:
        if panel_key == "pg":
            pg_client = await get_pg_client(context)
            if pg_client is None:
                clear_flow_state(context)
                await edit_text(query, "🌐 این سرور در حال حاضر در دسترس نیست.", back_keyboard())
                return ConversationHandler.END
            # The group comes from the PLAN, not a global setting — that is what
            # lets two plans sell the same panel through different groups.
            group = str(target.get("group") or "").strip()
            group_ids = await pg_client.resolve_group_ids([group]) if group else []
            if not group_ids:
                clear_flow_state(context)
                LOG.warning("plan %s targets unknown PasarGuard group %r", plan_id, group)
                await edit_text(query, "گروه سرور پیدا نشد؛ لطفاً با پشتیبانی تماس بگیرید.", back_keyboard())
                return ConversationHandler.END
            provisioning = context.application.bot_data["provisioning"]
            links = await provisioning.process_pg_package_purchase(
                pg_client=pg_client,
                group_ids=group_ids,
                user_id=update.effective_user.id,
                pkg=pkg,
                days=int(pkg.get("days") or 0),
                client_name=client_name,
                idempotency_key=idem,
                discount_code=discount_code,
                expected_discount=discount_amount,
                plan_id=plan_id,
                category_id=str(plan.get("category_id") or ""),
            )
        else:
            provisioning = await _provisioning_for_panel(context, panel_key)
            if provisioning is None:
                clear_flow_state(context)
                await edit_text(query, "🌐 این سرور در حال حاضر در دسترس نیست.", back_keyboard())
                return ConversationHandler.END
            links = await provisioning.process_package_purchase(
                user_id=update.effective_user.id,
                pkg=pkg,
                client_name=client_name,
                idempotency_key=idem,
                discount_code=discount_code,
                expected_discount=discount_amount,
                plan_id=plan_id,
                category_id=str(plan.get("category_id") or ""),
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
            await _answer_query(query, "این خرید در حال پردازش است…")
            return ConversationHandler.END
        LOG.exception("package purchase failed user_id=%s", update.effective_user.id)
        clear_flow_state(context)
        await edit_text(query, f"❌ خطا در ساخت سرویس:\n{html.escape(str(exc))}", back_keyboard())
        return ConversationHandler.END

    if panel_key != "pg" and str(pkg.get("kind")) == "unlimited":
        uris: list[str] = []
        for link in links:
            try:
                uris.extend(await provisioning.panel.fetch_config_uris(link))
            except Exception:
                LOG.exception("fetch_config_uris failed for package")
        uris = [u for u in uris if u]
        if not uris:
            await edit_text(
                query,
                "✅ بسته ساخته شد، اما دریافت لینک کانفیگ کمی طول کشید.\n"
                "از بخش «اشتراک‌های من» می‌توانید کانفیگ را ببینید یا با پشتیبانی تماس بگیرید.",
                back_keyboard(),
            )
            clear_flow_state(context)
            return ConversationHandler.END
        await edit_text(query, "✅ <b>بسته با موفقیت فعال شد.</b>\n\nلینک کانفیگ در پیام بعدی ارسال می‌شود.")
        for i, uri in enumerate(uris):
            png = await qr.png(uri)
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=BytesIO(png),
                caption=(
                    f"✅ <b>{html.escape(str(pkg['title']))}</b>\n"
                    "<i>سرویس شما فعال شد 🌟</i>\n\n"
                    "🔗 <b>لینک کانفیگ شما:</b>\n"
                    f"<code>{html.escape(uri)}</code>\n\n"
                    "📲 این لینک را در اپلیکیشن خود وارد کنید یا QR را اسکن کنید."
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=back_keyboard() if i == len(uris) - 1 else None,
            )
        clear_flow_state(context)
        return ConversationHandler.END

    await edit_text(query, "✅ <b>سرویس شما با موفقیت ساخته شد.</b>\n\nلینک اتصال و QR Code در پیام بعدی ارسال می‌شود.")
    for i, sub_link in enumerate(links):
        png = await qr.png(sub_link)
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=BytesIO(png),
            caption=(
                "✅ <b>پرداخت با موفقیت انجام شد!</b>\n"
                f"<i>{html.escape(str(pkg['title']))} 🌟</i>\n"
                "<code>─────────────────────</code>\n"
                f"{_package_volume_label(pkg)}\n"
                f"{_package_duration_label(pkg)}\n"
                "<code>─────────────────────</code>\n"
                "🔗 <b>لینک اشتراک شما:</b>\n"
                f"<code>{html.escape(sub_link)}</code>\n\n"
                "📲 لینک بالا را در اپلیکیشن خود وارد کنید یا QR را اسکن کنید."
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard() if i == len(links) - 1 else None,
        )
    clear_flow_state(context)
    return ConversationHandler.END


async def main_menu_keyboard(user_id: int, db: AsyncDatabase) -> InlineKeyboardMarkup:
    agent = await db.get_agent(user_id)
    lbl = await resolve_nav_labels(db)
    rows: list[list[InlineKeyboardButton]] = []
    if await primary_buy_available(db):
        rows.append([InlineKeyboardButton(lbl["buy"], callback_data="menu:buy")])
    rows += [
        [
            InlineKeyboardButton(lbl["subs"], callback_data="menu:subs"),
            InlineKeyboardButton(lbl["renew"], callback_data="menu:renew"),
        ],
        [
            InlineKeyboardButton(lbl["wallet"], callback_data="menu:wallet"),
            InlineKeyboardButton(lbl["account"], callback_data="menu:account"),
        ],
    ]
    if await panel2_available(db):
        rows.insert(1, [InlineKeyboardButton(f"🌐 {await panel2_label(db)}", callback_data="menu:buy2")])
    if agent and not int(agent["disabled"] or 0):
        try:
            permissions = {str(item) for item in json.loads(agent["permissions"] or "[]")}
        except Exception:
            permissions = {"buy", "test"}
        if "test" in permissions:
            rows.append([InlineKeyboardButton(lbl["test_config"], callback_data="menu:test_config")])
    else:
        last = [InlineKeyboardButton(lbl["agent_request"], callback_data="menu:agent_request")]
        # Regular users: a one-time free test, when enabled.
        if await free_test_enabled(db):
            last.insert(0, InlineKeyboardButton(lbl["test_config"], callback_data="menu:test_config"))
        rows.append(last)
    rows.append([InlineKeyboardButton(lbl["support"], callback_data="menu:support")])
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


def _gb_token(gb: int | None) -> str:
    """Volume marker inside callback data; "-" means the plan sets its own."""
    return "-" if gb is None else str(int(gb))


def package_name_keyboard(plan_id: str, gb: int | None) -> InlineKeyboardMarkup:
    token = _gb_token(gb)
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎲 نام رندوم", callback_data=f"pkg:name:random:{plan_id}:{token}")],
            [InlineKeyboardButton("✍️ نام دلخواه", callback_data=f"pkg:name:custom:{plan_id}:{token}")],
            [InlineKeyboardButton("❌ انصراف", callback_data="buy:cancel")],
        ]
    )


def package_confirm_keyboard(plan_id: str, gb: int | None, discount: dict | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        discount_button_row(discount or {}, prefix="buy"),
        [InlineKeyboardButton("✅ تایید و خرید", callback_data=f"pkg:ok:{plan_id}:{_gb_token(gb)}"),
         InlineKeyboardButton("❌ انصراف", callback_data="buy:cancel")],
    ])


def buy_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ تایید و خرید", callback_data="buy:confirm"), InlineKeyboardButton("❌ انصراف", callback_data="buy:cancel")]]
    )


def renew_confirm_keyboard(discount: dict | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        discount_button_row(discount or {}, prefix="renew"),
        [InlineKeyboardButton("✅ تایید تمدید", callback_data="renew:confirm"),
         InlineKeyboardButton("❌ انصراف", callback_data="renew:cancel")],
    ])


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
            [InlineKeyboardButton("✅ تایید نماینده کیف‌پولی", callback_data=f"agent_admin:approve:{ref_id}")],
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


def agent_admin_pricing_keyboard() -> InlineKeyboardMarkup:
    """Package-based agent pricing: the agent pays each package's 'agent price'.
    Per-GB is only a fallback for panels that have no packages."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ تایید قیمتِ بسته‌ها و ادامه", callback_data="agent_admin:pg:packages")],
            [InlineKeyboardButton("✍️ قیمت گیگیِ سفارشی (پنل بدون بسته)", callback_data="agent_admin:pg:custom")],
            [InlineKeyboardButton("🔁 پیش‌فرض سیستم", callback_data="agent_admin:pg:def")],
            [InlineKeyboardButton("❌ لغو تایید", callback_data="agent_admin:cancel")],
        ]
    )


async def agent_pricing_text(db: AsyncDatabase) -> str:
    """Summary of what THIS agent will pay per plan (the plan's agent price,
    falling back to the user price when it isn't set). Same for all agents;
    edited per plan in the «پلن‌های فروش» tab."""
    data = await catalog.load_catalog(db)
    lines = ["💼 <b>قیمت‌گذاری نماینده</b>", "نماینده هر پلن را با «قیمت نماینده»یِ همان پلن می‌خرد:", ""]
    any_plan = False
    for cat in catalog.visible_categories(data):
        plans = catalog.plans_in_category(data, cat["id"])
        if not plans:
            continue
        any_plan = True
        emoji = str(cat.get("emoji") or "").strip()
        heading = f"{emoji} {cat['title']}".strip()
        lines.append(f"🔹 <b>{html.escape(heading)}</b>")
        for plan in plans:
            variable = str((plan.get("volume") or {}).get("mode")) == catalog.VOLUME_VARIABLE
            gb = None
            if variable:
                choices = catalog.gb_choices(plan)
                gb = choices[0] if choices else 0
            agent_price = catalog.price_for(plan, gb=gb, is_agent=True)
            user_price = catalog.price_for(plan, gb=gb, is_agent=False)
            tag = "" if agent_price != user_price else " <i>(= قیمت کاربر)</i>"
            prefix = "از " if variable else ""
            lines.append(f"   • {html.escape(str(plan['title']))}: <b>{prefix}{agent_price:,}</b> ت{tag}")
        lines.append("")
    if not any_plan:
        lines.append("⚠️ پلنی تعریف نشده؛ «قیمت گیگیِ سفارشی» را انتخاب کنید.")
    else:
        lines.append("برای تغییرِ قیمتِ نماینده‌یِ هر پلن، تب «پلن‌های فروش» را ویرایش کنید.")
    return "\n".join(lines)


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
            [InlineKeyboardButton("🔁 پیش‌فرض سیستم (۰)", callback_data="agent_admin:dt:def")],
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


def main_reply_keyboard(labels: dict[str, str], *, is_agent: bool = False, has_test: bool = False, has_panel2: bool = False, has_primary: bool = True, has_free_test: bool = False) -> ReplyKeyboardMarkup:
    def L(action: str) -> str:
        return labels.get(action) or NAV_DEFAULT_LABEL[action]

    # Hero "buy" button on top (full width), then balanced icy pairs below.
    rows = [
        [KeyboardButton(L("subs")), KeyboardButton(L("renew"))],
        [KeyboardButton(L("wallet")), KeyboardButton(L("account"))],
    ]
    # Hero buy button on top only when the primary panel is enabled.
    if has_primary:
        rows.insert(0, [KeyboardButton(L("buy"))])
    # Dedicated second-panel buy option as its own full-width row, right under
    # the hero buy button so it stands out.
    if has_panel2:
        rows.insert(1, [KeyboardButton(BTN_PANEL2)])
    fourth = []
    if is_agent and has_test:
        fourth.append(KeyboardButton(L("test_config")))
    if not is_agent and has_free_test:
        fourth.append(KeyboardButton(L("test_config")))
    if not is_agent:
        fourth.append(KeyboardButton(L("agent_request")))
    if fourth:
        rows.append(fourth)
    rows.append([KeyboardButton(L("support"))])
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
    return await main_menu_keyboard(update.effective_user.id, db)


async def _build_reply_keyboard(user_id: int, db: AsyncDatabase) -> ReplyKeyboardMarkup:
    agent = await db.get_agent(user_id)
    is_agent = bool(agent and not int(agent["disabled"] or 0))
    has_test = False
    if is_agent:
        try:
            permissions = {str(item) for item in json.loads(agent["permissions"] or "[]")}
            has_test = "test" in permissions
        except Exception:
            has_test = True
    has_panel2 = await panel2_available(db)
    has_primary = await primary_buy_available(db)
    has_free_test = (not is_agent) and await free_test_enabled(db)
    labels = await resolve_nav_labels(db)
    return main_reply_keyboard(labels, is_agent=is_agent, has_test=has_test, has_panel2=has_panel2, has_primary=has_primary, has_free_test=has_free_test)


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
    reply_kb = await _build_reply_keyboard(update.effective_user.id, db)
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


async def buy_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Main buy button — opens the sales catalog (categories, then plans).

    Which panel a plan runs on is now a property of the plan, so this no longer
    branches on the primary backend: the catalog already knows.
    """
    await ensure_user(update, context)
    db: AsyncDatabase = context.application.bot_data["db"]
    if not await audience_sales_is_open(db, update.effective_user.id):
        await show_sales_closed(update, context)
        return ConversationHandler.END
    await remove_keyboard(context, update.effective_chat.id, context.user_data.get(FLOW_PROMPT_KEY))
    clear_flow_state(context)
    labels = await resolve_nav_labels(db)
    return await show_catalog_root(update, context, labels["buy"])


async def buy2_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Legacy second-panel button.

    Plans carry their own target now, so a separate per-panel entry point is
    redundant; keep the button working by sending it to the same catalog.
    """
    return await buy_start(update, context)


def _checkout_is_panel2(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool((context.user_data.get("checkout") or {}).get("panel2"))


async def buy_gb_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db: AsyncDatabase = context.application.bot_data["db"]
    min_gb = await minimum_purchase_gb(db)
    p2 = _checkout_is_panel2(context)
    await send_flow_prompt(
        update,
        context,
        "📦 لطفاً حجم را از دکمه‌های همین کارت انتخاب کنید.\n\n"
        f"حداقل حجم مجاز: <b>{min_gb}</b> گیگ. برای وارد کردن عدد، گزینه <b>حجم دلخواه</b> را بزنید.",
        await gb_choice_keyboard(db, update.effective_user.id, "buy", "buy:cancel", min_gb, panel2=p2),
    )
    return BUY_GB


async def buy_gb_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _answer_query(query)
    db: AsyncDatabase = context.application.bot_data["db"]
    min_gb = await minimum_purchase_gb(db)
    gb = int(query.data.rsplit(":", 1)[1])
    if gb < min_gb:
        await edit_text(query, invalid_gb_text(min_gb), await gb_choice_keyboard(db, update.effective_user.id, "buy", "buy:cancel", min_gb, panel2=_checkout_is_panel2(context)))
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
    p2 = bool(checkout.get("panel2"))
    agent = await db.get_agent(update.effective_user.id)
    gb = int(checkout["gb"])
    qty = int(checkout["qty"])
    unit_price = await buy_unit_price(db, update.effective_user.id, gb, agent, panel2=p2)
    min_gb = await minimum_purchase_gb(db)
    if gb < min_gb:
        checkout.pop("gb", None)
        checkout.pop("qty", None)
        await send_flow_prompt(
            update,
            context,
            invalid_gb_text(min_gb),
            await gb_choice_keyboard(db, update.effective_user.id, "buy", "buy:cancel", min_gb, panel2=p2),
        )
        return BUY_GB
    total = gb * qty * unit_price
    method_label = "کیف پول نماینده" if agent else "کسر از کیف پول"
    server_line = f"🌐 سرور: <b>{html.escape(await panel2_label(db))}</b>\n" if p2 else ""
    client_name = str(checkout.get("client_name") or "").strip()
    # Stable idempotency token per built invoice: a double-tap on "confirm"
    # (possible because updates run concurrently) reuses the same key so the
    # purchase can never be charged/provisioned twice.
    checkout["idem"] = f"buy-{update.effective_user.id}-{secrets.token_hex(8)}"
    checkout.update(unit_price=unit_price, total=total, method_label=method_label)
    await send_flow_prompt(
        update,
        context,
        "🧾 <b>تایید نهایی سفارش</b>  ·  مرحله ۴ از ۴\n"
        "<code>─────────────────────</code>\n"
        f"{server_line}"
        f"📦 حجم هر اشتراک: <b>{gb}</b> گیگ\n"
        f"🔢 تعداد: <b>{qty}</b> عدد\n"
        f"💵 قیمت هر گیگ: <b>{unit_price:,}</b> تومان\n"
        f"🪪 نام کانفیگ: <b>{html.escape(client_name) if client_name else '🎲 رندوم'}</b>\n"
        f"💳 روش پرداخت: <b>{method_label}</b>\n"
        "<code>─────────────────────</code>\n"
        f"💰 مبلغ قابل پرداخت: <b>{total:,}</b> تومان\n\n"
        "✅ با تایید، سرویس شما فوری ساخته و تحویل داده می‌شود.",
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
    p2 = bool(checkout.get("panel2"))
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
            await gb_choice_keyboard(db, update.effective_user.id, "buy", "buy:cancel", min_gb, panel2=p2),
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

    if p2:
        provisioning = await get_panel2_provisioning(context)
        if provisioning is None:
            clear_flow_state(context)
            await edit_text(query, "🌐 این سرویس در حال حاضر در دسترس نیست.", back_keyboard())
            return ConversationHandler.END
    else:
        if not await panel1_enabled(db):
            clear_flow_state(context)
            await edit_text(query, "🌐 فروش از سرور اصلی غیرفعال شده است. مبلغی کسر نشد.", back_keyboard())
            return ConversationHandler.END
        provisioning = context.application.bot_data["provisioning"]
    qr: QRService = context.application.bot_data["qr"]
    await edit_flow_query(update, context, "⏳ <b>در حال ساخت سرویس...</b>\n\nلطفاً چند لحظه صبر کنید.")
    try:
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

    await edit_text(query, "⚡ <b>سرویس شما با موفقیت ساخته شد.</b>\n\nلینک اتصال و QR Code در پیام بعدی ارسال می‌شود.")
    for idx, sub_link in enumerate(links):
        safe_link = html.escape(sub_link)
        caption = (
            "✅ <b>پرداخت با موفقیت انجام شد!</b>\n"
            "<i>از اعتماد شما سپاسگزاریم 🌟</i>\n"
            "<code>─────────────────────</code>\n"
            f"📦 حجم هر اشتراک: <b>{gb}</b> گیگ × <b>{qty}</b> عدد\n"
            f"💰 مبلغ پرداختی: <b>{total:,}</b> تومان\n"
            f"💳 روش پرداخت: {html.escape(method_label)}\n"
            "<code>─────────────────────</code>\n"
            "🔗 <b>لینک اشتراک شما:</b>\n"
            f"<code>{safe_link}</code>\n\n"
            "📲 لینک بالا را در اپلیکیشن خود وارد کنید یا QR را اسکن کنید."
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
        "♾️ <b>بسته‌ی بی‌نهایت (مصرف منصفانه)</b>\n\n"
        "🌊 ترافیک نامحدود با سیاست مصرف منصفانه\n"
        f"⏳ مدت اعتبار: <b>{pkg['duration_days']:,}</b> روز\n"
        f"💰 قیمت: <b>{pkg['price']:,}</b> تومان\n\n"
        "✅ پس از خرید، <b>لینک مستقیم کانفیگ</b> برایتان ارسال می‌شود.\n"
        "✅ امکان خرید چند بسته وجود دارد."
    )
    # Fresh idempotency token per shown offer → a double-tap on buy cannot
    # charge/provision the package twice.
    context.user_data["infinite_idem"] = f"inf-{update.effective_user.id}-{secrets.token_hex(8)}"
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ خرید و دریافت کانفیگ", callback_data="infinite:buy")],
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
    panel = context.application.bot_data["panel"]
    qr: QRService = context.application.bot_data["qr"]
    await edit_flow_query(update, context, "⏳ <b>در حال ساخت بسته‌ی بی‌نهایت...</b>\n\nلطفاً چند لحظه صبر کنید.")
    try:
        links = await provisioning.process_infinite_purchase(
            user_id=update.effective_user.id,
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

    uris: list[str] = []
    for link in links:
        try:
            uris.extend(await panel.fetch_config_uris(link))
        except Exception:
            LOG.exception("fetch_config_uris failed for infinite package")
    uris = [u for u in uris if u]
    if not uris:
        await edit_text(
            query,
            "✅ بسته ساخته شد، اما دریافت لینک کانفیگ کمی طول کشید.\n"
            "از بخش «اشتراک‌های من» می‌توانید کانفیگ را ببینید یا با پشتیبانی تماس بگیرید.",
            back_keyboard(),
        )
        return
    await edit_text(query, "♾️ <b>بسته‌ی بی‌نهایت ساخته شد.</b>\n\nلینک کانفیگ در پیام بعدی ارسال می‌شود.")
    for idx, uri in enumerate(uris):
        caption = (
            "✅ <b>بسته‌ی بی‌نهایت فعال شد!</b>\n"
            "<i>مصرف منصفانه فعال است.</i>\n\n"
            "🔗 <b>لینک کانفیگ شما:</b>\n"
            f"<code>{html.escape(uri)}</code>\n\n"
            "این لینک را در اپلیکیشن خود وارد کنید یا QR را اسکن کنید."
        )
        png = await qr.png(uri)
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=BytesIO(png),
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard() if idx == len(uris) - 1 else None,
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
    agent_extra_lines = ""
    if agent:
        recent = await db.get_agent_recent_purchase_summary(user.id)
        test_usage = await db.get_agent_test_usage_today(user.id)
        agent_extra_lines = (
            f"\n🧾 خرید ۲۴ ساعت اخیر: {recent['total_toman']:,} تومان"
            f"\n📦 گیگ ۲۴ ساعت اخیر: {recent['total_gb']:,} گیگ"
            f"\n✅ سفارش ۲۴ ساعت اخیر: {recent['order_count']:,}"
            f"\n🧪 کانفیگ تست امروز: {test_usage['used']:,} / {test_usage['limit']:,}"
        )
    if not agent:
        access_level = "کاربر عادی"
        agent_lines = ""
    else:
        access_level = "نماینده"
        agent_lines = ""
    if agent:
        agent_lines += agent_extra_lines
    username = (user.username or snapshot["username"] or "").strip().lstrip("@")
    username_line = f"@{html.escape(username)}" if username else "ندارد"
    text = (
        "👤 <b>حساب کاربری شما</b>\n"
        "<code>─────────────────────</code>\n"
        f"🆔 یوزرآیدی: <code>{user.id}</code>\n"
        f"👤 نام: {html.escape(user.first_name or snapshot['first_name'] or '')}\n"
        f"📛 یوزرنیم: {username_line}\n"
        f"🔐 سطح دسترسی: <b>{access_level}</b>\n"
        f"📅 تاریخ عضویت: {format_join_date(snapshot['joined_at'])}\n"
        "<code>─────────────────────</code>\n"
        f"✅ سفارش‌های تاییدشده: {snapshot['approved_orders']}\n"
        f"👥 زیرمجموعه‌ها: {snapshot['referral_count']}\n"
        f"📦 کل حجم خریداری‌شده: {total_gb:,} گیگ\n"
        f"💰 کل هزینه ریالی: {snapshot['total_spent']:,} تومان\n"
        f"👛 موجودی کیف پول: <b>{snapshot['wallet_balance']:,}</b> تومان"
        f"{agent_lines}"
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
    # Infinite (fair-usage) configs are auto-disabled by the panel once the cap
    # is reached; treat a synced & depleted infinite config as disabled even if
    # the panel hasn't flipped the enable flag yet.
    if is_infinite and total_bytes > 0 and remaining <= 0:
        return "غیرفعال (سقف منصفانه)"
    if enabled is not None and int(enabled) == 0:
        return "غیرفعال (سقف منصفانه)" if is_infinite else "غیرفعال"
    if enabled is None:
        return "نامشخص"
    return "فعال"


def subscription_remaining_label(sub: dict) -> str:
    remaining = int(sub.get("panel_remaining_bytes") or 0)
    if remaining <= 0:
        return "نامشخص"
    return f"{remaining / (1024**3):.2f} گیگ"


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
        if int(sub.get("inbound_id") or 0) == PG_INBOUND_SENTINEL:
            # PasarGuard service: token-based link fetched live; show the service
            # and a button to (re)deliver its subscription link.
            vol_line = "♾️ بسته‌ی نامحدود (مصرف منصفانه)" if is_infinite else f"📦 حجم: {int(sub.get('gb') or 0)} گیگ"
            lines.append(
                f"{idx}. <b>{name}</b> | 🌐 سرور اختصاصی\n"
                f"   {vol_line}\n"
                "   🔗 برای دریافت لینک اتصال، دکمه‌ی پایین را بزنید."
            )
            raw_name = str(sub.get("client_email") or sub.get("sub_id") or "")
            pg_buttons.append(
                InlineKeyboardButton(f"🔗 لینک {raw_name[:18]}", callback_data=f"pgsub:link:{sub.get('sub_id')}")
            )
            continue
        if is_infinite:
            # Fair-usage "infinite" config: never reveal the volume cap or the
            # remaining traffic — only the type and on/off status.
            sub_enabled = sub.get("panel_enabled")
            total_b = int(sub.get("panel_total_bytes") or 0)
            remain_b = int(sub.get("panel_remaining_bytes") or 0)
            depleted = (sub_enabled is not None and int(sub_enabled) == 0) or (total_b > 0 and remain_b <= 0)
            status = "غیرفعال (سقف منصفانه)" if depleted else "فعال"
            lines.append(
                f"{idx}. <b>{name}</b> | ♾️ بی‌نهایت\n"
                f"   🆔 <code>{sub_id}</code>\n"
                f"   ♾️ بسته‌ی بی‌نهایت (مصرف منصفانه) | وضعیت: {status}"
            )
            if depleted:
                lines.append("   ♾️ این کانفیگ به سقف مصرف منصفانه رسیده و غیرفعال شده است.")
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
            f"   📦 حجم: {int(sub.get('gb') or 0)} گیگ | وضعیت: {subscription_status_label(sub)} | باقی‌مانده: {subscription_remaining_label(sub)}"
        )

        if is_test:
            lines.append(f"   🧪 نوع: تست | حجم واقعی: {volume_label} | اعتبار: ۱۰ دقیقه")

    nav: list[InlineKeyboardButton] = []
    if safe_page > 0:
        nav.append(InlineKeyboardButton("قبلی", callback_data=f"subs:page:{safe_page - 1}"))
    if safe_page < total_pages - 1:
        nav.append(InlineKeyboardButton("بعدی", callback_data=f"subs:page:{safe_page + 1}"))
    rows = []
    for b in pg_buttons:
        rows.append([b])
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
    if not sub or int(sub.get("inbound_id") or 0) != PG_INBOUND_SENTINEL:
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
            "🌐 <b>سرور اختصاصی</b>\n"
            "<code>─────────────────────</code>\n"
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


async def _pg_renewal_packages(db: AsyncDatabase) -> list[dict]:
    """PasarGuard plans offered for renewal, in the legacy package shape.

    Renewal replaces a service's plan in place, so only fixed-volume plans are
    offered — a variable plan has no single price to renew at. Reading from the
    catalog means a renewal quotes the same price the buy flow does.
    """
    data = await catalog.load_catalog(db)
    out: list[dict] = []
    for plan in data.get("plans") or []:
        if not plan.get("enabled"):
            continue
        target = plan.get("target") or {}
        if target.get("kind") != catalog.TARGET_PASARGUARD:
            continue
        if str((plan.get("volume") or {}).get("mode")) == catalog.VOLUME_VARIABLE:
            continue
        out.append(catalog.legacy_equivalent(plan))
    return out


async def _pg_current_plan_index(sub: dict, packages: list[dict]) -> int | None:
    """Index of the package matching the sub's current plan (kind + volume), or None."""
    kind = "unlimited" if int(sub.get("is_infinite") or 0) == 1 else "volume"
    gb = int(sub.get("gb") or 0)
    for i, pkg in enumerate(packages):
        if pkg.get("kind") == kind and int(pkg.get("gb") or 0) == gb:
            return i
    return None


async def renew_show_gb_options(update: Update, context: ContextTypes.DEFAULT_TYPE, *, sub_id: str, name: str) -> int:
    query = update.callback_query
    db: AsyncDatabase = context.application.bot_data["db"]
    min_gb = await minimum_purchase_gb(db)
    await edit_text(
        query,
        "➕ <b>تمدید حجمی</b>\n\n"
        f"کانفیگ: <b>{html.escape(name)}</b>\n\n"
        f"چه مقدار حجم اضافه شود؟\nحداقل حجم مجاز: <b>{min_gb}</b> گیگ",
        await gb_choice_keyboard(db, update.effective_user.id, "renew", "renew:cancel", min_gb),
    )
    context.user_data[FLOW_PROMPT_KEY] = query.message.message_id
    return RENEW_GB


async def renew_show_plans(update: Update, context: ContextTypes.DEFAULT_TYPE, *, sub_id: str, name: str, sub: dict) -> int:
    """Show every PasarGuard plan for renewal — the user's current plan is
    flagged, and any plan can be chosen (renew the same one or upgrade)."""
    query = update.callback_query
    db: AsyncDatabase = context.application.bot_data["db"]
    agent = await db.get_agent(update.effective_user.id)
    packages = await _pg_renewal_packages(db)
    if not packages:
        await edit_text(
            query,
            "🌐 در حال حاضر پلنی برای تمدید تعریف نشده است؛ لطفاً با پشتیبانی در ارتباط باشید.",
            back_keyboard(),
        )
        return ConversationHandler.END
    cur_idx = await _pg_current_plan_index(sub, packages)
    rows: list[list[InlineKeyboardButton]] = []
    for idx, pkg in enumerate(packages):
        price = package_price(pkg, agent)
        prefix = "♻️ پلن فعلی · " if idx == cur_idx else ""
        rows.append([InlineKeyboardButton(f"{prefix}{pkg.get('title') or '-'} — {price:,} ت", callback_data=f"renew:pkg:{idx}")])
    rows.append([InlineKeyboardButton("❌ انصراف", callback_data="renew:cancel")])
    cur_line = ""
    if cur_idx is not None:
        cur_line = f"🎁 پلن فعلی شما: <b>{html.escape(str(packages[cur_idx].get('title') or ''))}</b>\n"
    await edit_text(
        query,
        "🔁 <b>تمدید سرویس</b>\n\n"
        f"🪪 کانفیگ: <b>{html.escape(name)}</b>\n"
        f"{cur_line}\n"
        "می‌توانید همین پلن را تمدید کنید یا پلن دیگری (بالاتر) را انتخاب کنید 👇",
        InlineKeyboardMarkup(rows),
    )
    context.user_data[FLOW_PROMPT_KEY] = query.message.message_id
    return RENEW_SELECT


async def renew_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    sub_id = query.data.split(":", 2)[2]
    db: AsyncDatabase = context.application.bot_data["db"]
    sub = await db.get_subscription_for_user(update.effective_user.id, sub_id)
    if not sub:
        await edit_text(query, "⚠️ این اشتراک پیدا نشد. لطفاً دوباره از لیست انتخاب کنید.", back_keyboard())
        return ConversationHandler.END
    is_pg = int(sub.get("inbound_id") or 0) == PG_INBOUND_SENTINEL
    if is_pg:
        if not await pg_configured(db):
            await edit_text(
                query,
                "🌐 <b>این سرویس روی سرور اختصاصی (PasarGuard) است.</b>\n\n"
                "تمدید این سرویس فعلاً در دسترس نیست؛ برای تمدید با پشتیبانی در ارتباط باشید.",
                back_keyboard(),
            )
            return ConversationHandler.END
    elif await is_panel2_subscription(db, sub):
        # v1: dedicated-panel services are buy-only from the bot; renewal of them
        # isn't wired yet, so guide the user to support instead of failing on the
        # primary panel.
        await edit_text(
            query,
            "🌐 <b>این سرویس روی سرور اختصاصی است.</b>\n\n"
            "تمدید این نوع سرویس فعلاً از داخل ربات فعال نیست؛ برای تمدید با پشتیبانی در ارتباط باشید.",
            back_keyboard(),
        )
        return ConversationHandler.END
    name = str(sub.get("client_email") or sub_id)
    context.user_data["renewal"] = {"sub_id": sub_id, "client_name": name}
    # PasarGuard services renew by choosing a plan (same plan or an upgrade);
    # legacy panel services keep the per-GB volume flow.
    if is_pg:
        return await renew_show_plans(update, context, sub_id=sub_id, name=name, sub=sub)
    return await renew_show_gb_options(update, context, sub_id=sub_id, name=name)


async def renew_pkg_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    db: AsyncDatabase = context.application.bot_data["db"]
    try:
        idx = int(query.data.split(":", 2)[2])
    except Exception:
        return ConversationHandler.END
    renewal = context.user_data.setdefault("renewal", {})
    sub_id = str(renewal.get("sub_id") or "")
    name = str(renewal.get("client_name") or sub_id)
    if not sub_id:
        await edit_text(query, "⚠️ اطلاعات تمدید ناقص است. لطفاً دوباره شروع کنید.", back_keyboard())
        return ConversationHandler.END
    packages = await _pg_renewal_packages(db)
    if idx < 0 or idx >= len(packages):
        await edit_text(query, "⚠️ این پلن دیگر در دسترس نیست. لطفاً دوباره انتخاب کنید.", back_keyboard())
        return ConversationHandler.END
    pkg = packages[idx]
    agent = await db.get_agent(update.effective_user.id)
    price = package_price(pkg, agent)
    # Store the chosen plan snapshot itself (not just its index) so confirmation
    # never depends on re-deriving/looking up the index again.
    renewal["mode"] = "plan"
    renewal["pkg"] = dict(pkg)
    renewal["idem"] = f"renew-plan-{update.effective_user.id}-{secrets.token_hex(8)}"
    return await _render_renew_plan_invoice(update, context)


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
    method_label = "کیف پول نماینده" if agent else "کسر از کیف پول"
    renewal["idem"] = f"renew-{update.effective_user.id}-{secrets.token_hex(8)}"
    renewal.update(unit_price=unit_price, total=total, method_label=method_label)
    return await _render_renew_volume_invoice(update, context)


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
    if renewal.get("mode") == "plan" or isinstance(renewal.get("pkg"), dict):
        return await renew_confirm_plan(update, context)
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
    renew_discount = stored_discount_amount(context, "renewal")
    # This per-GB volume path only runs for legacy 3x-ui subs; PasarGuard subs
    # renew via the plan flow (mode == "plan" → renew_confirm_plan) above.
    await edit_flow_query(update, context, "⏳ <b>در حال تمدید اشتراک...</b>\n\nلطفاً چند لحظه صبر کنید.")
    try:
        sub_link = await provisioning.process_renewal(
            user_id=update.effective_user.id,
            sub_id=sub_id,
            gb=gb,
            unit_price=unit_price,
            final_total=total,
            idempotency_key=str(renewal.get("idem") or query.id),
            discount_code=stored_discount_code(context, "renewal"),
            expected_discount=stored_discount_amount(context, "renewal"),
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

    clear_flow_state(context)
    await edit_text(
        query,
        "✅ <b>تمدید با موفقیت انجام شد.</b>\n\n"
        f"📦 حجم اضافه‌شده: <b>{gb}</b> گیگ\n"
        f"💰 مبلغ پرداختی: <b>{max(0, total - renew_discount):,}</b> تومان"
        + (f" (تخفیف {renew_discount:,} تومان)" if renew_discount else "")
        + "\n"
        f"🟢 روش ثبت: {html.escape(method_label)}\n\n"
        "🔗 لینک اشتراک شما:\n"
        f"<code>{html.escape(sub_link)}</code>",
        back_keyboard(),
    )
    return ConversationHandler.END


async def renew_confirm_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Confirm renewing a PasarGuard service with the chosen plan (same or upgrade)."""
    query = update.callback_query
    db: AsyncDatabase = context.application.bot_data["db"]
    renewal = context.user_data.get("renewal") or {}
    sub_id = str(renewal.get("sub_id") or "")
    name = str(renewal.get("client_name") or sub_id)
    pkg = renewal.get("pkg")
    if not sub_id or not isinstance(pkg, dict) or int(pkg.get("gb") or 0) <= 0:
        clear_flow_state(context)
        await edit_text(query, "⚠️ اطلاعات تمدید کامل نیست. لطفاً دوباره شروع کنید.", back_keyboard())
        return ConversationHandler.END
    price = package_price(pkg, await db.get_agent(update.effective_user.id))
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
    renew_discount = stored_discount_amount(context, "renewal")
    await edit_flow_query(update, context, "⏳ <b>در حال تمدید سرویس...</b>\n\nلطفاً چند لحظه صبر کنید.")
    try:
        pg_client = await get_pg_client(context)
        if pg_client is None:
            raise ValueError("سرور اختصاصی در حال حاضر در دسترس نیست؛ لطفاً بعداً تلاش کنید.")
        sub_link = await provisioning.process_pg_plan_renewal(
            pg_client=pg_client,
            user_id=update.effective_user.id,
            sub_id=sub_id,
            pkg=pkg,
            idempotency_key=str(renewal.get("idem") or query.id),
            discount_code=stored_discount_code(context, "renewal"),
            expected_discount=stored_discount_amount(context, "renewal"),
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
        LOG.exception("plan renewal failed user_id=%s sub_id=%s", update.effective_user.id, sub_id)
        clear_flow_state(context)
        await edit_text(query, f"❌ خطا در تمدید سرویس:\n{html.escape(str(exc))}", back_keyboard())
        return ConversationHandler.END

    clear_flow_state(context)
    vol_line = "♾️ نامحدود (مصرف منصفانه)" if str(pkg.get("kind")) == "unlimited" else f"{int(pkg.get('gb') or 0)} گیگ"
    days = int(pkg.get("days") or 0)
    days_line = f"⏳ اعتبار: <b>{days}</b> روز (از اولین اتصال)\n" if days > 0 else ""
    await edit_text(
        query,
        "✅ <b>سرویس با موفقیت تمدید شد.</b>\n\n"
        f"🪪 کانفیگ: <b>{html.escape(name)}</b>\n"
        f"🎁 پلن: <b>{html.escape(str(pkg.get('title') or '-'))}</b>\n"
        f"📦 حجم: <b>{vol_line}</b>\n"
        f"{days_line}"
        f"💰 مبلغ پرداختی: <b>{max(0, price - renew_discount):,}</b> تومان"
        + (f" (تخفیف {renew_discount:,} تومان)" if renew_discount else "")
        + "\n\n"
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
    tiers = await get_price_tiers(db)
    if agent_price <= 0 and tiers:
        lines = [
            "⚡ <b>تعرفه پلکانی سرویس‌ها</b>",
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
    await new_flow_card(
        update,
        context,
        "🏷 <b>تعرفه سرویس‌ها</b>\n\n"
        f"💎 قیمت هر گیگابایت برای شما: <b>{unit_price:,}</b> تومان\n\n"
        "<i>پرداخت بر اساس حجم مصرفی — بدون محدودیت زمانی.</i>",
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
        "🛟 <b>پشتیبانی NavidVPN</b>\n\n"
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
    is_agent = bool(await db.get_agent(update.effective_user.id))
    # Regular users get a one-time free test; gate it on the toggle.
    if not is_agent and not await free_test_enabled(db):
        await new_flow_card(update, context, "🧪 تست رایگان در حال حاضر فعال نیست.", back_keyboard())
        return
    dur_label = "۱۰ دقیقه" if is_agent else "۱ روز"
    await new_flow_card(
        update,
        context,
        "⏳ <b>در حال ساخت کانفیگ تست...</b>\n\n"
        f"حجم تست ۲۰۰ مگابایت و اعتبار زمانی آن {dur_label} است.",
    )
    provisioning: ProvisioningService = context.application.bot_data["provisioning"]
    qr: QRService = context.application.bot_data["qr"]
    idem_key = query.id if query else f"test-{update.effective_user.id}-{update.effective_message.message_id}"
    try:
        # When PasarGuard is the primary backend, BOTH the agent test and the
        # free test are created there (same as real sales). Resolve the target
        # group once and hand it to whichever flow runs.
        pg_client = await get_pg_client(context) if await pg_is_primary(db) else None
        group_ids = None
        if pg_client is not None:
            group = (await db.get_setting("pg_group", "Tsco-Bot") or "Tsco-Bot").strip()
            group_ids = await pg_client.resolve_group_ids([group])
            if not group_ids:
                raise ValueError("سرور تست در دسترس نیست؛ لطفاً بعداً تلاش کنید.")
        if is_agent:
            sub_link = await provisioning.process_agent_test_config(
                user_id=update.effective_user.id,
                pg_client=pg_client,
                group_ids=group_ids,
                idempotency_key=idem_key,
            )
        else:
            # One-time free test on the primary backend.
            sub_link = await provisioning.process_free_test(
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

    await send_flow_prompt(
        update,
        context,
        "✅ <b>کانفیگ تست آماده شد.</b>\n\n"
        "📦 حجم: <b>۲۰۰ مگابایت</b>\n"
        f"⏱ اعتبار: <b>{dur_label}</b>\n\n"
        "لینک و QR Code در پیام بعدی ارسال می‌شود.",
        back_keyboard(),
    )
    png = await qr.png(sub_link)
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=BytesIO(png),
        caption=(
            f"🧪 <b>{'کانفیگ تست نماینده' if is_agent else 'کانفیگ تست رایگان'}</b>\n\n"
            "📦 حجم: <b>۲۰۰ مگابایت</b>\n"
            f"⏱ اعتبار: <b>{dur_label}</b>\n\n"
            f"<code>{html.escape(sub_link)}</code>"
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
        "💎 <b>کیف پول من</b>\n"
        "<code>─────────────────────</code>\n"
        f"💰 موجودی فعلی: <b>{wallet:,}</b> تومان\n"
        f"🏷 مبنای شارژ پیشنهادی: <b>{unit_price:,}</b> تومان\n\n"
        "برای افزایش موجودی، یکی از روش‌های زیر را انتخاب کنید 👇",
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
    context.user_data["topup"] = {"method": method, "unit_price": unit_price, "tier_mode": True}
    method_label = "کارت به کارت" if method == "card" else "رمزارز (تتر)"
    # Always ask for a free amount — the user types whatever they want to top up.
    await new_flow_card(
        update,
        context,
        f"💳 <b>شارژ کیف پول — {method_label}</b>\n"
        "<code>─────────────────────</code>\n"
        "💰 مبلغ موردنظر برای شارژ را به <b>تومان</b> وارد کنید.\n"
        "<i>مثال: 200000</i>\n\n"
        "فقط عدد بفرستید 👇",
        InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="topup:cancel")]]),
    )
    return TOPUP_CUSTOM_AMOUNT


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
            amount = int(topup["amount_toman"])
            approve_msg = (
                "🎉 <b>شارژ کیف پول شما تایید شد.</b>\n\n"
                f"مبلغ شارژشده: <b>{amount:,}</b> تومان"
            )
            await context.bot.send_message(
                chat_id=int(topup["user_id"]),
                text=approve_msg,
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
    """Handles approve / reject on the original request message."""
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

    # approve_open/approve_closed are accepted for old in-flight admin messages,
    # but they now enter the same wallet-only representative flow.
    context.user_data["admin_agent_approval"] = {
        "req_id": req_id,
        "access_level": "closed",
        "price_per_gb": None,
    }
    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=(
            f"⚙️ <b>تنظیم نماینده – کیف‌پولی</b>\n"
            f"درخواست: <code>{req_id}</code>\n\n"
            + await agent_pricing_text(db)
        ),
        reply_markup=agent_admin_pricing_keyboard(),
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
    if price_str == "packages":
        price = 0  # package agent prices govern; no per-GB rate
    elif price_str == "def":
        price = int(await db.get_setting("default_agent_price_per_gb", "0") or "0")
    else:
        price = int(price_str)

    approval["price_per_gb"] = price
    await query.answer()
    confirm_line = (
        "✅ قیمت‌گذاری: <b>بر اساس قیمتِ نماینده‌یِ بسته‌ها</b> ثبت شد."
        if price <= 0
        else f"✅ قیمت هر گیگ: <b>{price:,}</b> تومان ثبت شد."
    )
    await query.edit_message_text(
        f"{confirm_line}\n\n🧪 <b>سهمیه کانفیگ تست روزانه</b> را انتخاب کنید:",
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
    price_per_gb = int(approval.get("price_per_gb") or 0)
    req_id = approval["req_id"]

    summary = (
        f"📋 <b>خلاصه تنظیمات نماینده</b>\n\n"
        f"درخواست: <code>{req_id}</code>\n"
        "نوع پرداخت: <b>کیف‌پولی</b>\n"
    )
    summary += (
        "قیمت‌گذاری: <b>بر اساس قیمتِ نماینده‌یِ بسته‌ها</b>\n"
        if price_per_gb <= 0
        else f"قیمت هر گیگ: <b>{price_per_gb:,}</b> تومان\n"
    )
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
    price_per_gb = int(approval.get("price_per_gb") or 0)
    daily_test_limit = int(approval.get("daily_test_limit") or 0)

    db: AsyncDatabase = context.application.bot_data["db"]
    result = await db.approve_agent_request_as_agent(
        req_id=req_id,
        access_level="closed",
        credit_limit_toman=0,
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

    msg = (
        "✅ <b>درخواست نمایندگی شما تایید شد.</b>\n\n"
        "نوع پرداخت شما: <b>کیف‌پولی</b>\n"
    )
    if price_per_gb > 0:
        msg += f"قیمت هر گیگ: <b>{price_per_gb:,}</b> تومان\n"
    if daily_test_limit > 0:
        msg += f"کانفیگ تست روزانه: <b>{daily_test_limit}</b> عدد\n"
    msg += "برای خرید کافی است کیف پولتان شارژ باشد؛ سرویس بعد از پرداخت فوری ساخته می‌شود."

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
    """Handles reply keyboard navigation from any conversation state. Buttons are
    matched by their live (renamable) label via the in-memory routing index."""
    text = (update.effective_message.text or "").strip()
    action = _NAV_TEXT_TO_ACTION.get(text)
    clear_flow_state(context)
    if action == "buy":
        return await buy_start(update, context)
    if action == "panel2":
        return await buy2_start(update, context)
    if action == "renew":
        return await renew_start(update, context)
    if action == "subs":
        await my_subscriptions(update, context)
        return ConversationHandler.END
    if action == "account":
        await account_info(update, context)
        return ConversationHandler.END
    if action == "wallet":
        await wallet_info(update, context)
        return ConversationHandler.END
    if action == "tariffs":
        await tariffs_info(update, context)
        return ConversationHandler.END
    if action == "support":
        await support_info(update, context)
        return ConversationHandler.END
    if action == "test_config":
        await agent_test_config(update, context)
        return ConversationHandler.END
    if action == "agent_request":
        return await agent_request_start(update, context)
    return ConversationHandler.END


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

    if awaiting == "price":
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
    elif awaiting == "daily_test":
        approval["daily_test_limit"] = value
        price_per_gb = int(approval.get("price_per_gb") or 0)
        req_id = approval["req_id"]
        summary = (
            f"📋 <b>خلاصه تنظیمات نماینده</b>\n\n"
            f"درخواست: <code>{req_id}</code>\n"
            "نوع پرداخت: <b>کیف‌پولی</b>\n"
        )
        summary += (
            "قیمت‌گذاری: <b>بر اساس قیمتِ نماینده‌یِ بسته‌ها</b>\n"
            if price_per_gb <= 0
            else f"قیمت هر گیگ: <b>{price_per_gb:,}</b> تومان\n"
        )
        summary += f"کانفیگ تست روزانه: <b>{value}</b> عدد\n\n"
        summary += "آیا تایید نهایی می‌کنید؟"
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=summary,
            reply_markup=agent_admin_confirm_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="جلسه تایید نماینده معتبر نیست. لطفاً از پیام مدیریت دوباره شروع کنید.",
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
        CallbackQueryHandler(buy2_start, pattern=r"^menu:buy2$"),
        CallbackQueryHandler(renew_start, pattern=r"^menu:renew$"),
        CallbackQueryHandler(topup_c2c_start, pattern=r"^wallet:c2c$"),
        CallbackQueryHandler(topup_crypto_start, pattern=r"^wallet:crypto$"),
        CallbackQueryHandler(agent_request_start, pattern=r"^menu:agent_request$"),
        MessageHandler(_nav_filter, handle_nav_btn),
    ]
    states = {
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
        PKG_SELECT: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(catalog_category_select, pattern=r"^cat:[A-Za-z0-9_\-]+$"),
            CallbackQueryHandler(pkg_volume_select, pattern=r"^pkg:gb:[A-Za-z0-9_\-]+:\d+$"),
            CallbackQueryHandler(pkg_select, pattern=r"^pkg:sel:[A-Za-z0-9_\-]+$"),
            CallbackQueryHandler(buy_cancel, pattern=r"^buy:cancel$"),
        ],
        PKG_NAME_MODE: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(pkg_name_random, pattern=r"^pkg:name:random:[A-Za-z0-9_\-]+:(?:-|\d+)$"),
            CallbackQueryHandler(pkg_name_custom_start, pattern=r"^pkg:name:custom:[A-Za-z0-9_\-]+:(?:-|\d+)$"),
            CallbackQueryHandler(buy_cancel, pattern=r"^buy:cancel$"),
        ],
        PKG_NAME_INPUT: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(buy_cancel, pattern=r"^buy:cancel$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, pkg_name_input),
        ],
        PKG_CONFIRM: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(pkg_confirm, pattern=r"^pkg:ok:[A-Za-z0-9_\-]+:(?:-|\d+)$"),
            CallbackQueryHandler(pkg_discount_start, pattern=r"^buy:disc:add$"),
            CallbackQueryHandler(pkg_discount_clear, pattern=r"^buy:disc:clear$"),
            CallbackQueryHandler(buy_cancel, pattern=r"^buy:cancel$"),
        ],
        PKG_DISCOUNT: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(pkg_discount_back, pattern=r"^buy:disc:back$"),
            CallbackQueryHandler(buy_cancel, pattern=r"^buy:cancel$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, pkg_discount_typed),
        ],
        RENEW_SELECT: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(renew_page, pattern=r"^renew:page:\d+$"),
            CallbackQueryHandler(renew_search_start, pattern=r"^renew:search$"),
            CallbackQueryHandler(renew_clear_search, pattern=r"^renew:clear_search$"),
            CallbackQueryHandler(renew_pkg_select, pattern=r"^renew:pkg:\d+$"),
            CallbackQueryHandler(renew_select, pattern=r"^renew:sub:"),
            CallbackQueryHandler(renew_cancel, pattern=r"^renew:cancel$"),
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
            CallbackQueryHandler(renew_discount_start, pattern=r"^renew:disc:add$"),
            CallbackQueryHandler(renew_discount_clear, pattern=r"^renew:disc:clear$"),
            CallbackQueryHandler(renew_cancel, pattern=r"^renew:cancel$"),
        ],
        RENEW_DISCOUNT: [
            MessageHandler(_nav_filter, handle_nav_btn),
            CallbackQueryHandler(renew_discount_back, pattern=r"^renew:disc:back$"),
            CallbackQueryHandler(renew_cancel, pattern=r"^renew:cancel$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, renew_discount_typed),
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
    """Global safety net: log any unhandled error and tell the user gently,
    instead of leaving them with a silent failure. Best-effort — never raises."""
    LOG.exception("unhandled error while processing update", exc_info=context.error)
    try:
        if isinstance(update, Update):
            if update.callback_query:
                with contextlib.suppress(Exception):
                    await update.callback_query.answer("خطایی رخ داد. دوباره تلاش کنید.", show_alert=False)
            chat = update.effective_chat
            if chat is not None:
                with contextlib.suppress(Exception):
                    await context.bot.send_message(
                        chat_id=chat.id,
                        text="⚠️ مشکلی پیش آمد. لطفاً چند لحظه بعد دوباره تلاش کنید یا با پشتیبانی در ارتباط باشید.",
                        parse_mode=ParseMode.HTML,
                    )
    except Exception:
        LOG.exception("error handler itself failed")


def register_handlers(app: Application) -> None:
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("start", start))
    # Admin custom text input (group=-1 runs before ConversationHandler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_custom_text_handler), group=-1)
    # Admin callbacks (before conversation handlers so they match regardless of state)
    app.add_handler(CallbackQueryHandler(topup_admin_callback, pattern=r"^topup_admin:(approve|reject):"))
    app.add_handler(CallbackQueryHandler(agent_admin_callback, pattern=r"^agent_admin:(approve|approve_open|approve_closed|reject):"))
    app.add_handler(CallbackQueryHandler(agent_admin_custom_price_start, pattern=r"^agent_admin:pg:custom$"))
    app.add_handler(CallbackQueryHandler(agent_admin_custom_daily_test_start, pattern=r"^agent_admin:dt:custom$"))
    app.add_handler(CallbackQueryHandler(agent_admin_set_price, pattern=r"^agent_admin:pg:"))
    app.add_handler(CallbackQueryHandler(agent_admin_set_daily_test, pattern=r"^agent_admin:dt:"))
    app.add_handler(CallbackQueryHandler(agent_admin_final_confirm, pattern=r"^agent_admin:ok$"))
    app.add_handler(CallbackQueryHandler(agent_admin_flow_cancel, pattern=r"^agent_admin:cancel$"))
    # Merged conversation handler for all user flows
    app.add_handler(build_main_conversation())
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, pkg_name_input_standalone))
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
    app.add_handler(CallbackQueryHandler(catalog_category_select, pattern=r"^cat:[A-Za-z0-9_\-]+$"))
    app.add_handler(CallbackQueryHandler(pkg_volume_select, pattern=r"^pkg:gb:[A-Za-z0-9_\-]+:\d+$"))
    app.add_handler(CallbackQueryHandler(pkg_select, pattern=r"^pkg:sel:[A-Za-z0-9_\-]+$"))
    app.add_handler(CallbackQueryHandler(pkg_name_random, pattern=r"^pkg:name:random:[A-Za-z0-9_\-]+:(?:-|\d+)$"))
    app.add_handler(CallbackQueryHandler(pkg_name_custom_start, pattern=r"^pkg:name:custom:[A-Za-z0-9_\-]+:(?:-|\d+)$"))
    app.add_handler(CallbackQueryHandler(pkg_confirm, pattern=r"^pkg:ok:[A-Za-z0-9_\-]+:(?:-|\d+)$"))
    app.add_handler(CallbackQueryHandler(support_info, pattern=r"^menu:support$"))
    app.add_handler(CallbackQueryHandler(wallet_info, pattern=r"^menu:wallet$"))
