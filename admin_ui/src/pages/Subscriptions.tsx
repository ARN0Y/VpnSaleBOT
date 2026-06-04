import * as React from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import { Search, Power, PowerOff, ChevronLeft, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { UserLink } from "@/components/UserLink";
import { formatGb } from "@/lib/utils";

function useDebounced<T>(value: T, delay = 350): T {
  const [v, setV] = React.useState(value);
  React.useEffect(() => {
    const t = setTimeout(() => setV(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return v;
}

export function Subscriptions() {
  const [params] = useSearchParams();
  const [q, setQ] = React.useState(params.get("q") || "");
  const [page, setPage] = React.useState(1);
  const dq = useDebounced(q);
  const qc = useQueryClient();

  React.useEffect(() => { setQ(params.get("q") || ""); }, [params]);
  React.useEffect(() => setPage(1), [dq]);

  const { data, isFetching } = useQuery({
    queryKey: ["subscriptions", dq, page],
    queryFn: () => api.subscriptions(dq, page),
    placeholderData: keepPreviousData,
  });

  const toggle = useMutation({
    mutationFn: ({ subId, enabled }: { subId: string; enabled: boolean }) =>
      api.setSubEnabled(subId, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["subscriptions"] }),
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <CardTitle>کانفیگ‌ها {data ? `(${data.total})` : ""}</CardTitle>
          <div className="relative w-full sm:w-72">
            <Search className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input className="pr-9" placeholder="sub_id، نام کانفیگ یا آیدی…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {!data ? (
          <div className="space-y-2">
            {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-10" />)}
          </div>
        ) : (
          <>
            <Table className={isFetching ? "opacity-60 transition-opacity" : ""}>
              <THead>
                <TR>
                  <TH>نام کانفیگ</TH>
                  <TH>کاربر</TH>
                  <TH>حجم</TH>
                  <TH>وضعیت</TH>
                  <TH>اقدام</TH>
                </TR>
              </THead>
              <TBody>
                {data.items.map((s) => {
                  const subId = String(s.sub_id);
                  const enabled = Number(s.panel_enabled) === 1;
                  return (
                    <TR key={subId}>
                      <TD className="font-mono text-xs">
                        <Link to={`/subscriptions/${encodeURIComponent(subId)}`} className="text-white underline-offset-4 hover:underline">
                          {String(s.client_email || subId)}
                        </Link>
                      </TD>
                      <TD className="text-sm"><UserLink userId={String(s.user_id)} name={s.first_name} username={s.username} /></TD>
                      <TD>{formatGb(s.gb as number)} GB</TD>
                      <TD>
                        <Badge variant={enabled ? "success" : "muted"}>{enabled ? "فعال" : "غیرفعال"}</Badge>
                      </TD>
                      <TD>
                        <Button
                          size="sm"
                          variant={enabled ? "destructive" : "default"}
                          disabled={toggle.isPending}
                          onClick={() => toggle.mutate({ subId, enabled: !enabled })}
                        >
                          {enabled ? <PowerOff className="h-4 w-4" /> : <Power className="h-4 w-4" />}
                          {enabled ? "غیرفعال" : "فعال"}
                        </Button>
                      </TD>
                    </TR>
                  );
                })}
                {data.items.length === 0 && (
                  <TR>
                    <TD colSpan={5} className="py-8 text-center text-muted-foreground">موردی یافت نشد.</TD>
                  </TR>
                )}
              </TBody>
            </Table>
            <div className="mt-4 flex items-center justify-between text-sm">
              <span className="text-muted-foreground">صفحه {page} از {totalPages}</span>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                  <ChevronRight className="h-4 w-4" /> قبلی
                </Button>
                <Button size="sm" variant="outline" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
                  بعدی <ChevronLeft className="h-4 w-4" />
                </Button>
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
