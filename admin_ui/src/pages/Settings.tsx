import * as React from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  LockOpen,
  Lock,
  Save,
  Plus,
  Trash2,
  ShieldCheck,
  Server,
  DatabaseBackup,
  Store,
  Tags,
  CreditCard,
  MonitorCog,
  Layers,
  FlaskConical,
  Info,
  type LucideIcon,
} from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { CatalogTab } from "./settings/CatalogTab";

type Audience = "all" | "user" | "agent";
type Items = Record<string, string>;

/** Shop-wide fields, grouped so the page reads like a form and not a data dump. */
const SHOP_GROUPS: { title: string; hint?: string; fields: { key: string; label: string; hint?: string }[] }[] = [
  {
    title: "ارتباط و مدیریت",
    fields: [
      { key: "support_id", label: "آیدی پشتیبانی", hint: "با @ ، مثل @ElsaVPN_Support" },
      { key: "admin_user_ids", label: "ادمین‌های ربات", hint: "آیدی عددی، جدا شده با کاما." },
    ],
  },
  {
    title: "پیش‌فرض نماینده‌ها",
    hint: "این مقادیر فقط برای نماینده‌ی تازه‌تأییدشده استفاده می‌شوند و بعداً برای هر نماینده جداگانه قابل تغییرند.",
    fields: [
      { key: "default_agent_credit_limit_toman", label: "سقف اعتبار پیش‌فرض (تومان)", hint: "۰ = اعتبار نامحدود." },
      { key: "default_agent_price_per_gb", label: "قیمت هر گیگ پیش‌فرض (تومان)", hint: "۰ = همان تعرفه‌ی عمومی فروشگاه." },
    ],
  },
];
/** The per-GB tariff. Since plans arrived it no longer prices NEW purchases —
 *  it prices renewals (which add volume to an existing service) and the wallet
 *  top-up suggestion. Kept in its own tab so it cannot be mistaken for the
 *  shop's price list. */
const RENEW_FIELDS: { key: string; label: string; hint?: string }[] = [
  { key: "price_per_gb", label: "قیمت هر گیگ (تومان)", hint: "اگر «تعرفه پلکانی» پایین تعریف شود، آن جدول جای این عدد را می‌گیرد." },
  { key: "minimum_purchase_gb", label: "حداقل حجم تمدید (گیگ)", hint: "دکمه‌های حجمِ تمدید ضریب‌های ۱، ۲، ۳ و ۴ همین عددند." },
  { key: "purchase_duration_days", label: "مدت اعتبار پیش‌فرض (روز)", hint: "برای پلن‌ها، مدت اعتبار از خود پلن خوانده می‌شود." },
];

const SHOP_FIELD_KEYS = [
  ...SHOP_GROUPS.flatMap((g) => g.fields.map((f) => f.key)),
  ...RENEW_FIELDS.map((f) => f.key),
];

const PANEL_FIELDS: { key: string; label: string; type?: string; hint?: string }[] = [
  { key: "panel_base_url", label: "آدرس پنل 3x-ui", hint: "مثل https://panel.example.com:2053" },
  { key: "panel_username", label: "یوزرنیم پنل" },
  { key: "panel_password", label: "پسورد پنل", type: "password", hint: "خالی بگذارید تا تغییر نکند." },
  { key: "panel_inbound_id", label: "Inbound ID", hint: "کانفیگ‌ها داخل این اینباند ساخته می‌شوند." },
  { key: "sub_link_base", label: "آدرس پایه لینک اشتراک", hint: "همان دامنه/پورتی که لینک sub روی آن سرو می‌شود." },
];

/** Consistent section wrapper: icon + title + one-line explanation. */
function Section({
  icon: Icon,
  title,
  desc,
  children,
  accent,
  action,
}: {
  icon: LucideIcon;
  title: string;
  desc?: string;
  children: React.ReactNode;
  accent?: boolean;
  action?: React.ReactNode;
}) {
  return (
    <Card className={accent ? "border-brand/25" : undefined}>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="flex items-center gap-2">
              <Icon className={`h-5 w-5 ${accent ? "text-brand" : "text-muted-foreground"}`} /> {title}
            </CardTitle>
            {desc && <p className="mt-1 text-sm leading-6 text-muted-foreground">{desc}</p>}
          </div>
          {action}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">{children}</CardContent>
    </Card>
  );
}

/** Shared "idle / saving / saved" label for every save button on the page. */
function SaveButton({ m, label = "ذخیره" }: { m: { isPending: boolean; isSuccess: boolean }; label?: string }) {
  return (
    <>
      <Save className="h-4 w-4" /> {m.isPending ? "در حال ذخیره…" : m.isSuccess ? "ذخیره شد ✓" : label}
    </>
  );
}

function Note({ children, tone = "info" }: { children: React.ReactNode; tone?: "info" | "warn" }) {
  return (
    <div
      className={`flex items-start gap-2 rounded-xl border px-3 py-2 text-xs leading-6 ${
        tone === "warn"
          ? "border-amber-400/30 bg-amber-400/5 text-amber-200"
          : "border-border bg-white/[0.02] text-muted-foreground"
      }`}
    >
      <Info className="mt-1 h-3.5 w-3.5 shrink-0" />
      <div>{children}</div>
    </div>
  );
}

function ToggleRow({
  title,
  on,
  onChange,
  onLabel = "فعال",
  offLabel = "غیرفعال",
  busy,
}: {
  title: string;
  on: boolean;
  onChange: (v: boolean) => void;
  onLabel?: string;
  offLabel?: string;
  busy?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-white/[0.02] p-4">
      <div className="flex items-center gap-3">
        <span className="font-bold text-white">{title}</span>
        <Badge variant={on ? "success" : "danger"}>{on ? onLabel : offLabel}</Badge>
      </div>
      <div className="flex gap-2">
        <Button size="sm" disabled={busy || on} onClick={() => onChange(true)}><LockOpen className="h-4 w-4" /> {onLabel}</Button>
        <Button size="sm" variant="destructive" disabled={busy || !on} onClick={() => onChange(false)}><Lock className="h-4 w-4" /> {offLabel}</Button>
      </div>
    </div>
  );
}

function SalesRow({ title, audience, status, onToggle, busy }: {
  title: string; audience: Audience; status: string;
  onToggle: (a: Audience, s: "open" | "closed") => void; busy: boolean;
}) {
  const open = status !== "closed";
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-white/[0.02] p-4">
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

// ─────────────────────────── panels ───────────────────────────

function PrimaryBackendCard({ items }: { items: Items }) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const current = (items.primary_backend ?? "xui") === "pasarguard" ? "pasarguard" : "xui";
  const pgReady = (items.pg_enabled ?? "0") === "1" && !!(items.pg_base_url ?? "").trim();
  const xuiReady = !!(items.panel_base_url ?? "").trim();
  const save = useMutation({
    mutationFn: (backend: "xui" | "pasarguard") => api.setPrimaryBackend(backend),
    onSuccess: (_r, backend) => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      toast({ title: `پنل اصلی فروش: ${backend === "pasarguard" ? "PasarGuard" : "3x-ui"}`, variant: "success" });
    },
    onError: (e: Error) => toast({ title: "تغییر پنل اصلی ناموفق بود", description: e.message, variant: "error" }),
  });
  const options = [
    { key: "xui" as const, title: "پنل 3x-ui", hint: "فروش از طریق پنل 3x-ui", ready: xuiReady },
    { key: "pasarguard" as const, title: "پنل PasarGuard", hint: "فروش از طریق پنل PasarGuard", ready: pgReady },
  ];
  const activeReady = options.find((o) => o.key === current)?.ready;
  return (
    <Section
      icon={Server}
      accent
      title="پنل اصلی فروش"
      desc="دکمه‌ی «خرید سرویس» در ربات از کدام پنل بفروشد. هر زمان می‌توانید جابه‌جا کنید؛ سرویس‌های فروخته‌شده‌ی قبلی روی پنل خودشان دست‌نخورده می‌مانند."
    >
      <div className="grid gap-3 sm:grid-cols-2">
        {options.map((o) => {
          const active = current === o.key;
          return (
            <button
              key={o.key}
              onClick={() => save.mutate(o.key)}
              disabled={save.isPending || active}
              className={`rounded-2xl border p-4 text-right transition ${active ? "border-white/30 bg-white/[0.06]" : "border-border hover:border-white/20"}`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-bold text-white">{o.title}</span>
                {active ? <Badge variant="success">فعال</Badge> : o.ready ? <Badge variant="muted">آماده</Badge> : <Badge variant="muted">پیکربندی نشده</Badge>}
              </div>
              <p className="mt-1 text-xs text-muted-foreground">{o.hint}</p>
            </button>
          );
        })}
      </div>
      {!activeReady && (
        <Note tone="warn">
          پنل انتخاب‌شده هنوز کامل پیکربندی نشده است؛ تا وقتی اطلاعات اتصالش را پایین وارد و تست نکنید، خرید از آن انجام نمی‌شود.
        </Note>
      )}
    </Section>
  );
}

function PasarGuardCard({ items }: { items: Items }) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [pg, setPg] = React.useState(() => ({
    enabled: (items.pg_enabled ?? "0") === "1",
    label: items.pg_label || "سرور اختصاصی",
    base_url: items.pg_base_url ?? "",
    username: items.pg_username ?? "",
    password: "",
    group: items.pg_group ?? "",
    verify_tls: (items.pg_verify_tls ?? "1") !== "0",
    price_per_gb: items.pg_price_per_gb ?? "0",
    default_days: items.pg_default_days ?? "",
  }));
  type TestReport = { ok: boolean; error?: string; admin_username?: string; is_owner?: boolean; panel_version?: string; groups?: { id: number; name: string }[] };
  const [test, setTest] = React.useState<TestReport | null>(null);
  const upd = (k: string, v: string | boolean) => setPg((s) => ({ ...s, [k]: v }));
  const save = useMutation({
    mutationFn: () => api.setPasarGuard({ ...pg, price_per_gb: Number(pg.price_per_gb) || 0, default_days: pg.default_days.trim() }),
    onSuccess: () => {
      setPg((s) => ({ ...s, password: "" }));
      qc.invalidateQueries({ queryKey: ["settings"] });
      toast({ title: "تنظیمات PasarGuard ذخیره شد", variant: "success" });
    },
    onError: (e: Error) => toast({ title: "ذخیره ناموفق بود", description: e.message, variant: "error" }),
  });
  const testConn = useMutation({
    mutationFn: () => api.testPasarGuard({ base_url: pg.base_url, username: pg.username, password: pg.password || undefined, verify_tls: pg.verify_tls }),
    onSuccess: (r) => setTest(r),
    onError: (e) => setTest({ ok: false, error: String(e) }),
  });
  // Groups the test call discovered — the group name must match one of these exactly.
  const groupNames = (test?.groups || []).map((g) => g.name);
  const groupMismatch = test?.ok && pg.group.trim() !== "" && groupNames.length > 0 && !groupNames.includes(pg.group.trim());

  return (
    <Section
      icon={ShieldCheck}
      accent
      title="اتصال پنل PasarGuard"
      desc="یک حساب ادمینِ اختصاصی برای ربات در پنل بسازید و همان را اینجا وارد کنید. سرویس‌های این پنل با تعرفه‌ی گیگیِ فروشگاه فروخته می‌شوند."
    >
      <ToggleRow title="وضعیت پنل PasarGuard" on={pg.enabled} onChange={(v) => upd("enabled", v)} />
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="آدرس پنل" hint="با https و پورت، مثل https://panel.example.com:8000">
          <Input value={pg.base_url} onChange={(e) => upd("base_url", e.target.value)} placeholder="https://panel.example.com:8000" dir="ltr" />
        </Field>
        <Field label="گروه (group) پنل" hint="کاربر در این گروه ساخته می‌شود — دقیقاً همان نام داخل پنل.">
          <Input value={pg.group} onChange={(e) => upd("group", e.target.value)} placeholder="Elsa-Bot" dir="ltr" />
        </Field>
        <Field label="یوزرنیم ادمینِ ربات"><Input value={pg.username} onChange={(e) => upd("username", e.target.value)} dir="ltr" /></Field>
        <Field label="پسورد ادمینِ ربات" hint="خالی بگذارید تا تغییر نکند.">
          <Input type="password" value={pg.password} onChange={(e) => upd("password", e.target.value)} placeholder="بدون تغییر" dir="ltr" />
        </Field>
        <Field label="نام نمایشی سرویس (در ربات)" hint="عنوانی که کاربر در «اشتراک‌های من» می‌بیند.">
          <Input value={pg.label} onChange={(e) => upd("label", e.target.value)} placeholder="سرور اختصاصی" />
        </Field>
        <Field label="قیمت هر گیگ (تومان)" hint="۰ = همان تعرفه‌ی معمولِ فروشگاه.">
          <Input value={pg.price_per_gb} inputMode="numeric" onChange={(e) => upd("price_per_gb", e.target.value)} />
        </Field>
        <Field label="مدت اعتبار (روز)" hint="خالی = همان «مدت اعتبار خرید» تبِ فروشگاه.">
          <Input value={pg.default_days} inputMode="numeric" onChange={(e) => upd("default_days", e.target.value)} placeholder="خالی = پیش‌فرض فروشگاه" />
        </Field>
        <div className="flex items-center gap-2 pt-6">
          <input id="pg_verify" type="checkbox" checked={pg.verify_tls} onChange={(e) => upd("verify_tls", e.target.checked)} className="h-4 w-4 accent-[hsl(var(--brand))]" />
          <label htmlFor="pg_verify" className="text-sm text-muted-foreground">بررسی گواهی TLS (اگر cert معتبر دارید روشن بماند)</label>
        </div>
      </div>

      {test && (
        <div className={`rounded-xl border p-3 text-sm leading-6 ${test.ok ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-100" : "border-rose-400/30 bg-rose-400/10 text-rose-100"}`}>
          {test.ok ? (
            <span>
              ✅ اتصال موفق — ادمین: <b>{test.admin_username}</b>{test.panel_version ? ` · نسخه: ${test.panel_version}` : ""}
              {groupNames.length ? ` · گروه‌های موجود: ${groupNames.join("، ")}` : ""}
            </span>
          ) : (
            <span>❌ اتصال ناموفق: {test.error}</span>
          )}
        </div>
      )}
      {groupMismatch && (
        <Note tone="warn">
          نام گروهی که وارد کرده‌اید («{pg.group.trim()}») در این پنل وجود ندارد. با این تنظیم، ساختِ کانفیگ شکست می‌خورد؛
          یکی از گروه‌های موجود را دقیقاً وارد کنید.
        </Note>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button variant="outline" size="sm" disabled={testConn.isPending} onClick={() => testConn.mutate()}>
          {testConn.isPending ? "در حال تست…" : "تست اتصال"}
        </Button>
        <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>
          <SaveButton m={save} label="ذخیره PasarGuard" />
        </Button>
      </div>
    </Section>
  );
}

function AthenaCard({ items }: { items: Items }) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [f, setF] = React.useState(() => ({
    enabled: (items.athena_enabled ?? "0") === "1",
    label: items.athena_label || "سرور اختصاصی L2TP",
    base_url: items.athena_base_url ?? "",
    api_key: "",
    verify_tls: (items.athena_verify_tls ?? "1") !== "0",
  }));
  const upd = (k: keyof typeof f, v: string | boolean) => setF((s) => ({ ...s, [k]: v }));
  const keySet = (items.athena_api_key_set ?? "0") === "1";

  type Report = {
    ok: boolean; error?: string; admin?: string; role?: string; scopes?: string[];
    can_create_users?: boolean; rate_limit_per_minute?: number;
    nodes?: { id: number; name: string }[]; outbounds?: string[];
  };
  const [test, setTest] = React.useState<Report | null>(null);

  const save = useMutation({
    mutationFn: () => api.setAthena(f),
    onSuccess: () => {
      setF((s) => ({ ...s, api_key: "" }));
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["catalog"] });
      toast({ title: "تنظیمات پنل L2TP ذخیره شد", variant: "success" });
    },
    onError: (e: Error) => toast({ title: "ذخیره ناموفق بود", description: e.message, variant: "error" }),
  });
  const doTest = useMutation({
    mutationFn: () => api.testAthena({ base_url: f.base_url, api_key: f.api_key || undefined, verify_tls: f.verify_tls }),
    onSuccess: (r) => setTest(r),
    onError: (e: Error) => setTest({ ok: false, error: e.message }),
  });

  return (
    <Section
      icon={ShieldCheck}
      accent
      title="اتصال پنل L2TP/SSTP (Athena)"
      desc="پنل اختصاصی خودتان. سرویس‌های این پنل به‌جای لینک اشتراک، نام کاربری و رمز عبور دارند و ربات همان را به مشتری می‌دهد. کلید API را از خود پنل بسازید: Settings ← API keys ← Create."
    >
      <ToggleRow title="وضعیت پنل L2TP" on={f.enabled} onChange={(v) => upd("enabled", v)} />
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="آدرس پنل" hint="با پورت، مثل https://panel.example.com:8443">
          <Input value={f.base_url} onChange={(e) => upd("base_url", e.target.value)} dir="ltr" placeholder="https://panel.example.com:8443" />
        </Field>
        <Field label="کلید API" hint={keySet ? "تنظیم شده — خالی بگذارید تا تغییر نکند." : "کلیدی که در پنل ساختید (ath_...)"}>
          <Input type="password" value={f.api_key} onChange={(e) => upd("api_key", e.target.value)} dir="ltr" placeholder={keySet ? "بدون تغییر" : "ath_..."} />
        </Field>
        <Field label="نام نمایشی سرویس" hint="عنوانی که کنار پلن‌های این پنل دیده می‌شود.">
          <Input value={f.label} onChange={(e) => upd("label", e.target.value)} />
        </Field>
        <div className="flex items-center gap-2 pt-6">
          <input
            id="ath_verify"
            type="checkbox"
            checked={f.verify_tls}
            onChange={(e) => upd("verify_tls", e.target.checked)}
            className="h-4 w-4 accent-[hsl(var(--brand))]"
          />
          <label htmlFor="ath_verify" className="text-sm text-muted-foreground">
            بررسی گواهی TLS (اگر گواهی معتبر دارید روشن بماند)
          </label>
        </div>
      </div>

      {test && (
        <div className={`rounded-xl border p-3 text-sm leading-6 ${test.ok ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-100" : "border-rose-400/30 bg-rose-400/10 text-rose-100"}`}>
          {test.ok ? (
            <>
              <div>✅ اتصال موفق — ادمین: <b>{test.admin}</b> ({test.role})</div>
              <div>
                🖥 سرورها: {(test.nodes || []).map((n) => n.name).join("، ") || "—"}
                {" · "}🌍 خروجی‌ها: {(test.outbounds || []).join("، ") || "—"}
              </div>
              {!test.can_create_users && (
                <div className="mt-1 text-amber-200">
                  ⚠️ این کلید مجوز <code dir="ltr">users:write</code> ندارد؛ اتصال برقرار است ولی ربات نمی‌تواند سرویس بسازد.
                </div>
              )}
            </>
          ) : (
            <>❌ اتصال ناموفق: {test.error}</>
          )}
        </div>
      )}

      <Note>
        هر پلن جداگانه تعیین می‌کند روی کدام سرور و کدام خروجیِ این پنل ساخته شود — از تب «پلن‌های فروش».
      </Note>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button variant="outline" size="sm" disabled={doTest.isPending} onClick={() => doTest.mutate()}>
          {doTest.isPending ? "در حال تست…" : "تست اتصال"}
        </Button>
        <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>
          <SaveButton m={save} label="ذخیره پنل L2TP" />
        </Button>
      </div>
    </Section>
  );
}

// ─────────────────────────── free test config ───────────────────────────

function TestConfigCard({ items }: { items: Items }) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [f, setF] = React.useState(() => ({
    enabled: (items.test_config_enabled ?? "1") === "1",
    mb: items.test_config_mb || "200",
    minutes: items.test_config_minutes || "10",
    user_limit: items.test_config_user_limit ?? "1",
    agent_default_limit: items.default_agent_daily_test_limit || "5",
  }));
  const set = (k: keyof typeof f, v: unknown) => setF((s) => ({ ...s, [k]: v }));
  const save = useMutation({
    mutationFn: () =>
      api.setTestConfig({
        enabled: f.enabled,
        mb: Number(f.mb) || 200,
        minutes: Number(f.minutes) || 10,
        user_limit: Math.max(0, Number(f.user_limit) || 0),
        agent_default_limit: Math.max(0, Number(f.agent_default_limit) || 0),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      toast({ title: "تنظیمات کانفیگ تست ذخیره شد", variant: "success" });
    },
    onError: (e: Error) => toast({ title: "ذخیره ناموفق بود", description: e.message, variant: "error" }),
  });
  const userLimit = Math.max(0, Number(f.user_limit) || 0);

  return (
    <Section
      icon={FlaskConical}
      title="کانفیگ تست رایگان"
      desc="کانفیگ تست همیشه از همان پنلی ساخته می‌شود که «پنل اصلی فروش» است. کاربر عادی سهمیه‌ی مادام‌العمر دارد و نماینده‌ها سهمیه‌ی روزانه."
    >
      <ToggleRow title="دریافت کانفیگ تست" on={f.enabled} onChange={(v) => set("enabled", v)} />
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="حجم (مگابایت)" hint="حجم هر کانفیگ تست.">
          <Input value={f.mb} inputMode="numeric" onChange={(e) => set("mb", e.target.value)} />
        </Field>
        <Field label="اعتبار (دقیقه)" hint="روی PasarGuard شمارش از اولین اتصال کاربر شروع می‌شود.">
          <Input value={f.minutes} inputMode="numeric" onChange={(e) => set("minutes", e.target.value)} />
        </Field>
        <Field label="سهمیه کاربر عادی (کل، نه روزانه)" hint="۰ = کاربران عادی تست نمی‌گیرند و دکمه برایشان نمایش داده نمی‌شود.">
          <Input value={f.user_limit} inputMode="numeric" onChange={(e) => set("user_limit", e.target.value)} />
        </Field>
        <Field label="سهمیه روزانه نماینده (پیش‌فرض)" hint="برای نماینده‌هایی که سهمیه اختصاصی ندارند.">
          <Input value={f.agent_default_limit} inputMode="numeric" onChange={(e) => set("agent_default_limit", e.target.value)} />
        </Field>
      </div>
      <Note>
        {userLimit === 0
          ? "الان فقط نماینده‌ها می‌توانند کانفیگ تست بگیرند."
          : userLimit === 1
            ? "هر کاربر عادی فقط یک کانفیگ تست در طول عمر حسابش می‌گیرد؛ بعد از آن دکمه برایش پنهان می‌شود."
            : `هر کاربر عادی مجموعاً ${userLimit} کانفیگ تست می‌گیرد (نه روزانه).`}{" "}
        سهمیه‌ی اختصاصی هر نماینده را از صفحه‌ی همان کاربر می‌توانید جدا تنظیم کنید.
      </Note>
      <div className="flex justify-end">
        <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>
          <SaveButton m={save} label="ذخیره کانفیگ تست" />
        </Button>
      </div>
    </Section>
  );
}

// ─────────────────────────── backup ───────────────────────────

function BackupCard({ items }: { items: Items }) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [f, setF] = React.useState({
    enabled: (items.backup_enabled ?? "1") === "1",
    bot: (items.backup_include_bot ?? "1") === "1",
    xui: (items.backup_include_xui ?? "1") === "1",
    pg: (items.backup_include_pg ?? "0") === "1",
    pg_db: (items.backup_include_pg_db ?? "0") === "1",
    pg_db_container: items.pg_db_container ?? "",
    pg_db_user: items.pg_db_user || "pasarguard",
    pg_db_name: items.pg_db_name || "pasarguard",
    pg_db_dump_cmd: items.pg_db_dump_cmd ?? "",
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
        backup_include_pg_db: f.pg_db ? "on" : "off",
        pg_db_container: f.pg_db_container.trim(),
        pg_db_user: f.pg_db_user.trim() || "pasarguard",
        pg_db_name: f.pg_db_name.trim() || "pasarguard",
        pg_db_dump_cmd: f.pg_db_dump_cmd.trim(),
        backup_interval_value: String(Math.max(1, Number(f.interval_value) || 1)),
        backup_interval_unit: f.interval_unit,
        backup_telegram_chat_id: f.chat_id.trim(),
        backup_xui_timeout_seconds: items.backup_xui_timeout_seconds ?? "180",
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      toast({ title: "تنظیمات بکاپ ذخیره شد", variant: "success" });
    },
    onError: (e: Error) => toast({ title: "ذخیره ناموفق بود", description: e.message, variant: "error" }),
  });
  const Check = ({ k, label, hint }: { k: keyof typeof f; label: string; hint?: string }) => (
    <label className="flex items-start gap-2 rounded-xl border border-border bg-white/[0.02] p-3 text-sm">
      <input type="checkbox" checked={Boolean(f[k])} onChange={(e) => set(k, e.target.checked)} className="mt-0.5 h-4 w-4 accent-[hsl(var(--brand))]" />
      <span><span className="font-bold text-white">{label}</span>{hint && <span className="block text-[11px] text-muted-foreground">{hint}</span>}</span>
    </label>
  );

  const status = items.backup_last_status || "never";
  const STATUS: Record<string, { label: string; variant: "success" | "warning" | "danger" | "muted" }> = {
    ok: { label: "آخرین بکاپ موفق", variant: "success" },
    partial: { label: "آخرین بکاپ با هشدار", variant: "warning" },
    local: { label: "ساخته شد ولی به تلگرام ارسال نشد", variant: "warning" },
    failed: { label: "آخرین بکاپ ناموفق", variant: "danger" },
    running: { label: "در حال اجرا", variant: "muted" },
    never: { label: "هنوز بکاپی گرفته نشده", variant: "muted" },
  };
  const st = STATUS[status] ?? STATUS.never;

  return (
    <Section
      icon={DatabaseBackup}
      title="بکاپ‌گیری خودکار"
      desc="بکاپ زمان‌بندی‌شده به‌صورت خودکار ساخته و به تلگرام فرستاده می‌شود. اگر آیدی چت خالی باشد، آرشیو روی خود سرور می‌ماند (۳ نسخه‌ی آخر)."
      action={<Badge variant={st.variant}>{st.label}</Badge>}
    >
      <ToggleRow title="بکاپ خودکار" on={f.enabled} onChange={(v) => set("enabled", v)} />

      <div>
        <p className="mb-2 text-xs font-bold text-muted-foreground">چه چیزهایی داخل بکاپ باشد</p>
        <div className="grid gap-3 sm:grid-cols-3">
          <Check k="bot" label="دیتابیس ربات" hint="کاربران، سفارش‌ها، کیف پول" />
          <Check k="xui" label="پنل x-ui" hint="دیتابیس/اینباندهای 3x-ui" />
          <Check k="pg" label="PasarGuard (JSON)" hint="کاربران، ادمین‌ها و گروه‌ها (از API)" />
        </div>
      </div>

      <div className="space-y-3 rounded-xl border border-brand/25 bg-brand/[0.04] p-4">
        <Check k="pg_db" label="بکاپ کامل دیتابیس PasarGuard (دامپ SQL)" hint="کلِ دیتابیس Postgres با pg_dump — شاملِ تمام کاربران، مصرف و تاریخچه" />
        {f.pg_db && (
          <>
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="نام کانتینر دیتابیس" hint="خالی = تشخیص خودکار"><Input value={f.pg_db_container} onChange={(e) => set("pg_db_container", e.target.value)} placeholder="auto-detect" dir="ltr" /></Field>
              <Field label="یوزر دیتابیس"><Input value={f.pg_db_user} onChange={(e) => set("pg_db_user", e.target.value)} placeholder="pasarguard" dir="ltr" /></Field>
              <Field label="نام دیتابیس"><Input value={f.pg_db_name} onChange={(e) => set("pg_db_name", e.target.value)} placeholder="pasarguard" dir="ltr" /></Field>
            </div>
            <Field label="دستور دلخواهِ بکاپ (پیشرفته — اختیاری)" hint="اگر پر شود به‌جای حالت بالا اجرا می‌شود؛ باید دامپ را در STDOUT بدهد. می‌تواند ssh به سرور مستر هم باشد.">
              <textarea
                value={f.pg_db_dump_cmd}
                onChange={(e) => set("pg_db_dump_cmd", e.target.value)}
                rows={2}
                dir="ltr"
                placeholder="مثال: docker exec -i pasarguard-db pg_dump -U pasarguard pasarguard"
                className="w-full rounded-xl border border-input bg-card px-3 py-2 font-mono text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </Field>
            <Note tone="warn">
              نیازمندِ دسترسیِ پنل به Docker روی همان سرورِ PasarGuard است (یا یک دستورِ ssh دلخواه). خروجی به‌صورت <code>.sql.gz</code> در همان آرشیو قرار می‌گیرد.
            </Note>
          </>
        )}
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
        <Field label="آیدی چت تلگرام (مقصد بکاپ)" hint="آیدی عددی خودتان یا کانالی که ربات در آن ادمین است."><Input value={f.chat_id} onChange={(e) => set("chat_id", e.target.value)} dir="ltr" placeholder="-100..." /></Field>
      </div>

      {items.backup_last_error && status !== "ok" && (
        <Note tone="warn">آخرین پیام: <span className="font-mono text-[11px]">{items.backup_last_error.slice(0, 300)}</span></Note>
      )}

      <div className="flex justify-end">
        <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>
          <SaveButton m={save} label="ذخیره تنظیمات بکاپ" />
        </Button>
      </div>
    </Section>
  );
}

// ─────────────────────────── page ───────────────────────────

const TABS = [
  { key: "shop", label: "فروشگاه", icon: Store },
  { key: "pricing", label: "تعرفه تمدید", icon: Tags },
  { key: "payment", label: "پرداخت", icon: CreditCard },
  { key: "catalog", label: "پلن‌های فروش", icon: Layers },
  { key: "panels", label: "پنل‌ها", icon: Server },
  { key: "backup", label: "بکاپ", icon: DatabaseBackup },
  { key: "appearance", label: "ظاهر پنل", icon: MonitorCog },
] as const;
type TabKey = (typeof TABS)[number]["key"];

export function Settings() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [params, setParams] = useSearchParams();
  const tabParam = params.get("tab") as TabKey | null;
  const tab: TabKey = TABS.some((t) => t.key === tabParam) ? (tabParam as TabKey) : "shop";
  const setTab = (v: string) => setParams({ tab: v }, { replace: true });

  const { data, isLoading } = useQuery({ queryKey: ["settings"], queryFn: () => api.settings() });
  const items: Items = data?.items ?? {};
  const master = items.sales_status ?? "open";

  const [form, setForm] = React.useState<Record<string, string>>({});
  const [cards, setCards] = React.useState<{ number: string; name: string }[]>([]);
  const [tiers, setTiers] = React.useState<{ min_gb: string; price_per_gb: string }[]>([]);
  const inited = React.useRef(false);
  React.useEffect(() => {
    if (data && !inited.current) {
      const f: Record<string, string> = {};
      [...SHOP_FIELD_KEYS, ...PANEL_FIELDS.map((p) => p.key)].forEach((key) => (f[key] = items[key] ?? ""));
      setForm(f);
      try {
        const parsed = JSON.parse(items.payment_cards || "[]");
        setCards(Array.isArray(parsed) && parsed.length ? parsed.map((c) => ({ number: String(c.number || ""), name: String(c.name || "") })) : []);
      } catch {
        setCards([]);
      }
      try {
        const pt = JSON.parse(items.price_tiers || "[]");
        setTiers(Array.isArray(pt) ? pt.map((t) => ({ min_gb: String(t.min_gb ?? ""), price_per_gb: String(t.price_per_gb ?? "") })) : []);
      } catch {
        setTiers([]);
      }
      inited.current = true;
    }
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  const ok = (title: string) => ({
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["settings"] }); toast({ title, variant: "success" as const }); },
    onError: (e: Error) => toast({ title: "ذخیره ناموفق بود", description: e.message, variant: "error" as const }),
  });

  const saveCards = useMutation({ mutationFn: () => api.setPaymentCards(cards.filter((c) => c.number.trim())), ...ok("کارت‌های پرداخت ذخیره شد") });
  const save = useMutation({ mutationFn: () => api.updateSettings(form), ...ok("تنظیمات ذخیره شد") });
  const saveTiers = useMutation({
    mutationFn: () =>
      api.setPriceTiers(
        tiers
          .filter((t) => t.min_gb !== "" && t.price_per_gb !== "")
          .map((t) => ({ min_gb: Number(t.min_gb) || 0, price_per_gb: Number(t.price_per_gb) || 0 })),
      ),
    ...ok("تعرفه پلکانی ذخیره شد"),
  });
  const toggle = useMutation({ mutationFn: ({ a, s }: { a: Audience; s: "open" | "closed" }) => api.setSales(a, s), ...ok("وضعیت فروش به‌روزرسانی شد") });
  const uiMode = useMutation({
    mutationFn: (mode: "modern" | "classic") => api.setUiMode(mode),
    onSuccess: (_r, mode) => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      if (mode === "classic") window.location.href = "/admin";
    },
  });
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  if (isLoading || !data) return <div className="space-y-5"><Skeleton className="h-12" /><Skeleton className="h-40" /><Skeleton className="h-72" /></div>;

  const mode = items.ui_mode ?? "modern";
  const salesUser = items.sales_status_user ?? master;
  const salesAgent = items.sales_status_agent ?? master;
  const allClosed = salesUser === "closed" && salesAgent === "closed";
  // The shared text form is only edited on these two tabs, so the sticky save
  // bar is shown only there (it saves nothing on the others).
  const showStickySave = tab === "shop" || tab === "panels";  // the catalog tab saves itself

  return (
    <Tabs dir="rtl" value={tab} onValueChange={setTab} className="space-y-5 text-right">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <TabsList className="flex-wrap">
          {TABS.map((t) => (
            <TabsTrigger key={t.key} value={t.key} className="flex items-center gap-1.5">
              <t.icon className="h-4 w-4" /> {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
        {allClosed && <Badge variant="danger">فروش برای همه بسته است</Badge>}
      </div>

      {/* ───────────── فروشگاه ───────────── */}
      <TabsContent value="shop" className="space-y-5">
        <Section
          icon={Store}
          title="کنترل فروش"
          desc="فروش را برای کاربران عادی و نماینده‌ها جداگانه باز یا بسته کنید. بستنِ فروش سرویس‌های فعلی را از کار نمی‌اندازد."
        >
          <SalesRow title="کاربران عادی" audience="user" status={salesUser} onToggle={(a, s) => toggle.mutate({ a, s })} busy={toggle.isPending} />
          <SalesRow title="نماینده‌ها" audience="agent" status={salesAgent} onToggle={(a, s) => toggle.mutate({ a, s })} busy={toggle.isPending} />
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="subtle" disabled={toggle.isPending} onClick={() => toggle.mutate({ a: "all", s: "open" })}>باز کردن همه</Button>
            <Button size="sm" variant="subtle" disabled={toggle.isPending} onClick={() => toggle.mutate({ a: "all", s: "closed" })}>بستن همه</Button>
          </div>
        </Section>

        {SHOP_GROUPS.map((group) => (
          <Section key={group.title} icon={Store} title={group.title} desc={group.hint}>
            <div className="grid gap-4 sm:grid-cols-2">
              {group.fields.map((f) => (
                <Field key={f.key} label={f.label} hint={f.hint}>
                  <Input value={form[f.key] ?? ""} onChange={(e) => set(f.key, e.target.value)} />
                </Field>
              ))}
            </div>
          </Section>
        ))}

        <TestConfigCard items={items} />
      </TabsContent>

      {/* ───────────── تعرفه تمدید ───────────── */}
      <TabsContent value="pricing" className="space-y-5">
        <Note>
          قیمت <b className="text-white">خریدهای جدید</b> از تب «پلن‌های فروش» می‌آید، نه از اینجا.
          این تعرفه‌ی گیگی فقط دو جا استفاده می‌شود: <b className="text-white">تمدید سرویس</b> (که حجم به
          سرویس موجود اضافه می‌کند) و مبلغ پیشنهادی شارژ کیف پول.
        </Note>

        <Section icon={Tags} title="تعرفه گیگی" desc="نرخ پایه‌ای که تمدیدها با آن حساب می‌شوند. نماینده‌ای که نرخ اختصاصی دارد با نرخ خودش حساب می‌شود.">
          <div className="grid gap-4 sm:grid-cols-3">
            {RENEW_FIELDS.map((f) => (
              <Field key={f.key} label={f.label} hint={f.hint}>
                <Input value={form[f.key] ?? ""} inputMode="numeric" onChange={(e) => set(f.key, e.target.value)} />
              </Field>
            ))}
          </div>
          <div className="flex justify-end">
            <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>
              <SaveButton m={save} label="ذخیره تعرفه" />
            </Button>
          </div>
        </Section>

        <Section
          icon={Tags}
          title="تعرفه پلکانی (اختیاری)"
          desc="نرخ هر گیگ بر اساس حجم تمدید. هر بازه تا شروع بازه‌ی بعدی ادامه دارد. خالی گذاشتن یعنی همان «تعرفه گیگی» بالا."
        >
          {tiers.length === 0 && <Note>هیچ بازه‌ای تعریف نشده — نرخ ثابت بالا اعمال می‌شود.</Note>}
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
              <Button variant="destructive" size="icon" onClick={() => setTiers((xs) => xs.filter((_, j) => j !== i))}>
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Button variant="outline" size="sm" disabled={tiers.length >= 12} onClick={() => setTiers((xs) => [...xs, { min_gb: "", price_per_gb: "" }])}>
              <Plus className="h-4 w-4" /> افزودن بازه
            </Button>
            <Button size="sm" disabled={saveTiers.isPending} onClick={() => saveTiers.mutate()}>
              <SaveButton m={saveTiers} label="ذخیره تعرفه پلکانی" />
            </Button>
          </div>
        </Section>
      </TabsContent>

      {/* ───────────── پرداخت ───────────── */}
      <TabsContent value="payment" className="space-y-5">
        <Section
          icon={CreditCard}
          title="کارت‌های پرداخت (چرخشی)"
          desc="تا ۸ کارت اضافه کنید؛ ربات برای هر واریز به‌ترتیب چرخشی یکی را نشان می‌دهد تا بار روی کارت‌ها پخش شود."
        >
          {cards.length === 0 && <Note>هیچ کارتی اضافه نشده — از دکمه‌ی پایین اضافه کنید.</Note>}
          {cards.map((c, i) => (
            <div key={i} className="flex flex-col gap-2 rounded-xl border border-border bg-white/[0.02] p-3 sm:flex-row sm:items-end">
              <div className="flex-1">
                <Field label={`شماره کارت ${i + 1}`}>
                  <Input value={c.number} inputMode="numeric" dir="ltr" placeholder="6037-xxxx-xxxx-xxxx" onChange={(e) => setCards((xs) => xs.map((x, j) => (j === i ? { ...x, number: e.target.value } : x)))} />
                </Field>
              </div>
              <div className="flex-1">
                <Field label="به نام">
                  <Input value={c.name} placeholder="نام صاحب کارت" onChange={(e) => setCards((xs) => xs.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
                </Field>
              </div>
              <Button variant="destructive" size="icon" onClick={() => setCards((xs) => xs.filter((_, j) => j !== i))}>
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Button variant="outline" size="sm" disabled={cards.length >= 8} onClick={() => setCards((xs) => [...xs, { number: "", name: "" }])}>
              <Plus className="h-4 w-4" /> افزودن کارت
            </Button>
            <Button size="sm" disabled={saveCards.isPending} onClick={() => saveCards.mutate()}>
              <SaveButton m={saveCards} label="ذخیره کارت‌ها" />
            </Button>
          </div>
        </Section>

        <Section icon={CreditCard} title="پرداخت رمزارزی" desc="آدرس ولت تتر که در ربات به کاربر نمایش داده می‌شود.">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="آدرس تتر (TRC20)">
              <Input value={form.crypto_address ?? ""} dir="ltr" onChange={(e) => set("crypto_address", e.target.value)} />
            </Field>
          </div>
          <div className="flex justify-end">
            <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>
              <SaveButton m={save} label="ذخیره" />
            </Button>
          </div>
        </Section>
      </TabsContent>

      {/* ───────────── پلن‌های فروش ───────────── */}
      <TabsContent value="catalog" className="space-y-5">
        <CatalogTab />
      </TabsContent>

      {/* ───────────── پنل‌ها ───────────── */}
      <TabsContent value="panels" className="space-y-5">
        <PrimaryBackendCard items={items} />

        <Section icon={Server} title="اتصال پنل 3x-ui" desc="اطلاعات اتصال ربات به پنل 3x-ui. پسورد را خالی بگذارید تا تغییر نکند.">
          <div className="grid gap-4 sm:grid-cols-2">
            {PANEL_FIELDS.map(({ key, label, type, hint }) => (
              <Field key={key} label={label} hint={hint}>
                <Input
                  type={type || "text"}
                  dir="ltr"
                  value={form[key] ?? ""}
                  onChange={(e) => set(key, e.target.value)}
                  placeholder={type === "password" ? "بدون تغییر" : ""}
                />
              </Field>
            ))}
          </div>
        </Section>

        <PasarGuardCard items={items} />

        <AthenaCard items={items} />
      </TabsContent>

      {/* ───────────── بکاپ ───────────── */}
      <TabsContent value="backup" className="space-y-5">
        <BackupCard items={items} />
      </TabsContent>

      {/* ───────────── ظاهر پنل ───────────── */}
      <TabsContent value="appearance" className="space-y-5">
        <Section
          icon={MonitorCog}
          title="حالت نمایش پنل مدیریت"
          desc="انتخاب کنید پنل مدیریت با چه ظاهری باز شود. تغییر بلافاصله اعمال می‌شود و نیازی به ری‌استارت نیست."
        >
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
              <p className="mt-1 text-xs text-muted-foreground">همین پنل — سریع، حرفه‌ای و پیشنهادی</p>
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
          <Note tone="warn">
            پنل کلاسیک صفحه‌ی «مدیریت PasarGuard» و کارت‌های جدید تنظیمات را ندارد؛ برای مدیریت PasarGuard روی حالت مدرن بمانید.
          </Note>
        </Section>
      </TabsContent>

      {showStickySave && (
        <div className="sticky bottom-4 flex justify-end">
          <Button size="lg" disabled={save.isPending} onClick={() => save.mutate()}>
            <SaveButton m={save} label={tab === "panels" ? "ذخیره تنظیمات پنل 3x-ui" : "ذخیره تنظیمات فروشگاه"} />
          </Button>
        </div>
      )}
    </Tabs>
  );
}
