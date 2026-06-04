import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LockOpen, Lock, Save, Plus, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

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

export function Settings() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["settings"], queryFn: () => api.settings() });
  const items = data?.items ?? {};
  const master = items.sales_status ?? "open";

  const [form, setForm] = React.useState<Record<string, string>>({});
  const [cards, setCards] = React.useState<{ number: string; name: string }[]>([]);
  const [inf, setInf] = React.useState({ enabled: false, cap_gb: "100", duration_days: "30", price: "0" });
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
      inited.current = true;
    }
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  const saveCards = useMutation({
    mutationFn: () => api.setPaymentCards(cards.filter((c) => c.number.trim())),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });

  const save = useMutation({
    mutationFn: () => api.updateSettings(form),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
  const saveInf = useMutation({
    mutationFn: () =>
      api.setInfinite({
        enabled: inf.enabled,
        cap_gb: Number(inf.cap_gb) || 0,
        duration_days: Number(inf.duration_days) || 0,
        price: Number(inf.price) || 0,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
  const toggle = useMutation({
    mutationFn: ({ a, s }: { a: Audience; s: "open" | "closed" }) => api.setSales(a, s),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
  const uiMode = useMutation({
    mutationFn: (mode: "modern" | "classic") => api.setUiMode(mode),
    onSuccess: (_r, mode) => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      // Switching to classic → load the classic panel; modern stays in-app.
      if (mode === "classic") window.location.href = "/admin";
    },
  });
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  if (isLoading || !data) return <div className="space-y-5"><Skeleton className="h-40" /><Skeleton className="h-72" /></div>;

  const mode = items.ui_mode ?? "modern";

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>حالت نمایش پنل</CardTitle>
          <p className="text-sm text-muted-foreground">
            انتخاب کنید پنل مدیریت با چه ظاهری باز شود. تغییر بلافاصله اعمال می‌شود (نیازی به ری‌استارت نیست).
          </p>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2">
            <button
              onClick={() => uiMode.mutate("modern")}
              disabled={uiMode.isPending}
              className={`rounded-2xl border p-4 text-right transition ${mode !== "classic" ? "border-white/30 bg-white/[0.06]" : "border-border hover:border-white/20"}`}
            >
              <div className="flex items-center justify-between">
                <span className="font-bold text-white">داشبورد مدرن</span>
                {mode !== "classic" && <Badge variant="success">فعال</Badge>}
              </div>
              <p className="mt-1 text-xs text-muted-foreground">React/shadcn، سریع و حرفه‌ای (پیشنهادی)</p>
            </button>
            <button
              onClick={() => uiMode.mutate("classic")}
              disabled={uiMode.isPending}
              className={`rounded-2xl border p-4 text-right transition ${mode === "classic" ? "border-white/30 bg-white/[0.06]" : "border-border hover:border-white/20"}`}
            >
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
          <CardTitle>کارت‌های پرداخت (چرخشی)</CardTitle>
          <p className="text-sm text-muted-foreground">
            تا ۸ کارت اضافه کنید؛ ربات برای هر واریز به‌ترتیب چرخشی (round-robin) یکی را نشان می‌دهد و بار روی کارت‌ها پخش می‌شود.
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          {cards.length === 0 && <p className="text-sm text-muted-foreground">هیچ کارتی اضافه نشده — از دکمه‌ی پایین اضافه کنید.</p>}
          {cards.map((c, i) => (
            <div key={i} className="flex flex-col gap-2 rounded-xl border border-border bg-white/[0.02] p-3 sm:flex-row sm:items-end">
              <div className="flex-1">
                <Field label={`شماره کارت ${i + 1}`}>
                  <Input
                    value={c.number}
                    inputMode="numeric"
                    placeholder="6037-xxxx-xxxx-xxxx"
                    onChange={(e) => setCards((xs) => xs.map((x, j) => (j === i ? { ...x, number: e.target.value } : x)))}
                  />
                </Field>
              </div>
              <div className="flex-1">
                <Field label="به نام">
                  <Input
                    value={c.name}
                    placeholder="نام صاحب کارت"
                    onChange={(e) => setCards((xs) => xs.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))}
                  />
                </Field>
              </div>
              <Button variant="destructive" size="icon" onClick={() => setCards((xs) => xs.filter((_, j) => j !== i))}>
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))}
          <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
            <Button variant="outline" size="sm" disabled={cards.length >= 8} onClick={() => setCards((xs) => [...xs, { number: "", name: "" }])}>
              <Plus className="h-4 w-4" /> افزودن کارت
            </Button>
            <Button size="sm" disabled={saveCards.isPending} onClick={() => saveCards.mutate()}>
              {saveCards.isPending ? "در حال ذخیره…" : saveCards.isSuccess ? "ذخیره شد ✓" : "ذخیره کارت‌ها"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>بسته‌ی بی‌نهایت (مصرف منصفانه)</CardTitle>
          <p className="text-sm text-muted-foreground">
            بسته‌ای با حجم بالا و قیمت سفارشی. وقتی مصرف کاربر به سقف منصفانه برسد، کانفیگ به‌صورت خودکار و بدون اخطار در پنل غیرفعال می‌شود و در «اشتراک‌های من» با وضعیت غیرفعال نمایش داده می‌شود. هنگام خرید، فقط لینک کانفیگ‌ها برای کاربر ارسال می‌شود (نه لینک اشتراک).
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between rounded-xl border border-border bg-white/[0.02] p-4">
            <div className="flex items-center gap-3">
              <span className="font-bold text-white">وضعیت بسته</span>
              <Badge variant={inf.enabled ? "success" : "danger"}>{inf.enabled ? "فعال" : "غیرفعال"}</Badge>
            </div>
            <div className="flex gap-2">
              <Button size="sm" disabled={inf.enabled} onClick={() => setInf((s) => ({ ...s, enabled: true }))}>
                <LockOpen className="h-4 w-4" /> فعال
              </Button>
              <Button size="sm" variant="destructive" disabled={!inf.enabled} onClick={() => setInf((s) => ({ ...s, enabled: false }))}>
                <Lock className="h-4 w-4" /> غیرفعال
              </Button>
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="سقف مصرف منصفانه (گیگ)" hint="بعد از این حجم، کانفیگ غیرفعال می‌شود">
              <Input value={inf.cap_gb} inputMode="numeric" onChange={(e) => setInf((s) => ({ ...s, cap_gb: e.target.value }))} />
            </Field>
            <Field label="مدت اعتبار (روز)">
              <Input value={inf.duration_days} inputMode="numeric" onChange={(e) => setInf((s) => ({ ...s, duration_days: e.target.value }))} />
            </Field>
            <Field label="قیمت سفارشی (تومان)">
              <Input value={inf.price} inputMode="numeric" onChange={(e) => setInf((s) => ({ ...s, price: e.target.value }))} />
            </Field>
          </div>
          <div className="flex justify-end">
            <Button size="sm" disabled={saveInf.isPending} onClick={() => saveInf.mutate()}>
              {saveInf.isPending ? "در حال ذخیره…" : saveInf.isSuccess ? "ذخیره شد ✓" : "ذخیره بسته‌ی بی‌نهایت"}
            </Button>
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

      <Card>
        <CardHeader>
          <CardTitle>اتصال پنل 3x-ui</CardTitle>
          <p className="text-sm text-muted-foreground">پسورد را خالی بگذارید تا تغییر نکند.</p>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          {PANEL_FIELDS.map(({ key, label, type }) => (
            <Field key={key} label={label}>
              <Input type={type || "text"} value={form[key] ?? ""} onChange={(e) => set(key, e.target.value)} placeholder={type === "password" ? "بدون تغییر" : ""} />
            </Field>
          ))}
        </CardContent>
      </Card>

      <div className="sticky bottom-4 flex justify-end">
        <Button size="lg" disabled={save.isPending} onClick={() => save.mutate()}>
          <Save className="h-4 w-4" /> {save.isPending ? "در حال ذخیره…" : save.isSuccess ? "ذخیره شد ✓" : "ذخیره همه تنظیمات"}
        </Button>
      </div>
    </div>
  );
}
