import * as React from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, RefreshCw, Power, PowerOff, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { CopyButton } from "@/components/ui/copy-button";
import { PanelBadge } from "@/components/ui/panel-badge";
import { isPasarGuard } from "@/lib/backend";
import { gbFromBytes, jalaliDate } from "@/lib/utils";

function n(v: unknown): number { return Number(v || 0); }
function s(v: unknown): string { return v == null ? "" : String(v); }

export function SubscriptionDetail() {
  const { subId = "" } = useParams();
  const qc = useQueryClient();
  const [vol, setVol] = React.useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["sub-detail", subId],
    queryFn: () => api.subscriptionDetail(subId),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["sub-detail", subId] });

  const sync = useMutation({ mutationFn: () => api.syncSub(subId), onSuccess: refresh });
  const toggle = useMutation({ mutationFn: (en: boolean) => api.setSubEnabled(subId, en), onSuccess: refresh });
  const setVolM = useMutation({ mutationFn: () => api.setSubVolume(subId, Number(vol) || 0), onSuccess: refresh });

  if (isLoading || !data) return <Skeleton className="h-80" />;
  const enabled = n(data.panel_enabled) === 1;
  // A PasarGuard config lives on a different panel: the 3x-ui sync / enable /
  // volume calls do not apply to it (the API rejects them with a 409), so we
  // show what it is instead of offering actions that cannot work.
  const pg = isPasarGuard(data.inbound_id);

  return (
    <div className="space-y-5">
      <Link to="/subscriptions" className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-white">
        <ArrowRight className="h-4 w-4" /> بازگشت به کانفیگ‌ها
      </Link>

      <div className="grid gap-5 lg:grid-cols-[1fr_20rem]">
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <CardTitle>{s(data.client_email) || subId}</CardTitle>
              <div className="flex items-center gap-2">
                <PanelBadge inboundId={data.inbound_id} />
                <Badge variant={enabled ? "success" : "muted"}>{enabled ? "فعال" : "غیرفعال"}</Badge>
              </div>
            </div>
            <div className="flex items-center gap-1.5">
              <code className="text-xs text-muted-foreground">{subId}</code>
              <CopyButton value={subId} />
            </div>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-3 text-sm md:grid-cols-3">
              <Item k="کاربر" v={s(data.user_id)} />
              <Item k="پنل سازنده" v={pg ? "PasarGuard" : "3x-ui"} />
              <Item k="حجم خرید" v={`${n(data.gb)} GB`} />
              <Item k="کل حجم پنل" v={`${gbFromBytes(data.panel_total_bytes)} GB`} />
              <Item k="مصرف" v={`${gbFromBytes(data.panel_used_bytes)} GB`} />
              <Item k="باقی‌مانده" v={`${gbFromBytes(data.panel_remaining_bytes)} GB`} />
              <Item k="تاریخ خرید" v={jalaliDate(n(data.created_at))} />
              <Item k="آخرین سینک" v={jalaliDate(n(data.panel_synced_at))} />
              <Item k="تعداد تمدید" v={String(n(data.renewed_count))} />
            </dl>
          </CardContent>
        </Card>

        <div className="space-y-5">
          {pg ? (
            <Card className="border-brand/25">
              <CardHeader>
                <CardTitle className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-brand" /> سرویس PasarGuard</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 text-sm leading-6 text-muted-foreground">
                <p>
                  این کانفیگ روی پنل <b className="text-white">PasarGuard</b> ساخته شده است، نه روی 3x-ui.
                  فعال/غیرفعال‌سازی، سینک و تغییر حجم برای آن باید از خودِ پنل PasarGuard انجام شود.
                </p>
                <Link to="/pasarguard">
                  <Button className="w-full" variant="outline"><ShieldCheck className="h-4 w-4" /> رفتن به مدیریت PasarGuard</Button>
                </Link>
              </CardContent>
            </Card>
          ) : (
            <>
              <Card>
                <CardHeader><CardTitle>اقدامات پنل</CardTitle></CardHeader>
                <CardContent className="space-y-2">
                  <Button className="w-full" variant="outline" disabled={sync.isPending} onClick={() => sync.mutate()}>
                    <RefreshCw className="h-4 w-4" /> سینک از 3x-ui
                  </Button>
                  {enabled ? (
                    <Button className="w-full" variant="destructive" disabled={toggle.isPending} onClick={() => toggle.mutate(false)}>
                      <PowerOff className="h-4 w-4" /> غیرفعال‌سازی
                    </Button>
                  ) : (
                    <Button className="w-full" disabled={toggle.isPending} onClick={() => toggle.mutate(true)}>
                      <Power className="h-4 w-4" /> فعال‌سازی
                    </Button>
                  )}
                </CardContent>
              </Card>
              <Card>
                <CardHeader><CardTitle>تنظیم حجم کل</CardTitle></CardHeader>
                <CardContent className="space-y-3">
                  <Field label="حجم کل جدید (GB)" hint="نباید کمتر از مصرف فعلی باشد.">
                    <Input value={vol} onChange={(e) => setVol(e.target.value)} inputMode="numeric" placeholder={gbFromBytes(data.panel_total_bytes)} />
                  </Field>
                  <Button className="w-full" disabled={setVolM.isPending || !vol} onClick={() => setVolM.mutate()}>ذخیره</Button>
                </CardContent>
              </Card>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Item({ k, v }: { k: string; v: string }) {
  return (
    <div className="rounded-xl border border-border bg-white/[0.02] p-3">
      <div className="text-xs text-muted-foreground">{k}</div>
      <div className="mt-1 font-bold text-white">{v}</div>
    </div>
  );
}
