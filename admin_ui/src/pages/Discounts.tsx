import * as React from "react";
import { useMutation, useQuery, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import {
  AlertTriangle, BadgePercent, CalendarClock, Check, Copy, Eye, EyeOff, Info,
  Pencil, Plus, Search, Ticket, Trash2, TrendingDown, Users2,
} from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { StatTile } from "@/components/ui/stat-tile";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { JalaliDateInput } from "@/components/ui/jalali-date-input";
import { useToast } from "@/components/ui/toast";
import { UserLink } from "@/components/UserLink";
import { toman, jalaliDate } from "@/lib/utils";
import { startOfDayTs, endOfDayTs } from "@/lib/jalali";
import type { DiscountCode, DiscountBundle } from "@/lib/types";

const AUDIENCE_LABEL: Record<string, string> = {
  all: "همه",
  users: "فقط کاربران عادی",
  agents: "فقط نماینده‌ها",
  new: "فقط اولین خرید",
};
const APPLIES_LABEL: Record<string, string> = {
  all: "خرید و تمدید",
  purchase: "فقط خرید",
  renewal: "فقط تمدید",
};

function blank(): DiscountCode {
  return {
    code: "", title: "", kind: "percent", value: 20,
    max_discount_toman: 0, min_order_toman: 0, max_order_toman: 0,
    starts_at: 0, ends_at: 0, max_uses: 0, max_uses_per_user: 1,
    audience: "all", applies_to: "all",
    user_ids: [], plan_ids: [], category_ids: [],
    enabled: true, note: "", used_count: 0, total_discount_toman: 0,
    created_at: 0, updated_at: 0,
  };
}

function suggestCode(): string {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  let out = "";
  for (let i = 0; i < 8; i += 1) out += alphabet[Math.floor(Math.random() * alphabet.length)];
  return out;
}

/** Mirrors discounts.py:amount_for so the editor previews the real number. */
function previewAmount(code: DiscountCode, base: number): number {
  const total = Math.max(0, Math.floor(base));
  if (total <= 0) return 0;
  let raw = code.kind === "percent"
    ? Math.floor((total * Math.min(100, Math.max(0, code.value))) / 100)
    : Math.max(0, Math.floor(code.value));
  if (code.max_discount_toman > 0) raw = Math.min(raw, code.max_discount_toman);
  return Math.max(0, Math.min(raw, total));
}

/** The same live/expired/exhausted reading the bot applies. */
function statusOf(code: DiscountCode): { label: string; variant: "success" | "warning" | "danger" | "muted" } {
  const now = Math.floor(Date.now() / 1000);
  if (!code.enabled) return { label: "غیرفعال", variant: "muted" };
  if (code.max_uses > 0 && code.used_count >= code.max_uses) return { label: "ظرفیت تمام", variant: "danger" };
  if (code.ends_at > 0 && code.ends_at <= now) return { label: "منقضی", variant: "danger" };
  if (code.starts_at > 0 && code.starts_at > now) return { label: "زمان‌بندی‌شده", variant: "warning" };
  return { label: "فعال", variant: "success" };
}

function tsToDate(ts: number): Date | null {
  return ts > 0 ? new Date(ts * 1000) : null;
}

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[0.72rem] font-bold text-white">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[0.65rem] leading-5 text-muted-foreground">{hint}</span>}
    </label>
  );
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

function Editor({
  open, onOpenChange, initial, bundle, onSaved,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  initial: DiscountCode | null;
  bundle: DiscountBundle;
  onSaved: () => void;
}) {
  const { toast } = useToast();
  const isNew = !initial;
  const [form, setForm] = React.useState<DiscountCode>(initial || blank());
  const [userIds, setUserIds] = React.useState("");
  const originalCode = React.useRef("");

  React.useEffect(() => {
    if (!open) return;
    const start = initial || blank();
    setForm(start);
    setUserIds((start.user_ids || []).join(", "));
    originalCode.current = initial?.code || "";
  }, [open, initial]);

  const set = <K extends keyof DiscountCode>(key: K, value: DiscountCode[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const save = useMutation({
    mutationFn: () =>
      api.saveDiscount(
        {
          ...form,
          user_ids: userIds
            .split(/[^0-9]+/)
            .map((x) => Number(x))
            .filter((x) => x > 0),
        },
        originalCode.current,
      ),
    onSuccess: (r) => {
      const problems = r.problems || [];
      toast({
        title: problems.length ? "ذخیره شد — با هشدار" : "کد تخفیف ذخیره شد",
        description: problems.join(" · ") || undefined,
        variant: problems.length ? "info" : "success",
      });
      onOpenChange(false);
      onSaved();
    },
    onError: (e: Error) => toast({ title: "ذخیره نشد", description: e.message, variant: "error" }),
  });

  const sample = form.min_order_toman > 0 ? form.min_order_toman : 200000;
  const preview = previewAmount(form, sample);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] max-w-3xl overflow-y-auto">
        <DialogTitle className="flex items-center gap-2">
          <Ticket className="h-5 w-5 text-brand" />
          {isNew ? "کد تخفیف جدید" : `ویرایش ${initial?.code}`}
        </DialogTitle>

        <div className="mt-5 space-y-5">
          <section className="grid gap-4 sm:grid-cols-2">
            <Row label="کد" hint="حروف انگلیسی، عدد، خط تیره. کاربر با حروف کوچک هم بزند کار می‌کند.">
              <div className="flex gap-2">
                <Input
                  dir="ltr"
                  value={form.code}
                  placeholder="NOWRUZ1405"
                  onChange={(e) => set("code", e.target.value.toUpperCase().replace(/[^A-Z0-9_-]/g, ""))}
                />
                <Button variant="outline" size="icon" title="ساخت کد تصادفی"
                  onClick={() => set("code", suggestCode())}>
                  <Copy className="h-4 w-4" />
                </Button>
              </div>
            </Row>
            <Row label="عنوان (اختیاری)" hint="فقط برای خودتان، در ربات نمایش داده نمی‌شود.">
              <Input value={form.title} placeholder="کمپین عید" onChange={(e) => set("title", e.target.value)} />
            </Row>
          </section>

          <section className="space-y-3 rounded-xl border border-border bg-black/20 p-4">
            <div className="flex items-center gap-2 text-sm font-bold text-white">
              <BadgePercent className="h-4 w-4 text-muted-foreground" /> مقدار تخفیف
            </div>
            <div className="grid gap-4 sm:grid-cols-3">
              <Row label="نوع">
                <Select value={form.kind} onChange={(e) => set("kind", e.target.value as DiscountCode["kind"])}>
                  <option value="percent">درصدی</option>
                  <option value="fixed">مبلغ ثابت</option>
                </Select>
              </Row>
              <Row label={form.kind === "percent" ? "درصد (۱ تا ۱۰۰)" : "مبلغ (تومان)"}>
                <NumberInput value={form.value} onChange={(v) => set("value", v)} />
              </Row>
              {form.kind === "percent" && (
                <Row label="سقف تخفیف (تومان)" hint="۰ یعنی بدون سقف.">
                  <NumberInput value={form.max_discount_toman} onChange={(v) => set("max_discount_toman", v)} placeholder="۰" />
                </Row>
              )}
            </div>
            <div className="rounded-lg border border-emerald-400/25 bg-emerald-400/5 p-3 text-xs leading-6 text-emerald-100">
              روی سفارش <b>{toman(sample)}</b> تومانی: تخفیف <b>{toman(preview)}</b> تومان،
              پرداختی <b>{toman(sample - preview)}</b> تومان.
            </div>
          </section>

          <section className="space-y-3 rounded-xl border border-border bg-black/20 p-4">
            <div className="flex items-center gap-2 text-sm font-bold text-white">
              <CalendarClock className="h-4 w-4 text-muted-foreground" /> بازه‌ی زمانی
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <JalaliDateInput
                label="از تاریخ"
                value={tsToDate(form.starts_at)}
                onChange={(d) => set("starts_at", d ? startOfDayTs(d) : 0)}
              />
              <JalaliDateInput
                label="تا پایان تاریخ"
                align="end"
                value={form.ends_at ? new Date((form.ends_at - 1) * 1000) : null}
                onChange={(d) => set("ends_at", d ? endOfDayTs(d) : 0)}
              />
            </div>
            <p className="text-[0.65rem] text-muted-foreground">
              خالی گذاشتن یعنی بدون محدودیت. «تا تاریخ» شامل خودِ همان روز تا پایان شب است.
            </p>
          </section>

          <section className="space-y-3 rounded-xl border border-border bg-black/20 p-4">
            <div className="flex items-center gap-2 text-sm font-bold text-white">
              <Users2 className="h-4 w-4 text-muted-foreground" /> چه کسی، چند بار
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Row label="گروه کاربران">
                <Select value={form.audience} onChange={(e) => set("audience", e.target.value as DiscountCode["audience"])}>
                  {Object.entries(AUDIENCE_LABEL).map(([v, label]) => (
                    <option key={v} value={v}>{label}</option>
                  ))}
                </Select>
              </Row>
              <Row label="نوع سفارش">
                <Select value={form.applies_to} onChange={(e) => set("applies_to", e.target.value as DiscountCode["applies_to"])}>
                  {Object.entries(APPLIES_LABEL).map(([v, label]) => (
                    <option key={v} value={v}>{label}</option>
                  ))}
                </Select>
              </Row>
              <Row label="سقف کل استفاده" hint="۰ یعنی نامحدود.">
                <NumberInput value={form.max_uses} onChange={(v) => set("max_uses", v)} placeholder="نامحدود" />
              </Row>
              <Row label="سقف استفاده هر نفر" hint="۰ یعنی نامحدود. پیش‌فرض ۱ بار.">
                <NumberInput value={form.max_uses_per_user} onChange={(v) => set("max_uses_per_user", v)} placeholder="نامحدود" />
              </Row>
              <div className="sm:col-span-2">
                <Row label="فقط برای این آیدی‌های عددی (اختیاری)" hint="با کاما جدا کنید. خالی = همه‌ی کاربرانِ گروه بالا.">
                  <Input dir="ltr" value={userIds} placeholder="123456789, 987654321"
                    onChange={(e) => setUserIds(e.target.value)} />
                </Row>
              </div>
            </div>
          </section>

          <section className="space-y-3 rounded-xl border border-border bg-black/20 p-4">
            <div className="flex items-center gap-2 text-sm font-bold text-white">
              <TrendingDown className="h-4 w-4 text-muted-foreground" /> محدوده‌ی سفارش
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Row label="حداقل مبلغ سفارش (تومان)" hint="۰ = بدون حداقل.">
                <NumberInput value={form.min_order_toman} onChange={(v) => set("min_order_toman", v)} placeholder="۰" />
              </Row>
              <Row label="حداکثر مبلغ سفارش (تومان)" hint="۰ = بدون سقف.">
                <NumberInput value={form.max_order_toman} onChange={(v) => set("max_order_toman", v)} placeholder="بدون سقف" />
              </Row>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Row label="فقط این پلن‌ها" hint="هیچ‌کدام انتخاب نشود = همه‌ی پلن‌ها.">
                <div className="max-h-32 space-y-1 overflow-y-auto rounded-xl border border-border bg-card p-2">
                  {bundle.plans.length === 0 && <div className="p-1 text-xs text-muted-foreground">پلنی تعریف نشده.</div>}
                  {bundle.plans.map((p) => (
                    <label key={p.id} className="flex items-center gap-2 rounded-lg px-1 py-0.5 text-xs hover:bg-white/5">
                      <input
                        type="checkbox"
                        className="h-3.5 w-3.5 accent-[hsl(var(--brand))]"
                        checked={form.plan_ids.includes(p.id)}
                        onChange={(e) =>
                          set("plan_ids", e.target.checked
                            ? [...form.plan_ids, p.id]
                            : form.plan_ids.filter((x) => x !== p.id))
                        }
                      />
                      <span className="text-foreground">{p.title || p.id}</span>
                    </label>
                  ))}
                </div>
              </Row>
              <Row label="فقط این دسته‌ها" hint="هیچ‌کدام انتخاب نشود = همه‌ی دسته‌ها.">
                <div className="max-h-32 space-y-1 overflow-y-auto rounded-xl border border-border bg-card p-2">
                  {bundle.categories.length === 0 && <div className="p-1 text-xs text-muted-foreground">دسته‌ای تعریف نشده.</div>}
                  {bundle.categories.map((c) => (
                    <label key={c.id} className="flex items-center gap-2 rounded-lg px-1 py-0.5 text-xs hover:bg-white/5">
                      <input
                        type="checkbox"
                        className="h-3.5 w-3.5 accent-[hsl(var(--brand))]"
                        checked={form.category_ids.includes(c.id)}
                        onChange={(e) =>
                          set("category_ids", e.target.checked
                            ? [...form.category_ids, c.id]
                            : form.category_ids.filter((x) => x !== c.id))
                        }
                      />
                      <span className="text-foreground">{`${c.emoji || ""} ${c.title}`.trim()}</span>
                    </label>
                  ))}
                </div>
              </Row>
            </div>
          </section>

          <Row label="یادداشت (اختیاری)">
            <Input value={form.note} placeholder="برای کمپین اینستاگرام" onChange={(e) => set("note", e.target.value)} />
          </Row>

          <label className="flex items-start gap-2 rounded-xl border border-border bg-white/[0.02] p-3 text-sm">
            <input type="checkbox" checked={form.enabled} className="mt-0.5 h-4 w-4 accent-[hsl(var(--brand))]"
              onChange={(e) => set("enabled", e.target.checked)} />
            <span>
              <span className="font-bold text-white">کد فعال باشد</span>
              <span className="block text-[11px] text-muted-foreground">
                غیرفعال کردن، کد را بدون حذف کردن از دسترس خارج می‌کند؛ آمار استفاده‌ی قبلی حفظ می‌شود.
              </span>
            </span>
          </label>

          <div className="flex justify-start gap-2 pt-1">
            <Button disabled={save.isPending || !form.code} onClick={() => save.mutate()}>
              <Check className="h-4 w-4" /> {save.isPending ? "در حال ذخیره…" : "ذخیره"}
            </Button>
            <Button variant="subtle" onClick={() => onOpenChange(false)}>انصراف</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Detail({ code, onClose }: { code: string; onClose: () => void }) {
  const { data } = useQuery({
    queryKey: ["discount", code],
    queryFn: () => api.discountDetail(code),
  });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogTitle className="flex items-center gap-2">
          <Ticket className="h-5 w-5 text-brand" /> <span dir="ltr">{code}</span>
        </DialogTitle>
        {!data ? (
          <div className="mt-4 space-y-2"><Skeleton className="h-20" /><Skeleton className="h-40" /></div>
        ) : (
          <div className="mt-4 space-y-4">
            <div className="grid gap-3 sm:grid-cols-4">
              <StatTile label="دفعات استفاده" value={toman(data.stats.used)} />
              <StatTile label="کاربران یکتا" value={toman(data.stats.buyers)} />
              <StatTile label="تخفیف داده‌شده" value={toman(data.stats.given)} sub="تومان" />
              <StatTile label="فروش ناخالص" value={toman(data.stats.gross)} sub="تومان" />
            </div>
            <Table>
              <THead>
                <TR><TH>کاربر</TH><TH>سفارش</TH><TH>تخفیف</TH><TH>وضعیت</TH><TH>تاریخ</TH></TR>
              </THead>
              <TBody>
                {data.redemptions.map((r) => (
                  <TR key={r.order_id}>
                    <TD><UserLink userId={r.user_id} name={r.first_name} username={r.username} /></TD>
                    <TD className="text-xs">
                      {toman(r.base_toman)} · {r.order_kind === "renewal" ? "تمدید" : "خرید"}
                    </TD>
                    <TD>{toman(r.amount_toman)}</TD>
                    <TD>
                      <Badge variant={r.status === "used" ? "success" : "muted"}>
                        {r.status === "used" ? "اعمال شد" : "برگشت خورد"}
                      </Badge>
                    </TD>
                    <TD className="text-xs text-muted-foreground">{jalaliDate(r.created_at)}</TD>
                  </TR>
                ))}
                {data.redemptions.length === 0 && (
                  <TR><TD colSpan={5} className="py-6 text-center text-muted-foreground">هنوز استفاده نشده.</TD></TR>
                )}
              </TBody>
            </Table>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function Discounts() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [q, setQ] = React.useState("");
  const [state, setState] = React.useState("all");
  const [editing, setEditing] = React.useState<DiscountCode | null>(null);
  const [editorOpen, setEditorOpen] = React.useState(false);
  const [detail, setDetail] = React.useState("");

  const { data, isFetching } = useQuery({
    queryKey: ["discounts", q, state],
    queryFn: () => api.discounts(q, state),
    placeholderData: keepPreviousData,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["discounts"] });

  const toggle = useMutation({
    mutationFn: ({ code, enabled }: { code: string; enabled: boolean }) => api.toggleDiscount(code, enabled),
    onSuccess: refresh,
    onError: (e: Error) => toast({ title: "تغییر وضعیت ناموفق بود", description: e.message, variant: "error" }),
  });
  const remove = useMutation({
    mutationFn: (code: string) => api.deleteDiscount(code),
    onSuccess: () => { toast({ title: "کد حذف شد", variant: "success" }); refresh(); },
    onError: (e: Error) => toast({ title: "حذف ناموفق بود", description: e.message, variant: "error" }),
  });

  const openNew = () => { setEditing(null); setEditorOpen(true); };
  const openEdit = (code: DiscountCode) => { setEditing(code); setEditorOpen(true); };

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile icon={Ticket} label="کل کدها" value={toman(data?.overview.total ?? 0)} />
        <StatTile icon={BadgePercent} label="کدهای فعال" value={toman(data?.overview.active ?? 0)} />
        <StatTile icon={Users2} label="دفعات استفاده" value={toman(data?.overview.uses ?? 0)} />
        <StatTile icon={TrendingDown} label="تخفیف داده‌شده" value={toman(data?.overview.given ?? 0)} sub="تومان" />
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <CardTitle>کدهای تخفیف</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                هر کد شرط‌های خودش را دارد: بازه‌ی زمانی، سقف استفاده، گروه کاربران، محدوده‌ی مبلغ و پلن‌های مجاز.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative w-full sm:w-56">
                <Search className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input className="pr-9" placeholder="جست‌وجوی کد یا عنوان" value={q} onChange={(e) => setQ(e.target.value)} />
              </div>
              <Select className="w-40" value={state} onChange={(e) => setState(e.target.value)}>
                <option value="all">همه</option>
                <option value="active">فعال</option>
                <option value="expired">منقضی / تمام‌شده</option>
                <option value="disabled">غیرفعال</option>
              </Select>
              <Button onClick={openNew}><Plus className="h-4 w-4" /> کد جدید</Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {!data ? (
            <div className="space-y-2">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-12" />)}</div>
          ) : data.items.length === 0 ? (
            <div className="flex flex-col items-center gap-3 py-12 text-center">
              <Ticket className="h-8 w-8 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">هنوز کد تخفیفی نساخته‌اید.</p>
              <Button size="sm" onClick={openNew}><Plus className="h-4 w-4" /> اولین کد را بسازید</Button>
            </div>
          ) : (
            <Table className={isFetching ? "opacity-60 transition-opacity" : ""}>
              <THead>
                <TR>
                  <TH>کد</TH><TH>تخفیف</TH><TH>شرایط</TH><TH>استفاده</TH><TH>وضعیت</TH><TH />
                </TR>
              </THead>
              <TBody>
                {data.items.map((c) => {
                  const st = statusOf(c);
                  const limits = [
                    AUDIENCE_LABEL[c.audience],
                    APPLIES_LABEL[c.applies_to],
                    c.min_order_toman > 0 ? `از ${toman(c.min_order_toman)} ت` : "",
                    c.max_order_toman > 0 ? `تا ${toman(c.max_order_toman)} ت` : "",
                    c.plan_ids.length ? `${c.plan_ids.length} پلن` : "",
                    c.category_ids.length ? `${c.category_ids.length} دسته` : "",
                    c.user_ids.length ? `${c.user_ids.length} کاربر خاص` : "",
                  ].filter(Boolean);
                  return (
                    <TR key={c.code}>
                      <TD>
                        <div className="font-bold text-white" dir="ltr">{c.code}</div>
                        {c.title && <div className="text-[0.68rem] text-muted-foreground">{c.title}</div>}
                      </TD>
                      <TD className="whitespace-nowrap">
                        {c.kind === "percent" ? `${c.value}٪` : `${toman(c.value)} ت`}
                        {c.kind === "percent" && c.max_discount_toman > 0 && (
                          <div className="text-[0.65rem] text-muted-foreground">سقف {toman(c.max_discount_toman)} ت</div>
                        )}
                      </TD>
                      <TD>
                        <div className="flex flex-wrap gap-1">
                          {limits.map((l, i) => (
                            <span key={i} className="rounded-md bg-white/[0.04] px-1.5 py-0.5 text-[0.65rem] text-muted-foreground">{l}</span>
                          ))}
                        </div>
                        {(c.starts_at > 0 || c.ends_at > 0) && (
                          <div className="mt-1 text-[0.65rem] text-muted-foreground">
                            {c.starts_at > 0 ? jalaliDate(c.starts_at) : "…"} تا {c.ends_at > 0 ? jalaliDate(c.ends_at) : "…"}
                          </div>
                        )}
                      </TD>
                      <TD className="whitespace-nowrap text-xs">
                        {toman(c.used_count)}{c.max_uses > 0 ? ` / ${toman(c.max_uses)}` : ""}
                        <div className="text-[0.65rem] text-muted-foreground">
                          {c.max_uses_per_user > 0 ? `${c.max_uses_per_user} بار هر نفر` : "نامحدود هر نفر"}
                        </div>
                      </TD>
                      <TD><Badge variant={st.variant}>{st.label}</Badge></TD>
                      <TD>
                        <div className="flex items-center justify-end gap-0.5">
                          <Button size="icon" variant="ghost" title="گزارش استفاده" onClick={() => setDetail(c.code)}>
                            <Info className="h-4 w-4" />
                          </Button>
                          <Button size="icon" variant="ghost" title={c.enabled ? "غیرفعال کردن" : "فعال کردن"}
                            onClick={() => toggle.mutate({ code: c.code, enabled: !c.enabled })}>
                            {c.enabled ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
                          </Button>
                          <Button size="icon" variant="ghost" title="ویرایش" onClick={() => openEdit(c)}>
                            <Pencil className="h-4 w-4" />
                          </Button>
                          <ConfirmDialog
                            trigger={
                              <Button size="icon" variant="ghost" title="حذف"
                                className="text-rose-300 hover:bg-rose-500/10 hover:text-rose-200">
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            }
                            title={`حذف کد ${c.code}؟`}
                            description="کد از دسترس خارج می‌شود. سابقه‌ی استفاده‌ی قبلی روی سفارش‌ها باقی می‌ماند. اگر فقط می‌خواهید موقتاً بسته شود، به‌جای حذف آن را غیرفعال کنید."
                            confirmLabel="حذف"
                            destructive
                            onConfirm={() => remove.mutate(c.code)}
                          />
                        </div>
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          )}
          {data && data.items.some((c) => statusOf(c).variant === "danger") && (
            <p className="mt-3 flex items-center gap-2 text-[0.7rem] text-amber-300">
              <AlertTriangle className="h-3.5 w-3.5" />
              کدهای منقضی یا تمام‌ظرفیت در ربات پذیرفته نمی‌شوند و به کاربر دلیلش گفته می‌شود.
            </p>
          )}
        </CardContent>
      </Card>

      {data && (
        <Editor
          open={editorOpen}
          onOpenChange={setEditorOpen}
          initial={editing}
          bundle={data}
          onSaved={refresh}
        />
      )}
      {detail && <Detail code={detail} onClose={() => setDetail("")} />}
    </div>
  );
}
