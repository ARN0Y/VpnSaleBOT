import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import type { UserDetailBundle } from "@/lib/types";

export function n(v: unknown): number {
  return Number(v || 0);
}
export function s(v: unknown): string {
  return v == null ? "" : String(v);
}

export interface AgentPayload {
  access_level: string;
  credit_limit_toman: number;
  credit_used_toman: number;
  price_per_gb: number;
  daily_test_limit: number;
}

export const PERIODS: readonly (readonly [string, string])[] = [
  ["all", "همه"],
  ["24h", "۲۴ ساعت"],
  ["7d", "۷ روز"],
  ["30d", "۳۰ روز"],
];

export function useUserDetail(id: number, period: string, topupPeriod: string, subsPage: number) {
  return useQuery({
    queryKey: ["user-detail", id, period, topupPeriod, subsPage],
    queryFn: () => api.userDetail(id, period, topupPeriod, subsPage),
    placeholderData: (prev: UserDetailBundle | undefined) => prev,
  });
}

export function useUserMutations(id: number) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const ok = (title: string) => {
    qc.invalidateQueries({ queryKey: ["user-detail", id] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
    toast({ title, variant: "success" });
  };
  const fail = () => toast({ title: "عملیات ناموفق بود", description: "دوباره تلاش کنید.", variant: "error" });

  return {
    ban: useMutation({ mutationFn: (reason: string) => api.banUser(id, reason), onSuccess: () => ok("کاربر مسدود شد"), onError: fail }),
    unban: useMutation({ mutationFn: () => api.unbanUser(id), onSuccess: () => ok("دسترسی کاربر فعال شد"), onError: fail }),
    wallet: useMutation({ mutationFn: (amt: number) => api.setWallet(id, amt), onSuccess: () => ok("کیف پول به‌روزرسانی شد"), onError: fail }),
    message: useMutation({ mutationFn: (text: string) => api.messageUser(id, text), onSuccess: () => ok("پیام ارسال شد"), onError: fail }),
    agent: useMutation({ mutationFn: (p: AgentPayload) => api.updateAgent(id, p), onSuccess: () => ok("تنظیمات نمایندگی ذخیره شد"), onError: fail }),
    sync: useMutation({ mutationFn: () => api.syncUserSubs(id), onSuccess: () => ok("کانفیگ‌ها از پنل سینک شد"), onError: fail }),
    bulk: useMutation({ mutationFn: (enabled: boolean) => api.bulkUserSubs(id, enabled), onSuccess: () => ok("عملیات گروهی در صف رویدادها قرار گرفت"), onError: fail }),
    subToggle: useMutation({
      mutationFn: (v: { subId: string; enabled: boolean }) => api.setSubEnabled(v.subId, v.enabled),
      onSuccess: () => ok("وضعیت کانفیگ تغییر کرد"),
      onError: fail,
    }),
  };
}

export type UserMutations = ReturnType<typeof useUserMutations>;
