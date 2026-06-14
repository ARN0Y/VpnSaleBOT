import {
  LayoutDashboard,
  ShoppingCart,
  Users,
  Wallet,
  Handshake,
  Boxes,
  Megaphone,
  Activity,
  SlidersHorizontal,
  ShieldCheck,
  UserPlus,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
  group: "main" | "ops" | "system";
}

// Single source of truth shared by the rail and the command palette.
export const NAV: NavItem[] = [
  { to: "/", label: "داشبورد", icon: LayoutDashboard, end: true, group: "main" },
  { to: "/orders", label: "سفارش‌ها", icon: ShoppingCart, group: "main" },
  { to: "/users", label: "کاربران", icon: Users, group: "main" },
  { to: "/subscriptions", label: "کانفیگ‌ها", icon: Boxes, group: "main" },
  { to: "/topups", label: "شارژ کیف پول", icon: Wallet, group: "ops" },
  { to: "/agent-requests", label: "درخواست نمایندگی", icon: Handshake, group: "ops" },
  { to: "/broadcast", label: "پیام همگانی", icon: Megaphone, group: "ops" },
  { to: "/pasarguard/monitor", label: "مانیتورینگ پنل پاسارگارد", icon: ShieldCheck, group: "ops" },
  { to: "/pasarguard/new-admin", label: "ساخت اکانت ادمین نماینده", icon: UserPlus, group: "ops" },
  { to: "/events", label: "رویدادها", icon: Activity, group: "system" },
  { to: "/settings", label: "تنظیمات", icon: SlidersHorizontal, group: "system" },
];

export const PAGE_TITLES: Record<string, string> = {
  "/": "داشبورد",
  "/orders": "سفارش‌ها",
  "/users": "کاربران",
  "/subscriptions": "کانفیگ‌ها",
  "/topups": "شارژ کیف پول",
  "/agent-requests": "درخواست‌های نمایندگی",
  "/broadcast": "پیام همگانی",
  "/pasarguard/monitor": "مانیتورینگ پنل پاسارگارد",
  "/pasarguard/new-admin": "ساخت اکانت ادمین نماینده",
  "/events": "رویدادها",
  "/settings": "تنظیمات",
};
