import { RefreshCw, Ban, ShieldCheck, MessageSquare, Wallet, Crown, User as UserIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { CopyButton } from "@/components/ui/copy-button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { jalaliDate } from "@/lib/utils";
import { n, s, type UserMutations } from "./helpers";

export function UserHero({
  user,
  mutations,
  onJump,
}: {
  user: Record<string, unknown>;
  mutations: UserMutations;
  onJump: (tab: string) => void;
}) {
  const disabled = n(user.user_disabled) === 1;
  const isAgent = !!s(user.access_level);
  const initial = (s(user.first_name) || s(user.username) || "U").trim().charAt(0).toUpperCase();
  const levelLabel = isAgent ? "نماینده" : "کاربر عادی";

  return (
    <div className="relative overflow-hidden rounded-3xl border border-border bg-card/70">
      {/* ambient glow */}
      <div className="pointer-events-none absolute -right-16 -top-24 h-64 w-64 rounded-full bg-white/[0.05] blur-3xl" />
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-l from-transparent via-white/20 to-transparent" />

      <div className="relative flex flex-col gap-5 p-6 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-center gap-4">
          <div className="relative">
            <span className="flex h-20 w-20 items-center justify-center rounded-3xl bg-gradient-to-br from-white via-white/85 to-white/50 text-3xl font-black text-background shadow-xl">
              {initial}
            </span>
            <span
              className={`absolute -bottom-1 -left-1 flex h-6 w-6 items-center justify-center rounded-full border-2 border-card ${
                disabled ? "bg-rose-500" : "bg-emerald-500"
              }`}
              title={disabled ? "مسدود" : "فعال"}
            >
              {disabled ? <Ban className="h-3 w-3 text-white" /> : <ShieldCheck className="h-3 w-3 text-white" />}
            </span>
          </div>

          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-black tracking-tight text-white">{s(user.first_name) || "بدون نام"}</h1>
              {isAgent ? (
                <Badge className="gap-1">
                  <Crown className="h-3 w-3" /> {levelLabel}
                </Badge>
              ) : (
                <Badge variant="muted" className="gap-1">
                  <UserIcon className="h-3 w-3" /> {levelLabel}
                </Badge>
              )}
              {disabled && <Badge variant="danger">مسدود</Badge>}
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
              <span dir="ltr">{user.username ? `@${s(user.username)}` : "—"}</span>
              <span className="flex items-center gap-1.5">
                شناسه: <code className="text-white">{s(user.user_id)}</code>
                <CopyButton value={s(user.user_id)} />
              </span>
              <span>عضویت: {jalaliDate(n(user.joined_at))}</span>
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => onJump("wallet")}>
            <Wallet className="h-4 w-4" /> کیف پول
          </Button>
          <Button variant="outline" size="sm" onClick={() => onJump("message")}>
            <MessageSquare className="h-4 w-4" /> پیام
          </Button>
          <Button variant="outline" size="sm" disabled={mutations.sync.isPending} onClick={() => mutations.sync.mutate()}>
            <RefreshCw className={`h-4 w-4 ${mutations.sync.isPending ? "animate-spin" : ""}`} /> سینک
          </Button>
          {disabled ? (
            <Button size="sm" disabled={mutations.unban.isPending} onClick={() => mutations.unban.mutate()}>
              <ShieldCheck className="h-4 w-4" /> فعال‌سازی
            </Button>
          ) : (
            <ConfirmDialog
              destructive
              title="مسدودسازی کاربر؟"
              description="دسترسی کاربر به ربات بسته می‌شود. می‌توانید بعداً دوباره فعال کنید."
              confirmLabel="مسدود کن"
              onConfirm={() => mutations.ban.mutate("از پنل مدیریت")}
              trigger={
                <Button variant="destructive" size="sm">
                  <Ban className="h-4 w-4" /> مسدودسازی
                </Button>
              }
            />
          )}
        </div>
      </div>
    </div>
  );
}
