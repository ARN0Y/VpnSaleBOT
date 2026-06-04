# VpnSaleBOT

ربات فروش VPN روی تلگرام + پنل وب مدیریت (FastAPI) + داشبورد مدرن React/shadcn.
اتصال مستقیم به پنل **3x-ui (xray)**؛ فروش بر اساس حجم، کیف پول، کارت‌به‌کارت و تتر،
سیستم نمایندگی، کانفیگ تست، ساخت کانفیگِ ضد-تکرار، و کنترل جداگانه‌ی فروش
برای کاربر/نماینده.

## برنچ‌ها (دو برند، یک کدبیس)
- **`main`** → برند «تسکو نتورک».
- **`elsa`** → برند «ElsaVPN».
تفاوت دو برنچ فقط چند فایلِ برندینگ است (`brand.ts`، متن‌های ربات، عنوان پنل، پیش‌فرض‌ها).
بهبودهای کدِ مشترک را روی `main` بزنید و با `git merge main` به `elsa` منتقل کنید.

## ساختار
```
async_storefront/   ربات تلگرام (python-telegram-bot)
admin_panel/        پنل وب FastAPI + API جیسون (/admin/api/v1) + پنل کلاسیک Jinja
admin_ui/           داشبورد React + Vite + shadcn/Radix (خروجی build در admin_ui/dist)
requirements-async.txt
.env.example        نمونه‌ی پیکربندی (فایل واقعی .env هرگز commit نمی‌شود)
```

## راه‌اندازی
```bash
python -m venv .venv && source .venv/bin/activate   # ویندوز: .venv\Scripts\activate
pip install -r requirements-async.txt
cp .env.example .env      # مقادیر را پر کنید
python -m async_storefront.app     # ربات
python -m admin_panel.main         # پنل وب  →  http://<ip>:8080/admin  و داشبورد جدید: /admin/app
```

> دیتابیس در اولین اجرا خودکار ساخته می‌شود. `.env` و `*.db` و `backup/` در `.gitignore`
> هستند و هرگز در مخزن قرار نمی‌گیرند.

## داشبورد جدید (admin_ui)
خروجی build شده (`admin_ui/dist/`) داخل مخزن هست تا سرور **بدون نیاز به Node** فقط با
`git pull` به‌روز شود. برای تغییر فرانت‌اند، روی یک ماشین دارای Node:
```bash
cd admin_ui && npm install && npm run build
```

## امکانات کلیدی
- 🛒 خرید/تمدید بر اساس حجم با تحویل آنی لینک + QR
- 💳 کیف پول، کارت‌به‌کارت و تتر
- 🤝 نمایندگی (اعتباری/کیف‌پولی) + کانفیگ تست
- 🔐 ساخت کانفیگ **idempotent** (بدون هم‌نام شدن / خطای user already exists)
- 🔀 کنترل جداگانه‌ی باز/بسته بودن فروش برای کاربر و نماینده
- 📊 داشبورد مدرن: کاربران، سفارش‌ها، کانفیگ‌ها، شارژها، نمایندگی، پیام همگانی، رویدادها، تنظیمات
- 🗄 بکاپ خودکار دیتابیس ربات و x-ui
