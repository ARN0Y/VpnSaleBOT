import * as React from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import { Search, Ban, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Pager } from "@/components/ui/pager";
import { toman, jalaliDate } from "@/lib/utils";

const FILTERS = [
  { key: "all", label: "همه" },
  { key: "agents", label: "نماینده‌ها" },
  { key: "users", label: "کاربران عادی" },
];

function useDebounced<T>(value: T, delay = 350): T {
  const [v, setV] = React.useState(value);
  React.useEffect(() => {
    const t = setTimeout(() => setV(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return v;
}

export function Users() {
  const [params] = useSearchParams();
  const [q, setQ] = React.useState(params.get("q") || "");
  const [filter, setFilter] = React.useState("all");
  const [page, setPage] = React.useState(1);
  const dq = useDebounced(q);
  const qc = useQueryClient();
  React.useEffect(() => { setQ(params.get("q") || ""); }, [params]);
  React.useEffect(() => { setPage(1); }, [dq, filter]);
  const { data, isFetching } = useQuery({
    queryKey: ["users", dq, filter, page],
    queryFn: () => api.users(dq, filter, page),
    placeholderData: keepPreviousData,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["users"] });
  const ban = useMutation({
    mutationFn: (id: number) => api.banUser(id, "از پنل مدیریت"),
    onSuccess: invalidate,
  });
  const unban = useMutation({ mutationFn: (id: number) => api.unbanUser(id), onSuccess: invalidate });
  const busy = ban.isPending || unban.isPending;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <CardTitle>کاربران</CardTitle>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="flex gap-1 rounded-xl border border-border bg-white/[0.02] p-1">
              {FILTERS.map((f) => (
                <Button
                  key={f.key}
                  size="sm"
                  variant={filter === f.key ? "default" : "ghost"}
                  onClick={() => setFilter(f.key)}
                >
                  {f.label}
                </Button>
              ))}
            </div>
            <div className="relative w-full sm:w-64">
              <Search className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                className="pr-9"
                placeholder="نام، یوزرنیم یا آیدی…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
            </div>
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
                <TH>نقش</TH>
                <TH>سفارش‌ها</TH>
                <TH>حجم خرید (GB)</TH>
                <TH>مجموع خرید</TH>
                <TH>کیف پول</TH>
                <TH>عضویت</TH>
                <TH>اقدام</TH>
              </TR>
            </THead>
            <TBody>
              {data.items.map((u) => (
                <TR key={u.user_id}>
                  <TD>
                    <Link to={`/users/${u.user_id}`} className="block transition hover:opacity-80">
                      <div className="font-bold text-white underline-offset-4 hover:underline">{u.first_name || "—"}</div>
                      <div className="text-xs text-muted-foreground">
                        {u.username ? `@${u.username}` : u.user_id}
                      </div>
                    </Link>
                  </TD>
                  <TD>
                    {u.access_level === "open" ? (
                      <Badge variant="success">نماینده (باز)</Badge>
                    ) : u.access_level === "closed" ? (
                      <Badge>نماینده</Badge>
                    ) : (
                      <Badge variant="muted">کاربر</Badge>
                    )}
                  </TD>
                  <TD>{toman(u.approved_orders)}</TD>
                  <TD>{toman(u.total_gb_purchased)}</TD>
                  <TD>{toman(u.total_spent)}</TD>
                  <TD>{toman(u.wallet_balance)}</TD>
                  <TD className="text-xs text-muted-foreground">{jalaliDate(u.joined_at)}</TD>
                  <TD>
                    {Number(u.disabled) === 1 ? (
                      <Button size="sm" variant="outline" disabled={busy} onClick={() => unban.mutate(u.user_id)}>
                        <ShieldCheck className="h-4 w-4" /> رفع مسدودی
                      </Button>
                    ) : (
                      <Button size="sm" variant="destructive" disabled={busy} onClick={() => ban.mutate(u.user_id)}>
                        <Ban className="h-4 w-4" /> مسدود
                      </Button>
                    )}
                  </TD>
                </TR>
              ))}
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
