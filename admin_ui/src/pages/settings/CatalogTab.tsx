import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Plus, Trash2, Save, Layers, Tag, AlertTriangle, GripVertical, Eye, EyeOff,
  Server, ShieldCheck, ChevronDown, ChevronUp, Copy, Info,
} from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import type {
  Category, Plan, PlanPricing, PlanTarget, PricingMode, VolumeMode,
} from "@/lib/types";

const rid = (p: string) => `${p}_${Math.random().toString(16).slice(2, 10)}`;
const n = (v: unknown) => Math.max(0, Number(v) || 0);
const money = (v: number) => v.toLocaleString("en-US");

/** Mirrors async_storefront/catalog.py:price_for so the editor previews the
 *  exact number the bot will charge. Kept deliberately small and pure. */
function priceFor(plan: Plan, gb: number | null, agent: boolean): number {
  const p = plan.pricing;
  const eff = gb ?? n(plan.volume.gb);
  let total = 0;
  if (p.mode === "fixed") {
    total = n(p.price);
    if (agent && n(p.agent_price) > 0) total = n(p.agent_price);
  } else if (p.mode === "linear") {
    let base = n(p.base), per = n(p.per_gb);
    if (agent) {
      if (n(p.agent_base) > 0) base = n(p.agent_base);
      if (n(p.agent_per_gb) > 0) per = n(p.agent_per_gb);
    }
    total = base + per * eff;
  } else {
    const tiers = [...(p.tiers || [])].sort((a, b) => n(a.min_gb) - n(b.min_gb));
    let rate = 0;
    for (const t of tiers) {
      if (eff >= n(t.min_gb)) {
        rate = agent && n(t.agent_price_per_gb) > 0 ? n(t.agent_price_per_gb) : n(t.price_per_gb);
      } else break;
    }
    let base = n(p.base);
    if (agent && n(p.agent_base) > 0) base = n(p.agent_base);
    total = base + rate * eff;
  }
  const r = n(p.round_to);
  if (r > 1 && total > 0) total = Math.round(total / r) * r;
  return Math.max(0, Math.round(total));
}

function gbChoices(plan: Plan, limit = 12): number[] {
  if (plan.volume.mode !== "variable") return [];
  let step = n(plan.volume.step_gb) || n(plan.volume.min_gb) || 1;
  if (step <= 0) step = 1;
  const low = n(plan.volume.min_gb) || step;
  const high = n(plan.volume.max_gb);
  const out: number[] = [];
  for (let v = low; out.length < limit; v += step) {
    if (high && v > high) break;
    out.push(v);
  }
  return out;
}

function emptyPricing(): PlanPricing {
  return { mode: "fixed", price: 0, agent_price: 0, base: 0, agent_base: 0, per_gb: 0, agent_per_gb: 0, tiers: [], round_to: 0 };
}

function newPlan(categoryId: string, sort: number): Plan {
  return {
    id: rid("plan"), category_id: categoryId, title: "", enabled: true, sort,
    target: { kind: "pasarguard", group: "" },
    volume: { mode: "fixed", gb: 0, days: 30, min_gb: 0, max_gb: 0, step_gb: 0 },
    display: { volume_label: "", note: "", badge: "" },
    pricing: emptyPricing(),
  };
}

const PRICING_LABEL: Record<PricingMode, string> = {
  fixed: "قیمت مقطوع",
  linear: "پایه + هر گیگ",
  tiered: "پلکانی",
};
const PRICING_HINT: Record<PricingMode, string> = {
  fixed: "یک قیمت ثابت برای کل پلن.",
  linear: "قیمت = پایه + (حجم × نرخ هر گیگ).",
  tiered: "نرخ هر گیگ از بازه‌ای که حجم در آن می‌افتد؛ می‌توانید قیمت پایه هم اضافه کنید.",
};

function Segmented<T extends string>({
  value, onChange, options,
}: { value: T; onChange: (v: T) => void; options: { v: T; label: string }[] }) {
  return (
    <div className="inline-flex rounded-xl border border-border bg-white/[0.02] p-1">
      {options.map((o) => (
        <button
          key={o.v}
          type="button"
          onClick={() => onChange(o.v)}
          className={`rounded-lg px-3 py-1.5 text-xs font-bold transition ${
            value === o.v ? "bg-brand text-white shadow-sm" : "text-muted-foreground hover:text-white"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function NumberField({ label, hint, value, onChange, suffix }: {
  label: string; hint?: string; value: number | string; onChange: (v: number) => void; suffix?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <div className="relative">
        <Input
          value={String(value ?? "")}
          inputMode="numeric"
          onChange={(e) => onChange(n(e.target.value))}
          className={suffix ? "pl-14" : undefined}
        />
        {suffix && (
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[11px] text-muted-foreground">
            {suffix}
          </span>
        )}
      </div>
    </Field>
  );
}

// ─────────────────────────── plan card ───────────────────────────

function PlanEditor({
  plan, groups, panels, problems, onChange, onRemove, onMove, categories,
}: {
  plan: Plan;
  groups: { id: number; name: string }[];
  panels: { key: string; label: string }[];
  problems: string[];
  onChange: (p: Plan) => void;
  onRemove: () => void;
  onMove: (dir: -1 | 1) => void;
  categories: Category[];
}) {
  const [open, setOpen] = React.useState(!plan.title);
  const set = (patch: Partial<Plan>) => onChange({ ...plan, ...patch });
  const setVolume = (patch: Partial<Plan["volume"]>) => set({ volume: { ...plan.volume, ...patch } });
  const setPricing = (patch: Partial<PlanPricing>) => set({ pricing: { ...plan.pricing, ...patch } });
  const setDisplay = (patch: Partial<Plan["display"]>) => set({ display: { ...plan.display, ...patch } });

  const variable = plan.volume.mode === "variable";
  const previewGb = variable ? (gbChoices(plan)[0] ?? 0) : n(plan.volume.gb);
  const userPrice = priceFor(plan, variable ? previewGb : null, false);
  const agentPrice = priceFor(plan, variable ? previewGb : null, true);
  const shownVolume = plan.display.volume_label.trim()
    || (previewGb > 0 ? `${previewGb} گیگ` : "نامحدود");
  const masked = Boolean(plan.display.volume_label.trim()) && previewGb > 0;

  return (
    <div className={`rounded-2xl border p-4 transition ${problems.length ? "border-amber-400/40 bg-amber-400/[0.03]" : "border-border bg-white/[0.02]"}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <button type="button" onClick={() => setOpen((v) => !v)} className="flex min-w-0 flex-1 items-center gap-2 text-right">
          <GripVertical className="h-4 w-4 shrink-0 text-muted-foreground" />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              {plan.display.badge && <span className="text-sm">{plan.display.badge}</span>}
              <b className="text-white">{plan.title || "پلن بدون نام"}</b>
              {!plan.enabled && <Badge variant="muted">غیرفعال</Badge>}
              {problems.length > 0 && (
                <Badge variant="warning" className="gap-1">
                  <AlertTriangle className="h-3 w-3" /> نیازمند اصلاح
                </Badge>
              )}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
              <span>{shownVolume}{masked && <span className="text-amber-300/80"> (واقعی: {previewGb} گیگ)</span>}</span>
              <span>·</span>
              <span>{n(plan.volume.days) > 0 ? `${plan.volume.days} روز` : "بدون انقضا"}</span>
              <span>·</span>
              <span className="text-white">{variable ? "از " : ""}{money(userPrice)} ت</span>
              <span className="text-brand">نماینده {money(agentPrice)} ت</span>
              <span>·</span>
              <span className="inline-flex items-center gap-1">
                {plan.target.kind === "pasarguard"
                  ? <><ShieldCheck className="h-3 w-3" /> {plan.target.group || "بدون گروه"}</>
                  : <><Server className="h-3 w-3" /> {panels.find((p) => p.key === (plan.target as { panel: string }).panel)?.label || "3x-ui"}</>}
              </span>
            </div>
          </div>
          {open ? <ChevronUp className="h-4 w-4 shrink-0" /> : <ChevronDown className="h-4 w-4 shrink-0" />}
        </button>
        <div className="flex items-center gap-1">
          <Button size="icon" variant="ghost" title="بالا" onClick={() => onMove(-1)}><ChevronUp className="h-4 w-4" /></Button>
          <Button size="icon" variant="ghost" title="پایین" onClick={() => onMove(1)}><ChevronDown className="h-4 w-4" /></Button>
          <Button size="icon" variant="ghost" title={plan.enabled ? "غیرفعال کن" : "فعال کن"} onClick={() => set({ enabled: !plan.enabled })}>
            {plan.enabled ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
          </Button>
          <Button size="icon" variant="destructive" title="حذف" onClick={onRemove}><Trash2 className="h-4 w-4" /></Button>
        </div>
      </div>

      {problems.length > 0 && (
        <div className="mt-3 rounded-xl border border-amber-400/30 bg-amber-400/5 p-3 text-xs leading-6 text-amber-200">
          {problems.map((p, i) => <div key={i}>• {p}</div>)}
        </div>
      )}

      {open && (
        <div className="mt-4 space-y-5 border-t border-border pt-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="نام پلن" hint="همان چیزی که کاربر روی دکمه می‌بیند.">
              <Input value={plan.title} onChange={(e) => set({ title: e.target.value })} placeholder="مثلاً: ۵۰ گیگ ماهیانه" />
            </Field>
            <Field label="دسته" hint="پلن در این دسته از منوی ربات نمایش داده می‌شود.">
              <select
                value={plan.category_id}
                onChange={(e) => set({ category_id: e.target.value })}
                className="h-10 w-full rounded-xl border border-input bg-card px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {categories.map((c) => <option key={c.id} value={c.id}>{`${c.emoji || ""} ${c.title}`.trim()}</option>)}
              </select>
            </Field>
          </div>

          {/* ── target: technical, deliberately separate from pricing ── */}
          <section className="space-y-3 rounded-xl border border-border bg-black/20 p-4">
            <div className="flex items-center gap-2 text-sm font-bold text-white">
              <Server className="h-4 w-4 text-muted-foreground" /> این پلن روی کدام سرور ساخته شود
            </div>
            <Segmented
              value={plan.target.kind}
              onChange={(kind) => set({ target: kind === "pasarguard" ? { kind, group: "" } : { kind, panel: "1" } } as { target: PlanTarget })}
              options={[{ v: "pasarguard" as const, label: "PasarGuard" }, { v: "xui" as const, label: "3x-ui" }]}
            />
            {plan.target.kind === "pasarguard" ? (
              <Field label="گروه پنل" hint="کاربر داخل این گروه ساخته می‌شود. هر پلن می‌تواند گروه خودش را داشته باشد.">
                <select
                  value={plan.target.group}
                  onChange={(e) => set({ target: { kind: "pasarguard", group: e.target.value } })}
                  className="h-10 w-full rounded-xl border border-input bg-card px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <option value="">— انتخاب گروه —</option>
                  {groups.map((g) => <option key={g.id} value={g.name}>{g.name}</option>)}
                  {/* A group configured before it was renamed/removed in the panel would
                      otherwise vanish from the select and silently reset to "no group". */}
                  {plan.target.group && !groups.some((g) => g.name === (plan.target as { group: string }).group) && (
                    <option value={plan.target.group}>{plan.target.group} (در پنل یافت نشد)</option>
                  )}
                </select>
              </Field>
            ) : (
              <Field label="پنل 3x-ui">
                <select
                  value={plan.target.panel}
                  onChange={(e) => set({ target: { kind: "xui", panel: e.target.value } })}
                  className="h-10 w-full rounded-xl border border-input bg-card px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {panels.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
                </select>
              </Field>
            )}
          </section>

          {/* ── what is provisioned ── */}
          <section className="space-y-3 rounded-xl border border-border bg-black/20 p-4">
            <div className="flex items-center gap-2 text-sm font-bold text-white">
              <Layers className="h-4 w-4 text-muted-foreground" /> حجم و مدت (آنچه واقعاً روی پنل اعمال می‌شود)
            </div>
            <Segmented
              value={plan.volume.mode}
              onChange={(mode: VolumeMode) => setVolume({ mode })}
              options={[{ v: "fixed" as const, label: "حجم ثابت" }, { v: "variable" as const, label: "کاربر خودش انتخاب کند" }]}
            />
            {variable ? (
              <div className="grid gap-4 sm:grid-cols-4">
                <NumberField label="از (گیگ)" value={plan.volume.min_gb} onChange={(v) => setVolume({ min_gb: v })} />
                <NumberField label="تا (گیگ)" value={plan.volume.max_gb} onChange={(v) => setVolume({ max_gb: v })} hint="۰ = بی‌نهایت" />
                <NumberField label="گام (گیگ)" value={plan.volume.step_gb} onChange={(v) => setVolume({ step_gb: v })} />
                <NumberField label="مدت (روز)" value={plan.volume.days} onChange={(v) => setVolume({ days: v })} hint="۰ = بدون انقضا" />
              </div>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2">
                <NumberField label="حجم واقعی (گیگ)" value={plan.volume.gb} onChange={(v) => setVolume({ gb: v })} hint="۰ = واقعاً نامحدود روی پنل" />
                <NumberField label="مدت (روز)" value={plan.volume.days} onChange={(v) => setVolume({ days: v })} hint="۰ = بدون انقضا" />
              </div>
            )}
          </section>

          {/* ── what the buyer is told ── */}
          <section className="space-y-3 rounded-xl border border-brand/25 bg-brand/[0.04] p-4">
            <div className="flex items-center gap-2 text-sm font-bold text-white">
              <Eye className="h-4 w-4 text-brand" /> آنچه کاربر می‌بیند
            </div>
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="برچسب حجم" hint='خالی = همان حجم واقعی. برای «مصرف منصفانه» بنویسید: نامحدود'>
                <Input value={plan.display.volume_label} onChange={(e) => setDisplay({ volume_label: e.target.value })} placeholder="خالی = حجم واقعی" />
              </Field>
              <Field label="نشان (اختیاری)" hint="یک ایموجی کنار نام پلن، مثل ⭐">
                <Input value={plan.display.badge} onChange={(e) => setDisplay({ badge: e.target.value })} placeholder="⭐" />
              </Field>
              <Field label="توضیح کوتاه (اختیاری)" hint="زیر نام پلن به کاربر نشان داده می‌شود.">
                <Input value={plan.display.note} onChange={(e) => setDisplay({ note: e.target.value })} />
              </Field>
            </div>
            {masked && (
              <div className="flex items-start gap-2 rounded-lg border border-amber-400/30 bg-amber-400/5 p-2.5 text-[11px] leading-6 text-amber-200">
                <Info className="mt-1 h-3.5 w-3.5 shrink-0" />
                کاربر «{plan.display.volume_label}» می‌بیند، ولی روی پنل {previewGb} گیگ اعمال می‌شود. این عدد در ربات هیچ‌جا نشان داده نمی‌شود.
              </div>
            )}
          </section>

          {/* ── pricing ── */}
          <section className="space-y-3 rounded-xl border border-border bg-black/20 p-4">
            <div className="flex items-center gap-2 text-sm font-bold text-white">
              <Tag className="h-4 w-4 text-muted-foreground" /> قیمت‌گذاری
            </div>
            <Segmented
              value={plan.pricing.mode}
              onChange={(mode: PricingMode) => setPricing({ mode })}
              options={(["fixed", "linear", "tiered"] as PricingMode[]).map((v) => ({ v, label: PRICING_LABEL[v] }))}
            />
            <p className="text-[11px] text-muted-foreground">{PRICING_HINT[plan.pricing.mode]}</p>

            {plan.pricing.mode === "fixed" && (
              <div className="grid gap-4 sm:grid-cols-2">
                <NumberField label="قیمت کاربر" suffix="تومان" value={plan.pricing.price} onChange={(v) => setPricing({ price: v })} />
                <NumberField label="قیمت نماینده" suffix="تومان" value={plan.pricing.agent_price} onChange={(v) => setPricing({ agent_price: v })} hint="۰ = همان قیمت کاربر" />
              </div>
            )}

            {plan.pricing.mode === "linear" && (
              <div className="grid gap-4 sm:grid-cols-2">
                <NumberField label="قیمت پایه (کاربر)" suffix="تومان" value={plan.pricing.base} onChange={(v) => setPricing({ base: v })} />
                <NumberField label="قیمت پایه (نماینده)" suffix="تومان" value={plan.pricing.agent_base} onChange={(v) => setPricing({ agent_base: v })} hint="۰ = مثل کاربر" />
                <NumberField label="هر گیگ (کاربر)" suffix="تومان" value={plan.pricing.per_gb} onChange={(v) => setPricing({ per_gb: v })} />
                <NumberField label="هر گیگ (نماینده)" suffix="تومان" value={plan.pricing.agent_per_gb} onChange={(v) => setPricing({ agent_per_gb: v })} hint="۰ = مثل کاربر" />
              </div>
            )}

            {plan.pricing.mode === "tiered" && (
              <div className="space-y-2">
                {(plan.pricing.tiers || []).length === 0 && (
                  <p className="text-xs text-muted-foreground">هنوز پله‌ای تعریف نشده — با دکمه‌ی پایین اضافه کنید.</p>
                )}
                {(plan.pricing.tiers || []).map((t, i) => (
                  <div key={i} className="flex flex-col gap-2 rounded-lg border border-border bg-white/[0.02] p-3 sm:flex-row sm:items-end">
                    <div className="flex-1"><NumberField label="از این حجم به بالا (گیگ)" value={t.min_gb} onChange={(v) => setPricing({ tiers: plan.pricing.tiers.map((x, j) => j === i ? { ...x, min_gb: v } : x) })} /></div>
                    <div className="flex-1"><NumberField label="هر گیگ (کاربر)" suffix="تومان" value={t.price_per_gb} onChange={(v) => setPricing({ tiers: plan.pricing.tiers.map((x, j) => j === i ? { ...x, price_per_gb: v } : x) })} /></div>
                    <div className="flex-1"><NumberField label="هر گیگ (نماینده)" suffix="تومان" value={t.agent_price_per_gb} onChange={(v) => setPricing({ tiers: plan.pricing.tiers.map((x, j) => j === i ? { ...x, agent_price_per_gb: v } : x) })} /></div>
                    <Button variant="destructive" size="icon" onClick={() => setPricing({ tiers: plan.pricing.tiers.filter((_, j) => j !== i) })}><Trash2 className="h-4 w-4" /></Button>
                  </div>
                ))}
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" size="sm" onClick={() => setPricing({ tiers: [...(plan.pricing.tiers || []), { min_gb: 0, price_per_gb: 0, agent_price_per_gb: 0 }] })}>
                    <Plus className="h-4 w-4" /> افزودن پله
                  </Button>
                  <div className="w-40"><NumberField label="قیمت پایه (اختیاری)" suffix="تومان" value={plan.pricing.base} onChange={(v) => setPricing({ base: v })} /></div>
                </div>
              </div>
            )}

            {plan.pricing.mode !== "fixed" && (
              <NumberField label="گرد کردن قیمت به" suffix="تومان" value={plan.pricing.round_to} onChange={(v) => setPricing({ round_to: v })} hint="مثلاً ۱۰۰۰ تا قیمت‌ها رند شوند. ۰ = بدون گرد کردن." />
            )}
          </section>

          {/* ── live preview: exactly what the bot will charge ── */}
          <section className="rounded-xl border border-emerald-400/25 bg-emerald-400/[0.04] p-4">
            <div className="mb-2 text-sm font-bold text-emerald-100">پیش‌نمایش قیمت</div>
            {variable ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[22rem] text-xs">
                  <thead className="text-emerald-200/70">
                    <tr><th className="p-1.5 text-right">حجم</th><th className="p-1.5 text-right">کاربر</th><th className="p-1.5 text-right">نماینده</th></tr>
                  </thead>
                  <tbody className="text-emerald-50">
                    {gbChoices(plan).map((g) => (
                      <tr key={g} className="border-t border-emerald-400/10">
                        <td className="p-1.5">{g} گیگ</td>
                        <td className="p-1.5">{money(priceFor(plan, g, false))} ت</td>
                        <td className="p-1.5">{money(priceFor(plan, g, true))} ت</td>
                      </tr>
                    ))}
                    {gbChoices(plan).length === 0 && <tr><td colSpan={3} className="p-2 text-emerald-200/70">بازه‌ی حجم را تعریف کنید.</td></tr>}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="flex flex-wrap gap-6 text-sm text-emerald-50">
                <span>کاربر: <b>{money(userPrice)}</b> تومان</span>
                <span>نماینده: <b>{money(agentPrice)}</b> تومان</span>
                <span className="text-emerald-200/80">نمایش به کاربر: <b>{shownVolume}</b></span>
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────── page ───────────────────────────

export function CatalogTab() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data, isLoading } = useQuery({ queryKey: ["catalog"], queryFn: () => api.catalog() });

  const [categories, setCategories] = React.useState<Category[]>([]);
  const [plans, setPlans] = React.useState<Plan[]>([]);
  const [problems, setProblems] = React.useState<Record<string, string[]>>({});
  const [dirty, setDirty] = React.useState(false);
  const loaded = React.useRef(false);

  React.useEffect(() => {
    if (data && !loaded.current) {
      setCategories(data.catalog.categories);
      setPlans(data.catalog.plans);
      setProblems(data.problems || {});
      loaded.current = true;
    }
  }, [data]);

  const mark = () => setDirty(true);
  const save = useMutation({
    mutationFn: () => api.saveCatalog({ categories, plans }),
    onSuccess: (r) => {
      setProblems(r.problems || {});
      setDirty(false);
      qc.invalidateQueries({ queryKey: ["catalog"] });
      const bad = Object.keys(r.problems || {}).length;
      toast({
        title: bad ? `ذخیره شد — ${bad} پلن نیاز به اصلاح دارد` : "پلن‌ها ذخیره شد",
        variant: bad ? "info" : "success",
      });
    },
    onError: (e: Error) => toast({ title: "ذخیره ناموفق بود", description: e.message, variant: "error" }),
  });

  if (isLoading || !data) return <div className="space-y-4"><Skeleton className="h-24" /><Skeleton className="h-72" /></div>;

  const groups = data.groups || [];
  const panels = data.panels || [];

  const addCategory = () => {
    setCategories((cs) => [...cs, { id: rid("cat"), title: "دسته جدید", emoji: "🌐", description: "", enabled: true, sort: cs.length }]);
    mark();
  };
  const updateCategory = (id: string, patch: Partial<Category>) => {
    setCategories((cs) => cs.map((c) => (c.id === id ? { ...c, ...patch } : c)));
    mark();
  };
  const removeCategory = (id: string) => {
    const count = plans.filter((p) => p.category_id === id).length;
    if (count && !window.confirm(`این دسته ${count} پلن دارد. با حذف دسته، پلن‌ها به دسته‌ی اول منتقل می‌شوند. ادامه می‌دهید؟`)) return;
    setCategories((cs) => cs.filter((c) => c.id !== id));
    setPlans((ps) => {
      const fallback = categories.find((c) => c.id !== id)?.id || "";
      return ps.map((p) => (p.category_id === id ? { ...p, category_id: fallback } : p));
    });
    mark();
  };
  const addPlan = (categoryId: string) => {
    setPlans((ps) => [...ps, newPlan(categoryId, ps.filter((p) => p.category_id === categoryId).length)]);
    mark();
  };
  const updatePlan = (p: Plan) => { setPlans((ps) => ps.map((x) => (x.id === p.id ? p : x))); mark(); };
  const removePlan = (id: string) => { setPlans((ps) => ps.filter((p) => p.id !== id)); mark(); };
  const duplicatePlan = (p: Plan) => {
    setPlans((ps) => [...ps, { ...structuredClone(p), id: rid("plan"), title: `${p.title} (کپی)`, sort: p.sort + 1 }]);
    mark();
  };
  const movePlan = (id: string, dir: -1 | 1) => {
    setPlans((ps) => {
      const plan = ps.find((p) => p.id === id);
      if (!plan) return ps;
      const siblings = ps.filter((p) => p.category_id === plan.category_id).sort((a, b) => a.sort - b.sort);
      const i = siblings.findIndex((p) => p.id === id);
      const j = i + dir;
      if (j < 0 || j >= siblings.length) return ps;
      const a = siblings[i], b = siblings[j];
      return ps.map((p) => (p.id === a.id ? { ...p, sort: b.sort } : p.id === b.id ? { ...p, sort: a.sort } : p));
    });
    mark();
  };

  const badCount = Object.keys(problems).length;

  return (
    <div className="space-y-5">
      <Card className="border-brand/20 bg-gradient-to-br from-brand/[0.07] to-transparent">
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle className="flex items-center gap-2"><Layers className="h-5 w-5 text-brand" /> پلن‌های فروش</CardTitle>
              <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
                اینجا فقط <b className="text-white">آنچه می‌فروشید</b> را تعریف می‌کنید. تنظیمات فنی سرورها (آدرس، یوزر و پسورد) در تب «پنل‌ها» است.
                هر پلن خودش تعیین می‌کند روی کدام سرور و کدام گروه ساخته شود، پس می‌توانید مثلاً پلن ویژه را روی یک گروه دیگر ببرید.
              </p>
            </div>
            <div className="flex items-center gap-2">
              {badCount > 0 && <Badge variant="warning" className="gap-1"><AlertTriangle className="h-3 w-3" /> {badCount} پلن ناقص</Badge>}
              {dirty && <Badge variant="warning">ذخیره نشده</Badge>}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
            <span>دسته‌ها: <b className="text-white">{categories.length}</b></span>
            <span>پلن‌ها: <b className="text-white">{plans.length}</b></span>
            <span>فعال: <b className="text-white">{plans.filter((p) => p.enabled).length}</b></span>
            {data.groups_error
              ? <span className="text-amber-300">گروه‌های PasarGuard خوانده نشد: {data.groups_error}</span>
              : <span>گروه‌های PasarGuard: <b className="text-white">{groups.length}</b></span>}
          </div>
        </CardContent>
      </Card>

      {categories.length === 0 && (
        <Card>
          <CardContent className="py-10 text-center">
            <p className="text-sm text-muted-foreground">هنوز دسته‌ای ندارید. برای شروع یک دسته بسازید (مثلاً «عادی» و «ویژه»).</p>
            <Button className="mt-4" onClick={addCategory}><Plus className="h-4 w-4" /> ساخت دسته</Button>
          </CardContent>
        </Card>
      )}

      {[...categories].sort((a, b) => a.sort - b.sort).map((cat) => {
        const catPlans = plans.filter((p) => p.category_id === cat.id).sort((a, b) => a.sort - b.sort);
        return (
          <Card key={cat.id}>
            <CardHeader>
              <div className="flex flex-wrap items-end justify-between gap-3">
                <div className="grid flex-1 gap-3 sm:grid-cols-[5rem_1fr_1.5fr]">
                  <Field label="ایموجی"><Input value={cat.emoji} onChange={(e) => updateCategory(cat.id, { emoji: e.target.value })} placeholder="🌐" /></Field>
                  <Field label="نام دسته"><Input value={cat.title} onChange={(e) => updateCategory(cat.id, { title: e.target.value })} /></Field>
                  <Field label="توضیح (اختیاری)" hint="بالای لیست پلن‌های این دسته نمایش داده می‌شود."><Input value={cat.description} onChange={(e) => updateCategory(cat.id, { description: e.target.value })} /></Field>
                </div>
                <div className="flex items-center gap-1">
                  <Button size="icon" variant="ghost" title={cat.enabled ? "غیرفعال کن" : "فعال کن"} onClick={() => updateCategory(cat.id, { enabled: !cat.enabled })}>
                    {cat.enabled ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
                  </Button>
                  <Button size="icon" variant="destructive" title="حذف دسته" onClick={() => removeCategory(cat.id)}><Trash2 className="h-4 w-4" /></Button>
                </div>
              </div>
              {!cat.enabled && <Badge variant="muted" className="mt-2 w-fit">این دسته در ربات نمایش داده نمی‌شود</Badge>}
            </CardHeader>
            <CardContent className="space-y-3">
              {catPlans.length === 0 && <p className="text-xs text-muted-foreground">هنوز پلنی در این دسته نیست.</p>}
              {catPlans.map((plan) => (
                <div key={plan.id} className="group relative">
                  <PlanEditor
                    plan={plan}
                    groups={groups}
                    panels={panels}
                    categories={categories}
                    problems={problems[plan.id] || []}
                    onChange={updatePlan}
                    onRemove={() => removePlan(plan.id)}
                    onMove={(d) => movePlan(plan.id, d)}
                  />
                  <Button
                    size="icon" variant="ghost" title="کپی این پلن"
                    className="absolute -top-2 left-24 opacity-0 transition group-hover:opacity-100"
                    onClick={() => duplicatePlan(plan)}
                  ><Copy className="h-4 w-4" /></Button>
                </div>
              ))}
              <Button variant="outline" size="sm" onClick={() => addPlan(cat.id)}><Plus className="h-4 w-4" /> افزودن پلن به این دسته</Button>
            </CardContent>
          </Card>
        );
      })}

      {categories.length > 0 && (
        <Button variant="outline" onClick={addCategory}><Plus className="h-4 w-4" /> افزودن دسته</Button>
      )}

      <div className="sticky bottom-4 flex justify-end">
        <Button size="lg" disabled={save.isPending || !dirty} onClick={() => save.mutate()}>
          <Save className="h-4 w-4" /> {save.isPending ? "در حال ذخیره…" : dirty ? "ذخیره پلن‌ها" : "ذخیره شد ✓"}
        </Button>
      </div>
    </div>
  );
}
