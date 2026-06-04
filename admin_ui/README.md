# ElsaVPN Admin UI (React + shadcn/Radix)

داشبورد جدید مدیریت روی **React + Vite + TypeScript + TailwindCSS + Radix/shadcn + Lucide + TanStack Query/Table + Recharts**.
این SPA با همان نشستِ کوکیِ پنل از API جیسونِ `/admin/api/v1` داده می‌گیرد.

## مهم: سرور به Node نیاز ندارد
Build روی هر ماشینی که Node دارد (سیستم خودت/CI) انجام می‌شود؛ خروجی پوشه‌ی
`dist/` است که FastAPI آن را به‌صورت استاتیک سرو می‌کند. کافی است `dist/` را
کنار پروژه روی سرور بگذاری.

## توسعه (dev)
```bash
cd admin_ui
npm install
npm run dev      # http://localhost:5173/admin/app  (API به 127.0.0.1:8080 پروکسی می‌شود)
```
برای dev، پنل بک‌اند را هم بالا نگه دار: `python -m admin_panel.main`.

## ساخت برای پروداکشن
```bash
cd admin_ui
npm install
npm run build    # خروجی در admin_ui/dist
```
سپس فقط همین `dist/` را روی سرور، در مسیر `admin_ui/dist` قرار بده.
FastAPI به‌صورت خودکار آن را زیر آدرس زیر سرو می‌کند:

```
http://<سرور>:8080/admin/app
```

اگر `dist/` وجود نداشته باشد، پنل کلاسیکِ Jinja (`/admin`) بدون هیچ تغییری کار می‌کند.
پس می‌توانی تدریجی مهاجرت کنی.

## ساختار
```
src/
  brand.ts              ← نام/تم برند (تنها فرق دو نسخه‌ی ElsaVPN و تسکو)
  lib/        api.ts, types.ts, status.ts, utils.ts
  components/ ui/ (button,card,input,badge,table,skeleton) + layout/
  auth/       AuthContext.tsx
  pages/      Login, Dashboard, Orders, Users, Placeholder
```

## مسیرهای API مصرف‌شده (`/admin/api/v1`)
`POST /login` · `POST /logout` · `GET /me` · `GET /dashboard` ·
`GET /orders` · `GET /users` · `GET /topups` · `GET /agent-requests` · `GET /settings`

> صفحاتِ topups/subscriptions/settings فعلاً Placeholder هستند و در فازهای بعدی
> به همین الگو تکمیل می‌شوند؛ مدیریتشان تا آن زمان در پنل کلاسیک در دسترس است.
