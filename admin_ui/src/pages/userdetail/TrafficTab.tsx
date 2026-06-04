import { Link } from "react-router-dom";
import { Gauge, Boxes, ArrowDownUp } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Donut } from "@/components/ui/donut";
import { StatTile } from "@/components/ui/stat-tile";
import { EmptyState } from "@/components/ui/empty-state";
import { gbFromBytes } from "@/lib/utils";
import { n, s } from "./helpers";
import type { UserDetailBundle } from "@/lib/types";

export function TrafficTab({ data }: { data: UserDetailBundle }) {
  const subs = data.subscriptions;
  const totalBytes = subs.reduce((a, x) => a + n(x.panel_total_bytes), 0);
  const usedBytes = subs.reduce((a, x) => a + n(x.panel_used_bytes), 0);
  const remainBytes = Math.max(0, totalBytes - usedBytes);
  const usedPct = totalBytes > 0 ? Math.round((usedBytes / totalBytes) * 100) : 0;
  const activeCount = subs.filter((x) => n(x.panel_enabled) === 1).length;

  const top = subs
    .slice()
    .sort((a, b) => n(b.panel_used_bytes) - n(a.panel_used_bytes))
    .slice(0, 10);

  if (subs.length === 0) {
    return (
      <Card>
        <CardContent>
          <EmptyState icon={Gauge} title="داده‌ی ترافیکی نیست" hint="این کاربر کانفیگی ندارد یا هنوز سینک نشده است." />
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-5">
      <div className="grid gap-5 lg:grid-cols-[20rem_1fr]">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><ArrowDownUp className="h-4 w-4" /> مصرف کلی (این صفحه)</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col items-center gap-4">
            <Donut
              centerTop={`${usedPct}%`}
              centerBottom="مصرف‌شده"
              segments={[
                { label: "مصرف", value: usedBytes, color: "hsl(0 0% 92%)" },
                { label: "باقی", value: remainBytes, color: "hsl(240 4% 18%)" },
              ]}
            />
            <div className="grid w-full grid-cols-2 gap-2 text-center text-xs">
              <div className="rounded-xl border border-border bg-white/[0.02] p-2">
                <div className="text-muted-foreground">مصرف‌شده</div>
                <div className="font-black text-white">{gbFromBytes(usedBytes)} GB</div>
              </div>
              <div className="rounded-xl border border-border bg-white/[0.02] p-2">
                <div className="text-muted-foreground">باقی‌مانده</div>
                <div className="font-black text-white">{gbFromBytes(remainBytes)} GB</div>
              </div>
            </div>
          </CardContent>
        </Card>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-2">
          <StatTile icon={Gauge} label="کل حجم (این صفحه)" value={`${gbFromBytes(totalBytes)} GB`} />
          <StatTile icon={Gauge} label="مصرف‌شده" value={`${gbFromBytes(usedBytes)} GB`} />
          <StatTile icon={Boxes} label="کانفیگ‌های فعال" value={`${activeCount} / ${subs.length}`} />
          <StatTile icon={Gauge} label="میانگین مصرف هر کانفیگ" value={`${gbFromBytes(subs.length ? usedBytes / subs.length : 0)} GB`} />
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>پرمصرف‌ترین کانفیگ‌ها</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {top.map((sub) => {
            const total = n(sub.panel_total_bytes);
            const used = n(sub.panel_used_bytes);
            const pct = total > 0 ? (used / total) * 100 : 0;
            const tone = pct >= 90 ? "danger" : pct >= 70 ? "warning" : "default";
            const subId = s(sub.sub_id);
            return (
              <Link
                key={subId}
                to={`/subscriptions/${encodeURIComponent(subId)}`}
                className="block rounded-xl border border-border bg-white/[0.02] p-3 transition hover:border-white/15"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-bold text-white">{s(sub.client_email) || subId}</span>
                  <Badge variant={n(sub.panel_enabled) === 1 ? "success" : "muted"}>
                    {n(sub.panel_enabled) === 1 ? "فعال" : "غیرفعال"}
                  </Badge>
                </div>
                <div className="mt-2">
                  <Progress value={pct} tone={tone} />
                  <div className="mt-1 flex justify-between text-[0.68rem] text-muted-foreground">
                    <span>{gbFromBytes(used)} GB مصرف</span>
                    <span>از {gbFromBytes(total)} GB</span>
                  </div>
                </div>
              </Link>
            );
          })}
        </CardContent>
      </Card>
    </div>
  );
}
