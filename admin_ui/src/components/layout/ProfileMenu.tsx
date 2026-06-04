import { LogOut, ChevronDown, ShieldCheck } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/auth/AuthContext";

export function ProfileMenu() {
  const { username, logout } = useAuth();
  const name = username || "admin";
  const initial = name.trim().charAt(0).toUpperCase() || "A";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="group flex items-center gap-2.5 rounded-full border border-border bg-white/[0.02] py-1 pe-3 ps-1 outline-none transition hover:border-white/20">
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-gradient-to-br from-white/90 to-white/60 text-sm font-black text-background">
          {initial}
        </span>
        <span className="hidden text-right sm:block">
          <span className="block text-xs font-bold leading-tight text-white">{name}</span>
          <span className="block text-[0.6rem] leading-tight text-muted-foreground">مدیر سیستم</span>
        </span>
        <ChevronDown className="h-4 w-4 text-muted-foreground transition group-data-[state=open]:rotate-180" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel>
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-full bg-gradient-to-br from-white/90 to-white/60 text-base font-black text-background">
              {initial}
            </span>
            <div>
              <div className="text-sm font-bold text-white">{name}</div>
              <div className="flex items-center gap-1 text-[0.65rem] text-emerald-300">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-300" /> آنلاین • مدیر سیستم
              </div>
            </div>
          </div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled className="opacity-70">
          <ShieldCheck className="h-4 w-4" /> دسترسی کامل مدیریت
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem destructive onSelect={() => void logout()}>
          <LogOut className="h-4 w-4" /> خروج از حساب
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
