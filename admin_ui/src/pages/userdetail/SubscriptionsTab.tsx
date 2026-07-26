import * as React from "react";
import { Link } from "react-router-dom";
import { RefreshCw, Power, PowerOff, Boxes, Search, ExternalLink, FlaskConical } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { CopyButton } from "@/components/ui/copy-button";
import { EmptyState } from "@/components/ui/empty-state";
import { PanelBadge } from "@/components/ui/panel-badge";
import { isPasarGuard } from "@/lib/backend";
import { gbFromBytes, jalaliDate } from "@/lib/utils";
import { n, s, type UserMutations } from "./helpers";
import type { UserDetailBundle } from "@/lib/types";

export function SubscriptionsTab({
  data,
  mutations,
  subsPage,
  setSubsPage,
}: {
  data: UserDetailBundle;
  mutations: UserMutations;
  subsPage: number;
  setSubsPage: (updater: (p: number) => number) => void;
}) {
  const [filter, setFilter] = React.useState("");
  const [status, setStatus] = React.useState<"all" | "active" | "disabled" | "test">("all");

  const counts = {
    active: data.subscriptions.filter((x) => n(x.panel_enabled) === 1).length,
    disabled: data.subscriptions.filter((x) => n(x.panel_enabled) !== 1).length,
    test: data.subscriptions.filter((x) => n(x.is_test) === 1).length,
  };
  const STATUS_CHIPS = [
    { key: "all" as const, label: `همه (${data.subscriptions.length})` },
    { key: "active" as const, label: `فعال (${counts.active})` },
    { key: "disabled" as const, label: `غیرفعال (${counts.disabled})` },
    { key: "test" as const, label: `تست (${counts.test})` },
  ];

  const list = data.subscriptions.filter((sub) => {
    const f = filter.trim().toLowerCase();
    const textOk = !f || s(sub.client_email).toLowerCase().includes(f) || s(sub.sub_id).toLowerCase().includes(f);
    const statusOk =
      status === "all" ||
      (status === "active" && n(sub.panel_enabled) === 1) ||
      (status === "disabled" && n(sub.panel_enabled) !== 1) ||
      (status === "test" && n(sub.is_test) === 1);
    return textOk && statusOk;
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <CardTitle>کانفیگ‌ها ({data.subs_total})</CardTitle>
          <div className="flex items-center gap-2">
            <div className="relative w-48">
              <Search className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input className="h-9 pr-9" placeholder="فیلتر این صفحه…" value={filter} onChange={(e) => setFilter(e.target.value)} />
            </div>
            <Button size="sm" variant="outline" disabled={mutations.sync.isPending} onClick={() => mutations.sync.mutate()}>
              <RefreshCw className={`h-4 w-4 ${mutations.sync.isPending ? "animate-spin" : ""}`} /> سینک
            </Button>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-1">
          {STATUS_CHIPS.map((c) => (
            <Button key={c.key} size="sm" variant={status === c.key ? "default" : "ghost"} onClick={() => setStatus(c.key)}>
              {c.label}
            </Button>
          ))}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {list.length === 0 ? (
          <EmptyState icon={Boxes} title="کانفیگی یافت نشد" hint="این کاربر هنوز کانفیگی نخریده یا فیلتر مطابقتی ندارد." />
        ) : (
          list.map((sub) => {
            const subId = s(sub.sub_id);
            const enabled = n(sub.panel_enabled) === 1;
            const isTest = n(sub.is_test) === 1;
            const pg = isPasarGuard(sub.inbound_id);
            const total = n(sub.panel_total_bytes);
            const used = n(sub.panel_used_bytes);
            const pct = total > 0 ? (used / total) * 100 : 0;
            const tone = pct >= 90 ? "danger" : pct >= 70 ? "warning" : "default";
            return (
              <div key={subId} className="rounded-2xl border border-border bg-white/[0.02] p-4 transition hover:border-white/15">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <b className="text-white">{s(sub.client_email) || "بدون نام"}</b>
                      <PanelBadge inboundId={sub.inbound_id} />
                      {isTest && (
                        <Badge variant="warning" className="gap-1">
                          <FlaskConical className="h-3 w-3" /> تست
                        </Badge>
                      )}
                      <Badge variant={enabled ? "success" : "muted"}>{enabled ? "فعال" : "غیرفعال"}</Badge>
                    </div>
                    <div className="mt-1 flex items-center gap-1.5">
                      <code className="text-[0.68rem] text-muted-foreground">{subId}</code>
                      <CopyButton value={subId} />
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {/* Enable/disable drives 3x-ui; PasarGuard configs are managed
                        from their own panel, so the button is not offered. */}
                    {!pg && (
                      <Button
                        size="sm"
                        variant={enabled ? "destructive" : "default"}
                        disabled={mutations.subToggle.isPending}
                        onClick={() => mutations.subToggle.mutate({ subId, enabled: !enabled })}
                      >
                        {enabled ? <PowerOff className="h-4 w-4" /> : <Power className="h-4 w-4" />}
                        {enabled ? "غیرفعال" : "فعال"}
                      </Button>
                    )}
                    <Link to={`/subscriptions/${encodeURIComponent(subId)}`}>
                      <Button size="sm" variant="outline">
                        <ExternalLink className="h-4 w-4" /> جزئیات
                      </Button>
                    </Link>
                  </div>
                </div>

                <div className="mt-3">
                  <div className="mb-1 flex items-center justify-between text-xs text-muted-foreground">
                    <span>
                      مصرف {gbFromBytes(used)} از {isTest ? gbFromBytes(total) : `${n(sub.gb)}`} GB
                    </span>
                    <span>باقی‌مانده {gbFromBytes(sub.panel_remaining_bytes)} GB</span>
                  </div>
                  <Progress value={pct} tone={tone} />
                </div>

                <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[0.7rem] text-muted-foreground">
                  <span>خرید: {jalaliDate(n(sub.created_at))}</span>
                  <span>آخرین سینک: {jalaliDate(n(sub.panel_synced_at))}</span>
                </div>
              </div>
            );
          })
        )}

        {data.subs_total_pages > 1 && (
          <div className="flex items-center justify-between pt-1 text-sm">
            <span className="text-muted-foreground">صفحه {data.subs_page} از {data.subs_total_pages}</span>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" disabled={subsPage <= 1} onClick={() => setSubsPage((p) => p - 1)}>قبلی</Button>
              <Button size="sm" variant="outline" disabled={subsPage >= data.subs_total_pages} onClick={() => setSubsPage((p) => p + 1)}>بعدی</Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
