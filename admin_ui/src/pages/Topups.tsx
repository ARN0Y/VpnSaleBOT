import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { UserLink } from "@/components/UserLink";
import { toman, jalaliDate } from "@/lib/utils";

const STATUSES = [
  { key: "pending", label: "در انتظار" },
  { key: "approved", label: "تایید شده" },
  { key: "rejected", label: "رد شده" },
  { key: "all", label: "همه" },
];

export function Topups() {
  const [status, setStatus] = React.useState("pending");
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["topups", status],
    queryFn: () => api.topups(status),
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["topups"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const approve = useMutation({ mutationFn: (id: string) => api.approveTopup(id), onSuccess: invalidate });
  const reject = useMutation({ mutationFn: (id: string) => api.rejectTopup(id), onSuccess: invalidate });
  const busy = approve.isPending || reject.isPending;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <CardTitle>درخواست‌های شارژ {data ? `(${data.count})` : ""}</CardTitle>
          <div className="flex gap-1 rounded-xl border border-border bg-white/[0.02] p-1">
            {STATUSES.map((s) => (
              <Button key={s.key} size="sm" variant={status === s.key ? "default" : "ghost"} onClick={() => setStatus(s.key)}>
                {s.label}
              </Button>
            ))}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading || !data ? (
          <div className="space-y-2">
            {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-12" />)}
          </div>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>کاربر</TH>
                <TH>روش</TH>
                <TH>مبلغ</TH>
                <TH>وضعیت</TH>
                <TH>تاریخ</TH>
                <TH>اقدام</TH>
              </TR>
            </THead>
            <TBody>
              {data.items.map((t) => {
                const id = String(t.topup_id);
                const st = String(t.status);
                return (
                  <TR key={id}>
                    <TD><UserLink userId={String(t.user_id)} name={t.first_name} username={t.username} /></TD>
                    <TD className="text-xs">{t.method === "crypto" ? "تتر" : "کارت"}</TD>
                    <TD>{toman(t.amount_toman as number)}</TD>
                    <TD>
                      <Badge
                        variant={st === "approved" ? "success" : st === "rejected" ? "danger" : "warning"}
                      >
                        {st === "approved" ? "تایید" : st === "rejected" ? "رد" : "در انتظار"}
                      </Badge>
                    </TD>
                    <TD className="text-xs text-muted-foreground">{jalaliDate(t.created_at as number)}</TD>
                    <TD>
                      {st === "pending" ? (
                        <div className="flex gap-2">
                          <Button size="sm" disabled={busy} onClick={() => approve.mutate(id)}>
                            <Check className="h-4 w-4" /> تایید
                          </Button>
                          <Button
                            size="sm"
                            variant="destructive"
                            disabled={busy}
                            onClick={() => reject.mutate(id)}
                          >
                            <X className="h-4 w-4" /> رد
                          </Button>
                        </div>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                    </TD>
                  </TR>
                );
              })}
              {data.items.length === 0 && (
                <TR>
                  <TD colSpan={6} className="py-8 text-center text-muted-foreground">موردی نیست.</TD>
                </TR>
              )}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
