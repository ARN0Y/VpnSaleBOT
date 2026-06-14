import * as React from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Users, Eye, Power, Trash2, ShieldCheck, UserPlus, KeyRound, CheckCircle2, Activity } from "lucide-react";
import { api } from "@/lib/api";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { CopyButton } from "@/components/ui/copy-button";
import { useToast } from "@/components/ui/toast";
import { gbFromBytes } from "@/lib/utils";

type Row = Record<string, unknown>;

function dataLimitLabel(v: unknown): string {
  const n = Number(v || 0);
  return !v || n <= 0 ? "نامحدود" : `${gbFromBytes(n)} گیگابایت`;
}

// ───────────────────────── monitoring roster ─────────────────────────
function MonitorTab() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["pg-admins"],
    queryFn: () => api.pgAdmins(),
    refetchInterval: 30000,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["pg-admins"] });
  const setStatus = useMutation({
    mutationFn: (p: { username: string; status: "active" | "disabled" }) => api.pgSetAdminStatus(p.username, p.status),
    onSuccess: () => { invalidate(); toast({ title: "وضعیت حساب ادمین به‌روزرسانی شد", variant: "success" }); },
    onError: (e: Error) => toast({ title: "خطا", description: e.message, variant: "error" }),
  });
  const del = useMutation({
    mutationFn: (username: string) => api.pgDeleteAdmin(username),
    onSuccess: () => { invalidate(); toast({ title: "حساب ادمین حذف شد", variant: "success" }); },
    onError: (e: Error) => toast({ title: "خطا در حذف", description: e.message, variant: "error" }),
  });

  const admins = data?.admins || [];

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5 text-brand" /> حساب‌های ادمین نماینده {data?.ok ? `(${admins.length})` : ""}
          </CardTitle>
          <Button size="sm" variant="ghost" onClick={() => refetch()} disabled={isFetching}>
            <RefreshCw className={isFetching ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> به‌روزرسانی
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-12" />)}</div>
        ) : !data?.ok ? (
          <div className="rounded-xl border border-rose-400/30 bg-rose-500/5 p-4 text-sm text-rose-200">
            {data?.error || "ارتباط با پنل PasarGuard برقرار نشد."}
          </div>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>نام کاربری</TH>
                <TH>سطح دسترسی</TH>
                <TH>وضعیت</TH>
                <TH>تعداد حساب</TH>
                <TH>مصرف کل</TH>
                <TH>محدودیت حجم</TH>
                <TH>عملیات</TH>
              </TR>
            </THead>
            <TBody>
              {admins.map((a: Row) => {
                const username = String(a.username);
                const isActive = String(a.status) === "active";
                return (
                  <TR key={username}>
                    <TD className="font-mono text-xs font-bold text-white">{username}</TD>
                    <TD className="text-xs text-muted-foreground">{String(a.role_name || "—")}</TD>
                    <TD><Badge variant={isActive ? "success" : "default"}>{isActive ? "فعال" : "غیرفعال"}</Badge></TD>
                    <TD className="text-xs"><span className="inline-flex items-center gap-1"><Users className="h-3.5 w-3.5" />{Number(a.total_users || 0)}</span></TD>
                    <TD className="text-xs">{gbFromBytes(a.used_traffic)} گیگابایت</TD>
                    <TD className="text-xs">{dataLimitLabel(a.data_limit)}</TD>
                    <TD>
                      <div className="flex flex-wrap gap-1.5">
                        <Button size="sm" asChild>
                          <Link to={`/pasarguard/admin/${encodeURIComponent(username)}`}><Eye className="h-4 w-4" /> مشاهده حساب‌ها</Link>
                        </Button>
                        <Button size="sm" variant="ghost" disabled={setStatus.isPending} onClick={() => setStatus.mutate({ username, status: isActive ? "disabled" : "active" })}>
                          <Power className="h-4 w-4" /> {isActive ? "غیرفعال‌سازی" : "فعال‌سازی"}
                        </Button>
                        <ConfirmDialog
                          trigger={<Button size="sm" variant="destructive"><Trash2 className="h-4 w-4" /></Button>}
                          title={`حذف حساب ادمین «${username}»`}
                          description="حساب ادمین از پنل PasarGuard حذف می‌شود. حساب‌های کاربری ایجادشده توسط این نماینده دست‌نخورده باقی می‌مانند."
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
                <TR><TD colSpan={7} className="py-8 text-center text-muted-foreground">تاکنون حساب ادمینی برای نمایندگان ایجاد نشده است.</TD></TR>
              )}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

// ───────────────────────── create admin ─────────────────────────
function genPassword(): string {
  const U = "ABCDEFGHJKLMNPQRSTUVWXYZ";
  const L = "abcdefghijkmnpqrstuvwxyz";
  const D = "23456789";
  const S = "!@#$%*-_";
  const pick = (s: string) => s[Math.floor(Math.random() * s.length)];
  const out = [pick(U), pick(U), pick(U), pick(D), pick(D), pick(S), ...Array.from({ length: 7 }, () => pick(L + D))];
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out.join("");
}

function CredRow({ label, value, icon }: { label: string; value: string; icon?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-border bg-white/[0.02] px-3.5 py-2.5">
      <span className="flex items-center gap-2 text-xs text-muted-foreground">{icon && <KeyRound className="h-4 w-4" />} {label}</span>
      <span className="flex items-center gap-2"><span className="font-mono text-sm text-white" dir="ltr">{value}</span><CopyButton value={value} /></span>
    </div>
  );
}

function CreateTab() {
  const { toast } = useToast();
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState(genPassword());
  const [roleId, setRoleId] = React.useState("");
  const [dataLimit, setDataLimit] = React.useState("");
  const [note, setNote] = React.useState("");
  const [result, setResult] = React.useState<{ username: string; password: string; panel_url?: string } | null>(null);

  const rolesQ = useQuery({ queryKey: ["pg-roles"], queryFn: () => api.pgRoles() });
  const create = useMutation({
    mutationFn: () => api.pgCreateAdmin({
      username: username.trim(),
      password: password.trim() || undefined,
      role_id: roleId ? Number(roleId) : undefined,
      data_limit_gb: dataLimit ? Number(dataLimit) : undefined,
      note: note.trim() || undefined,
    }),
    onSuccess: (d) => {
      if (!d.ok) { toast({ title: "ایجاد حساب ناموفق بود", description: d.error, variant: "error" }); return; }
      setResult({ username: d.username!, password: d.password!, panel_url: d.panel_url });
      toast({ title: "حساب ادمین ایجاد شد", variant: "success" });
      setUsername("");
      setPassword(genPassword());
    },
    onError: (e: Error) => toast({ title: "خطا", description: e.message, variant: "error" }),
  });

  const validUser = username.trim().replace(/[^A-Za-z0-9_]/g, "").length >= 3;
  const validPass = password.trim().length >= 12 && (password.match(/[A-Z]/g)?.length ?? 0) >= 2;

  return (
    <div className="grid gap-4 lg:grid-cols-5">
      <Card className="lg:col-span-3">
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><UserPlus className="h-5 w-5 text-brand" /> صدور حساب ادمین</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>نام کاربری</Label>
              <Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="reseller_ali" dir="ltr" />
              <p className="text-[11px] text-muted-foreground">حروف لاتین، اعداد و زیرخط — حداقل ۳ نویسه.</p>
            </div>
            <div className="space-y-1.5">
              <Label>گذرواژه</Label>
              <div className="flex gap-2">
                <Input value={password} onChange={(e) => setPassword(e.target.value)} dir="ltr" />
                <Button type="button" variant="subtle" onClick={() => setPassword(genPassword())} title="تولید گذرواژه"><RefreshCw className="h-4 w-4" /></Button>
                <CopyButton value={password} />
              </div>
              <p className={validPass ? "text-[11px] text-emerald-300" : "text-[11px] text-amber-300"}>حداقل ۱۲ نویسه و دو حرف بزرگ.</p>
            </div>
            <div className="space-y-1.5">
              <Label>سطح دسترسی</Label>
              <Select value={roleId} onChange={(e) => setRoleId(e.target.value)}>
                <option value="">نقش پیش‌فرض نماینده</option>
                {(rolesQ.data?.roles || []).filter((r) => !r.is_owner).map((r) => (
                  <option key={r.id} value={r.id}>{r.name}</option>
                ))}
              </Select>
              <p className="text-[11px] text-muted-foreground">تعیین‌کننده‌ی دسترسی‌های حساب در پنل.</p>
            </div>
            <div className="space-y-1.5">
              <Label>محدودیت حجم (گیگابایت)</Label>
              <Input value={dataLimit} onChange={(e) => setDataLimit(e.target.value.replace(/[^0-9.]/g, ""))} placeholder="اختیاری — خالی = نامحدود" dir="ltr" />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <Label>توضیحات</Label>
              <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="اختیاری" />
            </div>
          </div>
          <Button onClick={() => create.mutate()} disabled={!validUser || !validPass || create.isPending}>
            <UserPlus className="h-4 w-4" /> {create.isPending ? "در حال ایجاد…" : "ایجاد حساب ادمین"}
          </Button>
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle className="text-sm">اطلاعات ورود</CardTitle>
        </CardHeader>
        <CardContent>
          {result ? (
            <div className="space-y-2.5">
              <div className="flex items-center gap-2 text-sm font-bold text-emerald-200"><CheckCircle2 className="h-4 w-4" /> حساب با موفقیت ایجاد شد</div>
              <CredRow label="آدرس پنل" value={result.panel_url || "—"} />
              <CredRow label="نام کاربری" value={result.username} />
              <CredRow label="گذرواژه" value={result.password} icon />
              <p className="text-[11px] text-amber-300">گذرواژه تنها یک‌بار نمایش داده می‌شود؛ آن را ذخیره کنید.</p>
            </div>
          ) : (
            <div className="grid h-full min-h-[12rem] place-items-center text-center text-xs text-muted-foreground">
              پس از ایجاد حساب، اطلاعات ورود در این بخش نمایش داده می‌شود.
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export function PasarGuardManage() {
  return (
    <Tabs defaultValue="monitor" className="space-y-4">
      <TabsList>
        <TabsTrigger value="monitor" className="flex items-center gap-1.5"><ShieldCheck className="h-4 w-4" /> مانیتورینگ</TabsTrigger>
        <TabsTrigger value="create" className="flex items-center gap-1.5"><UserPlus className="h-4 w-4" /> صدور حساب ادمین</TabsTrigger>
      </TabsList>
      <TabsContent value="monitor"><MonitorTab /></TabsContent>
      <TabsContent value="create"><CreateTab /></TabsContent>
    </Tabs>
  );
}
