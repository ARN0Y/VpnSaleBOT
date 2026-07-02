import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { UserLink } from "@/components/UserLink";
import { jalaliDate, toman } from "@/lib/utils";

const STATUSES = [
  { key: "pending", label: "در انتظار" },
  { key: "approved", label: "تایید شده" },
  { key: "rejected", label: "رد شده" },
];

function RequestCard({ row, onChanged }: { row: Record<string, unknown>; onChanged: () => void }) {
  const id = String(row.req_id);
  const [price, setPrice] = React.useState("0");

  const approve = useMutation({
    mutationFn: () =>
      api.approveAgentRequest(id, {
        price_per_gb: Number(price) || 0,
      }),
    onSuccess: onChanged,
  });
  const reject = useMutation({ mutationFn: () => api.rejectAgentRequest(id), onSuccess: onChanged });
  const busy = approve.isPending || reject.isPending;
  const status = String(row.status);

  return (
    <div className="rounded-2xl border border-border bg-white/[0.02] p-4">
      <div className="flex items-center justify-between">
        <UserLink userId={String(row.user_id)} name={row.first_name} username={row.username} />
        <span className="text-xs text-muted-foreground">{jalaliDate(row.created_at as number)}</span>
      </div>
      <p className="mt-2 whitespace-pre-wrap rounded-xl bg-black/20 p-3 text-sm text-slate-300">
        {String(row.text || "—")}
      </p>
      {status === "pending" ? (
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <div className="text-[0.68rem] text-muted-foreground">قیمت هر گیگ</div>
            <Input className="h-9 w-36" value={price} onChange={(e) => setPrice(e.target.value)} inputMode="numeric" />
          </div>
          <div className="rounded-xl border border-border bg-white/[0.03] px-3 py-2 text-xs text-muted-foreground">
            خرید نماینده‌ها فقط از کیف پول انجام می‌شود.
          </div>
          <div className="flex gap-2">
            <Button size="sm" disabled={busy} onClick={() => approve.mutate()}>
              <Check className="h-4 w-4" /> تایید
            </Button>
            <Button size="sm" variant="destructive" disabled={busy} onClick={() => reject.mutate()}>
              <X className="h-4 w-4" /> رد
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-3">
          <Badge variant={status === "approved" ? "success" : "danger"}>
            {status === "approved" ? "تایید شده" : "رد شده"}
          </Badge>
        </div>
      )}
    </div>
  );
}

export function AgentRequests() {
  const [status, setStatus] = React.useState("pending");
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["agent-requests", status],
    queryFn: () => api.agentRequests(status),
  });
  const onChanged = () => {
    qc.invalidateQueries({ queryKey: ["agent-requests"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <CardTitle>درخواست‌های نمایندگی {data ? `(${data.count})` : ""}</CardTitle>
          <div className="flex gap-1 rounded-xl border border-border bg-white/[0.02] p-1">
            {STATUSES.map((s) => (
              <Button key={s.key} size="sm" variant={status === s.key ? "default" : "ghost"} onClick={() => setStatus(s.key)}>
                {s.label}
              </Button>
            ))}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading || !data ? (
          Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-28" />)
        ) : data.items.length === 0 ? (
          <div className="py-8 text-center text-muted-foreground">موردی نیست.</div>
        ) : (
          data.items.map((row) => (
            <RequestCard key={String(row.req_id)} row={row} onChanged={onChanged} />
          ))
        )}
        <p className="pt-1 text-xs text-muted-foreground">مبلغ‌ها به تومان • {toman(data?.count || 0)} مورد</p>
      </CardContent>
    </Card>
  );
}
