import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  LockOpen, Lock, Save, Plus, Trash2, Server, Wifi, WifiOff, Settings2, DatabaseBackup,
  Bot, KeyRound,
} from "lucide-react";
import { api, setCsrf } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Field } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { CatalogTab } from "./settings/CatalogTab";

type Audience = "all" | "user" | "agent";
type Tri = "" | "true" | "false";

const RUNTIME_FIELDS: { key: string; label: string; hint?: string }[] = [
  { key: "support_id", label: "آیدی پشتیبانی", hint: "با @ ، مثل @YourSupport" },
  { key: "price_per_gb", label: "نرخ تمدید هر گیگ (تومان)", hint: "قیمت خرید در «پلن‌های فروش» تعیین می‌شود." },
  { key: "minimum_purchase_gb", label: "حداقل حجم تمدید (گیگ)" },
  { key: "default_agent_price_per_gb", label: "نرخ پیش‌فرض نماینده (هر گیگ)" },
];
const PANEL_FIELDS: { key: string; label: string; type?: string }[] = [
  { key: "panel_base_url", label: "آدرس پنل 3x-ui" },
  { key: "panel_username", label: "یوزرنیم پنل" },
  { key: "panel_password", label: "پسورد پنل", type: "password" },
  { key: "panel_inbound_id", label: "Inbound ID" },
  { key: "sub_link_base", label: "آدرس پایه لینک اشتراک" },
];
// action -> default label (must match async_storefront/handlers.py NAV_ACTIONS)

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

function durationHint(days: string | number): string {
  const n = Number(days) || 0;
  if (n <= 0) return "بدون انقضا";
  const months = n / 30;
  if (months >= 1) return Number.isInteger(months) ? `≈ ${months} ماه` : `≈ ${months.toFixed(1)} ماه`;
  return `${n} روز`;
}

function PrimaryPanelCard({ items }: { items: Record<string, string> }) {
  const qc = useQueryClient();
  const backend = (items.primary_backend ?? "xui") === "pasarguard" ? "pasarguard" : "xui";
  const pgConfigured = (items.pg_enabled ?? "0") === "1" && Boolean((items.pg_base_url ?? "").trim());
  const xuiEnabled = (items.panel_enabled ?? "1") !== "0";
  const setBackend = useMutation({
    mutationFn: (b: "xui" | "pasarguard") => api.setPrimaryBackend(b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
  const Option = ({ id, title, desc }: { id: "xui" | "pasarguard"; title: string; desc: string }) => {
    const active = backend === id;
    return (
      <button
        type="button"
        onClick={() => !active && setBackend.mutate(id)}
        disabled={setBackend.isPending}
        className={`flex flex-col items-start gap-1 rounded-2xl border p-4 text-right transition ${
          active ? "border-brand/60 bg-brand/15 shadow-brand-glow" : "border-border bg-white/[0.02] hover:border-brand/30 hover:bg-white/[0.04]"
        }`}
      >
        <div className="flex w-full items-center justify-between">
          <span className="font-black text-white">{title}</span>
          {active ? <Badge variant="success">پنل اصلی فعلی</Badge> : <span className="text-[11px] text-muted-foreground">انتخاب</span>}
        </div>
        <span className="text-xs text-muted-foreground">{desc}</span>
      </button>
    );
  };
  return (
    <Card className="border-brand/30">
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Server className="h-5 w-5 text-brand" /> پنل اصلیِ فروش</CardTitle>
        <p className="text-sm text-muted-foreground">
          دکمه‌ی «خرید سرویس» در ربات از این پنل می‌فروشد. قیمت‌ها و بسته‌های هر پنل را در کارتِ همان پنل (پایین) تعیین کنید.
          پنل دوم به‌صورت یک دکمه‌ی خریدِ جداگانه باقی می‌ماند.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Option id="xui" title="۳x-ui (پنل اصلی)" desc="فروش از پنل 3x-ui؛ بسته‌ها/قیمت در کارت «پنل اصلی 3x-ui»." />
          <Option id="pasarguard" title="PasarGuard" desc="فروش از پنل PasarGuard؛ بسته‌ها/قیمت در کارت «پنل PasarGuard»." />
        </div>
        {backend === "pasarguard" && !pgConfigured && (
          <div className="rounded-xl border border-amber-400/30 bg-amber-400/5 px-3 py-2 text-xs text-amber-200">
            ⚠️ PasarGuard به‌عنوان پنل اصلی انتخاب شده اما هنوز پیکربندی/فعال نشده است؛ تا تکمیلِ کارتِ PasarGuard، دکمه‌ی خرید نمایش داده نمی‌شود.
          </div>
        )}
        {backend === "xui" && !xuiEnabled && (
          <div className="rounded-xl border border-amber-400/30 bg-amber-400/5 px-3 py-2 text-xs text-amber-200">
            ⚠️ پنل 3x-ui به‌عنوان پنل اصلی انتخاب شده اما در کارتِ «پنل اصلی 3x-ui» غیرفعال است؛ دکمه‌ی خرید نمایش داده نمی‌شود.
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function PasarGuardCard({ items }: { items: Record<string, string> }) {
  const qc = useQueryClient();
  const [f, setF] = React.useState({
    enabled: (items.pg_enabled ?? "0") === "1",
    label: items.pg_label || "سرور اختصاصی",
    base_url: items.pg_base_url ?? "",
    username: items.pg_username ?? "",
    password: "",
    group: items.pg_group || "Tsco-Bot",
    verify_tls: (items.pg_verify_tls ?? "1") !== "0",
    default_days: items.pg_default_days ?? "30",
  });
  const set = (k: keyof typeof f, v: unknown) => setF((s) => ({ ...s, [k]: v }));

  const save = useMutation({
    mutationFn: () => api.setPasarGuard({ ...f, default_days: Number(f.default_days) || 30 }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
  const [test, setTest] = React.useState<{ ok: boolean; msg: string } | null>(null);
  const doTest = useMutation({
    mutationFn: () => api.testPasarGuard({ base_url: f.base_url, username: f.username, password: f.password || undefined, verify_tls: f.verify_tls }),
    onSuccess: (r) =>
      setTest(
        r.ok
          ? { ok: true, msg: `اتصال موفق — ادمین: ${r.admin_username ?? "?"}${r.panel_version ? ` · نسخه ${r.panel_version}` : ""}${r.groups?.length ? ` · گروه‌ها: ${r.groups.map((g) => g.name).join("، ")}` : ""}` }
          : { ok: false, msg: r.error || "اتصال ناموفق" },
      ),
    onError: (e: Error) => setTest({ ok: false, msg: e.message }),
  });

  return (
    <Card className="border-brand/20">
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Server className="h-5 w-5 text-brand" /> پنل PasarGuard</CardTitle>
        <p className="text-sm text-muted-foreground">
          اتصال به یک پنل PasarGuard و فروش از آن با همان سیستم بسته‌ها. قیمت‌گذاری این پنل از طریق «بسته‌های این سرور» در پایین تعیین می‌شود. برای حفظ پسورد فعلی، فیلد پسورد را خالی بگذارید.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-white/[0.02] p-4">
          <div className="flex items-center gap-3">
            <span className="font-bold text-white">وضعیت پنل PasarGuard</span>
            <Badge variant={f.enabled ? "success" : "danger"}>{f.enabled ? "فعال" : "غیرفعال"}</Badge>
          </div>
          <div className="flex gap-2">
            <Button size="sm" disabled={f.enabled} onClick={() => set("enabled", true)}><LockOpen className="h-4 w-4" /> فعال</Button>
            <Button size="sm" variant="destructive" disabled={!f.enabled} onClick={() => set("enabled", false)}><Lock className="h-4 w-4" /> غیرفعال</Button>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="نام دکمه‌ی خرید" hint="در منوی ربات نمایش داده می‌شود"><Input value={f.label} onChange={(e) => set("label", e.target.value)} placeholder="سرور اختصاصی" /></Field>
          <Field label="گروه (group) پنل" hint="کاربر در این گروه ساخته می‌شود"><Input value={f.group} onChange={(e) => set("group", e.target.value)} placeholder="Tsco-Bot" /></Field>
          <Field label="آدرس پنل (با https و پورت)"><Input value={f.base_url} onChange={(e) => set("base_url", e.target.value)} placeholder="https://panel.example.com:8000" dir="ltr" /></Field>
          <Field label="یوزرنیم ادمین ربات"><Input value={f.username} onChange={(e) => set("username", e.target.value)} dir="ltr" /></Field>
          <Field label="پسورد ادمین ربات"><Input type="password" value={f.password} onChange={(e) => set("password", e.target.value)} placeholder="بدون تغییر" dir="ltr" /></Field>
          <Field label="مدت اعتبار پیش‌فرض (روز)" hint="۰ = بدون انقضا"><Input value={f.default_days} inputMode="numeric" onChange={(e) => set("default_days", e.target.value)} /></Field>
        </div>

        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <input type="checkbox" checked={f.verify_tls} onChange={(e) => set("verify_tls", e.target.checked)} className="h-4 w-4 accent-[hsl(var(--brand))]" />
          بررسی گواهی TLS (اگر گواهی معتبر دارید روشن بماند)
        </label>

        {test && (
          <div className={`rounded-xl border p-3 text-sm ${test.ok ? "border-emerald-400/30 bg-emerald-500/5 text-emerald-200" : "border-rose-400/30 bg-rose-500/5 text-rose-200"}`}>
            {test.ok ? "✅ " : "❌ "}{test.msg}
          </div>
        )}

        <div className="flex flex-wrap justify-end gap-2">
          <Button size="sm" variant="outline" disabled={doTest.isPending} onClick={() => doTest.mutate()}>{doTest.isPending ? "در حال تست…" : "تست اتصال"}</Button>
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
  const [f, setF] = React.useState(() => ({
    enabled: (items.backup_enabled ?? "1") === "1",
    bot: (items.backup_include_bot ?? "1") === "1",
    xui: (items.backup_include_xui ?? "0") === "1",
    pg: (items.backup_include_pg ?? "0") === "1",
    interval_value: items.backup_interval_value || "60",
    interval_unit: items.backup_interval_unit || "minutes",
    chat_id: items.backup_telegram_chat_id ?? "",
    token: "",
    pg_mode: items.pg_backup_mode || "auto",
    pg_compose: items.pg_backup_compose_file || "/opt/pasarguard/docker-compose.yml",
    pg_max_age: items.pg_backup_max_age_minutes || "360",
  }));
  const set = (k: keyof typeof f, v: unknown) => setF((s) => ({ ...s, [k]: v }));
  const tokenSet = (items.backup_bot_token_set ?? "0") === "1";

  const save = useMutation({
    mutationFn: () => {
      const payload: Record<string, string> = {
        backup_enabled: f.enabled ? "on" : "off",
        backup_include_bot: f.bot ? "on" : "off",
        backup_include_xui: f.xui ? "on" : "off",
        backup_include_pg: f.pg ? "on" : "off",
        backup_interval_value: String(Math.max(1, Number(f.interval_value) || 1)),
        backup_interval_unit: f.interval_unit,
        backup_telegram_chat_id: f.chat_id.trim(),
        backup_xui_timeout_seconds: items.backup_xui_timeout_seconds ?? "180",
        pg_backup_mode: f.pg_mode,
        pg_backup_compose_file: f.pg_compose.trim(),
        pg_backup_max_age_minutes: String(Math.max(0, Number(f.pg_max_age) || 0)),
      };
      // Blank token means "keep the current one" — never send an empty string,
      // the server would otherwise have to guess.
      if (f.token.trim()) payload.backup_bot_token = f.token.trim();
      return api.updateSettings(payload);
    },
    onSuccess: () => { setF((s) => ({ ...s, token: "" })); qc.invalidateQueries({ queryKey: ["settings"] }); },
  });
  const run = useMutation({ mutationFn: () => api.runBackup(), onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }) });

  const STATUS: Record<string, { label: string; variant: "success" | "warning" | "danger" | "muted" }> = {
    ok: { label: "آخرین بکاپ ارسال شد", variant: "success" },
    partial: { label: "ارسال شد ولی با هشدار", variant: "warning" },
    local: { label: "فقط روی سرور ماند (ارسال نشد)", variant: "warning" },
    failed: { label: "آخرین بکاپ ناموفق", variant: "danger" },
    running: { label: "در حال اجرا", variant: "muted" },
    never: { label: "هنوز بکاپی گرفته نشده", variant: "muted" },
  };
  const st = STATUS[items.backup_last_status || "never"] ?? STATUS.never;
  const pgStatus = items.backup_last_pg_status || "off";
  const pgMb = Math.round(Number(items.backup_last_pg_db_bytes || 0) / (1024 * 1024) * 10) / 10;
  const r = run.data;

  const Check = ({ k, label, hint }: { k: keyof typeof f; label: string; hint?: string }) => (
    <label className="flex items-start gap-2 rounded-xl border border-border bg-white/[0.02] p-3 text-sm">
      <input type="checkbox" checked={Boolean(f[k])} onChange={(e) => set(k, e.target.checked)} className="mt-0.5 h-4 w-4 accent-[hsl(var(--brand))]" />
      <span><span className="font-bold text-white">{label}</span>{hint && <span className="block text-[11px] text-muted-foreground">{hint}</span>}</span>
    </label>
  );

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2"><DatabaseBackup className="h-5 w-5 text-muted-foreground" /> بکاپ‌گیری خودکار</CardTitle>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              بکاپ ساخته و به کانال تلگرام ارسال می‌شود. اگر آیدی چت خالی باشد، فایل فقط روی همین سرور می‌ماند — یعنی اگر سرور از دست برود بکاپی در کار نیست.
            </p>
          </div>
          <Badge variant={st.variant}>{st.label}</Badge>
        </div>
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

        <div>
          <p className="mb-2 text-xs font-bold text-muted-foreground">چه چیزهایی بکاپ گرفته شود</p>
          <div className="grid gap-3 sm:grid-cols-3">
            <Check k="bot" label="دیتابیس کامل ربات" hint="کاربران، کیف پول، سفارش‌ها، نماینده‌ها، تنظیمات" />
            <Check k="pg" label="پنل PasarGuard" hint="قابل بازگردانی با pasarguard restore" />
            <Check k="xui" label="پنل x-ui" hint="فقط اگر هنوز از 3x-ui استفاده می‌کنید" />
          </div>
        </div>

        {f.pg && (
          <div className="space-y-3 rounded-xl border border-brand/25 bg-brand/[0.04] p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm font-bold text-white">تنظیمات بکاپ PasarGuard</span>
              {pgStatus === "ok" ? (
                <Badge variant="success">آخرین بکاپ پنل موفق{pgMb > 0 ? ` — ${pgMb} MB` : ""}</Badge>
              ) : pgStatus === "failed" ? (
                <Badge variant="danger">آخرین بکاپ پنل ناموفق</Badge>
              ) : null}
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="روش تهیه بکاپ" hint="خودکار: اول دستور رسمی پنل، در نبود آن pg_dump مستقیم.">
                <select value={f.pg_mode} onChange={(e) => set("pg_mode", e.target.value)}
                  className="h-10 w-full rounded-xl border border-input bg-card px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  <option value="auto">خودکار (پیشنهادی)</option>
                  <option value="cli">فقط دستور رسمی pasarguard</option>
                  <option value="native">فقط pg_dump مستقیم</option>
                </select>
              </Field>
              <Field label="عمر مجاز آرشیو آماده (دقیقه)" hint="اگر پنل تازه بکاپ گرفته باشد دوباره دامپ نمی‌گیریم. ۰ = همیشه تازه.">
                <Input value={f.pg_max_age} inputMode="numeric" onChange={(e) => set("pg_max_age", e.target.value)} />
              </Field>
              <div className="sm:col-span-2">
                <Field label="مسیر docker-compose پنل" hint="فقط اگر پنل را جای دیگری نصب کرده‌اید تغییر دهید.">
                  <Input value={f.pg_compose} dir="ltr" onChange={(e) => set("pg_compose", e.target.value)} />
                </Field>
              </div>
            </div>
            <div className="rounded-lg border border-emerald-400/25 bg-emerald-400/5 p-3 text-xs leading-6 text-emerald-100">
              آرشیو پنل جدا از بکاپ ربات ارسال می‌شود و دستور بازگردانی داخل کپشن همان پیام است:
              <code className="mt-1 block text-emerald-200" dir="ltr">pasarguard restore &lt;file&gt;</code>
            </div>
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="فاصله‌ی بکاپ"><Input value={f.interval_value} inputMode="numeric" onChange={(e) => set("interval_value", e.target.value)} /></Field>
          <Field label="واحد">
            <select value={f.interval_unit} onChange={(e) => set("interval_unit", e.target.value)}
              className="h-10 w-full rounded-xl border border-input bg-card px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <option value="minutes">دقیقه</option>
              <option value="hours">ساعت</option>
              <option value="days">روز</option>
              <option value="weeks">هفته</option>
            </select>
          </Field>
          <Field label="آیدی چت/کانال مقصد" hint="ربات باید در کانال ادمین باشد."><Input value={f.chat_id} dir="ltr" placeholder="-100..." onChange={(e) => set("chat_id", e.target.value)} /></Field>
        </div>

        <Field label="توکن ربات بکاپ" hint={tokenSet ? "تنظیم شده — خالی بگذارید تا تغییر نکند." : "خالی = همان ربات فروشگاه. می‌توانید ربات جدا بسازید."}>
          <Input type="password" value={f.token} dir="ltr" placeholder={tokenSet ? "بدون تغییر" : "خالی = ربات فروشگاه"} onChange={(e) => set("token", e.target.value)} />
        </Field>

        {items.backup_last_error && (items.backup_last_status || "") !== "ok" && (
          <div className="rounded-xl border border-amber-400/30 bg-amber-400/5 p-3 text-xs leading-6 text-amber-200">
            آخرین پیام: <span className="font-mono">{items.backup_last_error.slice(0, 300)}</span>
          </div>
        )}

        {r && (
          <div className={`rounded-xl border p-3 text-xs leading-6 ${r.ok ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-100" : "border-rose-400/30 bg-rose-400/10 text-rose-100"}`}>
            {r.ok ? (
              <>
                ✅ بکاپ ساخته شد ({r.mode}) — {r.delivered ? "به تلگرام ارسال شد" : "ارسال نشد، روی سرور ماند"}
                {r.pg?.included && (
                  <span className="block">
                    🛡 پنل PasarGuard: {r.pg.restorable ? "قابل بازگردانی ✓" : "قابل بازگردانی نیست ✗"} — دیتابیس {r.pg.db_mb} مگابایت ({r.pg.mode})
                  </span>
                )}
                {r.errors?.length ? <span className="block">⚠️ {r.errors.join(" | ").slice(0, 200)}</span> : null}
              </>
            ) : (
              <>❌ {r.error}</>
            )}
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-2">
          <Button variant="outline" size="sm" disabled={run.isPending} onClick={() => run.mutate()}>
            <DatabaseBackup className="h-4 w-4" /> {run.isPending ? "در حال گرفتن بکاپ…" : "بکاپ فوری"}
          </Button>
          <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>
            <Save className="h-4 w-4" /> {save.isPending ? "در حال ذخیره…" : save.isSuccess ? "ذخیره شد ✓" : "ذخیره تنظیمات بکاپ"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/** The bot's own connection. Separate from the shop because it is the one
 *  section that decides whether the bot runs at all. */
function BotCard({ items }: { items: Record<string, string> }) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [f, setF] = React.useState(() => ({
    bot_token: items.bot_token ?? "",
    bot_username: items.bot_username ?? "",
    admin_user_ids: items.admin_user_ids ?? "",
    proxy_url: items.proxy_url ?? "",
    proxy_enabled: (items.proxy_enabled ?? "") === "1",
  }));
  const set = (k: keyof typeof f, v: unknown) => setF((s) => ({ ...s, [k]: v }));

  const save = useMutation({
    mutationFn: () => api.updateSettings({
      bot_token: f.bot_token.trim(),
      bot_username: f.bot_username.trim().replace(/^@/, ""),
      admin_user_ids: f.admin_user_ids,
      proxy_url: f.proxy_url.trim(),
      proxy_enabled: f.proxy_enabled ? "1" : "0",
    }),
    onSuccess: () => {
      toast({
        title: "تنظیمات ربات ذخیره شد",
        description: "برای اعمال توکن یا پروکسی جدید، سرویس ربات باید یک بار ری‌استارت شود.",
        variant: "success",
      });
      qc.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (e: Error) => toast({ title: "ذخیره نشد", description: e.message, variant: "error" }),
  });

  const hasToken = Boolean(f.bot_token.trim());
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2"><Bot className="h-5 w-5 text-muted-foreground" /> اتصال ربات</CardTitle>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              توکن از BotFather. تا وقتی توکن ثبت نشده باشد، سرویس ربات منتظر می‌ماند و
              به‌محض ذخیره‌شدن خودش شروع می‌کند.
            </p>
          </div>
          <Badge variant={hasToken ? "success" : "danger"}>{hasToken ? "توکن ثبت شده" : "بدون توکن"}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field label="توکن ربات" hint="مثل 123456789:AAH… — از @BotFather">
          <Input type="password" value={f.bot_token} dir="ltr"
                 onChange={(e) => set("bot_token", e.target.value)} placeholder="123456789:AAH…" />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="یوزرنیم ربات" hint="بدون @ — فقط برای نمایش">
            <Input value={f.bot_username} dir="ltr"
                   onChange={(e) => set("bot_username", e.target.value)} placeholder="MyShopBot" />
          </Field>
          <Field label="ادمین‌های ربات" hint="آیدی عددی تلگرام، جدا با کاما. تنها منبع تعیین ادمین.">
            <Input value={f.admin_user_ids} dir="ltr"
                   onChange={(e) => set("admin_user_ids", e.target.value)} placeholder="123456789,987654321" />
          </Field>
        </div>
        <div className="space-y-3 rounded-xl border border-border bg-white/[0.02] p-4">
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" checked={f.proxy_enabled}
                   onChange={(e) => set("proxy_enabled", e.target.checked)}
                   className="mt-0.5 h-4 w-4 accent-[hsl(var(--brand))]" />
            <span>
              <span className="font-bold text-white">اتصال از طریق پروکسی</span>
              <span className="block text-[11px] leading-5 text-muted-foreground">
                فقط اگر سرور به تلگرام دسترسی مستقیم ندارد. آدرس با خاموش‌کردن پاک نمی‌شود.
              </span>
            </span>
          </label>
          <Field label="آدرس پروکسی">
            <Input value={f.proxy_url} dir="ltr" placeholder="socks5h://user:pass@127.0.0.1:1080"
                   onChange={(e) => set("proxy_url", e.target.value)} />
          </Field>
        </div>
        <div className="flex justify-end">
          <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>
            <Save className="h-4 w-4" /> {save.isPending ? "در حال ذخیره…" : "ذخیره تنظیمات ربات"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/** The panel's own account. Kept apart from everything else because getting it
 *  wrong locks the operator out of the thing they are editing. */
function AccountCard() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data } = useQuery({ queryKey: ["account"], queryFn: () => api.account() });
  const [username, setUsername] = React.useState<string | null>(null);
  const [current, setCurrent] = React.useState("");
  const [next, setNext] = React.useState("");
  const [confirm, setConfirm] = React.useState("");

  const save = useMutation({
    mutationFn: () => api.saveAccount({
      username: username ?? data?.username ?? "",
      current_password: current,
      new_password: next,
    }),
    onSuccess: (r) => {
      if (r.csrf) setCsrf(r.csrf);
      toast({ title: "حساب پنل به‌روزرسانی شد", variant: "success" });
      setCurrent(""); setNext(""); setConfirm(""); setUsername(null);
      qc.invalidateQueries({ queryKey: ["account"] });
    },
    onError: (e: Error) => toast({ title: "تغییر نکرد", description: e.message, variant: "error" }),
  });

  const saveSession = useMutation({
    mutationFn: (hours: number) => api.saveAccount({ session_hours: hours }),
    onSuccess: () => {
      toast({ title: "مدت نشست ذخیره شد", variant: "success" });
      qc.invalidateQueries({ queryKey: ["account"] });
    },
    onError: (e: Error) => toast({ title: "ذخیره نشد", description: e.message, variant: "error" }),
  });

  if (!data) return <Skeleton className="h-64" />;
  const mismatch = Boolean(next) && Boolean(confirm) && next !== confirm;
  const canSave = Boolean(current) && !mismatch &&
    (Boolean(next) || (username !== null && username !== data.username));

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><KeyRound className="h-5 w-5 text-muted-foreground" /> حساب مدیر پنل</CardTitle>
          <p className="text-sm leading-6 text-muted-foreground">
            رمز به‌صورت هش ذخیره می‌شود و در هیچ فایلی نوشته نمی‌شود. برای تغییر نام کاربری
            یا رمز، رمز فعلی لازم است — نشستِ باز به‌تنهایی کافی نیست.
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="نام کاربری">
              <Input value={username ?? data.username} dir="ltr"
                     onChange={(e) => setUsername(e.target.value)} />
            </Field>
            <Field label="رمز فعلی" hint="برای تایید هویت لازم است.">
              <Input type="password" value={current} dir="ltr"
                     onChange={(e) => setCurrent(e.target.value)} />
            </Field>
            <Field label="رمز جدید" hint={`حداقل ${data.min_password_length} کاراکتر. خالی = بدون تغییر.`}>
              <Input type="password" value={next} dir="ltr"
                     onChange={(e) => setNext(e.target.value)} />
            </Field>
            <Field label="تکرار رمز جدید">
              <Input type="password" value={confirm} dir="ltr"
                     onChange={(e) => setConfirm(e.target.value)} />
            </Field>
          </div>
          {mismatch && (
            <div className="rounded-xl border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              رمز جدید و تکرارش یکی نیستند.
            </div>
          )}
          <div className="flex justify-end">
            <Button size="sm" disabled={!canSave || save.isPending} onClick={() => save.mutate()}>
              <Save className="h-4 w-4" /> {save.isPending ? "در حال ذخیره…" : "ذخیره حساب"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-sm">نشست</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <Field label="مدت اعتبار نشست (ساعت)" hint="بعد از این مدت باید دوباره وارد شوید.">
            <div className="flex gap-2">
              <Input defaultValue={String(data.session_hours)} inputMode="decimal" dir="ltr"
                     id="session-hours" className="max-w-40" />
              <Button size="sm" variant="outline" disabled={saveSession.isPending}
                      onClick={() => {
                        const el = document.getElementById("session-hours") as HTMLInputElement | null;
                        saveSession.mutate(Number(el?.value) || data.session_hours);
                      }}>
                ذخیره
              </Button>
            </div>
          </Field>
        </CardContent>
      </Card>
    </div>
  );
}

export function Settings() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["settings"], queryFn: () => api.settings() });
  const items = data?.items ?? {};
  const master = items.sales_status ?? "open";

  const [tab, setTab] = React.useState("general");
  const [form, setForm] = React.useState<Record<string, string>>({});
  const [cards, setCards] = React.useState<{ number: string; name: string }[]>([]);
  const [p1Enabled, setP1Enabled] = React.useState(true);
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
      setP1Enabled((items.panel_enabled ?? "1") !== "0");
      inited.current = true;
    }
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  const saveCards = useMutation({ mutationFn: () => api.setPaymentCards(cards.filter((c) => c.number.trim())), onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }) });
  const save = useMutation({ mutationFn: () => api.updateSettings(form), onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }) });
  const savePanelPrimary = useMutation({
    mutationFn: (enabled: boolean) => api.setPanelPrimary(enabled),
    onSuccess: (_r, enabled) => { setP1Enabled(enabled); qc.invalidateQueries({ queryKey: ["settings"] }); },
  });
  const toggle = useMutation({ mutationFn: ({ a, s }: { a: Audience; s: "open" | "closed" }) => api.setSales(a, s), onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }) });
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  if (isLoading || !data) return <div className="space-y-5"><Skeleton className="h-12 w-72" /><Skeleton className="h-72" /></div>;

  const TABS = [
    { v: "bot", label: "ربات" },
    { v: "general", label: "فروشگاه" },
    { v: "sales", label: "فروش" },
    { v: "payment", label: "پرداخت" },
    { v: "catalog", label: "پلن‌های فروش" },
    { v: "panels", label: "پنل‌ها" },
    { v: "backup", label: "بکاپ" },
    { v: "account", label: "حساب پنل" },
  ];

  return (
    <Tabs value={tab} onValueChange={setTab} className="space-y-6">
      <div className="sticky top-16 z-20 -mx-1 overflow-x-auto pb-1">
        <TabsList className="w-full justify-start">
          {TABS.map((t) => (
            <TabsTrigger key={t.v} value={t.v} className="px-4 py-2 text-sm">{t.label}</TabsTrigger>
          ))}
        </TabsList>
      </div>

      {/* ───────────── General ───────────── */}
      <TabsContent value="bot" className="space-y-6">
        <BotCard items={items} />
      </TabsContent>

      <TabsContent value="account" className="space-y-6">
        <AccountCard />
      </TabsContent>

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
            <CardTitle>قیمت‌گذاری کجاست؟</CardTitle>
            <p className="text-sm text-muted-foreground">
              همه‌ی قیمت‌ها و بسته‌ها (حجمی و نامحدود) برای هر سرور، در تب <b>«پنل‌ها»</b> و زیر همان سرور تنظیم می‌شوند. هر سرور بسته‌های مخصوص خودش را دارد و کاملاً قابل سفارشی‌سازی است.
            </p>
          </CardHeader>
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

      <TabsContent value="panels" className="space-y-6">
        <PrimaryPanelCard items={items} />
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
          </CardContent>
        </Card>


        <PasarGuardCard items={items} />
      </TabsContent>

      {/* ───────────── Sales catalog ───────────── */}
      <TabsContent value="catalog" className="space-y-6">
        <CatalogTab />
      </TabsContent>

      {/* ───────────── Backup ───────────── */}
      <TabsContent value="backup" className="space-y-6">
        <BackupCard items={items} />
      </TabsContent>

      {/* The backup tab has its own save button; this bar saves the shop/panel
          fields, so showing it there would just be confusing. */}
      {tab !== "backup" && tab !== "catalog" && (
        <div className="sticky bottom-4 flex justify-end">
          <Button size="lg" disabled={save.isPending} onClick={() => save.mutate()}>
            <Save className="h-4 w-4" /> {save.isPending ? "در حال ذخیره…" : save.isSuccess ? "ذخیره شد ✓" : "ذخیره تنظیمات فروشگاه و پنل"}
          </Button>
        </div>
      )}
    </Tabs>
  );
}
