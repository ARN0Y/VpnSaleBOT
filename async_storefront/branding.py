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
