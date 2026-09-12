import * as React from "react";
import { useMutation, useQuery, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import {
  AlertTriangle, Boxes, Eye, EyeOff, Globe, HardDrive, Plus, RefreshCw,
  Save, Search, Server, Trash2, Users2,
} from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Field } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { StatTile } from "@/components/ui/stat-tile";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Pager } from "@/components/ui/pager";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { UserLink } from "@/components/UserLink";
import { toman, jalaliDate } from "@/lib/utils";
import type { ResellerPackage, ResellerSettings, SoldPanel } from "@/lib/types";

const GB = 1024 ** 3;

/** Traffic the way the operator authors it and the buyer reads it. */
function trafficLabel(gigabytes: number): string {
  const total = Math.max(0, Math.floor(gigabytes || 0));
  if (total <= 0) return "بدون حجم";
  if (total >= 1024 && total % 1024 === 0) return `${total / 1024} ترابایت`;
  if (total >= 1024) return `${(total / 1024).toFixed(1)} ترابایت`;
  return `${total} گیگابایت`;
}

function bytesLabel(bytes: number): string {
  const total = Math.max(0, Number(bytes) || 0);
  if (total <= 0) return "۰";
  const gigabytes = total / GB;
  if (gigabytes >= 1024) return `${(gigabytes / 1024).toFixed(2)} ترابایت`;
  return `${gigabytes.toFixed(1)} گیگابایت`;
}

function NumberInput({ value, onChange, placeholder }: {
  value: number; onChange: (v: number) => void; placeholder?: string;
}) {
  return (
    <Input
      inputMode="numeric"
      value={value ? String(value) : ""}
      placeholder={placeholder}
      onChange={(e) => onChange(Number(e.target.value.replace(/[^0-9]/g, "")) || 0)}
    />
  );
}

function PackageRow({ pkg, problems, onChange, onRemove }: {
  pkg: ResellerPackage;
  problems: string[];
  onChange: (next: ResellerPackage) => void;
  onRemove: () => void;
}) {
  const set = (patch: Partial<ResellerPackage>) => onChange({ ...pkg, ...patch });
  return (
    <div className={`rounded-2xl border p-4 ${problems.length ? "border-amber-400/40 bg-amber-400/[0.03]" : "border-border bg-white/[0.02]"}`}>
      <div className="grid grid-cols-[1fr_auto] items-start gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <b className="text-white">{pkg.title || "بسته بدون نام"}</b>
            {!pkg.enabled && <Badge variant="muted">غیرفعال</Badge>}
            {problems.length > 0 && (
              <Badge variant="warning" className="gap-1">
                <AlertTriangle className="h-3 w-3" /> نیازمند اصلاح
              </Badge>
            )}
          </div>
          <div className="mt-1 text-[0.68rem] text-muted-foreground">
            دکمه‌ای که کاربر می‌بیند:{" "}
            <span className="text-foreground">
              📦 {trafficLabel(pkg.traffic_gb)} - {toman(pkg.price)} تومان
            </span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <Button size="icon" variant="ghost" title={pkg.enabled ? "غیرفعال کردن" : "فعال کردن"}
                  onClick={() => set({ enabled: !pkg.enabled })}>
            {pkg.enabled ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
          </Button>
          <Button size="icon" variant="ghost" title="حذف بسته" onClick={onRemove}
                  className="text-rose-300 hover:bg-rose-500/10 hover:text-rose-200">
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {problems.length > 0 && (
        <div className="mt-3 rounded-xl border border-amber-400/30 bg-amber-400/5 p-2 text-[0.68rem] leading-6 text-amber-200">
          {problems.map((p, i) => <div key={i}>• {p}</div>)}
        </div>
      )}

      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="نام بسته">
          <Input value={pkg.title} onChange={(e) => set({ title: e.target.value })}
                 placeholder="مثلاً: ۲۰ ترابایت" />
        </Field>
        <Field label="حجم (گیگابایت)" hint="۱۰۲۴ گیگ = ۱ ترابایت">
          <NumberInput value={pkg.traffic_gb} onChange={(v) => set({ traffic_gb: v })} placeholder="20480" />
        </Field>
        <Field label="قیمت (تومان)">
          <NumberInput value={pkg.price} onChange={(v) => set({ price: v })} placeholder="4200000" />
        </Field>
        <Field label="اعتبار (روز)" hint="۰ = بدون محدودیت زمانی">
          <NumberInput value={pkg.days} onChange={(v) => set({ days: v })} placeholder="۰" />
        </Field>
        <div className="sm:col-span-2 lg:col-span-4">
          <Field label="توضیح (اختیاری)" hint="زیر نام بسته در فاکتور نمایش داده می‌شود.">
            <Input value={pkg.note} onChange={(e) => set({ note: e.target.value })} />
          </Field>
        </div>
      </div>
    </div>
  );
}

function SoldPanels() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [q, setQ] = React.useState("");
  const [status, setStatus] = React.useState("all");
  const [page, setPage] = React.useState(1);
  const { data, isFetching } = useQuery({
    queryKey: ["reseller-panels", q, status, page],
    queryFn: () => api.resellerPanels(q, status, page),
    placeholderData: keepPreviousData,
  });

  const sync = useMutation({
    mutationFn: (panelId: string) => api.syncResellerPanel(panelId),
    onSuccess: (r) => {
      toast({
        title: r.ok ? "به‌روزرسانی شد" : "پیدا نشد",
        description: r.ok ? undefined : r.error,
        variant: r.ok ? "success" : "error",
      });
      qc.invalidateQueries({ queryKey: ["reseller-panels"] });
    },
    onError: (e: Error) => toast({ title: "ناموفق", description: e.message, variant: "error" }),
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <CardTitle className="text-sm">پنل‌های فروخته‌شده</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative w-full sm:w-56">
              <Search className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input className="pr-9" placeholder="یوزرنیم یا خریدار…" value={q}
                     onChange={(e) => { setQ(e.target.value); setPage(1); }} />
            </div>
            <Select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}
                    className="w-36">
              <option value="all">همه</option>
              <option value="active">فعال</option>
              <option value="expired">منقضی</option>
              <option value="disabled">غیرفعال</option>
            </Select>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {!data ? (
          <div className="space-y-2">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-10" />)}</div>
        ) : (
          <Table className={isFetching ? "opacity-60 transition-opacity" : ""}>
            <THead>
              <TR>
                <TH>خریدار</TH><TH>یوزرنیم پنل</TH><TH>بسته</TH>
                <TH>حجم</TH><TH>مصرف</TH><TH>مبلغ</TH><TH>تاریخ</TH><TH></TH>
              </TR>
            </THead>
            <TBody>
              {data.items.map((panel: SoldPanel) => {
                const used = Number(panel.used_bytes || 0);
                const total = Number(panel.traffic_bytes || 0);
                const share = total > 0 ? Math.round((used / total) * 100) : 0;
                return (
                  <TR key={panel.panel_id}>
                    <TD><UserLink userId={panel.user_id} name={panel.first_name} username={panel.username} /></TD>
                    <TD><code className="text-[0.7rem] text-white" dir="ltr">{panel.pg_username}</code></TD>
                    <TD className="text-xs">{panel.title || "—"}</TD>
                    <TD className="whitespace-nowrap text-xs"><bdi>{bytesLabel(total)}</bdi></TD>
                    <TD className="whitespace-nowrap text-xs">
                      <bdi>{bytesLabel(used)}</bdi>
                      <div className="mt-1 h-1 w-20 overflow-hidden rounded-full bg-white/5">
                        <div className={`h-full rounded-full ${share >= 90 ? "bg-rose-400/70" : "bg-primary/70"}`}
                             style={{ width: `${Math.min(100, share)}%` }} />
                      </div>
                    </TD>
                    <TD className="whitespace-nowrap"><bdi>{toman(panel.price_toman)}</bdi></TD>
                    <TD className="text-xs text-muted-foreground">{jalaliDate(panel.created_at)}</TD>
                    <TD>
                      <Button size="icon" variant="ghost" title="به‌روزرسانی مصرف از سرور"
                              disabled={sync.isPending}
                              onClick={() => sync.mutate(panel.panel_id)}>
                        <RefreshCw className="h-4 w-4" />
                      </Button>
                    </TD>
                  </TR>
                );
              })}
              {data.items.length === 0 && (
                <TR><TD colSpan={8} className="py-8 text-center text-muted-foreground">
                  هنوز پنلی فروخته نشده است.
                </TD></TR>
              )}
            </TBody>
          </Table>
        )}
        {data && <Pager page={page} hasMore={data.has_more} onPage={setPage} loading={isFetching} />}
      </CardContent>
    </Card>
  );
}

export function Reseller() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data, isLoading } = useQuery({ queryKey: ["reseller"], queryFn: () => api.reseller() });

  const [packages, setPackages] = React.useState<ResellerPackage[] | null>(null);
  const [settings, setSettings] = React.useState<ResellerSettings | null>(null);
  const loaded = React.useRef(false);

  React.useEffect(() => {
    if (data && !loaded.current) {
      setPackages(data.packages);
      setSettings(data.settings);
      loaded.current = true;
    }
  }, [data]);

  const savePackages = useMutation({
    mutationFn: () => api.saveResellerPackages(packages || []),
    onSuccess: (r) => {
      setPackages(r.packages);
      const bad = Object.keys(r.problems || {}).length;
      toast({
        title: bad ? `ذخیره شد — ${bad} بسته نیاز به اصلاح دارد` : "بسته‌ها ذخیره شد",
        variant: bad ? "info" : "success",
      });
      qc.invalidateQueries({ queryKey: ["reseller"] });
    },
    onError: (e: Error) => toast({ title: "ذخیره نشد", description: e.message, variant: "error" }),
  });

  const saveSettings = useMutation({
    mutationFn: () => api.saveResellerSettings(settings as unknown as Record<string, unknown>),
    onSuccess: () => {
      toast({ title: "تنظیمات ذخیره شد", variant: "success" });
      qc.invalidateQueries({ queryKey: ["reseller"] });
    },
    onError: (e: Error) => toast({ title: "ذخیره نشد", description: e.message, variant: "error" }),
  });

  if (isLoading || !data || !packages || !settings) {
    return <div className="space-y-4"><Skeleton className="h-24" /><Skeleton className="h-72" /></div>;
  }

  const setS = <K extends keyof ResellerSettings>(k: K, v: ResellerSettings[K]) =>
    setSettings((s) => (s ? { ...s, [k]: v } : s));
  const problems = data.problems || {};

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile icon={Server} label="پنل‌های فروخته‌شده" value={toman(data.overview.total)} />
        <StatTile icon={Users2} label="پنل فعال" value={toman(data.overview.active)} />
        <StatTile icon={HardDrive} label="حجم فروخته‌شده" value={bytesLabel(data.overview.sold_bytes)} />
        <StatTile icon={Boxes} label="درآمد" value={toman(data.overview.revenue)} sub="تومان" />
      </div>

      <Tabs defaultValue="packages" className="space-y-5">
        <TabsList>
          <TabsTrigger value="packages" className="px-4 py-2 text-sm">بسته‌های فروش</TabsTrigger>
          <TabsTrigger value="settings" className="px-4 py-2 text-sm">تنظیمات</TabsTrigger>
          <TabsTrigger value="sold" className="px-4 py-2 text-sm">پنل‌های فروخته‌شده</TabsTrigger>
        </TabsList>

        <TabsContent value="packages" className="space-y-4">
          <Card className="border-brand/20 bg-gradient-to-br from-brand/[0.07] to-transparent">
            <CardHeader>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <CardTitle className="flex items-center gap-2">
                    <Globe className="h-5 w-5 text-brand" /> بسته‌های پنل نمایندگی
                  </CardTitle>
                  <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
                    هر بسته یک حساب ادمین در پنل پاسارگارد می‌سازد با حجم اختصاصی خودش.
                    خریدار با همان مشخصات وارد می‌شود و کاربران خودش را می‌سازد.
                  </p>
                </div>
                <Button disabled={savePackages.isPending} onClick={() => savePackages.mutate()}>
                  <Save className="h-4 w-4" />
                  {savePackages.isPending ? "در حال ذخیره…" : "ذخیره بسته‌ها"}
                </Button>
              </div>
            </CardHeader>
          </Card>

          {packages.map((pkg, i) => (
            <PackageRow
              key={pkg.id || i}
              pkg={pkg}
              problems={problems[pkg.id] || []}
              onChange={(next) => setPackages((ps) => (ps || []).map((p, j) => (j === i ? next : p)))}
              onRemove={() => setPackages((ps) => (ps || []).filter((_, j) => j !== i))}
            />
          ))}

          <Button
            variant="outline"
            onClick={() => setPackages((ps) => [...(ps || []), {
              id: "", title: "", traffic_gb: 0, price: 0, days: 0,
              user_limit: 0, note: "", enabled: true, sort: (ps || []).length,
            }])}
          >
            <Plus className="h-4 w-4" /> افزودن بسته
          </Button>
        </TabsContent>

        <TabsContent value="settings" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">تنظیمات فروش پنل</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <label className="flex items-start gap-2 rounded-xl border border-border bg-white/[0.02] p-3 text-sm">
                <input type="checkbox" checked={settings.enabled}
                       onChange={(e) => setS("enabled", e.target.checked)}
                       className="mt-0.5 h-4 w-4 accent-[hsl(var(--brand))]" />
                <span>
                  <span className="font-bold text-white">فروش پنل نمایندگی فعال باشد</span>
                  <span className="block text-[11px] leading-5 text-muted-foreground">
                    با خاموش بودن، دکمه‌ی نمایندگی در ربات دقیقاً مثل قبل مستقیم به فرم درخواست می‌رود.
                  </span>
                </span>
              </label>
              <label className="flex items-start gap-2 rounded-xl border border-border bg-white/[0.02] p-3 text-sm">
                <input type="checkbox" checked={settings.topup_enabled}
                       onChange={(e) => setS("topup_enabled", e.target.checked)}
                       className="mt-0.5 h-4 w-4 accent-[hsl(var(--brand))]" />
                <span>
                  <span className="font-bold text-white">افزایش حجم پنل فعال باشد</span>
                  <span className="block text-[11px] leading-5 text-muted-foreground">
                    نماینده می‌تواند همان بسته‌ها را دوباره بخرد؛ حجم به پنل فعلی‌اش اضافه می‌شود.
                  </span>
                </span>
              </label>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="آدرس ورود پنل" hint="همان آدرسی که به خریدار داده می‌شود.">
                  <Input value={settings.login_url} dir="ltr"
                         placeholder="https://panel.example.com:8000/dashboard/"
                         onChange={(e) => setS("login_url", e.target.value)} />
                </Field>
                <Field label="نام نقش در پاسارگارد"
                       hint="اگر نباشد ساخته می‌شود؛ دسترسی فقط به کاربران خودِ نماینده.">
                  <Input value={settings.role_name} dir="ltr"
                         onChange={(e) => setS("role_name", e.target.value)} />
                </Field>
                <Field label="پیش‌وند یوزرنیم"
                       hint="یوزرنیم پنل به شکل «پیش‌وند_آیدی_کد» ساخته می‌شود.">
                  <Input value={settings.username_prefix} dir="ltr"
                         onChange={(e) => setS("username_prefix", e.target.value)} />
                </Field>
              </div>
              <div className="flex justify-end">
                <Button disabled={saveSettings.isPending} onClick={() => saveSettings.mutate()}>
                  <Save className="h-4 w-4" />
                  {saveSettings.isPending ? "در حال ذخیره…" : "ذخیره تنظیمات"}
                </Button>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="sold"><SoldPanels /></TabsContent>
      </Tabs>
    </div>
  );
}
