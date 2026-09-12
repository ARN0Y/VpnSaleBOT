"""How the shop looks: the banner above the welcome, and the button styling.

Telegram gives a bot very little control over appearance. Two things it does
give, and both are here:

  * A photo above the welcome message. Setting one is what turns a wall of text
    into something that looks like a shop.
  * Emoji on the buttons. Telegram will not colour a reply-keyboard button, so
    "coloured buttons" in practice means a leading emoji per action — the same
    trick the bots this was modelled on use. The operator picks it, and it is
    stitched onto whatever label they chose.

Everything is a setting, so changing the look never means a deploy.
"""
from __future__ import annotations

SETTING_BANNER = "banner_file_id"
SETTING_BANNER_URL = "banner_url"
SETTING_BANNER_ENABLED = "banner_enabled"
SETTING_BUTTON_STYLE = "button_style"

# Telegram renders a reply keyboard in its own colours; the only per-button
# signal a bot controls is the text. These sets give each action a consistent
# leading glyph so the keyboard reads as a designed thing rather than a list.
STYLES: dict[str, dict[str, str]] = {
    "plain": {},
    "colorful": {
        "buy": "🟢", "renew": "🔵", "subs": "🟣", "account": "⚪️",
        "wallet": "🟡", "support": "🔴", "test_config": "🟠", "agent_request": "💎",
    },
    "minimal": {
        "buy": "▸", "renew": "▸", "subs": "▸", "account": "▸",
        "wallet": "▸", "support": "▸", "test_config": "▸", "agent_request": "▸",
    },
}
STYLE_LABELS: dict[str, str] = {
    "plain": "بدون تغییر (همان متن دکمه)",
    "colorful": "رنگی (دایره‌های رنگی)",
    "minimal": "مینیمال (فلش ساده)",
}


def style_label(name: str) -> str:
    return STYLE_LABELS.get(name, name)


def decorate(action: str, label: str, style: str) -> str:
    """A button label with its style glyph, without doubling one already there."""
    glyph = STYLES.get(style or "plain", {}).get(action, "")
    text = str(label or "").strip()
    if not glyph or not text or text.startswith(glyph):
        return text
    return f"{glyph} {text}"


async def button_style(db) -> str:
    name = str(await db.get_setting(SETTING_BUTTON_STYLE, "plain") or "plain").strip()
    return name if name in STYLES else "plain"


async def banner(db) -> str:
    """What to send above the welcome, or "" for none.

    A file id uploaded through the panel is preferred: Telegram serves it back
    instantly and it cannot rot the way a URL can. A URL is the fallback for
    operators who would rather host it themselves.
    """
    if str(await db.get_setting(SETTING_BANNER_ENABLED, "1") or "1").strip() in {"0", "false", "off", "no"}:
        return ""
    file_id = str(await db.get_setting(SETTING_BANNER, "") or "").strip()
    if file_id:
        return file_id
    return str(await db.get_setting(SETTING_BANNER_URL, "") or "").strip()


# ── the admin panel's sign-in screen ──
# Its look is configurable because the panel is handed to a customer who wants
# it to be theirs. The settings are read by a PUBLIC endpoint: this page renders
# before anyone has signed in, so it cannot ask for credentials to decorate
# itself. Nothing secret goes near it.

SETTING_LOGIN_TITLE = "login_title"
SETTING_LOGIN_TAGLINE = "login_tagline"
SETTING_LOGIN_IMAGE = "login_image_url"
SETTING_LOGIN_LAYOUT = "login_layout"
SETTING_LOGIN_OVERLAY = "login_overlay"

LAYOUTS: dict[str, str] = {
    "split-right": "دو ستونه — تصویر سمت چپ",
    "split-left": "دو ستونه — تصویر سمت راست",
    "centered": "تک ستونه — کارت روی تصویر",
}
DEFAULT_LOGIN_TITLE = "ورود مدیریت"
DEFAULT_LOGIN_TAGLINE = "دسترسی مدیر به پنل فروش"


async def login_look(db) -> dict:
    """Everything the sign-in screen needs to draw itself.

    Always a complete, presentable configuration — a value that was never set,
    or set to something unknown, falls back rather than leaving the page blank.
    """
    layout = str(await db.get_setting(SETTING_LOGIN_LAYOUT, "") or "").strip()
    try:
        overlay = int(await db.get_setting(SETTING_LOGIN_OVERLAY, "55") or 55)
    except (TypeError, ValueError):
        overlay = 55
    return {
        "title": str(await db.get_setting(SETTING_LOGIN_TITLE, "") or "").strip() or DEFAULT_LOGIN_TITLE,
        "tagline": str(await db.get_setting(SETTING_LOGIN_TAGLINE, "") or "").strip() or DEFAULT_LOGIN_TAGLINE,
        "image_url": str(await db.get_setting(SETTING_LOGIN_IMAGE, "") or "").strip(),
        "layout": layout if layout in LAYOUTS else "split-right",
        # How far to dim the artwork. The operator picks the picture, so the
        # panel cannot assume it is dark enough for white text.
        "overlay": max(0, min(90, overlay)),
        "layouts": [{"key": k, "label": v} for k, v in LAYOUTS.items()],
    }
