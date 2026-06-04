import { useLocation } from "react-router-dom";
import { ProfileMenu } from "./ProfileMenu";
import { PAGE_TITLES } from "@/lib/nav";

export function Topbar() {
  const { pathname } = useLocation();
  const title =
    PAGE_TITLES[pathname] ??
    (pathname.startsWith("/users/")
      ? "پروفایل کاربر"
      : pathname.startsWith("/subscriptions/")
        ? "جزئیات کانفیگ"
        : "");

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center justify-between gap-3 border-b border-border bg-background/70 px-4 backdrop-blur-xl lg:px-8">
      <div className="min-w-0">
        <div className="text-[0.62rem] font-bold uppercase tracking-widest text-muted-foreground">پنل مدیریت</div>
        <h1 className="truncate text-lg font-black leading-tight text-white">{title}</h1>
      </div>
      <ProfileMenu />
    </header>
  );
}
