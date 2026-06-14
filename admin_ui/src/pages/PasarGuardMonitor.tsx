import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Users, Eye, Power, Trash2, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { CopyButton } from "@/components/ui/copy-button";
import { useToast } from "@/components/ui/toast";
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

function dataLimitLabel(v: unknown): string {
  const n = Number(v || 0);
  return !v || n <= 0 ? "نامحدود" : `${gbFromBytes(n)} گیگ`;
}

const USER_STATUS: Record<string, { label: string; variant: "success" | "warning" | "danger" | "default" }> = {
  active: { label: "فعال", variant: "success" },
  disabled: { label: "غیرفعال", variant: "default" },
  limited: { label: "اتمام حجم", variant: "warning" },
  expired: { label: "منقضی", variant: "danger" },
  on_hold: { label: "در انتظار", variant: "warning" },
};

function UserStatus({ status }: { status: unknown }) {
  const s = USER_STATUS[String(status)] ?? { label: String(status || "—"), variant: "default" as const };
  return <Badge variant={s.variant}>{s.label}</Badge>;
}

function AdminUsers({ username }: { username: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["pg-admin-users", username],
    queryFn: () => api.pgAdminUsers(username),
    refetchInterval: 20000,
  });

  if (isLoading) return <Skeleton className="h-40" />;
  if (!data?.ok) return <div className="py-4 text-sm text-rose-300">خطا: {data?.error || "دریافت اطلاعات ناموفق بود"}</div>;

  const users = data.users || [];
  return (
    <div className="space-y-3">
      <div className="text-xs text-muted-foreground">
        مجموع اکانت‌های ساخته‌شده توسط این نماینده: <b className="text-white">{data.total}</b>
      </div>
      <Table>
        <THead>
          <TR>
            <TH>یوزرنیم</TH>
            <TH>وضعیت</TH>
            <TH>مصرف</TH>
            <TH>حجم</TH>
            <TH>انقضا</TH>
            <TH>آخرین اتصال</TH>
            <TH>لینک</TH>
          </TR>
        </THead>
        <TBody>
          {users.map((u: Row) => (
            <TR key={String(u.username)}>
              <TD className="font-mono text-xs">{String(u.username)}</TD>
              <TD><UserStatus status={u.status} /></TD>
              <TD className="text-xs">{gbFromBytes(u.used_traffic)} گیگ</TD>
              <TD className="text-xs">{dataLimitLabel(u.data_limit)}</TD>
              <TD className="text-xs text-muted-foreground">{fmtIso(u.expire)}</TD>
              <TD className="text-xs text-muted-foreground">{fmtIso(u.online_at)}</TD>
              <TD>{u.subscription_url ? <CopyButton value={String(u.subscription_url)} title="کپی لینک اشتراک" /> : "—"}</TD>
            </TR>
          ))}
          {users.length === 0 && (
            <TR>
              <TD colSpan={7} className="py-6 text-center text-muted-foreground">این نماینده هنوز اکانتی نساخته است.</TD>
            </TR>
          )}
        </TBody>
      </Table>
    </div>
  );
}

export function PasarGuardMonitor() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [selected, setSelected] = React.useState<string | null>(null);

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["pg-admins"],
    queryFn: () => api.pgAdmins(),
    refetchInterval: 30000,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["pg-admins"] });
    qc.invalidateQueries({ queryKey: ["pg-admin-users"] });
  };
  const setStatus = useMutation({
    mutationFn: (p: { username: string; status: "active" | "disabled" }) => api.pgSetAdminStatus(p.username, p.status),
    onSuccess: () => { invalidate(); toast({ title: "وضعیت نماینده بروزرسانی شد", variant: "success" }); },
    onError: (e: Error) => toast({ title: "خطا", description: e.message, variant: "error" }),
  });
  const del = useMutation({
    mutationFn: (username: string) => api.pgDeleteAdmin(username),
    onSuccess: (_d, username) => {
      invalidate();
      if (selected === username) setSelected(null);
      toast({ title: "اکانت ادمین حذف شد", variant: "success" });
    },
    onError: (e: Error) => toast({ title: "خطا در حذف", description: e.message, variant: "error" }),
  });

  const admins = data?.admins || [];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck className="h-5 w-5 text-brand" />
              مانیتورینگ ادمین‌های پاسارگارد {data?.ok ? `(${admins.length})` : ""}
            </CardTitle>
            <Button size="sm" variant="ghost" onClick={() => refetch()} disabled={isFetching}>
              <RefreshCw className={isFetching ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> بروزرسانی
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-2">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-12" />)}</div>
          ) : !data?.ok ? (
            <div className="rounded-xl border border-rose-400/30 bg-rose-500/5 p-4 text-sm text-rose-200">
              {data?.error || "اتصال به پنل پاسارگارد ناموفق بود."}
            </div>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>یوزرنیم ادمین</TH>
                  <TH>نقش</TH>
                  <TH>وضعیت</TH>
                  <TH>تعداد اکانت</TH>
                  <TH>مصرف کل</TH>
                  <TH>سقف حجم</TH>
                  <TH>اقدام</TH>
                </TR>
              </THead>
              <TBody>
                {admins.map((a: Row) => {
                  const username = String(a.username);
                  const isActive = String(a.status) === "active";
                  return (
                    <TR key={username} className={selected === username ? "bg-brand/[0.06]" : undefined}>
                      <TD className="font-mono text-xs font-bold text-white">{username}</TD>
                      <TD className="text-xs text-muted-foreground">{String(a.role_name || "—")}</TD>
                      <TD><Badge variant={isActive ? "success" : "default"}>{isActive ? "فعال" : "غیرفعال"}</Badge></TD>
                      <TD className="text-xs"><span className="inline-flex items-center gap-1"><Users className="h-3.5 w-3.5" />{Number(a.total_users || 0)}</span></TD>
                      <TD className="text-xs">{gbFromBytes(a.used_traffic)} گیگ</TD>
                      <TD className="text-xs">{dataLimitLabel(a.data_limit)}</TD>
                      <TD>
                        <div className="flex flex-wrap gap-1.5">
                          <Button size="sm" variant={selected === username ? "default" : "subtle"} onClick={() => setSelected(selected === username ? null : username)}>
                            <Eye className="h-4 w-4" /> اکانت‌ها
                          </Button>
                          <Button size="sm" variant="ghost" disabled={setStatus.isPending} onClick={() => setStatus.mutate({ username, status: isActive ? "disabled" : "active" })}>
                            <Power className="h-4 w-4" /> {isActive ? "غیرفعال" : "فعال"}
                          </Button>
                          <ConfirmDialog
                            trigger={<Button size="sm" variant="destructive"><Trash2 className="h-4 w-4" /></Button>}
                            title={`حذف ادمین «${username}»؟`}
                            description="اکانت ادمین از پنل پاسارگارد حذف می‌شود. اکانت‌های کاربرانی که ساخته دست‌نخورده می‌مانند."
                            confirmLabel="حذف"
                            destructive
                            onConfirm={() => del.mutate(username)}
                          />
                        </div>
                      </TD>
                    </TR>
                  );
                })}
                {admins.length === 0 && (
                  <TR><TD colSpan={7} className="py-8 text-center text-muted-foreground">هنوز هیچ اکانت ادمینی برای نماینده‌ها نساخته‌اید.</TD></TR>
                )}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {selected && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm">
              <Users className="h-4 w-4 text-brand" /> اکانت‌های ساخته‌شده توسط <span className="font-mono text-brand">{selected}</span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <AdminUsers username={selected} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
