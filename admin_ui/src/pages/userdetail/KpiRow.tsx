import { Wallet, TrendingUp, Gauge, Boxes } from "lucide-react";
import { StatTile } from "@/components/ui/stat-tile";
import { toman } from "@/lib/utils";
import { n } from "./helpers";
import type { UserDetailBundle } from "@/lib/types";

export function KpiRow({ data }: { data: UserDetailBundle }) {
  const u = data.user;

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatTile icon={Wallet} label="کیف پول" value={`${toman(u.wallet_balance)}`} sub="تومان" />
      <StatTile icon={TrendingUp} label="کل خرید" value={`${toman(u.total_spent)}`} sub="تومان" />
      <StatTile icon={Gauge} label="کل ترافیک خریداری‌شده" value={`${toman(u.total_gb_purchased)}`} sub="گیگابایت" />
      <StatTile icon={Boxes} label="تعداد کانفیگ‌ها" value={toman(data.subs_total)} sub="کانفیگ فعال/غیرفعال" />
    </div>
  );
}
