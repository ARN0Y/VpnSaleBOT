import * as React from "react";
import { ShoppingCart, RefreshCw, Wallet, Activity, TrendingUp, Boxes, Receipt, Calculator } from "lucide-react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { StatTile } from "@/components/ui/stat-tile";
import { InfoRow } from "./parts";
import { toman, jalaliDate } from "@/lib/utils";
import { n, s } from "./helpers";
import { statusBadge } from "@/lib/status";
import type { UserDetailBundle } from "@/lib/types";

interface Event {
  kind: "order" | "topup";
  title: string;
  amount: number;
  status: string;
  at: number;
  renewal?: boolean;
}

export function OverviewTab({ data }: { data: UserDetailBundle }) {
  const u = data.user;
  const isAgent = !!s(u.access_level);

  const orderSum = data.orders.reduce((acc, o) => acc + n(o.final_price), 0);
  const avgOrder = data.orders.length ? Math.round(orderSum / data.orders.length) : 0;
  const approvedTopups = data.topups.filter((t) => s(t.status) === "approved").length;

  const events = React.useMemo<Event[]>(() => {
    const orders: Event[] = data.orders.map((o) => ({
      kind: "order",
      title: s(o.subscription_name) || s(o.client_name) || "سفارش",
      amount: n(o.final_price),
      status: s(o.status),
      at: n(o.created_at),
      renewal: o.order_type === "renewal",
    }));
    const topups: Event[] = data.topups.map((t) => ({
      kind: "topup",
      title: t.method === "crypto" ? "شارژ رمزارزی" : "شارژ کارت‌به‌کارت",
      amount: n(t.amount_toman),
      status: s(t.status),
      at: n(t.created_at),
    }));
    return [...orders, ...topups].sort((a, b) => b.at - a.at).slice(0, 16);
  }, [data]);

  const chart = React.useMemo(
    () =>
      data.orders
        .slice()
        .reverse()
        .map((o) => ({ name: jalaliDate(n(o.created_at)).split(" ")[0], value: n(o.final_price) })),
    [data],
  );

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile icon={Receipt} label="مجموع شارژ (بازه)" value={toman(data.topups_total)} sub="تومان" />
        <StatTile icon={ShoppingCart} label="تعداد سفارش (بازه)" value={toman(data.orders.length)} sub={`${toman(approvedTopups)} شارژ تاییدشده`} />
        <StatTile icon={Calculator} label="میانگین مبلغ سفارش" value={toman(avgOrder)} sub="تومان" />
        <StatTile icon={Boxes} label="کل کانفیگ‌ها" value={toman(data.subs_total)} sub="کانفیگ" />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><TrendingUp className="h-4 w-4" /> روند خرید کاربر</CardTitle>
        </CardHeader>
        <CardContent>
          {chart.length === 0 ? (
            <EmptyState icon={TrendingUp} title="داده‌ای برای نمودار نیست" hint="با ثبت سفارش، روند این‌جا رسم می‌شود." />
          ) : (
            <div className="h-48 w-full" dir="ltr">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chart}>
                  <defs>
                    <linearGradient id="ud-spend" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="hsl(0 0% 92%)" stopOpacity={0.3} />
                      <stop offset="100%" stopColor="hsl(0 0% 92%)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="name" tick={{ fill: "hsl(240 5% 58%)", fontSize: 10 }} tickLine={false} axisLine={false} />
                  <YAxis hide />
                  <Tooltip
                    cursor={{ stroke: "hsl(240 4% 25%)" }}
                    contentStyle={{ background: "hsl(240 5% 8%)", border: "1px solid hsl(240 4% 15%)", borderRadius: 12, color: "#fff" }}
                    formatter={(v: number) => [toman(v) + " تومان", "مبلغ"]}
                  />
                  <Area type="monotone" dataKey="value" stroke="hsl(0 0% 95%)" fill="url(#ud-spend)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-5 lg:grid-cols-[1fr_20rem]">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Activity className="h-4 w-4" /> فعالیت اخیر</CardTitle>
          </CardHeader>
          <CardContent>
            {events.length === 0 ? (
              <EmptyState icon={Activity} title="فعالیتی ثبت نشده" hint="سفارش‌ها و شارژهای کاربر این‌جا نمایش داده می‌شوند." />
            ) : (
              <ol className="relative space-y-4 before:absolute before:bottom-2 before:right-[15px] before:top-2 before:w-px before:bg-border">
                {events.map((e, i) => {
                  const st = statusBadge(e.status);
                  const Icon = e.kind === "topup" ? Wallet : e.renewal ? RefreshCw : ShoppingCart;
                  return (
                    <li key={i} className="relative flex items-start gap-3">
                      <span className="z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-border bg-card text-foreground">
                        <Icon className="h-4 w-4" />
                      </span>
                      <div className="flex-1 rounded-xl border border-border bg-white/[0.02] p-3">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-sm font-bold text-white">
                            {e.kind === "topup" ? e.title : e.renewal ? `تمدید: ${e.title}` : `خرید: ${e.title}`}
                          </span>
                          <Badge variant={st.variant}>{st.label}</Badge>
                        </div>
                        <div className="mt-1 flex items-center justify-between text-xs text-muted-foreground">
                          <span>{toman(e.amount)} تومان</span>
                          <span>{jalaliDate(e.at)}</span>
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ol>
            )}
          </CardContent>
        </Card>

        <div className="space-y-5">
          <Card>
            <CardHeader><CardTitle>اطلاعات حساب</CardTitle></CardHeader>
            <CardContent>
              <dl>
                <InfoRow k="کیف پول" v={`${toman(u.wallet_balance)} ت`} />
                <InfoRow k="کل خرید" v={`${toman(u.total_spent)} ت`} accent="emerald" />
                <InfoRow k="کل ترافیک" v={`${toman(u.total_gb_purchased)} GB`} />
                <InfoRow k="تعداد سفارش‌ها" v={toman(u.approved_orders)} />
                <InfoRow k="تعداد کانفیگ" v={toman(data.subs_total)} />
                {isAgent && <InfoRow k="اعتبار" v={`${toman(u.credit_used_toman)} / ${toman(u.credit_limit_toman)}`} />}
                {isAgent && <InfoRow k="تست امروز" v={`${n(u.daily_test_used)} / ${n(u.daily_test_limit)}`} />}
              </dl>
            </CardContent>
          </Card>

          {data.agent_24h && (
            <Card>
              <CardHeader><CardTitle>۲۴ ساعت اخیر</CardTitle></CardHeader>
              <CardContent>
                <dl>
                  <InfoRow k="خرید تومانی" v={`${toman(data.agent_24h.total_toman)} ت`} accent="emerald" />
                  <InfoRow k="خرید گیگ" v={`${n(data.agent_24h.total_gb)} GB`} />
                  <InfoRow k="تعداد سفارش" v={String(n(data.agent_24h.order_count))} />
                </dl>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
