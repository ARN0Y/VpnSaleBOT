import { Wallet, TrendingUp, Gauge, Boxes } from "lucide-react";
import { StatTile } from "@/components/ui/stat-tile";
import { Ring } from "@/components/ui/progress";
import { toman } from "@/lib/utils";
import { n, s } from "./helpers";
import type { UserDetailBundle } from "@/lib/types";

export function KpiRow({ data }: { data: UserDetailBundle }) {
  const u = data.user;
  const isAgent = !!s(u.access_level);
  const creditLimit = n(u.credit_limit_toman);
  const creditUsed = n(u.credit_used_toman);
  const creditPct = creditLimit > 0 ? (creditUsed / creditLimit) * 100 : 0;
  const creditTone = creditPct >= 90 ? "danger" : creditPct >= 70 ? "warning" : "success";

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatTile icon={Wallet} label="کیف پول" value={`${toman(u.wallet_balance)}`} sub="تومان" />
      <StatTile icon={TrendingUp} label="کل خرید" value={`${toman(u.total_spent)}`} sub="تومان" />
      <StatTile icon={Gauge} label="کل ترافیک خریداری‌شده" value={`${toman(u.total_gb_purchased)}`} sub="گیگابایت" />
      {isAgent ? (
        <div className="flex items-center gap-4 rounded-2xl border border-border bg-card/60 p-4">
          <Ring value={creditPct} tone={creditTone} size={62} stroke={6} />
          <div className="min-w-0">
            <div className="text-[0.7rem] font-medium text-muted-foreground">اعتبار مصرف‌شده</div>
            <div className="mt-1 text-sm font-black text-white">
              {toman(creditUsed)} <span className="text-muted-foreground">/ {toman(creditLimit)}</span>
            </div>
            <div className="text-[0.62rem] text-muted-foreground">تومان</div>
          </div>
        </div>
      ) : (
        <StatTile icon={Boxes} label="تعداد کانفیگ‌ها" value={toman(data.subs_total)} sub="کانفیگ فعال/غیرفعال" />
      )}
    </div>
  );
}
