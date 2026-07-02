import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Users as UsersIcon,
  Handshake,
  Wallet,
  Gauge,
  TrendingUp,
  Inbox,
  ArrowLeft,
  type LucideIcon,
} from "lucide-react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { UserLink } from "@/components/UserLink";
import { toman, jalaliDate } from "@/lib/utils";
import { statusBadge } from "@/lib/status";

function Kpi({ icon: Icon, label, value, sub }: { icon: LucideIcon; label: string; value: string; sub?: string }) {
  return (
    <Card className="relative overflow-hidden transition hover:border-white/20">
      <div className="pointer-events-none absolute -left-6 -top-8 h-24 w-24 rounded-full bg-white/[0.03] blur-2xl" />
      <CardContent className="p-5">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-muted-foreground">{label}</span>
          <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-white/[0.03] text-foreground">
            <Icon className="h-5 w-5" />
          </span>
        </div>
        <div className="mt-3 text-3xl font-black tracking-tight text-white">{value}</div>
        {sub && <div className="mt-1 text-[0.7rem] text-muted-foreground">{sub}</div>}
      </CardContent>
    </Card>
  );
}

function ActionItem({ icon: Icon, label, count, to }: { icon: LucideIcon; label: string; count: number; to: string }) {
  return (
    <Link
      to={to}
      className="flex items-center gap-3 rounded-xl border border-border bg-white/[0.02] p-3 transition hover:border-white/20 hover:bg-white/[0.04]"
    >
      <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-border bg-white/[0.03] text-foreground">
        <Icon className="h-5 w-5" />
      </span>
      <div className="flex-1">
        <div className="text-sm font-bold text-white">{label}</div>
        <div className="text-xs text-muted-foreground">در انتظار رسیدگی</div>
      </div>
      <div className="flex items-center gap-2">
        {count > 0 ? <Badge variant="warning">{toman(count)}</Badge> : <Badge variant="muted">۰</Badge>}
        <ArrowLeft className="h-4 w-4 text-muted-foreground" />
      </div>
    </Link>
  );
}

export function Dashboard() {
  const { data, isLoading } = useQuery({ queryKey: ["dashboard"], queryFn: () => api.dashboard(), refetchInterval: 15000 });

  if (isLoading || !data) {
    return (
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-28" />)}
        <Skeleton className="h-72 xl:col-span-4" />
      </div>
    );
  }

  const m = data.metrics;
  const chart = data.recent_orders.slice().reverse().map((o) => ({
    name: jalaliDate(o.created_at).split(" ")[0],
    value: Number(o.final_price || 0),
  }));

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi icon={TrendingUp} label="درآمد کل" value={`${toman(m.revenue)}`} sub="تومان" />
        <Kpi icon={UsersIcon} label="کاربران" value={toman(m.users)} sub={`${toman(m.agents)} نماینده`} />
        <Kpi icon={Gauge} label="ترافیک فروخته‌شده" value={toman(m.traffic_gb)} sub="گیگابایت" />
        <Kpi icon={Handshake} label="نماینده‌ها" value={toman(m.agents)} sub="پرداخت از کیف پول" />
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader><CardTitle>روند مبلغ سفارش‌های اخیر</CardTitle></CardHeader>
          <CardContent>
            <div className="h-60 w-full" dir="ltr">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chart}>
                  <defs>
                    <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="hsl(0 0% 92%)" stopOpacity={0.32} />
                      <stop offset="100%" stopColor="hsl(0 0% 92%)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="name" tick={{ fill: "hsl(240 5% 58%)", fontSize: 11 }} tickLine={false} axisLine={false} />
                  <YAxis hide />
                  <Tooltip
                    cursor={{ stroke: "hsl(240 4% 25%)" }}
                    contentStyle={{ background: "hsl(240 5% 8%)", border: "1px solid hsl(240 4% 15%)", borderRadius: 12, color: "#fff" }}
                    formatter={(v: number) => [toman(v) + " تومان", "مبلغ"]}
                  />
                  <Area type="monotone" dataKey="value" stroke="hsl(0 0% 95%)" fill="url(#g)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>نیازمند اقدام</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <ActionItem icon={Wallet} label="شارژهای در انتظار" count={m.pending_topups} to="/topups" />
            <ActionItem icon={Inbox} label="درخواست‌های نمایندگی" count={m.pending_agent_requests} to="/agent-requests" />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>آخرین فعالیت</CardTitle>
            <Link to="/orders" className="flex items-center gap-1 text-xs font-bold text-muted-foreground hover:text-white">
              مشاهده همه <ArrowLeft className="h-3.5 w-3.5" />
            </Link>
          </div>
        </CardHeader>
        <CardContent>
          <Table>
            <THead>
              <TR>
                <TH>کاربر</TH><TH>کانفیگ</TH><TH>نوع</TH><TH>حجم</TH><TH>مبلغ</TH><TH>وضعیت</TH><TH>تاریخ</TH>
              </TR>
            </THead>
            <TBody>
              {data.recent_orders.map((o) => {
                const s = statusBadge(o.status);
                const extra = Number(o.subscription_count || 0) > 1 ? ` +${Number(o.subscription_count) - 1}` : "";
                return (
                  <TR key={o.order_id}>
                    <TD><UserLink userId={o.user_id} name={o.first_name} username={o.username} /></TD>
                    <TD>
                      <div className="text-white">{String(o.subscription_name || o.client_name || "رندوم")}{extra}</div>
                      {o.subscription_id ? <code className="text-[0.68rem] text-muted-foreground">{String(o.subscription_id)}</code> : null}
                    </TD>
                    <TD className="text-xs">{o.order_type === "renewal" ? "تمدید" : "خرید"}</TD>
                    <TD>{o.gb}×{o.qty}</TD>
                    <TD>{toman(o.final_price)}</TD>
                    <TD><Badge variant={s.variant}>{s.label}</Badge></TD>
                    <TD className="text-xs text-muted-foreground">{jalaliDate(o.created_at)}</TD>
                  </TR>
                );
              })}
              {data.recent_orders.length === 0 && (
                <TR><TD colSpan={7} className="py-8 text-center text-muted-foreground">هنوز سفارشی ثبت نشده است.</TD></TR>
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
