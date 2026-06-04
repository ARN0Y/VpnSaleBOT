# ❄️ NavidVPN Bot

ربات فروش VPN روی تلگرام به‌همراه پنل وب مدیریت.
فروش بر اساس حجم (گیگ)، کیف پول، پرداخت کارت‌به‌کارت و تتر، سیستم نمایندگی،
کانفیگ تست، و اتصال مستقیم به پنل **3x-ui (xray)**.

---

## پیش‌نیازها
- Python 3.11+
- یک پنل 3x-ui فعال و در دسترس
- یک توکن ربات از [@BotFather](https://t.me/BotFather)

## نصب

```bash
# ۱) ساخت محیط مجازی و نصب وابستگی‌ها
python -m venv .venv
source .venv/bin/activate        # ویندوز: .venv\Scripts\activate
pip install -r requirements-async.txt

# ۲) ساخت فایل تنظیمات و پر کردن مقادیر
cp .env.example .env
nano .env                        # مقادیر <...> را با اطلاعات واقعی پر کنید
```

> دیتابیس به‌صورت خودکار در اولین اجرا ساخته می‌شود (نیازی به دیتابیس قبلی نیست — این یک نصب تازه است).

## اجرا

دو سرویس مستقل دارد که هرکدام را در یک پراسس جدا اجرا کنید:

```bash
# ربات تلگرام
python -m async_storefront.app

# پنل وب مدیریت (روی ADMIN_HOST:ADMIN_PORT از فایل .env)
python -m admin_panel.main
```

پنل مدیریت روی `http://<آی‌پی-سرور>:8080/admin` در دسترس است و با
`ADMIN_PANEL_USERNAME` / `ADMIN_PANEL_PASSWORD` وارد می‌شوید.

## اجرای دائمی با systemd (نمونه)

```ini
# /etc/systemd/system/navidvpn-bot.service
[Unit]
Description=NavidVPN Telegram Bot
After=network.target

[Service]
WorkingDirectory=/root/NavidVPN_Bot
ExecStart=/root/NavidVPN_Bot/.venv/bin/python -m async_storefront.app
Restart=always
EnvironmentFile=/root/NavidVPN_Bot/.env

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/navidvpn-admin.service
[Unit]
Description=NavidVPN Admin Panel
After=network.target

[Service]
WorkingDirectory=/root/NavidVPN_Bot
ExecStart=/root/NavidVPN_Bot/.venv/bin/python -m admin_panel.main
Restart=always
EnvironmentFile=/root/NavidVPN_Bot/.env

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now navidvpn-bot navidvpn-admin
```

## امکانات کلیدی
- 🛒 خرید/تمدید سرویس بر اساس حجم با تحویل آنی لینک + QR
- 💎 کیف پول، کارت‌به‌کارت و تتر
- 🤝 سیستم نمایندگی با اعتبار باز یا کیف‌پولی + کانفیگ تست
- 🔐 ساخت کانفیگ **ضد-تکرار**: هیچ‌گاه دو کانفیگ هم‌نام ساخته نمی‌شود و خطای
  `user already exists` پنل به‌صورت idempotent مدیریت می‌شود.
- 🔀 کنترل **جداگانه‌ی باز/بسته بودن فروش** برای کاربران عادی و نماینده‌ها
- 🗄 بکاپ خودکار دیتابیس ربات و x-ui با ارسال به تلگرام

## تنظیمات قابل تغییر از پنل وب
قیمت، حداقل خرید، اطلاعات پرداخت، آیدی پشتیبانی، اتصال پنل 3x-ui،
وضعیت فروش (عادی/نماینده)، و زمان‌بندی بکاپ — همه از مسیر
`/admin/settings` بدون نیاز به تغییر کد قابل مدیریت‌اند.
