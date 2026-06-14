import * as React from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Users, Database, Activity, RefreshCw, ShieldCheck, Wifi } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Progress } from "@/components/ui/progress";
import { StatTile } from "@/components/ui/stat-tile";
import { EmptyState } from "@/components/ui/empty-state";
import { CopyButton } from "@/components/ui/copy-button";
import { api } from "@/lib/api";
import { gbFromBytes } from "@/lib/utils";

type Row = Record<string, unknown>;

function fmtIso(v: unknown): string {
  if (!v) return "—";
  try {
    return new Intl.DateTimeFormat("fa-IR", { dateStyle: "short", timeStyle: "short" }).format(new Date(String(v)));
  } catch {
    return String(v);
  }
}

function isOnline(v: unknown): boolean {
  if (!v) return false;
  const t = new Date(String(v)).getTime();
  return Number.isFinite(t) && Date.now() - t < 3 * 60 * 1000;
}

const USER_STATUS: Record<string, { label: string; variant: "success" | "warning" | "danger" | "default" }> = {
  active: { label: "فعال", variant: "success" },
  disabled: { label: "غیرفعال", variant: "default" },
  limited: { label: "اتمام حجم", variant: "warning" },
  expired: { label: "منقضی", variant: "danger" },
  on_hold: { label: "در انتظار", variant: "warning" },
};

function StatusBadge({ status }: { status: unknown }) {
  const s = USER_STATUS[String(status)] ?? { label: String(status || "—"), variant: "default" as const };
  return <Badge variant={s.variant}>{s.label}</Badge>;
}

function UsageCell({ used, limit }: { used: number; limit: number }) {
  if (!limit || limit <= 0) {
    return <span className="text-xs">{gbFromBytes(used)} / <span className="text-muted-foreground">نامحدود</span></span>;
  }
  const pct = (used / limit) * 100;
  return (
    <div className="min-w-[8rem] space-y-1">
      <div className="text-[11px] text-muted-foreground">{gbFromBytes(used)} / {gbFromBytes(limit)} گیگابایت</div>
      <Progress value={pct} tone={pct >= 90 ? "danger" : pct >= 70 ? "warning" : "success"} />
    </div>
  );
}

export function PasarGuardAdminDetail() {
  const { username = "" } = useParams();
  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["pg-admin-users", username],
    queryFn: () => api.pgAdminUsers(username),
    refetchInterval: 20000,
  });

  const users = (data?.users || []) as Row[];
  const admin = (data?.admin || {}) as Row;
  const totalUsed = users.reduce((acc, u) => acc + Number(u.used_traffic || 0), 0);
  const onlineCount = users.filter((u) => isOnline(u.online_at)).length;
  const adminActive = String(admin.status || "active") === "active";

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" asChild>
            <Link to="/pasarguard"><ArrowRight className="h-4 w-4" /> بازگشت</Link>
          </Button>
          <div>
            <h1 className="flex items-center gap-2 text-lg font-black text-white">
              <ShieldCheck className="h-5 w-5 text-brand" /> حساب‌های نماینده
              <span className="font-mono text-brand" dir="ltr">{username}</span>
            </h1>
            <p className="text-xs text-muted-foreground">
              نقش: {String(admin.role_name || "—")} · وضعیت:{" "}
              <span className={adminActive ? "text-emerald-300" : "text-muted-foreground"}>{adminActive ? "فعال" : "غیرفعال"}</span>
            </p>
          </div>
        </div>
        <Button size="sm" variant="ghost" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCw className={isFetching ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> به‌روزرسانی
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile icon={Users} label="تعداد حساب" value={String(data?.total ?? users.length)} />
        <StatTile icon={Wifi} label="آنلاین" value={String(onlineCount)} sub="در ۳ دقیقه‌ی اخیر" />
        <StatTile icon={Database} label="مصرف کل" value={`${gbFromBytes(totalUsed)} گیگابایت`} />
        <StatTile icon={Activity} label="محدودیت حجم ادمین" value={!admin.data_limit || Number(admin.data_limit) <= 0 ? "نامحدود" : `${gbFromBytes(admin.data_limit)} گیگابایت`} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">فهرست حساب‌های کاربری</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-2">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-12" />)}</div>
          ) : !data?.ok ? (
            <div className="rounded-xl border border-rose-400/30 bg-rose-500/5 p-4 text-sm text-rose-200">
              {data?.error || "دریافت اطلاعات ناموفق بود."}
            </div>
          ) : users.length === 0 ? (
            <EmptyState icon={Users} title="حسابی وجود ندارد" hint="این نماینده هنوز حساب کاربری ایجاد نکرده است." />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>نام کاربری</TH>
                  <TH>وضعیت</TH>
                  <TH>مصرف</TH>
                  <TH>تاریخ انقضا</TH>
                  <TH>آخرین اتصال</TH>
                  <TH>تاریخ ایجاد</TH>
                  <TH>لینک اشتراک</TH>
                </TR>
              </THead>
              <TBody>
                {users.map((u) => (
                  <TR key={String(u.username)}>
                    <TD className="font-mono text-xs font-bold text-white">{String(u.username)}</TD>
                    <TD>
                      <div className="flex items-center gap-1.5">
                        <StatusBadge status={u.status} />
                        {isOnline(u.online_at) && <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_8px] shadow-emerald-400/60" title="آنلاین" />}
                      </div>
                    </TD>
                    <TD><UsageCell used={Number(u.used_traffic || 0)} limit={Number(u.data_limit || 0)} /></TD>
                    <TD className="text-xs text-muted-foreground">{fmtIso(u.expire)}</TD>
                    <TD className="text-xs text-muted-foreground">{fmtIso(u.online_at)}</TD>
                    <TD className="text-xs text-muted-foreground">{fmtIso(u.created_at)}</TD>
                    <TD>{u.subscription_url ? <CopyButton value={String(u.subscription_url)} title="کپی لینک اشتراک" /> : "—"}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
