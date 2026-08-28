import * as React from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import {
  BadgeCheck, Clock, Coins, Database, Download, Filter, Search, ShoppingCart, Users, X,
} from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { StatTile } from "@/components/ui/stat-tile";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Pager } from "@/components/ui/pager";
import { UserLink } from "@/components/UserLink";
import { JalaliDateInput } from "@/components/ui/jalali-date-input";
import { toman, jalaliDate } from "@/lib/utils";
import { statusBadge, paymentLabel, PAYMENT_METHODS } from "@/lib/status";
import { formatJalali, startOfDayTs, endOfDayTs, fromJalali, toJalali } from "@/lib/jalali";
import type { OrderFilters } from "@/lib/types";

function useDebounced<T>(value: T, delay = 350): T {
  const [v, setV] = React.useState(value);
  React.useEffect(() => {
    const t = setTimeout(() => setV(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return v;
}

type QuickRange = "all" | "today" | "yesterday" | "7d" | "30d" | "month" | "custom";

/** Quick ranges resolve to the same [from, to] the pickers produce. */
function quickRange(kind: QuickRange): { from: Date | null; to: Date | null } {
  const now = new Date();
  const day = (offset: number) =>
    new Date(now.getFullYear(), now.getMonth(), now.getDate() + offset);
  switch (kind) {
    case "today":
      return { from: day(0), to: day(0) };
    case "yesterday":
      return { from: day(-1), to: day(-1) };
    case "7d":
      return { from: day(-6), to: day(0) };
    case "30d":
      return { from: day(-29), to: day(0) };
    case "month": {
      const j = toJalali(now);
      return { from: fromJalali({ jy: j.jy, jm: j.jm, jd: 1 }), to: day(0) };
    }
    default:
      return { from: null, to: null };
  }
}

const QUICK: { key: QuickRange; label: string }[] = [
  { key: "all", label: "همه" },
  { key: "today", label: "امروز" },
  { key: "yesterday", label: "دیروز" },
  { key: "7d", label: "۷ روز" },
  { key: "30d", label: "۳۰ روز" },
  { key: "month", label: "این ماه" },
];

export function Orders() {
  const [params] = useSearchParams();
  const [q, setQ] = React.useState(params.get("q") || "");
  const [quick, setQuick] = React.useState<QuickRange>("all");
  const [from, setFrom] = React.useState<Date | null>(null);
  const [to, setTo] = React.useState<Date | null>(null);
  const [status, setStatus] = React.useState("");
  const [type, setType] = React.useState("");
  const [method, setMethod] = React.useState("");
  const [sort, setSort] = React.useState("newest");
  const [minAmount, setMinAmount] = React.useState("");
  const [maxAmount, setMaxAmount] = React.useState("");
  const [showFilters, setShowFilters] = React.useState(false);
  const [page, setPage] = React.useState(1);

  const dq = useDebounced(q);
  const dMin = useDebounced(minAmount);
  const dMax = useDebounced(maxAmount);
  React.useEffect(() => { setQ(params.get("q") || ""); }, [params]);

  const applyQuick = (kind: QuickRange) => {
    setQuick(kind);
    const range = quickRange(kind);
    setFrom(range.from);
    setTo(range.to);
  };

  // Editing a picker by hand means the operator has left the quick ranges behind.
  const setFromManual = (d: Date | null) => { setFrom(d); setQuick("custom"); };
  const setToManual = (d: Date | null) => { setTo(d); setQuick("custom"); };

  const filters: OrderFilters = React.useMemo(
    () => ({
      q: dq,
      from: from ? startOfDayTs(from) : 0,
      to: to ? endOfDayTs(to) : 0,
      status,
      type,
      method,
      sort,
      min_amount: Number(dMin) || 0,
      max_amount: Number(dMax) || 0,
    }),
    [dq, from, to, status, type, method, sort, dMin, dMax],
  );

  React.useEffect(() => { setPage(1); }, [filters]);

  const { data, isFetching, error } = useQuery({
    queryKey: ["orders", filters, page],
    queryFn: () => api.orders(filters, page),
    placeholderData: keepPreviousData,
  });

  const activeCount =
    (from ? 1 : 0) + (to ? 1 : 0) + (status ? 1 : 0) + (type ? 1 : 0) +
    (method ? 1 : 0) + (dMin ? 1 : 0) + (dMax ? 1 : 0);

  const reset = () => {
    setQ(""); setStatus(""); setType(""); setMethod(""); setSort("newest");
    setMinAmount(""); setMaxAmount(""); applyQuick("all");
  };

  const rangeLabel =
    from || to
      ? `${from ? formatJalali(from) : "ابتدا"} تا ${to ? formatJalali(to) : "امروز"}`
      : "کل بازه";

  const s = data?.summary;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <CardTitle>سفارش‌ها</CardTitle>
              <div className="mt-1 text-[0.7rem] text-muted-foreground">{rangeLabel}</div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative w-full sm:w-64">
                <Search className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  className="pr-9"
                  placeholder="جست‌وجو: شناسه، کاربر، نام کانفیگ…"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                />
              </div>
              <Button
                variant={showFilters ? "default" : "outline"}
                size="sm"
                onClick={() => setShowFilters((v) => !v)}
              >
                <Filter className="h-4 w-4" /> فیلترها
                {activeCount > 0 && (
                  <span className="rounded-md bg-black/25 px-1.5 text-[0.65rem]">{activeCount}</span>
                )}
              </Button>
              <Button variant="outline" size="sm" asChild>
                <a href={api.ordersExportUrl(filters)} download>
                  <Download className="h-4 w-4" /> خروجی CSV
                </a>
              </Button>
            </div>
          </div>
        </CardHeader>

        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            {QUICK.map((r) => (
              <button
                key={r.key}
                type="button"
                onClick={() => applyQuick(r.key)}
                className={
                  "rounded-xl border px-3 py-1.5 text-xs transition-colors " +
                  (quick === r.key
                    ? "border-primary/50 bg-primary/15 text-white"
                    : "border-border bg-white/[0.02] text-muted-foreground hover:text-white")
                }
              >
                {r.label}
              </button>
            ))}
            {activeCount > 0 && (
              <button
                type="button"
                onClick={reset}
                className="flex items-center gap-1 rounded-xl border border-border px-3 py-1.5 text-xs text-muted-foreground hover:border-destructive/40 hover:text-destructive"
              >
                <X className="h-3 w-3" /> پاک کردن فیلترها
              </button>
            )}
          </div>

          {showFilters && (
            <div className="grid gap-3 rounded-2xl border border-border bg-white/[0.02] p-3 sm:grid-cols-2 lg:grid-cols-4">
              <JalaliDateInput label="از تاریخ" value={from} onChange={setFromManual} />
              <JalaliDateInput label="تا تاریخ" value={to} onChange={setToManual} align="end" />
              <div>
                <div className="mb-1 text-[0.7rem] text-muted-foreground">وضعیت</div>
                <Select value={status} onChange={(e) => setStatus(e.target.value)}>
                  <option value="">همه</option>
                  <option value="approved">تایید شده</option>
                  <option value="pending">در انتظار</option>
                  <option value="rejected">رد شده</option>
                </Select>
              </div>
              <div>
                <div className="mb-1 text-[0.7rem] text-muted-foreground">نوع سفارش</div>
                <Select value={type} onChange={(e) => setType(e.target.value)}>
                  <option value="">همه</option>
                  <option value="purchase">خرید جدید</option>
                  <option value="renewal">تمدید</option>
                </Select>
              </div>
              <div>
                <div className="mb-1 text-[0.7rem] text-muted-foreground">روش پرداخت</div>
                <Select value={method} onChange={(e) => setMethod(e.target.value)}>
                  <option value="">همه</option>
                  {PAYMENT_METHODS.map((m) => (
                    <option key={m} value={m}>{paymentLabel(m)}</option>
                  ))}
                </Select>
              </div>
              <div>
                <div className="mb-1 text-[0.7rem] text-muted-foreground">مرتب‌سازی</div>
                <Select value={sort} onChange={(e) => setSort(e.target.value)}>
                  <option value="newest">جدیدترین</option>
                  <option value="oldest">قدیمی‌ترین</option>
                  <option value="amount_desc">بیشترین مبلغ</option>
                  <option value="amount_asc">کمترین مبلغ</option>
                </Select>
              </div>
              <div>
                <div className="mb-1 text-[0.7rem] text-muted-foreground">حداقل مبلغ (تومان)</div>
                <Input
                  inputMode="numeric"
                  value={minAmount}
                  onChange={(e) => setMinAmount(e.target.value.replace(/[^0-9]/g, ""))}
                  placeholder="۰"
                />
              </div>
              <div>
                <div className="mb-1 text-[0.7rem] text-muted-foreground">حداکثر مبلغ (تومان)</div>
                <Input
                  inputMode="numeric"
                  value={maxAmount}
                  onChange={(e) => setMaxAmount(e.target.value.replace(/[^0-9]/g, ""))}
                  placeholder="بدون سقف"
                />
              </div>
            </div>
          )}

          {error ? (
            <div className="rounded-2xl border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
              بازه یا فیلتر انتخاب‌شده معتبر نیست.
            </div>
          ) : !s ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
              {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-24 rounded-2xl" />)}
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
              <StatTile icon={ShoppingCart} label="تعداد سفارش" value={toman(s.total_count)} sub={`${toman(s.approved_count)} تایید شده`} />
              <StatTile icon={Coins} label="درآمد تایید شده" value={toman(s.approved_amount)} sub="تومان" />
              <StatTile icon={Users} label="خریداران یکتا" value={toman(s.buyers)} sub="کاربر" />
              <StatTile icon={Database} label="حجم فروخته‌شده" value={toman(s.approved_gb)} sub="گیگابایت" />
              <StatTile icon={BadgeCheck} label="میانگین هر سفارش" value={toman(s.avg_order_value)} sub="تومان" />
              <StatTile
                icon={Clock}
                label="در انتظار / رد شده"
                value={`${toman(s.pending_count)} / ${toman(s.rejected_count)}`}
                sub={s.discount_amount ? `${toman(s.discount_amount)} تخفیف` : "بدون تخفیف"}
              />
            </div>
          )}
        </CardContent>
      </Card>

      {s && (s.top_buyers.length > 0 || s.by_payment_method.length > 0) && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader><CardTitle className="text-sm">بیشترین خرید در این بازه</CardTitle></CardHeader>
            <CardContent>
              {s.top_buyers.length === 0 ? (
                <div className="py-4 text-center text-xs text-muted-foreground">موردی نیست.</div>
              ) : (
                <div className="space-y-2">
                  {s.top_buyers.map((b, i) => (
                    <div key={b.user_id} className="flex items-center justify-between gap-2 rounded-xl border border-border bg-white/[0.02] px-3 py-2">
                      <div className="flex min-w-0 items-center gap-2">
                        <span className="w-5 shrink-0 text-center text-[0.7rem] text-muted-foreground">{i + 1}</span>
                        <UserLink userId={b.user_id} name={b.first_name} username={b.username} />
                      </div>
                      <div className="shrink-0 text-left">
                        <div className="text-sm font-bold text-white">{toman(b.spent)}</div>
                        <div className="text-[0.65rem] text-muted-foreground">
                          {toman(b.approved_orders)} سفارش · {toman(b.gb)} GB
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="text-sm">تفکیک روش پرداخت</CardTitle></CardHeader>
            <CardContent>
              {s.by_payment_method.length === 0 ? (
                <div className="py-4 text-center text-xs text-muted-foreground">موردی نیست.</div>
              ) : (
                <div className="space-y-2">
                  {s.by_payment_method.map((m) => {
                    const share = s.approved_amount > 0 ? Math.round((m.amount / s.approved_amount) * 100) : 0;
                    return (
                      <div key={m.payment_method} className="rounded-xl border border-border bg-white/[0.02] px-3 py-2">
                        <div className="flex items-center justify-between text-sm">
                          <span className="text-white">{paymentLabel(m.payment_method)}</span>
                          <span className="font-bold text-white">{toman(m.amount)}</span>
                        </div>
                        <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/5">
                          <div className="h-full rounded-full bg-primary/70" style={{ width: `${share}%` }} />
                        </div>
                        <div className="mt-1 text-[0.65rem] text-muted-foreground">
                          {toman(m.orders)} سفارش · {share}٪
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      <Card>
        <CardContent className="pt-6">
          {!data ? (
            <div className="space-y-2">
              {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-10" />)}
            </div>
          ) : (
            <Table className={isFetching ? "opacity-60 transition-opacity" : ""}>
              <THead>
                <TR>
                  <TH>کاربر</TH>
                  <TH>کانفیگ</TH>
                  <TH>نوع</TH>
                  <TH>حجم</TH>
                  <TH>مبلغ</TH>
                  <TH>پرداخت</TH>
                  <TH>وضعیت</TH>
                  <TH>تاریخ</TH>
                </TR>
              </THead>
              <TBody>
                {data.items.map((o) => {
                  const badge = statusBadge(o.status);
                  const extra = Number(o.subscription_count || 0) > 1 ? ` +${Number(o.subscription_count) - 1}` : "";
                  return (
                    <TR key={o.order_id}>
                      <TD><UserLink userId={o.user_id} name={o.first_name} username={o.username} /></TD>
                      <TD>
                        <div className="text-white">{String(o.subscription_name || o.client_name || "رندوم")}{extra}</div>
                        {o.subscription_id ? <code className="text-[0.68rem] text-muted-foreground">{String(o.subscription_id)}</code> : null}
                      </TD>
                      <TD className="text-xs">{o.order_type === "renewal" ? "تمدید" : "خرید"}</TD>
                      <TD>{o.gb}×{o.qty} GB</TD>
                      <TD>{toman(o.final_price)}</TD>
                      <TD className="text-xs text-muted-foreground">{paymentLabel(String(o.payment_method || ""))}</TD>
                      <TD><Badge variant={badge.variant}>{badge.label}</Badge></TD>
                      <TD className="text-xs text-muted-foreground">{jalaliDate(o.created_at)}</TD>
                    </TR>
                  );
                })}
                {data.items.length === 0 && (
                  <TR>
                    <TD colSpan={8} className="py-8 text-center text-muted-foreground">
                      در این بازه سفارشی ثبت نشده است.
                    </TD>
                  </TR>
                )}
              </TBody>
            </Table>
          )}
          {data && <Pager page={page} hasMore={data.has_more} onPage={setPage} loading={isFetching} />}
        </CardContent>
      </Card>
    </div>
  );
}
