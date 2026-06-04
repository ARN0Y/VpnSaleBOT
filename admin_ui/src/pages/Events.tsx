import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { jalaliDate } from "@/lib/utils";

const FILTERS = [
  { key: "active", label: "فعال" },
  { key: "completed", label: "تمام‌شده" },
  { key: "all", label: "همه" },
];

function statusVariant(s: string): "default" | "success" | "warning" | "danger" | "muted" {
  if (s === "completed" || s === "done") return "success";
  if (s === "failed" || s === "error") return "danger";
  if (s === "running" || s === "queued") return "warning";
  return "muted";
}

export function Events() {
  const [status, setStatus] = React.useState("active");
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["events", status],
    queryFn: () => api.events(status),
    refetchInterval: 5000,
  });
  const dismiss = useMutation({
    mutationFn: (id: string) => api.dismissEvent(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["events"] }),
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <CardTitle>رویدادها {data ? `(${data.count})` : ""}</CardTitle>
          <div className="flex gap-1 rounded-xl border border-border bg-white/[0.02] p-1">
            {FILTERS.map((f) => (
              <Button key={f.key} size="sm" variant={status === f.key ? "default" : "ghost"} onClick={() => setStatus(f.key)}>
                {f.label}
              </Button>
            ))}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading || !data ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-20" />)
        ) : data.items.length === 0 ? (
          <p className="py-8 text-center text-muted-foreground">رویدادی نیست.</p>
        ) : (
          data.items.map((e, i) => {
            const total = Number(e.total_count || 0);
            const success = Number(e.success_count || 0);
            const failed = Number(e.failed_count || 0);
            const pct = total > 0 ? Math.round(((success + failed) / total) * 100) : 0;
            return (
              <div key={i} className="rounded-xl border border-border bg-white/[0.02] p-4">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="font-bold text-white">{String(e.title || e.kind)}</div>
                    <div className="text-xs text-muted-foreground">{jalaliDate(Number(e.created_at))}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant={statusVariant(String(e.status))}>{String(e.status)}</Badge>
                    <Button size="icon" variant="ghost" onClick={() => dismiss.mutate(String(e.event_id))}>
                      <X className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
                {total > 0 && (
                  <div className="mt-3">
                    <div className="h-2 w-full overflow-hidden rounded-full bg-white/[0.06]">
                      <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${pct}%` }} />
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {success + failed} / {total} • موفق {success} • ناموفق {failed}
                      {e.last_error ? ` • خطا: ${String(e.last_error)}` : ""}
                    </div>
                  </div>
                )}
              </div>
            );
          })
        )}
      </CardContent>
    </Card>
  );
}
