import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LockOpen, Lock, Save, Plus, Trash2, Settings2 } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type Audience = "all" | "user" | "agent";

const RUNTIME_FIELDS: { key: string; label: string; hint?: string }[] = [
  { key: "price_per_gb", label: "قیمت هر گیگ (تومان)" },
  { key: "minimum_purchase_gb", label: "حداقل خرید (گیگ)" },
  { key: "crypto_address", label: "آدرس تتر" },
  { key: "support_id", label: "آیدی پشتیبانی", hint: "با @ ، مثل @YourSupport" },
  { key: "admin_user_ids", label: "ادمین‌ها", hint: "آیدی‌ها با کاما" },
  { key: "default_agent_credit_limit_toman", label: "سقف اعتبار پیش‌فرض نماینده" },
  { key: "default_agent_price_per_gb", label: "قیمت پیش‌فرض نماینده" },
];
const PANEL_FIELDS: { key: string; label: string; type?: string }[] = [
  { key: "panel_base_url", label: "آدرس پنل 3x-ui" },
  { key: "panel_username", label: "یوزرنیم پنل" },
  { key: "panel_password", label: "پسورد پنل", type: "password" },
  { key: "panel_inbound_id", label: "Inbound ID" },
  { key: "sub_link_base", label: "آدرس پایه لینک اشتراک" },
];

function SalesRow({ title, audience, status, onToggle, busy }: {
  title: string; audience: Audience; status: string;
  onToggle: (a: Audience, s: "open" | "closed") => void; busy: boolean;
}) {
  const open = status !== "closed";
  return (
    <div className="flex items-center justify-between rounded-xl border border-border bg-white/[0.02] p-4">
      <div className="flex items-center gap-3">
        <span className="font-bold text-white">{title}</span>
        <Badge variant={open ? "success" : "danger"}>{open ? "باز" : "بسته"}</Badge>
      </div>
      <div className="flex gap-2">
        <Button size="sm" disabled={busy || open} onClick={() => onToggle(audience, "open")}><LockOpen className="h-4 w-4" /> باز</Button>
        <Button size="sm" variant="destructive" disabled={busy || !open} onClick={() => onToggle(audience, "closed")}><Lock className="h-4 w-4" /> بستن</Button>
      </div>
    </div>
  );
}

function PasarGuardCard({ items }: { items: Record<string, string> }) {
  const qc = useQueryClient();
  const [pg, setPg] = React.useState(() => ({
    enabled: (items.pg_enabled ?? "0") === "1",
    label: items.pg_label || "سرور اختصاصی",
    base_url: items.pg_base_url ?? "",
    username: items.pg_username ?? "",
    password: "",
    group: items.pg_group || "Tsco-Bot",
    verify_tls: (items.pg_verify_tls ?? "1") !== "0",
    price_per_gb: items.pg_price_per_gb ?? "0",
    default_days: items.pg_default_days ?? "30",
  }));
  type TestReport = { ok: boolean; error?: string; admin_username?: string; is_owner?: boolean; panel_version?: string; groups?: { id: number; name: string }[] };
  const [test, setTest] = React.useState<TestReport | null>(null);
  const upd = (k: string, v: string | boolean) => setPg((s) => ({ ...s, [k]: v }));
  const save = useMutation({
    mutationFn: () => api.setPasarGuard({ ...pg, price_per_gb: Number(pg.price_per_gb) || 0, default_days: Number(pg.default_days) || 0 }),
    onSuccess: () => { setPg((s) => ({ ...s, password: "" })); qc.invalidateQueries({ queryKey: ["settings"] }); },
  });
  const testConn = useMutation({
    mutationFn: () => api.testPasarGuard({ base_url: pg.base_url, username: pg.username, password: pg.password || undefined, verify_tls: pg.verify_tls }),
    onSuccess: (r) => setTest(r),
    onError: (e) => setTest({ ok: false, error: String(e) }),
  });
  return (
    <Card className="border-brand/20">
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Settings2 className="h-5 w-5 text-brand" /> اتصال پنل PasarGuard</CardTitle>
        <p className="text-sm text-muted-foreground">
          اطلاعات اتصال ربات به پنل PasarGuard را وارد کنید. توصیه می‌شود یک حساب ادمینِ اختصاصی برای ربات در پنل بسازید و از همان استفاده کنید. سرویس‌های این پنل بر اساس تعرفه‌ی گیگیِ شایان فروخته می‌شوند. برای حفظ رمز فعلی، فیلد رمز عبور را خالی بگذارید.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-white/[0.02] p-4">
          <div className="flex items-center gap-3">
            <span className="font-bold text-white">وضعیت پنل PasarGuard</span>
            <Badge variant={pg.enabled ? "success" : "danger"}>{pg.enabled ? "فعال" : "غیرفعال"}</Badge>
          </div>
          <div className="flex gap-2">
            <Button size="sm" disabled={pg.enabled} onClick={() => upd("enabled", true)}><LockOpen className="h-4 w-4" /> فعال</Button>
            <Button size="sm" variant="destructive" disabled={!pg.enabled} onClick={() => upd("enabled", false)}><Lock className="h-4 w-4" /> غیرفعال</Button>
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="نام دکمه‌ی خرید (در ربات)"><Input value={pg.label} onChange={(e) => upd("label", e.target.value)} placeholder="سرور اختصاصی" /></Field>
          <Field label="گروه (group) پنل" hint="کاربر در این گروه ساخته می‌شود"><Input value={pg.group} onChange={(e) => upd("group", e.target.value)} placeholder="Tsco-Bot" /></Field>
          <Field label="آدرس پنل (با https و پورت)"><Input value={pg.base_url} onChange={(e) => upd("base_url", e.target.value)} placeholder="https://panel.example:8000" /></Field>
          <Field label="یوزرنیم ادمینِ ربات"><Input value={pg.username} onChange={(e) => upd("username", e.target.value)} /></Field>
          <Field label="پسورد ادمینِ ربات"><Input type="password" value={pg.password} onChange={(e) => upd("password", e.target.value)} placeholder="بدون تغییر" /></Field>
          <Field label="قیمت هر گیگ (تومان)" hint="۰ = همان نرخ گیگیِ شایان"><Input value={pg.price_per_gb} inputMode="numeric" onChange={(e) => upd("price_per_gb", e.target.value)} /></Field>
          <Field label="مدت اعتبار پیش‌فرض (روز)" hint="۰ = بدون انقضا"><Input value={pg.default_days} inputMode="numeric" onChange={(e) => upd("default_days", e.target.value)} /></Field>
          <div className="flex items-center gap-2 pt-6">
            <input id="pg_verify" type="checkbox" checked={pg.verify_tls} onChange={(e) => upd("verify_tls", e.target.checked)} className="h-4 w-4 accent-[hsl(var(--brand))]" />
            <label htmlFor="pg_verify" className="text-sm text-muted-foreground">بررسی گواهی TLS (اگر cert معتبر داری روشن بماند)</label>
          </div>
        </div>
        {test && (
          <div className={`rounded-xl border p-3 text-sm ${test.ok ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-100" : "border-rose-400/30 bg-rose-400/10 text-rose-100"}`}>
            {test.ok ? (
              <span>
                ✅ اتصال موفق — ادمین: <b>{test.admin_username}</b>{test.panel_version ? ` · نسخه: ${test.panel_version}` : ""}
                {test.groups && test.groups.length ? ` · گروه‌ها: ${test.groups.map((g) => g.name).join(", ")}` : ""}
              </span>
            ) : (
              <span>❌ اتصال ناموفق: {test.error}</span>
            )}
          </div>
        )}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Button variant="outline" size="sm" disabled={testConn.isPending} onClick={() => testConn.mutate()}>
            {testConn.isPending ? "در حال تست…" : "تست اتصال"}
          </Button>
          <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>
            <Save className="h-4 w-4" /> {save.isPending ? "در حال ذخیره…" : save.isSuccess ? "ذخیره شد ✓" : "ذخیره PasarGuard"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function BackupCard({ items }: { items: Record<string, string> }) {
  const qc = useQueryClient();
  const [f, setF] = React.useState({
    enabled: (items.backup_enabled ?? "1") === "1",
    bot: (items.backup_include_bot ?? "1") === "1",
    xui: (items.backup_include_xui ?? "1") === "1",
    pg: (items.backup_include_pg ?? "0") === "1",
    interval_value: items.backup_interval_value || "20",
    interval_unit: items.backup_interval_unit || "minutes",
    chat_id: items.backup_telegram_chat_id ?? "",
  });
  const set = (k: keyof typeof f, v: unknown) => setF((s) => ({ ...s, [k]: v }));
  const save = useMutation({
    mutationFn: () =>
      api.updateSettings({
        backup_enabled: f.enabled ? "on" : "off",
        backup_include_bot: f.bot ? "on" : "off",
        backup_include_xui: f.xui ? "on" : "off",
        backup_include_pg: f.pg ? "on" : "off",
        backup_interval_value: String(Math.max(1, Number(f.interval_value) || 1)),
        backup_interval_unit: f.interval_unit,
        backup_telegram_chat_id: f.chat_id.trim(),
        backup_xui_timeout_seconds: items.backup_xui_timeout_seconds ?? "180",
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
  const Check = ({ k, label, hint }: { k: keyof typeof f; label: string; hint?: string }) => (
    <label className="flex items-start gap-2 rounded-xl border border-border bg-white/[0.02] p-3 text-sm">
      <input type="checkbox" checked={Boolean(f[k])} onChange={(e) => set(k, e.target.checked)} className="mt-0.5 h-4 w-4 accent-[hsl(var(--brand))]" />
      <span><span className="font-bold text-white">{label}</span>{hint && <span className="block text-[11px] text-muted-foreground">{hint}</span>}</span>
    </label>
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Settings2 className="h-5 w-5 text-muted-foreground" /> بکاپ‌گیری خودکار</CardTitle>
        <p className="text-sm text-muted-foreground">بکاپِ زمان‌بندی‌شده به‌صورت خودکار ساخته و به تلگرام ارسال می‌شود. منابعی که باید در بکاپ باشند را انتخاب کنید.</p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-white/[0.02] p-4">
          <div className="flex items-center gap-3">
            <span className="font-bold text-white">بکاپ خودکار</span>
            <Badge variant={f.enabled ? "success" : "danger"}>{f.enabled ? "فعال" : "غیرفعال"}</Badge>
          </div>
          <div className="flex gap-2">
            <Button size="sm" disabled={f.enabled} onClick={() => set("enabled", true)}><LockOpen className="h-4 w-4" /> فعال</Button>
            <Button size="sm" variant="destructive" disabled={!f.enabled} onClick={() => set("enabled", false)}><Lock className="h-4 w-4" /> غیرفعال</Button>
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <Check k="bot" label="دیتابیس ربات" hint="کاربران، سفارش‌ها، کیف پول" />
          <Check k="xui" label="پنل x-ui" hint="دیتابیس/اینباندهای 3x-ui" />
          <Check k="pg" label="پنل PasarGuard" hint="کاربران، ادمین‌ها و گروه‌ها (از API)" />
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="فاصله‌ی بکاپ"><Input value={f.interval_value} inputMode="numeric" onChange={(e) => set("interval_value", e.target.value)} /></Field>
          <Field label="واحد">
            <select
              value={f.interval_unit}
              onChange={(e) => set("interval_unit", e.target.value)}
              className="h-10 w-full rounded-xl border border-input bg-card px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <option value="minutes">دقیقه</option>
              <option value="hours">ساعت</option>
              <option value="days">روز</option>
              <option value="weeks">هفته</option>
            </select>
          </Field>
          <Field label="آیدی چت تلگرام (مقصد بکاپ)"><Input value={f.chat_id} onChange={(e) => set("chat_id", e.target.value)} dir="ltr" placeholder="-100..." /></Field>
        </div>

        <div className="flex justify-end">
          <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>
            <Save className="h-4 w-4" /> {save.isPending ? "در حال ذخیره…" : save.isSuccess ? "ذخیره شد ✓" : "ذخیره تنظیمات بکاپ"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export function Settings() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["settings"], queryFn: () => api.settings() });
  const items = data?.items ?? {};
  const master = items.sales_status ?? "open";

  const [form, setForm] = React.useState<Record<string, string>>({});
  const [cards, setCards] = React.useState<{ number: string; name: string }[]>([]);
  const [inf, setInf] = React.useState({ enabled: false, cap_gb: "100", duration_days: "30", price: "0" });
  const [tiers, setTiers] = React.useState<{ min_gb: string; price_per_gb: string }[]>([]);
  const inited = React.useRef(false);
  React.useEffect(() => {
    if (data && !inited.current) {
      const f: Record<string, string> = {};
      [...RUNTIME_FIELDS, ...PANEL_FIELDS].forEach(({ key }) => (f[key] = items[key] ?? ""));
      setForm(f);
      try {
        const parsed = JSON.parse(items.payment_cards || "[]");
        setCards(Array.isArray(parsed) && parsed.length ? parsed.map((c) => ({ number: String(c.number || ""), name: String(c.name || "") })) : []);
      } catch {
        setCards([]);
      }
      setInf({
        enabled: (items.infinite_enabled ?? "0") === "1",
        cap_gb: items.infinite_cap_gb ?? "100",
        duration_days: items.infinite_duration_days ?? "30",
        price: items.infinite_price ?? "0",
      });
      try {
        const pt = JSON.parse(items.price_tiers || "[]");
        setTiers(Array.isArray(pt) ? pt.map((t) => ({ min_gb: String(t.min_gb ?? ""), price_per_gb: String(t.price_per_gb ?? "") })) : []);
      } catch {
        setTiers([]);
      }
      inited.current = true;
    }
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  const saveCards = useMutation({ mutationFn: () => api.setPaymentCards(cards.filter((c) => c.number.trim())), onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }) });
  const save = useMutation({ mutationFn: () => api.updateSettings(form), onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }) });
  const saveInf = useMutation({
    mutationFn: () => api.setInfinite({ enabled: inf.enabled, cap_gb: Number(inf.cap_gb) || 0, duration_days: Number(inf.duration_days) || 0, price: Number(inf.price) || 0 }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
  const saveTiers = useMutation({
    mutationFn: () => api.setPriceTiers(tiers.filter((t) => t.min_gb !== "" && t.price_per_gb !== "").map((t) => ({ min_gb: Number(t.min_gb) || 0, price_per_gb: Number(t.price_per_gb) || 0 }))),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
  const toggle = useMutation({ mutationFn: ({ a, s }: { a: Audience; s: "open" | "closed" }) => api.setSales(a, s), onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }) });
  const setBackend = useMutation({ mutationFn: (b: "xui" | "pasarguard") => api.setPrimaryBackend(b), onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }) });
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  if (isLoading || !data) return <div className="space-y-5"><Skeleton className="h-12 w-72" /><Skeleton className="h-72" /></div>;

  const TABS = [
    { v: "general", label: "عمومی" },
    { v: "sales", label: "فروش و تعرفه" },
    { v: "payment", label: "پرداخت" },
    { v: "panel", label: "پنل‌ها" },
  ];

  return (
    <Tabs defaultValue="general" className="space-y-6">
      <div className="sticky top-16 z-20 -mx-1 overflow-x-auto pb-1">
        <TabsList className="w-full justify-start">
          {TABS.map((t) => (
            <TabsTrigger key={t.v} value={t.v} className="px-4 py-2 text-sm">{t.label}</TabsTrigger>
          ))}
        </TabsList>
      </div>

      {/* ───────────── General ───────────── */}
      <TabsContent value="general" className="space-y-6">
        <Card>
          <CardHeader><CardTitle>تنظیمات فروشگاه</CardTitle></CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            {RUNTIME_FIELDS.map(({ key, label, hint }) => (
              <Field key={key} label={label} hint={hint}>
                <Input value={form[key] ?? ""} onChange={(e) => set(key, e.target.value)} />
              </Field>
            ))}
          </CardContent>
        </Card>
        <BackupCard items={items} />
      </TabsContent>

      {/* ───────────── Sales + Pricing ───────────── */}
      <TabsContent value="sales" className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>کنترل فروش</CardTitle>
            <p className="text-sm text-muted-foreground">فروش را برای کاربران عادی و نماینده‌ها جداگانه باز/بسته کنید.</p>
          </CardHeader>
          <CardContent className="space-y-3">
            <SalesRow title="کاربران عادی" audience="user" status={items.sales_status_user ?? master} onToggle={(a, s) => toggle.mutate({ a, s })} busy={toggle.isPending} />
            <SalesRow title="نماینده‌ها" audience="agent" status={items.sales_status_agent ?? master} onToggle={(a, s) => toggle.mutate({ a, s })} busy={toggle.isPending} />
            <div className="flex justify-end gap-2 pt-1">
              <Button size="sm" variant="subtle" disabled={toggle.isPending} onClick={() => toggle.mutate({ a: "all", s: "open" })}>باز کردن همه</Button>
              <Button size="sm" variant="subtle" disabled={toggle.isPending} onClick={() => toggle.mutate({ a: "all", s: "closed" })}>بستن همه</Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>تعرفه پلکانی (بر اساس حجم)</CardTitle>
            <p className="text-sm text-muted-foreground">قیمت هر گیگ را بر اساس بازه‌ی حجم خرید تعیین کنید؛ بازه تا شروع ردیف بعدی ادامه می‌یابد. خالی گذاشتن یعنی استفاده از «قیمت هر گیگ» ثابت.</p>
          </CardHeader>
          <CardContent className="space-y-3">
            {tiers.length === 0 && <p className="text-sm text-muted-foreground">هیچ بازه‌ای تعریف نشده — قیمت ثابت اعمال می‌شود.</p>}
            {tiers.map((t, i) => (
              <div key={i} className="flex flex-col gap-2 rounded-xl border border-border bg-white/[0.02] p-3 sm:flex-row sm:items-end">
                <div className="flex-1">
                  <Field label={`از این حجم به بالا (گیگ) — ردیف ${i + 1}`}>
                    <Input value={t.min_gb} inputMode="numeric" placeholder="مثلاً 5" onChange={(e) => setTiers((xs) => xs.map((x, j) => (j === i ? { ...x, min_gb: e.target.value } : x)))} />
                  </Field>
                </div>
                <div className="flex-1">
                  <Field label="قیمت هر گیگ (تومان)">
                    <Input value={t.price_per_gb} inputMode="numeric" placeholder="مثلاً 30000" onChange={(e) => setTiers((xs) => xs.map((x, j) => (j === i ? { ...x, price_per_gb: e.target.value } : x)))} />
                  </Field>
                </div>
                <Button variant="destructive" size="icon" onClick={() => setTiers((xs) => xs.filter((_, j) => j !== i))}><Trash2 className="h-4 w-4" /></Button>
              </div>
            ))}
            <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
              <Button variant="outline" size="sm" disabled={tiers.length >= 12} onClick={() => setTiers((xs) => [...xs, { min_gb: "", price_per_gb: "" }])}><Plus className="h-4 w-4" /> افزودن بازه</Button>
              <Button size="sm" disabled={saveTiers.isPending} onClick={() => saveTiers.mutate()}>{saveTiers.isPending ? "در حال ذخیره…" : saveTiers.isSuccess ? "ذخیره شد ✓" : "ذخیره تعرفه پلکانی"}</Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>بسته‌ی بی‌نهایت (مصرف منصفانه)</CardTitle>
            <p className="text-sm text-muted-foreground">بسته‌ای با حجم بالا و قیمت سفارشی؛ پس از رسیدن به سقف، کانفیگ خودکار در پنل غیرفعال می‌شود.</p>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between rounded-xl border border-border bg-white/[0.02] p-4">
              <div className="flex items-center gap-3">
                <span className="font-bold text-white">وضعیت بسته</span>
                <Badge variant={inf.enabled ? "success" : "danger"}>{inf.enabled ? "فعال" : "غیرفعال"}</Badge>
              </div>
              <div className="flex gap-2">
                <Button size="sm" disabled={inf.enabled} onClick={() => setInf((s) => ({ ...s, enabled: true }))}><LockOpen className="h-4 w-4" /> فعال</Button>
                <Button size="sm" variant="destructive" disabled={!inf.enabled} onClick={() => setInf((s) => ({ ...s, enabled: false }))}><Lock className="h-4 w-4" /> غیرفعال</Button>
              </div>
            </div>
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="سقف مصرف منصفانه (گیگ)" hint="بعد از این حجم، کانفیگ غیرفعال می‌شود">
                <Input value={inf.cap_gb} inputMode="numeric" onChange={(e) => setInf((s) => ({ ...s, cap_gb: e.target.value }))} />
              </Field>
              <Field label="مدت اعتبار (روز)"><Input value={inf.duration_days} inputMode="numeric" onChange={(e) => setInf((s) => ({ ...s, duration_days: e.target.value }))} /></Field>
              <Field label="قیمت سفارشی (تومان)"><Input value={inf.price} inputMode="numeric" onChange={(e) => setInf((s) => ({ ...s, price: e.target.value }))} /></Field>
            </div>
            <div className="flex justify-end">
              <Button size="sm" disabled={saveInf.isPending} onClick={() => saveInf.mutate()}>{saveInf.isPending ? "در حال ذخیره…" : saveInf.isSuccess ? "ذخیره شد ✓" : "ذخیره بسته‌ی بی‌نهایت"}</Button>
            </div>
          </CardContent>
        </Card>
      </TabsContent>

      {/* ───────────── Payment ───────────── */}
      <TabsContent value="payment" className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>کارت‌های پرداخت (چرخشی)</CardTitle>
            <p className="text-sm text-muted-foreground">تا ۸ کارت اضافه کنید؛ ربات برای هر واریز به‌ترتیب چرخشی یکی را نشان می‌دهد و بار روی کارت‌ها پخش می‌شود.</p>
          </CardHeader>
          <CardContent className="space-y-3">
            {cards.length === 0 && <p className="text-sm text-muted-foreground">هیچ کارتی اضافه نشده — از دکمه‌ی پایین اضافه کنید.</p>}
            {cards.map((c, i) => (
              <div key={i} className="flex flex-col gap-2 rounded-xl border border-border bg-white/[0.02] p-3 sm:flex-row sm:items-end">
                <div className="flex-1"><Field label={`شماره کارت ${i + 1}`}><Input value={c.number} inputMode="numeric" placeholder="6037-xxxx-xxxx-xxxx" onChange={(e) => setCards((xs) => xs.map((x, j) => (j === i ? { ...x, number: e.target.value } : x)))} /></Field></div>
                <div className="flex-1"><Field label="به نام"><Input value={c.name} placeholder="نام صاحب کارت" onChange={(e) => setCards((xs) => xs.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} /></Field></div>
                <Button variant="destructive" size="icon" onClick={() => setCards((xs) => xs.filter((_, j) => j !== i))}><Trash2 className="h-4 w-4" /></Button>
              </div>
            ))}
            <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
              <Button variant="outline" size="sm" disabled={cards.length >= 8} onClick={() => setCards((xs) => [...xs, { number: "", name: "" }])}><Plus className="h-4 w-4" /> افزودن کارت</Button>
              <Button size="sm" disabled={saveCards.isPending} onClick={() => saveCards.mutate()}>{saveCards.isPending ? "در حال ذخیره…" : saveCards.isSuccess ? "ذخیره شد ✓" : "ذخیره کارت‌ها"}</Button>
            </div>
          </CardContent>
        </Card>
      </TabsContent>

      {/* ───────────── Panels (per-type sub-tabs) ───────────── */}
      <TabsContent value="panel" className="space-y-5">
        <div>
          <h2 className="text-lg font-black text-white">مدیریت پنل‌ها</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            هر نوع پنل را در تبِ مربوط به خودش پیکربندی و به ربات وصل کنید. ربات از هر پنلِ فعال به‌عنوان یک سرورِ قابل‌فروش استفاده می‌کند.
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>پنلِ اصلیِ فروش</CardTitle>
            <p className="text-sm text-muted-foreground">
              دکمه‌ی اصلیِ «خرید سرویس» در ربات از کدام پنل بفروشد. می‌توانی هر زمان آن را از 3x-ui به PasarGuard منتقل کنی؛ بدون قطعی و بدون از دست رفتن سرویس‌های قبلی.
            </p>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 sm:grid-cols-2">
              {(["xui", "pasarguard"] as const).map((b) => {
                const active = (items.primary_backend ?? "xui") === b;
                const title = b === "xui" ? "پنل 3x-ui" : "پنل PasarGuard";
                return (
                  <button
                    key={b}
                    onClick={() => setBackend.mutate(b)}
                    disabled={setBackend.isPending}
                    className={`card-hover rounded-2xl border p-4 text-right transition ${active ? "border-brand/40 bg-brand/[0.06]" : "border-border hover:border-white/20"}`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-bold text-white">{title}</span>
                      {active && <Badge variant="success">پنل اصلی</Badge>}
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {b === "pasarguard" ? "فروش از طریق PasarGuard (باید پایین فعال و وصل باشد)" : "فروش از طریق پنل 3x-ui"}
                    </p>
                  </button>
                );
              })}
            </div>
          </CardContent>
        </Card>
        <Tabs defaultValue="xui" className="space-y-5">
          <TabsList>
            <TabsTrigger value="xui" className="px-4 py-2 text-sm">3x-ui</TabsTrigger>
            <TabsTrigger value="pasarguard" className="px-4 py-2 text-sm">PasarGuard</TabsTrigger>
          </TabsList>

          <TabsContent value="xui" className="space-y-5">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2"><Settings2 className="h-5 w-5 text-muted-foreground" /> اتصال پنل 3x-ui</CardTitle>
                <p className="text-sm text-muted-foreground">اطلاعات اتصال ربات به پنل 3x-ui. برای حفظ رمز فعلی، فیلد رمز عبور را خالی بگذارید.</p>
              </CardHeader>
              <CardContent className="grid gap-4 sm:grid-cols-2">
                {PANEL_FIELDS.map(({ key, label, type }) => (
                  <Field key={key} label={label}>
                    <Input type={type || "text"} value={form[key] ?? ""} onChange={(e) => set(key, e.target.value)} placeholder={type === "password" ? "بدون تغییر" : ""} />
                  </Field>
                ))}
                <div className="sm:col-span-2 flex justify-end">
                  <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>
                    <Save className="h-4 w-4" /> {save.isPending ? "در حال ذخیره…" : save.isSuccess ? "ذخیره شد ✓" : "ذخیره پنل 3x-ui"}
                  </Button>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="pasarguard" className="space-y-5">
            <PasarGuardCard items={items} />
          </TabsContent>
        </Tabs>
      </TabsContent>

      <div className="sticky bottom-4 flex justify-end">
        <Button size="lg" disabled={save.isPending} onClick={() => save.mutate()}>
          <Save className="h-4 w-4" /> {save.isPending ? "در حال ذخیره…" : save.isSuccess ? "ذخیره شد ✓" : "ذخیره تنظیمات فروشگاه و پنل"}
        </Button>
      </div>
    </Tabs>
  );
}
