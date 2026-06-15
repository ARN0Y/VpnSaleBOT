import * as React from "react";
import { useMutation } from "@tanstack/react-query";
import { Crown, BookText, ShieldCheck, KeyRound, CheckCircle2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Field } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { EmptyState } from "@/components/ui/empty-state";
import { CopyButton } from "@/components/ui/copy-button";
import { useToast } from "@/components/ui/toast";
import { api, ApiError } from "@/lib/api";
import { toman, jalaliDate } from "@/lib/utils";
import { n, s, type UserMutations } from "./helpers";
import type { UserDetailBundle } from "@/lib/types";

function PgResellerAdminCard({ userId, existing }: { userId: number; existing: string }) {
  const { toast } = useToast();
  const [result, setResult] = React.useState<{ username: string; password: string; panel_url?: string } | null>(null);
  const create = useMutation({
    mutationFn: () => api.pgCreateAdminForReseller(userId),
    onSuccess: (d) => {
      if (!d.ok) { toast({ title: "ساخت ناموفق", description: d.error, variant: "error" }); return; }
      setResult({ username: d.username!, password: d.password!, panel_url: d.panel_url });
      toast({ title: "اکانت ادمین پاسارگارد ساخته شد", variant: "success" });
    },
    onError: (e: Error) =>
      toast({
        title: e instanceof ApiError && e.status === 409 ? "این نماینده از قبل اکانت ادمین دارد" : "خطا",
        description: e.message,
        variant: "error",
      }),
  });

  const Cred = ({ label, value, icon }: { label: string; value: string; icon?: boolean }) => (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-border bg-white/[0.02] px-3 py-2.5">
      <span className="flex items-center gap-2 text-xs text-muted-foreground">{icon && <KeyRound className="h-4 w-4" />} {label}</span>
      <span className="flex items-center gap-2"><span className="font-mono text-sm text-white" dir="ltr">{value}</span><CopyButton value={value} /></span>
    </div>
  );

  return (
    <Card className="lg:col-span-2 border-brand/25">
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-brand" /> اکانت ادمین پاسارگارد برای این نماینده</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs leading-6 text-muted-foreground">
          با یک کلیک یک حساب ادمینِ اختصاصی در پنل پاسارگارد برای این نماینده صادر می‌شود (یوزرنیم بر اساس آیدی او،
          گذرواژه‌ی امنِ تصادفی، نقشِ «نماینده» با کنترل کامل فقط روی کاربرانِ خودش).
        </p>
        {existing && !result && (
          <div className="rounded-xl border border-amber-400/30 bg-amber-400/5 px-3 py-2 text-xs text-amber-200">
            این نماینده از قبل اکانت ادمین دارد: <span className="font-mono">{existing}</span>
          </div>
        )}
        {result ? (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm font-bold text-emerald-200"><CheckCircle2 className="h-4 w-4" /> اطلاعات ورود را به نماینده بدهید:</div>
            <Cred label="آدرس پنل" value={result.panel_url || "—"} />
            <Cred label="نام کاربری" value={result.username} />
            <Cred label="گذرواژه" value={result.password} icon />
            <p className="text-[11px] text-amber-300">گذرواژه تنها یک‌بار نمایش داده می‌شود؛ آن را ذخیره کنید.</p>
          </div>
        ) : (
          <Button disabled={create.isPending} onClick={() => create.mutate()}>
            <ShieldCheck className="h-4 w-4" /> {create.isPending ? "در حال ایجاد…" : existing ? "ساخت اکانت ادمین جدید" : "ساخت اکانت ادمین پاسارگارد"}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

export function AgentTab({ data, mutations }: { data: UserDetailBundle; mutations: UserMutations }) {
  const u = data.user;
  const isAgent = !!s(u.access_level);
  const [form, setForm] = React.useState({
    access_level: s(u.access_level) === "open" ? "open" : "closed",
    credit_limit_toman: String(n(u.credit_limit_toman)),
    credit_used_toman: String(n(u.credit_used_toman)),
    price_per_gb: String(n(u.price_per_gb)),
    daily_test_limit: String(n(u.daily_test_limit)),
  });
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  const PRICE_PRESETS = [150000, 180000, 200000, 250000];
  const CREDIT_PRESETS = [5000000, 10000000, 20000000, 50000000];
  const TEST_PRESETS = [0, 1, 3, 5, 10];
  const Chips = ({ field, options, fmt }: { field: keyof typeof form; options: number[]; fmt: (v: number) => string }) => (
    <div className="mt-1.5 flex flex-wrap gap-1">
      {options.map((v) => (
        <button
          key={v}
          type="button"
          onClick={() => set(field, String(v))}
          className={`rounded-lg border px-2 py-1 text-[0.65rem] font-bold transition ${
            Number(form[field]) === v ? "border-white/30 bg-white/[0.08] text-white" : "border-border text-muted-foreground hover:text-white"
          }`}
        >
          {fmt(v)}
        </button>
      ))}
    </div>
  );

  const creditLimit = n(u.credit_limit_toman);
  const creditUsed = n(u.credit_used_toman);
  const pct = creditLimit > 0 ? (creditUsed / creditLimit) * 100 : 0;

  const submit = () =>
    mutations.agent.mutate({
      access_level: form.access_level,
      credit_limit_toman: Number(form.credit_limit_toman) || 0,
      credit_used_toman: Number(form.credit_used_toman) || 0,
      price_per_gb: Number(form.price_per_gb) || 0,
      daily_test_limit: Number(form.daily_test_limit) || 0,
    });

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Crown className="h-4 w-4" /> {isAgent ? "تنظیمات نمایندگی" : "ارتقا به نماینده"}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {!isAgent && (
            <div className="rounded-xl border border-border bg-white/[0.02] p-3 text-xs leading-6 text-muted-foreground">
              این کاربر هنوز نماینده نیست. با ذخیره‌ی فرم زیر به نماینده تبدیل می‌شود و سپس می‌توانید تنظیماتش را ویرایش کنید.
            </div>
          )}
          {isAgent && creditLimit > 0 && (
            <div>
              <div className="mb-1 flex justify-between text-xs text-muted-foreground">
                <span>اعتبار مصرف‌شده</span>
                <span>{toman(creditUsed)} / {toman(creditLimit)}</span>
              </div>
              <Progress value={pct} tone={pct >= 90 ? "danger" : pct >= 70 ? "warning" : "success"} />
            </div>
          )}
          <Field label="سطح دسترسی">
            <Select value={form.access_level} onChange={(e) => set("access_level", e.target.value)}>
              <option value="closed">نیازمند پرداخت (کیف‌پولی)</option>
              <option value="open">دسترسی باز (اعتباری)</option>
            </Select>
          </Field>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <Field label="سقف اعتبار (ت)">
                <Input value={form.credit_limit_toman} onChange={(e) => set("credit_limit_toman", e.target.value)} inputMode="numeric" />
              </Field>
              <Chips field="credit_limit_toman" options={CREDIT_PRESETS} fmt={(v) => `${toman(v / 1000000)}م`} />
            </div>
            <Field label="اعتبار مصرف‌شده (ت)" hint="اصلاح دستی بدهی">
              <Input value={form.credit_used_toman} onChange={(e) => set("credit_used_toman", e.target.value)} inputMode="numeric" />
            </Field>
            <div>
              <Field label="قیمت هر گیگ (ت)" hint="۰ = تعرفه عمومی">
                <Input value={form.price_per_gb} onChange={(e) => set("price_per_gb", e.target.value)} inputMode="numeric" />
              </Field>
              <Chips field="price_per_gb" options={PRICE_PRESETS} fmt={(v) => `${toman(v / 1000)}ک`} />
            </div>
            <div>
              <Field label="سقف تست روزانه">
                <Input value={form.daily_test_limit} onChange={(e) => set("daily_test_limit", e.target.value)} inputMode="numeric" />
              </Field>
              <Chips field="daily_test_limit" options={TEST_PRESETS} fmt={(v) => String(v)} />
            </div>
          </div>
          <Button className="w-full" disabled={mutations.agent.isPending} onClick={submit}>
            {isAgent ? "ذخیره و اطلاع‌رسانی" : "تبدیل به نماینده"}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><BookText className="h-4 w-4" /> دفتر مالی نماینده</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {data.ledger.length === 0 ? (
            <EmptyState icon={BookText} title="گردش مالی ندارد" hint="تراکنش‌های اعتبار نماینده این‌جا ثبت می‌شوند." />
          ) : (
            data.ledger.map((l, i) => {
              const amt = n(l.amount_toman);
              return (
                <div key={i} className="flex items-center justify-between gap-2 rounded-xl border border-border bg-white/[0.02] px-3 py-2 text-sm">
                  <div className="min-w-0">
                    <div className="text-white">{s(l.kind)}</div>
                    <code className="text-[0.62rem] text-muted-foreground">{s(l.ref_id)}</code>
                  </div>
                  <div className="text-left">
                    <Badge variant={amt < 0 ? "success" : "warning"}>{toman(amt)}</Badge>
                    <div className="mt-0.5 text-[0.62rem] text-muted-foreground">{jalaliDate(n(l.created_at))}</div>
                  </div>
                </div>
              );
            })
          )}
        </CardContent>
      </Card>

      {isAgent && (
        <PgResellerAdminCard
          userId={n(u.user_id)}
          existing={s((data as unknown as { pg_admin_username?: string }).pg_admin_username)}
        />
      )}
    </div>
  );
}
