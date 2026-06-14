import * as React from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { ArrowRight, Users, Database, HardDrive, Clock, RefreshCw, ShieldCheck, Search } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Progress } from "@/components/ui/progress";
import { StatTile } from "@/components/ui/stat-tile";
import { EmptyState } from "@/components/ui/empty-state";
import { CopyButton } from "@/components/ui/copy-button";
import { Input } from "@/components/ui/input";
import { Pager } from "@/components/ui/pager";
import { api } from "@/lib/api";
import { gbFromBytes } from "@/lib/utils";

type Row = Record<string, unknown>;
const PAGE_SIZE = 25;

function fmtIso(v: unknown, withSeconds = false): string {
  if (!v) return "—";
  try {
    return new Intl.DateTimeFormat("fa-IR", {
      dateStyle: "short",
      timeStyle: withSeconds ? "medium" : "short",
    }).format(new Date(String(v)));
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
    return <span className="whitespace-nowrap text-xs">{gbFromBytes(used)} / <span className="text-muted-foreground">نامحدود</span></span>;
  }
  const pct = (used / limit) * 100;
  return (
    <div className="min-w-[7.5rem] space-y-1">
      <div className="whitespace-nowrap text-[11px] text-muted-foreground">{gbFromBytes(used)} / {gbFromBytes(limit)} گیگ</div>
      <Progress value={pct} tone={pct >= 90 ? "danger" : pct >= 70 ? "warning" : "success"} />
    </div>
  );
}

export function PasarGuardAdminDetail() {
  const { username = "" } = useParams();
  const [page, setPage] = React.useState(1);
  const [searchInput, setSearchInput] = React.useState("");
  const [search, setSearch] = React.useState("");

  // debounce search → reset to page 1
  React.useEffect(() => {
    const t = setTimeout(() => { setSearch(searchInput.trim()); setPage(1); }, 350);
    return () => clearTimeout(t);
  }, [searchInput]);

  const statsQ = useQuery({
    queryKey: ["pg-admin-stats", username],
    queryFn: () => api.pgAdminStats(username),
    refetchInterval: 60000,
  });
  const usersQ = useQuery({
    queryKey: ["pg-admin-users", username, page, search],
    queryFn: () => api.pgAdminUsers(username, (page - 1) * PAGE_SIZE, PAGE_SIZE, search),
    placeholderData: keepPreviousData,
    refetchInterval: 30000,
  });

  const data = usersQ.data;
  const users = (data?.users || []) as Row[];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const admin = (data?.admin || {}) as Row;
  const adminActive = String(admin.status || "active") === "active";
  const stats = statsQ.data?.ok ? statsQ.data : null;
  const statsLoading = statsQ.isLoading;

  return (
    <div dir="rtl" className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
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
              سطح دسترسی: {String(admin.role_name || "—")} · وضعیت ادمین:{" "}
              <span className={adminActive ? "text-emerald-300" : "text-muted-foreground"}>{adminActive ? "فعال" : "غیرفعال"}</span>
            </p>
          </div>
        </div>
        <Button size="sm" variant="ghost" onClick={() => { statsQ.refetch(); usersQ.refetch(); }} disabled={usersQ.isFetching}>
          <RefreshCw className={usersQ.isFetching ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> به‌روزرسانی
        </Button>
      </div>

      {/* KPI tiles */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {statsLoading ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-24" />)
        ) : (
          <>
            <StatTile icon={Users} label="تعداد کل حساب‌ها" value={stats ? String(stats.total) : "—"} sub={stats?.capped ? "نمایش تقریبی" : undefined} />
            <StatTile icon={Database} label="مصرف کل" value={stats ? `${gbFromBytes(stats.used)} گیگ` : "—"} />
            <StatTile icon={HardDrive} label="حجم کل تخصیص‌یافته" value={stats ? `${gbFromBytes(stats.allocated)} گیگ` : "—"} />
            <StatTile
              icon={Clock}
              label="ساخته‌شده در ۲۴ ساعت اخیر"
              value={stats ? `${gbFromBytes(stats.created_24h_data)} گیگ` : "—"}
              sub={stats ? `${stats.created_24h_count} حساب` : undefined}
            />
          </>
        )}
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <CardTitle className="text-sm">فهرست حساب‌های کاربری {total ? `(${total.toLocaleString("en-US")})` : ""}</CardTitle>
            <div className="relative w-full sm:w-72">
              <Search className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="جستجوی نام کاربری…"
                className="pr-9"
                dir="ltr"
              />
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {usersQ.isLoading ? (
            <div className="space-y-2">{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-12" />)}</div>
          ) : !data?.ok ? (
            <div className="rounded-xl border border-rose-400/30 bg-rose-500/5 p-4 text-sm text-rose-200">
              {data?.error || "دریافت اطلاعات ناموفق بود."}
            </div>
          ) : users.length === 0 ? (
            <EmptyState icon={Users} title="حسابی یافت نشد" hint={search ? "برای این جستجو نتیجه‌ای نیست." : "این نماینده هنوز حساب کاربری ایجاد نکرده است."} />
          ) : (
            <>
              <div className="overflow-x-auto">
                <Table>
                  <THead>
                    <TR>
                      <TH>#</TH>
                      <TH>نام کاربری</TH>
                      <TH>وضعیت</TH>
                      <TH>مصرف / حجم</TH>
                      <TH>حجم کل</TH>
                      <TH>تاریخ ایجاد</TH>
                      <TH>تاریخ انقضا</TH>
                      <TH>لینک اشتراک</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {users.map((u, i) => (
                      <TR key={String(u.username)}>
                        <TD className="text-[11px] text-muted-foreground">{(page - 1) * PAGE_SIZE + i + 1}</TD>
                        <TD className="font-mono text-xs font-bold text-white">
                          <span className="flex items-center gap-1.5">
                            {isOnline(u.online_at) && <span className="h-2 w-2 shrink-0 rounded-full bg-emerald-400 shadow-[0_0_8px] shadow-emerald-400/60" title="آنلاین" />}
                            {String(u.username)}
                          </span>
                        </TD>
                        <TD><StatusBadge status={u.status} /></TD>
                        <TD><UsageCell used={Number(u.used_traffic || 0)} limit={Number(u.data_limit || 0)} /></TD>
                        <TD className="whitespace-nowrap text-xs">{!u.data_limit || Number(u.data_limit) <= 0 ? "نامحدود" : `${gbFromBytes(u.data_limit)} گیگ`}</TD>
                        <TD className="whitespace-nowrap text-xs text-muted-foreground">{fmtIso(u.created_at, true)}</TD>
                        <TD className="whitespace-nowrap text-xs text-muted-foreground">{fmtIso(u.expire)}</TD>
                        <TD>{u.subscription_url ? <CopyButton value={String(u.subscription_url)} title="کپی لینک اشتراک" /> : "—"}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </div>
              <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
                <span>صفحه {page} از {totalPages.toLocaleString("en-US")}</span>
                <Pager page={page} hasMore={page < totalPages} onPage={setPage} loading={usersQ.isFetching} />
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
