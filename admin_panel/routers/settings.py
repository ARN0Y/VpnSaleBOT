from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, RedirectResponse

from async_storefront.env_sync import sync_env_from_admin
from async_storefront.util import now_ts

from ..backup import (
    DEFAULT_XUI_BACKUP_TIMEOUT_SECONDS,
    list_backup_files,
    normalize_xui_backup_timeout,
    run_backup_now,
)
from .common import current_admin_username, db, render

router = APIRouter(prefix="/admin/settings")
LOG = logging.getLogger(__name__)

EDITABLE_KEYS = (
    "price_per_gb",
    "minimum_purchase_gb",
    "card_number",
    "card_name",
    "crypto_address",
    "support_id",
    "admin_user_ids",
    "default_agent_credit_limit_toman",
    "default_agent_price_per_gb",
    "broadcast_rate_per_second",
    "broadcast_concurrency",
)
INT_SETTING_KEYS = {
    "price_per_gb",
    "minimum_purchase_gb",
    "default_agent_credit_limit_toman",
    "default_agent_price_per_gb",
    "broadcast_rate_per_second",
    "broadcast_concurrency",
}
SETTING_FORM_DEFAULTS = {
    "price_per_gb": "200000",
    "minimum_purchase_gb": "1",
    "default_agent_credit_limit_toman": "0",
    "default_agent_price_per_gb": "0",
    "broadcast_rate_per_second": "25",
    "broadcast_concurrency": "16",
}
BACKUP_UNITS = {"minutes", "hours", "days", "weeks"}
PANEL_FORM_KEYS = {"panel_base_url", "panel_username", "panel_password", "panel_inbound_id", "sub_link_base"}
SETTING_META = {
    "price_per_gb": {
        "label": "قیمت هر گیگ",
        "help": "تعرفه پایه برای کاربران عادی؛ نماینده‌ها اگر قیمت اختصاصی داشته باشند از قیمت خودشان استفاده می‌کنند.",
        "type": "number",
        "min": "0",
    },
    "minimum_purchase_gb": {
        "label": "حداقل میزان خرید",
        "help": "کمترین حجم مجاز برای خرید جدید و تمدید. دکمه‌های ربات با ضریب‌های ۱، ۲، ۳ و ۴ همین مقدار ساخته می‌شوند.",
        "type": "number",
        "min": "1",
    },
    "card_number": {"label": "شماره کارت"},
    "card_name": {"label": "نام صاحب کارت"},
    "crypto_address": {"label": "آدرس تتر"},
    "support_id": {"label": "آیدی پشتیبانی"},
    "admin_user_ids": {
        "label": "ادمین‌های ربات",
        "help": "هر خط یا هر کاما یک user_id ادمین. اگر خالی باشد، ADMIN_ID اصلی fallback می‌شود.",
    },
    "default_agent_credit_limit_toman": {"label": "سقف اعتبار پیش‌فرض نماینده", "type": "number", "min": "0"},
    "default_agent_price_per_gb": {"label": "قیمت پیش‌فرض نماینده", "type": "number", "min": "0"},
    "broadcast_rate_per_second": {
        "label": "سرعت ارسال پیام همگانی",
        "help": "حداکثر پیام در ثانیه برای broadcast؛ برای امنیت زیر سقف تلگرام clamp می‌شود.",
        "type": "number",
        "min": "1",
    },
    "broadcast_concurrency": {
        "label": "همزمانی ارسال پیام همگانی",
        "help": "تعداد workerهای همزمان برای broadcast. نرخ نهایی همچنان با محدودکننده کنترل می‌شود.",
        "type": "number",
        "min": "1",
    },
}


def as_int(value, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except Exception:
        return default


def normalize_admin_ids(value) -> str:
    ids: list[str] = []
    for part in str(value or "").replace("\n", ",").split(","):
        item = part.strip()
        if item.isdigit() and item not in ids:
            ids.append(item)
    return ",".join(ids)


def settings_values_from_form(form, current: dict[str, str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for key in EDITABLE_KEYS:
        fallback = current.get(key, SETTING_FORM_DEFAULTS.get(key, ""))
        raw = form.get(key, fallback)
        if key in INT_SETTING_KEYS:
            parsed = max(0, as_int(raw, as_int(fallback, 0)))
            if key == "minimum_purchase_gb":
                parsed = min(100000, max(1, parsed))
            elif key == "broadcast_rate_per_second":
                parsed = min(28, max(1, parsed))
            elif key == "broadcast_concurrency":
                parsed = min(64, max(1, parsed))
            values[key] = str(parsed)
        elif key == "admin_user_ids":
            values[key] = normalize_admin_ids(raw)
        else:
            values[key] = str(raw or "").strip()
    return values


def backup_values_from_form(form, current: dict[str, str] | None = None) -> dict[str, str]:
    current = current or {}
    unit = str(form.get("backup_interval_unit", "minutes") or "minutes").strip().lower()
    if unit not in BACKUP_UNITS:
        unit = "minutes"
    interval_value = max(1, as_int(form.get("backup_interval_value", form.get("backup_interval_days", "20")), 20))
    xui_timeout_seconds = normalize_xui_backup_timeout(
        form.get("backup_xui_timeout_seconds", str(DEFAULT_XUI_BACKUP_TIMEOUT_SECONDS))
    )
    values = {
        "backup_enabled": "1" if form.get("backup_enabled") == "on" else "0",
        "backup_include_bot": "1" if form.get("backup_include_bot") == "on" else "0",
        "backup_include_xui": "1" if form.get("backup_include_xui") == "on" else "0",
        "backup_include_pg": "1" if form.get("backup_include_pg") == "on" else "0",
        "backup_include_pg_db": "1" if form.get("backup_include_pg_db") == "on" else "0",
        "backup_interval_value": str(interval_value),
        "backup_interval_unit": unit,
        "backup_interval_days": str(interval_value),
        "backup_xui_timeout_seconds": str(xui_timeout_seconds),
        "backup_send_to_telegram": "1",
        "backup_telegram_chat_id": str(form.get("backup_telegram_chat_id", "-1003940678338") or "-1003940678338").strip(),
    }
    # PasarGuard full-DB-dump config (text). Only touched when the field is
    # present so a partial form can never wipe a saved command/container.
    _pg_db_defaults = {"pg_db_dump_cmd": "", "pg_db_container": "", "pg_db_user": "pasarguard", "pg_db_name": "pasarguard"}
    for key, default in _pg_db_defaults.items():
        if key in form:
            values[key] = str(form.get(key) or "").strip() or (default if key in {"pg_db_user", "pg_db_name"} else "")

    # Restore-verified PasarGuard backup settings.
    pg_mode = str(form.get("pg_backup_mode", current.get("pg_backup_mode", "auto")) or "auto").strip().lower()
    if pg_mode not in {"auto", "cli", "native"}:
        pg_mode = "auto"
    values["pg_backup_mode"] = pg_mode
    for key, default in (("pg_backup_compose_file", ""), ("pg_backup_dir", "")):
        values[key] = str(form.get(key, current.get(key, default)) or "").strip()
    values["pg_backup_max_age_minutes"] = str(
        max(0, as_int(form.get("pg_backup_max_age_minutes", current.get("pg_backup_max_age_minutes", "360")), 360))
    )
    values["pg_backup_timeout_seconds"] = str(
        min(3600, max(60, as_int(form.get("pg_backup_timeout_seconds", current.get("pg_backup_timeout_seconds", "900")), 900)))
    )
    # An empty token means "keep the current one" — the field is a write-only
    # secret, so saving the card must not silently clear a working token.
    token = str(form.get("backup_bot_token", "") or "").strip()
    values["backup_bot_token"] = token or str(current.get("backup_bot_token", "") or "")
    return values


def panel_values_from_form(form, current) -> dict[str, str | int]:
    current_base_url = str(current["base_url"] or "") if current else ""
    current_username = str(current["username"] or "") if current else ""
    current_password = str(current["password"] or "") if current else ""
    current_inbound_id = int(current["inbound_id"] or 0) if current else 0
    current_sub_link_base = str(current["sub_link_base"] or "") if current else ""
    return {
        "base_url": str(form.get("panel_base_url", current_base_url) or "").strip().rstrip("/"),
        "username": str(form.get("panel_username", current_username) or "").strip(),
        "password": str(form.get("panel_password", current_password) or ""),
        "inbound_id": max(0, as_int(form.get("panel_inbound_id", current_inbound_id), current_inbound_id)),
        "sub_link_base": str(form.get("sub_link_base", current_sub_link_base) or "").strip().rstrip("/"),
    }


# audience -> (broadcast target group, human label used in messages)
SALES_AUDIENCES = {
    "all": ("all", "همه کاربران"),
    "user": ("customers", "کاربران عادی"),
    "agent": ("agents", "نماینده‌ها"),
}


def normalize_sales_audience(value) -> str:
    audience = str(value or "all").strip().lower()
    return audience if audience in SALES_AUDIENCES else "all"


def sales_broadcast(new_sales_status: str, audience: str = "all") -> tuple[str, str]:
    _, label = SALES_AUDIENCES.get(audience, SALES_AUDIENCES["all"])
    scope = "" if audience == "all" else f" (مخصوص {label})"
    if new_sales_status == "closed":
        return (
            f"Sales closed broadcast ({audience})",
            (
                f"🔴 <b>اطلاعیه وضعیت فروش</b>{scope}\n\n"
                "فروش سرویس به‌صورت موقت بسته شد. سفارش جدید و تمدید تا اطلاع بعدی انجام نمی‌شود.\n"
                "به محض باز شدن فروش، همین‌جا اطلاع‌رسانی خواهد شد."
            ),
        )
    return (
        f"Sales opened broadcast ({audience})",
        (
            f"🟢 <b>اطلاعیه وضعیت فروش</b>{scope}\n\n"
            "فروش سرویس دوباره فعال شد. اکنون می‌توانید خرید جدید یا تمدید اشتراک انجام دهید."
        ),
    )


def sync_env(request: Request, *, settings: dict[str, str] | None = None, panel: dict[str, str | int] | None = None) -> None:
    env_path = getattr(request.app.state, "env_path", None)
    if not env_path:
        return
    try:
        sync_env_from_admin(env_path, settings=settings, panel=panel)
    except Exception:
        LOG.exception("failed to sync admin settings to .env")


@router.get("")
async def settings_index(request: Request):
    database = db(request)
    all_settings = {item["key"]: item["value"] for item in await database.admin_list_settings()}
    settings = [
        {
            "key": key,
            "value": all_settings.get(key, SETTING_FORM_DEFAULTS.get(key, "")),
            **SETTING_META.get(key, {}),
        }
        for key in EDITABLE_KEYS
    ]
    panel_settings = await database.get_panel_settings()
    backup = {
        "enabled": all_settings.get("backup_enabled", "0"),
        "interval_value": all_settings.get("backup_interval_value", all_settings.get("backup_interval_days", "20")),
        "interval_unit": all_settings.get("backup_interval_unit", "minutes"),
        "interval_days": all_settings.get("backup_interval_days", "1"),
        "xui_timeout_seconds": all_settings.get("backup_xui_timeout_seconds", str(DEFAULT_XUI_BACKUP_TIMEOUT_SECONDS)),
        "include_bot": all_settings.get("backup_include_bot", "1"),
        "include_xui": all_settings.get("backup_include_xui", "1"),
        "telegram_chat_id": all_settings.get("backup_telegram_chat_id", "-1003940678338"),
        "last_run_ts": all_settings.get("backup_last_run_ts", "0"),
        "last_status": all_settings.get("backup_last_status", "never"),
        "last_file": all_settings.get("backup_last_file", ""),
        "last_error": all_settings.get("backup_last_error", ""),
        "files": list_backup_files(request.app.state.backup_dir),
        "dir": str(request.app.state.backup_dir),
    }
    master_sales = all_settings.get("sales_status", "open")
    sales = {
        "status": master_sales,
        "user_status": all_settings.get("sales_status_user", master_sales),
        "agent_status": all_settings.get("sales_status_agent", master_sales),
        "updated_at": all_settings.get("sales_status_updated_at", "0"),
        "updated_by": all_settings.get("sales_status_updated_by", ""),
        "user_updated_at": all_settings.get("sales_status_user_updated_at", all_settings.get("sales_status_updated_at", "0")),
        "user_updated_by": all_settings.get("sales_status_user_updated_by", all_settings.get("sales_status_updated_by", "")),
        "agent_updated_at": all_settings.get("sales_status_agent_updated_at", all_settings.get("sales_status_updated_at", "0")),
        "agent_updated_by": all_settings.get("sales_status_agent_updated_by", all_settings.get("sales_status_updated_by", "")),
    }
    return render(
        request,
        "settings.html",
        {
            "settings": settings,
            "panel": dict(panel_settings) if panel_settings else {},
            "backup": backup,
            "sales": sales,
            "ui_mode": all_settings.get("ui_mode", "modern"),
            "title": "تنظیمات",
        },
    )


@router.post("")
async def settings_update(request: Request):
    form = await request.form()
    database = db(request)
    current_settings = {item["key"]: item["value"] for item in await database.admin_list_settings()}
    old_sales_status = str(current_settings.get("sales_status", "open") or "open").strip().lower()
    values = settings_values_from_form(form, current_settings)
    new_sales_status = "closed" if str(form.get("sales_status", "open")).strip().lower() == "closed" else "open"
    sales_changed = old_sales_status != new_sales_status
    values["sales_status"] = new_sales_status
    if sales_changed:
        values["sales_status_updated_at"] = str(now_ts())
        values["sales_status_updated_by"] = current_admin_username(request) or "admin"
    values.update(backup_values_from_form(form, current_settings))

    current_panel = await database.get_panel_settings()
    await database.admin_update_settings(values)
    panel_values = None
    if any(key in form for key in PANEL_FORM_KEYS):
        panel_values = panel_values_from_form(form, current_panel)
        await database.upsert_panel_settings(
            **panel_values,
            cookie=str(current_panel["cookie"] or "") if current_panel else "",
            cookie_ts=int(current_panel["cookie_ts"] or 0) if current_panel else 0,
        )
    sync_env(request, settings=values, panel=panel_values)

    if sales_changed:
        title, text = sales_broadcast(new_sales_status)
        targets = await database.admin_broadcast_targets("all")
        await database.create_admin_event(
            kind="sales_status_broadcast",
            title=title,
            payload={"audience": "all", "text": text, "already_html": True, "sales_status": new_sales_status},
            total_count=len(targets),
        )
    return RedirectResponse("/admin/settings", status_code=303)


@router.post("/sales")
async def update_sales_status(
    request: Request,
    sales_status: str = Form("open"),
    audience: str = Form("all"),
):
    database = db(request)
    audience = normalize_sales_audience(audience)
    new_sales_status = "closed" if str(sales_status or "open").strip().lower() == "closed" else "open"
    master = str(await database.get_setting("sales_status", "open") or "open").strip().lower()
    admin_name = current_admin_username(request) or "admin"
    now = str(now_ts())

    # Which concrete setting keys this audience controls.
    if audience == "all":
        target_keys = ("sales_status", "sales_status_user", "sales_status_agent")
    elif audience == "agent":
        target_keys = ("sales_status_agent",)
    else:
        target_keys = ("sales_status_user",)

    # Skip a no-op (and its broadcast) when nothing actually changes.
    changed = False
    for key in target_keys:
        current = str(await database.get_setting(key, master) or master).strip().lower()
        if current != new_sales_status:
            changed = True
            break
    if not changed:
        return RedirectResponse("/admin/settings", status_code=303)

    values: dict[str, str] = {}
    for key in target_keys:
        values[key] = new_sales_status
        values[f"{key}_updated_at"] = now
        values[f"{key}_updated_by"] = admin_name
    await database.admin_update_settings(values)
    sync_env(request, settings=values)

    broadcast_target, _label = SALES_AUDIENCES[audience]
    title, text = sales_broadcast(new_sales_status, audience)
    targets = await database.admin_broadcast_targets(broadcast_target)
    await database.create_admin_event(
        kind="sales_status_broadcast",
        title=title,
        payload={
            "audience": broadcast_target,
            "text": text,
            "already_html": True,
            "sales_status": new_sales_status,
        },
        total_count=len(targets),
    )
    return RedirectResponse("/admin/settings", status_code=303)


@router.post("/ui-mode")
async def update_ui_mode(request: Request, ui_mode: str = Form("modern")):
    mode = "classic" if str(ui_mode).strip().lower() == "classic" else "modern"
    await db(request).admin_update_settings({"ui_mode": mode})
    # Switching to modern → open the new dashboard; classic stays here.
    return RedirectResponse("/admin/app" if mode == "modern" else "/admin/settings", status_code=303)


@router.post("/backup/run")
async def run_backup(request: Request):
    await run_backup_now(request.app, source="manual")
    return RedirectResponse("/admin/settings", status_code=303)


@router.get("/backup/download/{filename}")
async def download_backup(request: Request, filename: str):
    safe_name = filename.replace("/", "").replace("\\", "")
    path = request.app.state.backup_dir / safe_name
    if not path.exists() or path.suffix != ".zip":
        return RedirectResponse("/admin/settings", status_code=303)
    return FileResponse(path, filename=safe_name, media_type="application/zip")
