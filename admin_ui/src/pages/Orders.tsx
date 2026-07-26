import * as React from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Pager } from "@/components/ui/pager";
import { UserLink } from "@/components/UserLink";
import { PanelBadge } from "@/components/ui/panel-badge";
import { toman, jalaliDate } from "@/lib/utils";
import { statusBadge } from "@/lib/status";
import type { Backend } from "@/lib/backend";

function useDebounced<T>(value: T, delay = 350): T {
  const [v, setV] = React.useState(value);
  React.useEffect(() => {
    const t = setTimeout(() => setV(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return v;
}

export function Orders() {
  const [params] = useSearchParams();
  const [q, setQ] = React.useState(params.get("q") || "");
  const [page, setPage] = React.useState(1);
  const dq = useDebounced(q);
  React.useEffect(() => { setQ(params.get("q") || ""); }, [params]);
  React.useEffect(() => { setPage(1); }, [dq]);
  const { data, isFetching } = useQuery({
    queryKey: ["orders", dq, page],
    queryFn: () => api.orders(dq, "all", page),
    placeholderData: keepPreviousData,
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <CardTitle>سفارش‌ها</CardTitle>
          <div className="relative w-full sm:w-72">
            <Search className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pr-9"
              placeholder="جست‌وجو: شناسه، کاربر، نام کانفیگ…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {!data ? (
          <div className="space-y-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-10" />
            ))}
          </div>
        ) : (
          <Table className={isFetching ? "opacity-60 transition-opacity" : ""}>
            <THead>
              <TR>
                <TH>کاربر</TH>
                <TH>کانفیگ</TH>
                <TH>پنل</TH>
                <TH>نوع</TH>
                <TH>حجم</TH>
                <TH>مبلغ</TH>
                <TH>وضعیت</TH>
                <TH>تاریخ</TH>
              </TR>
            </THead>
            <TBody>
              {data.items.map((o) => {
                const s = statusBadge(o.status);
                const extra = Number(o.subscription_count || 0) > 1 ? ` +${Number(o.subscription_count) - 1}` : "";
                return (
                  <TR key={o.order_id}>
                    <TD><UserLink userId={o.user_id} name={o.first_name} username={o.username} /></TD>
                    <TD>
                      <div className="text-white">{String(o.subscription_name || o.client_name || "رندوم")}{extra}</div>
                      {o.subscription_id ? <code className="text-[0.68rem] text-muted-foreground">{String(o.subscription_id)}</code> : null}
                    </TD>
                    <TD><PanelBadge backend={(o.backend as Backend) || "xui"} /></TD>
                    <TD className="text-xs">{o.order_type === "renewal" ? "تمدید" : "خرید"}</TD>
                    <TD>{o.gb}×{o.qty} GB</TD>
                    <TD>{toman(o.final_price)}</TD>
                    <TD>
                      <Badge variant={s.variant}>{s.label}</Badge>
                    </TD>
                    <TD className="text-xs text-muted-foreground">{jalaliDate(o.created_at)}</TD>
                  </TR>
                );
              })}
              {data.items.length === 0 && (
                <TR>
                  <TD colSpan={8} className="py-8 text-center text-muted-foreground">
                    موردی یافت نشد.
                  </TD>
                </TR>
              )}
            </TBody>
          </Table>
        )}
        {data && <Pager page={page} hasMore={data.has_more} onPage={setPage} loading={isFetching} />}
      </CardContent>
    </Card>
  );
}
