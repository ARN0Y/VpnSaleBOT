import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LockOpen, Lock, Save, Plus, Trash2, Server, Wifi, WifiOff, Settings2 } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Field } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type Audience = "all" | "user" | "agent";
type Tri = "" | "true" | "false";

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
// action -> default label (must match async_storefront/handlers.py NAV_ACTIONS)
const NAV_LABEL_FIELDS: { action: string; label: string; def: string }[] = [
  { action: "buy", label: "خرید سرویس", def: "⚡ خرید سرویس پرسرعت" },
  { action: "renew", label: "تمدید", def: "🔄 تمدید سرویس" },
  { action: "subs", label: "سرویس‌های من", def: "📦 سرویس‌های من" },
  { action: "account", label: "حساب کاربری", def: "🪪 حساب کاربری" },
  { action: "wallet", label: "کیف پول", def: "💎 کیف پول من" },
  { action: "tariffs", label: "تعرفه‌ها", def: "🏷 تعرفه‌ها" },
  { action: "support", label: "پشتیبانی", def: "🛟 تماس با پشتیبانی" },
  { action: "test_config", label: "تست رایگان", def: "🆓 دریافت تست رایگان" },
  { action: "agent_request", label: "درخواست نمایندگی", def: "🤝 درخواست نمایندگی" },
  { action: "infinite", label: "بسته‌ی بی‌نهایت", def: "♾️ بسته‌ی بی‌نهایت" },
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

type Pkg = { kind: "volume" | "unlimited"; title: string; gb: string; days: string; price: string; agent_price: string };

function PackageEditor({ panel, initial }: { panel: "1" | "2"; initial: string }) {
  const qc = useQueryClient();
  const [pkgs, setPkgs] = React.useState<Pkg[]>(() => {
    try {
      const arr = JSON.parse(initial || "[]");
      return Array.isArray(arr)
        ? arr.map((p) => ({
            kind: p.kind === "unlimited" ? "unlimited" : "volume",
            title: String(p.title ?? ""),
            gb: String(p.gb ?? ""),
            days: String(p.days ?? ""),
            price: String(p.price ?? ""),
            agent_price: String(p.agent_price ?? ""),
          }))
        : [];
    } catch {
      return [];
    }
  });
  const save = useMutation({
    mutationFn: () =>
      api.setPanelPackages(
        panel,
        pkgs
          .filter((p) => p.title.trim() && Number(p.price) > 0)
          .map((p) => ({
            kind: p.kind,
            title: p.title.trim(),
            gb: Number(p.gb) || 0,
            days: Number(p.days) || 0,
            price: Number(p.price) || 0,
            agent_price: Number(p.agent_price) || 0,
          })),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
  const upd = (i: number, patch: Partial<Pkg>) => setPkgs((xs) => xs.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  return (
    <div className="space-y-3 rounded-xl border border-border bg-white/[0.02] p-4">
      <div className="text-sm font-bold text-white">بسته‌های این سرور</div>
      <p className="text-xs text-muted-foreground">
        وقتی حداقل یک بسته بسازید، ربات به‌جای خرید گیگی، همین بسته‌ها را نشان می‌دهد. در بسته‌ی «نامحدود»، «سقف مصرف منصفانه» مخفی است و کاربر فقط «نامحدود» می‌بیند. در بسته‌ی حجمی، نماینده با نرخ گیگی خودش حساب می‌شود.
      </p>
      {pkgs.length === 0 && <p className="text-xs text-muted-foreground">هنوز بسته‌ای ساخته نشده — خرید گیگی فعال است.</p>}
      {pkgs.map((p, i) => (
        <div key={i} className="space-y-2 rounded-lg border border-border bg-white/[0.02] p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex gap-1">
              <button
                onClick={() => upd(i, { kind: "volume" })}
                className={`rounded-md px-2.5 py-1 text-xs font-bold transition ${p.kind === "volume" ? "bg-brand/20 text-white" : "text-muted-foreground hover:text-white"}`}
              >
                حجمی
              </button>
              <button
                onClick={() => upd(i, { kind: "unlimited" })}
                className={`rounded-md px-2.5 py-1 text-xs font-bold transition ${p.kind === "unlimited" ? "bg-brand/20 text-white" : "text-muted-foreground hover:text-white"}`}
              >
                نامحدود
              </button>
            </div>
            <Button variant="destructive" size="icon" onClick={() => setPkgs((xs) => xs.filter((_, j) => j !== i))}>
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <Field label="عنوان بسته (همان که کاربر می‌بیند)">
              <Input value={p.title} placeholder="مثلاً ۵۰ گیگ ماهانه" onChange={(e) => upd(i, { title: e.target.value })} />
            </Field>
            <Field label={p.kind === "unlimited" ? "سقف مصرف منصفانه (گیگ) — مخفی، ۰=بی‌نهایت واقعی" : "حجم (گیگ)"}>
              <Input value={p.gb} inputMode="numeric" onChange={(e) => upd(i, { gb: e.target.value })} />
            </Field>
            <Field label="مدت اعتبار (روز) — ۰ = بدون انقضا">
              <Input value={p.days} inputMode="numeric" onChange={(e) => upd(i, { days: e.target.value })} />
            </Field>
            <Field label="قیمت برای کاربر (تومان)">
              <Input value={p.price} inputMode="numeric" onChange={(e) => upd(i, { price: e.target.value })} />
            </Field>
            {p.kind === "unlimited" && (
              <Field label="قیمت برای نماینده (تومان)" hint="بسته‌ی حجمی برای نماینده با نرخ گیگی خودش حساب می‌شود">
                <Input value={p.agent_price} inputMode="numeric" onChange={(e) => upd(i, { agent_price: e.target.value })} />
              </Field>
            )}
          </div>
        </div>
      ))}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={pkgs.length >= 30}
          onClick={() => setPkgs((xs) => [...xs, { kind: "volume", title: "", gb: "", days: "30", price: "", agent_price: "" }])}
        >
          <Plus className="h-4 w-4" /> افزودن بسته
        </Button>
        <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "در حال ذخیره…" : save.isSuccess ? "ذخیره شد ✓" : "ذخیره بسته‌ها"}
        </Button>
      </div>
    </div>
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
  const [p2, setP2] = React.useState({
    enabled: false, label: "سرور اختصاصی", base_url: "", username: "", password: "",
    inbound_id: "0", sub_link_base: "", use_proxy: "" as Tri, proxy_url: "", price_per_gb: "7000",
  });
  const [p1Enabled, setP1Enabled] = React.useState(true);
  const [welcome, setWelcome] = React.useState("");
  const [navLabels, setNavLabels] = React.useState<Record<string, string>>({});
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
      const rawUse = items.panel2_use_proxy ?? "";
      setP2({
        enabled: (items.panel2_enabled ?? "0") === "1",
        label: items.panel2_label || "سرور اختصاصی",
        base_url: items.panel2_base_url ?? "",
        username: items.panel2_username ?? "",
        password: "",
        inbound_id: items.panel2_inbound_id ?? "0",
        sub_link_base: items.panel2_sub_link_base ?? "",
        use_proxy: (rawUse === "true" || rawUse === "false" ? rawUse : "") as Tri,
        proxy_url: items.panel2_proxy_url ?? "",
        price_per_gb: items.panel2_price_per_gb ?? "7000",
      });
      setP1Enabled((items.panel_enabled ?? "1") !== "0");
      setWelcome(items.welcome_text ?? "");
      setNavLabels(Object.fromEntries(NAV_LABEL_FIELDS.map((f) => [f.action, items[`btn_${f.action}_label`] ?? ""])));
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
  const savePanel2 = useMutation({
    mutationFn: () => api.setPanel2({
      enabled: p2.enabled, label: p2.label.trim() || "سرور اختصاصی", base_url: p2.base_url.trim(),
      username: p2.username.trim(), password: p2.password, inbound_id: Number(p2.inbound_id) || 0,
      sub_link_base: p2.sub_link_base.trim(), use_proxy: p2.use_proxy, proxy_url: p2.proxy_url.trim(),
      price_per_gb: Number(p2.price_per_gb) || 7000,
    }),
    onSuccess: () => { setP2((s) => ({ ...s, password: "" })); qc.invalidateQueries({ queryKey: ["settings"] }); },
  });
  const savePanelPrimary = useMutation({
    mutationFn: (enabled: boolean) => api.setPanelPrimary(enabled),
    onSuccess: (_r, enabled) => { setP1Enabled(enabled); qc.invalidateQueries({ queryKey: ["settings"] }); },
  });
  const saveTexts = useMutation({
    mutationFn: () => api.setTexts({ welcome_text: welcome, labels: navLabels }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
  const toggle = useMutation({ mutationFn: ({ a, s }: { a: Audience; s: "open" | "closed" }) => api.setSales(a, s), onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }) });
  const uiMode = useMutation({
    mutationFn: (mode: "modern" | "classic") => api.setUiMode(mode),
    onSuccess: (_r, mode) => { qc.invalidateQueries({ queryKey: ["settings"] }); if (mode === "classic") window.location.href = "/admin"; },
  });
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));
  const setp = (k: keyof typeof p2, v: string | boolean) => setP2((s) => ({ ...s, [k]: v }));

  if (isLoading || !data) return <div className="space-y-5"><Skeleton className="h-12 w-72" /><Skeleton className="h-72" /></div>;

  const mode = items.ui_mode ?? "modern";
  const TABS = [
    { v: "general", label: "عمومی" },
    { v: "sales", label: "فروش و تعرفه" },
    { v: "payment", label: "پرداخت" },
    { v: "texts", label: "متن‌ها و دکمه‌ها" },
    { v: "panels", label: "پنل‌ها" },
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
          <CardHeader>
            <CardTitle>حالت نمایش پنل</CardTitle>
            <p className="text-sm text-muted-foreground">تغییر بلافاصله اعمال می‌شود (نیازی به ری‌استارت نیست).</p>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 sm:grid-cols-2">
              <button onClick={() => uiMode.mutate("modern")} disabled={uiMode.isPending}
                className={`card-hover rounded-2xl border p-4 text-right transition ${mode !== "classic" ? "border-brand/40 bg-brand/[0.06]" : "border-border hover:border-white/20"}`}>
                <div className="flex items-center justify-between">
                  <span className="font-bold text-white">داشبورد مدرن</span>
                  {mode !== "classic" && <Badge variant="success">فعال</Badge>}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">React/shadcn، سریع و حرفه‌ای (پیشنهادی)</p>
              </button>
              <button onClick={() => uiMode.mutate("classic")} disabled={uiMode.isPending}
                className={`card-hover rounded-2xl border p-4 text-right transition ${mode === "classic" ? "border-brand/40 bg-brand/[0.06]" : "border-border hover:border-white/20"}`}>
                <div className="flex items-center justify-between">
                  <span className="font-bold text-white">پنل کلاسیک</span>
                  {mode === "classic" && <Badge variant="success">فعال</Badge>}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">نسخه‌ی قدیمی مبتنی بر صفحات سرور</p>
              </button>
            </div>
          </CardContent>
        </Card>

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

      {/* ───────────── Panels ───────────── */}
      {/* ───────────── Texts & button labels ───────────── */}
      <TabsContent value="texts" className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>پیام خوش‌آمدگویی</CardTitle>
            <p className="text-sm text-muted-foreground">
              متن صفحه‌ی شروع ربات. می‌توانید از <code className="rounded bg-white/10 px-1">{"{support}"}</code> برای درج خودکار آیدی پشتیبانی و از تگ‌های ساده‌ی HTML تلگرام (<code className="rounded bg-white/10 px-1">&lt;b&gt;</code>، <code className="rounded bg-white/10 px-1">&lt;i&gt;</code>، <code className="rounded bg-white/10 px-1">&lt;code&gt;</code>) استفاده کنید. خالی بگذارید تا متن پیش‌فرض نمایش داده شود.
            </p>
          </CardHeader>
          <CardContent className="space-y-3">
            <Textarea value={welcome} onChange={(e) => setWelcome(e.target.value)} rows={10} placeholder="خالی = متن پیش‌فرض" className="font-mono text-sm leading-7" dir="rtl" />
            <div className="flex justify-end">
              <Button size="sm" disabled={saveTexts.isPending} onClick={() => saveTexts.mutate()}>
                <Save className="h-4 w-4" /> {saveTexts.isPending ? "در حال ذخیره…" : saveTexts.isSuccess ? "ذخیره شد ✓" : "ذخیره متن‌ها و دکمه‌ها"}
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>نام دکمه‌های منوی ربات</CardTitle>
            <p className="text-sm text-muted-foreground">
              نام نمایشی هر دکمه‌ی منو را تغییر دهید (ایموجی هم می‌توانید بگذارید). خالی بگذارید تا نام پیش‌فرض استفاده شود. تغییرات تا چند لحظه بعد در ربات اعمال می‌شوند؛ کاربران با زدن /start منوی به‌روز را می‌بینند.
            </p>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            {NAV_LABEL_FIELDS.map((f) => (
              <Field key={f.action} label={f.label} hint={`پیش‌فرض: ${f.def}`}>
                <Input
                  value={navLabels[f.action] ?? ""}
                  placeholder={f.def}
                  onChange={(e) => setNavLabels((s) => ({ ...s, [f.action]: e.target.value }))}
                />
              </Field>
            ))}
            <div className="sm:col-span-2 flex justify-end">
              <Button size="sm" disabled={saveTexts.isPending} onClick={() => saveTexts.mutate()}>
                <Save className="h-4 w-4" /> {saveTexts.isPending ? "در حال ذخیره…" : saveTexts.isSuccess ? "ذخیره شد ✓" : "ذخیره متن‌ها و دکمه‌ها"}
              </Button>
            </div>
          </CardContent>
        </Card>
      </TabsContent>

      <TabsContent value="panels" className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Settings2 className="h-5 w-5 text-muted-foreground" /> پنل اصلی 3x-ui</CardTitle>
            <p className="text-sm text-muted-foreground">پسورد را خالی بگذارید تا تغییر نکند. با خاموش‌کردن این پنل، گزینه‌ی خرید از سرور اصلی در ربات پنهان می‌شود (تمدید سرویس‌های موجود همچنان کار می‌کند).</p>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-white/[0.02] p-4">
              <div className="flex items-center gap-3">
                <span className="font-bold text-white">وضعیت فروش از سرور اصلی</span>
                <Badge variant={p1Enabled ? "success" : "danger"}>{p1Enabled ? "فعال" : "غیرفعال"}</Badge>
              </div>
              <div className="flex gap-2">
                <Button size="sm" disabled={p1Enabled || savePanelPrimary.isPending} onClick={() => savePanelPrimary.mutate(true)}><LockOpen className="h-4 w-4" /> فعال</Button>
                <Button size="sm" variant="destructive" disabled={!p1Enabled || savePanelPrimary.isPending} onClick={() => savePanelPrimary.mutate(false)}><Lock className="h-4 w-4" /> غیرفعال</Button>
              </div>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              {PANEL_FIELDS.map(({ key, label, type }) => (
                <Field key={key} label={label}>
                  <Input type={type || "text"} value={form[key] ?? ""} onChange={(e) => set(key, e.target.value)} placeholder={type === "password" ? "بدون تغییر" : ""} />
                </Field>
              ))}
            </div>
            <div className="flex justify-end">
              <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>{save.isPending ? "در حال ذخیره…" : save.isSuccess ? "ذخیره شد ✓" : "ذخیره پنل اصلی"}</Button>
            </div>
            <PackageEditor panel="1" initial={items.panel_packages ?? ""} />
          </CardContent>
        </Card>

        <Card className="border-brand/20">
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Server className="h-5 w-5 text-brand" /> پنل دوم (سرور اختصاصی)</CardTitle>
            <p className="text-sm text-muted-foreground">
              یک پنل 3x-ui دیگر اضافه کنید که در ربات به‌عنوان یک گزینه‌ی خرید مجزا با قیمت اختصاصی نمایش داده می‌شود. می‌توانید اتصال این پنل را با پروکسی یا بدون پروکسی تنظیم کنید. پسورد خالی = بدون تغییر.
            </p>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-white/[0.02] p-4">
              <div className="flex items-center gap-3">
                <span className="font-bold text-white">وضعیت پنل دوم</span>
                <Badge variant={p2.enabled ? "success" : "danger"}>{p2.enabled ? "فعال" : "غیرفعال"}</Badge>
              </div>
              <div className="flex gap-2">
                <Button size="sm" disabled={p2.enabled} onClick={() => setp("enabled", true)}><LockOpen className="h-4 w-4" /> فعال</Button>
                <Button size="sm" variant="destructive" disabled={!p2.enabled} onClick={() => setp("enabled", false)}><Lock className="h-4 w-4" /> غیرفعال</Button>
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="نام دکمه‌ی خرید" hint="در منوی ربات نمایش داده می‌شود"><Input value={p2.label} onChange={(e) => setp("label", e.target.value)} placeholder="سرور اختصاصی" /></Field>
              <Field label="قیمت هر گیگ (تومان)" hint="قیمت اختصاصی این پنل"><Input value={p2.price_per_gb} inputMode="numeric" onChange={(e) => setp("price_per_gb", e.target.value)} placeholder="7000" /></Field>
              <Field label="آدرس پنل 3x-ui"><Input value={p2.base_url} onChange={(e) => setp("base_url", e.target.value)} placeholder="http://ip:port/path" /></Field>
              <Field label="آدرس پایه لینک اشتراک"><Input value={p2.sub_link_base} onChange={(e) => setp("sub_link_base", e.target.value)} placeholder="http://domain:port/sub" /></Field>
              <Field label="یوزرنیم پنل"><Input value={p2.username} onChange={(e) => setp("username", e.target.value)} /></Field>
              <Field label="پسورد پنل"><Input type="password" value={p2.password} onChange={(e) => setp("password", e.target.value)} placeholder="بدون تغییر" /></Field>
              <Field label="Inbound ID"><Input value={p2.inbound_id} inputMode="numeric" onChange={(e) => setp("inbound_id", e.target.value)} /></Field>
              <Field label="آدرس پروکسی" hint="نمونه: socks5h://user:pass@127.0.0.1:1080"><Input value={p2.proxy_url} onChange={(e) => setp("proxy_url", e.target.value)} placeholder="خالی = بدون پروکسی" /></Field>
            </div>

            <div className="rounded-xl border border-border bg-white/[0.02] p-4">
              <div className="mb-2 text-sm font-bold text-white">اتصال از طریق پروکسی</div>
              <div className="flex flex-wrap gap-2">
                {([
                  { v: "" as Tri, label: "خودکار", icon: Wifi, hint: "اگر آدرس پروکسی پر باشد استفاده می‌شود" },
                  { v: "true" as Tri, label: "با پروکسی", icon: Wifi },
                  { v: "false" as Tri, label: "بدون پروکسی", icon: WifiOff },
                ]).map((opt) => {
                  const active = p2.use_proxy === opt.v;
                  const Icon = opt.icon;
                  return (
                    <button key={opt.v || "auto"} onClick={() => setp("use_proxy", opt.v)}
                      className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-bold transition ${active ? "border-brand/50 bg-brand/15 text-white" : "border-border text-muted-foreground hover:text-white"}`}>
                      <Icon className="h-4 w-4" /> {opt.label}
                    </button>
                  );
                })}
              </div>
              <p className="mt-2 text-xs text-muted-foreground">تغییر این گزینه پس از یک بار ری‌استارت ربات اعمال می‌شود.</p>
            </div>

            <div className="flex justify-end">
              <Button size="sm" disabled={savePanel2.isPending} onClick={() => savePanel2.mutate()}>
                <Save className="h-4 w-4" /> {savePanel2.isPending ? "در حال ذخیره…" : savePanel2.isSuccess ? "ذخیره شد ✓" : "ذخیره پنل دوم"}
              </Button>
            </div>
            <PackageEditor panel="2" initial={items.panel2_packages ?? ""} />
          </CardContent>
        </Card>
      </TabsContent>

      <div className="sticky bottom-4 flex justify-end">
        <Button size="lg" disabled={save.isPending} onClick={() => save.mutate()}>
          <Save className="h-4 w-4" /> {save.isPending ? "در حال ذخیره…" : save.isSuccess ? "ذخیره شد ✓" : "ذخیره تنظیمات فروشگاه و پنل"}
        </Button>
      </div>
    </Tabs>
  );
}
