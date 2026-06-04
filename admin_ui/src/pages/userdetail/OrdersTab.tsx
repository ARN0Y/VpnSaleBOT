import * as React from "react";
import { Link } from "react-router-dom";
import { ShoppingCart, ExternalLink } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { CopyButton } from "@/components/ui/copy-button";
import { EmptyState } from "@/components/ui/empty-state";
import { PeriodTabs } from "./parts";
import { toman, jalaliDate } from "@/lib/utils";
import { n, s } from "./helpers";
import { statusBadge } from "@/lib/status";
import type { UserDetailBundle } from "@/lib/types";

export function OrdersTab({
  data,
  period,
  setPeriod,
}: {
  data: UserDetailBundle;
  period: string;
  setPeriod: (v: string) => void;
}) {
  const [status, setStatus] = React.useState("all");
  const filtered = data.orders.filter((o) => status === "all" || s(o.status) === status);
  const sum = filtered.reduce((acc, o) => acc + n(o.final_price), 0);
  const CHIPS = [
    { key: "all", label: "همه" },
    { key: "approved", label: "تایید" },
    { key: "pending", label: "در انتظار" },
    { key: "rejected", label: "رد" },
  ];

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>سفارش‌های کاربر</CardTitle>
          <PeriodTabs value={period} onChange={setPeriod} />
        </div>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap gap-1">
            {CHIPS.map((c) => (
              <Button key={c.key} size="sm" variant={status === c.key ? "default" : "ghost"} onClick={() => setStatus(c.key)}>
                {c.label}
              </Button>
            ))}
          </div>
          <span className="text-xs text-muted-foreground">
            {toman(filtered.length)} سفارش • مجموع <b className="text-white">{toman(sum)}</b> ت
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        {filtered.length === 0 ? (
          <EmptyState icon={ShoppingCart} title="سفارشی ثبت نشده" hint="در این بازه/فیلتر سفارشی وجود ندارد." />
        ) : (
          filtered.map((o) => {
            const st = statusBadge(s(o.status));
            const subId = s(o.subscription_id);
            const extra = n(o.subscription_count) > 1 ? ` +${n(o.subscription_count) - 1}` : "";
            return (
              <div key={s(o.order_id)} className="rounded-xl border border-border bg-white/[0.02] p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <Badge variant={o.order_type === "renewal" ? "warning" : "default"}>
                      {o.order_type === "renewal" ? "تمدید" : "خرید جدید"}
                    </Badge>
                    <span className="text-sm font-bold text-white">{s(o.subscription_name) || s(o.client_name) || "رندوم"}{extra}</span>
                  </div>
                  <Badge variant={st.variant}>{st.label}</Badge>
                </div>
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                  <span className="flex items-center gap-1.5">
                    <code>{s(o.order_id)}</code>
                    <CopyButton value={s(o.order_id)} />
                  </span>
                  <span>{toman(o.final_price)} تومان • {n(o.gb)}×{n(o.qty)} GB • {jalaliDate(n(o.created_at))}</span>
                </div>
                {subId && (
                  <Link to={`/subscriptions/${encodeURIComponent(subId)}`} className="mt-2 inline-flex items-center gap-1 text-xs font-bold text-white hover:underline">
                    <ExternalLink className="h-3.5 w-3.5" /> باز کردن کانفیگ
                  </Link>
                )}
              </div>
            );
          })
        )}
      </CardContent>
    </Card>
  );
}
