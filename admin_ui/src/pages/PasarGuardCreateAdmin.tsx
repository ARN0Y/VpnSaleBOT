import * as React from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { UserPlus, RefreshCw, KeyRound, CheckCircle2 } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { CopyButton } from "@/components/ui/copy-button";
import { useToast } from "@/components/ui/toast";

// >=12 chars, >=2 uppercase, digits + symbol — matches the panel policy.
function genPassword(): string {
  const U = "ABCDEFGHJKLMNPQRSTUVWXYZ";
  const L = "abcdefghijkmnpqrstuvwxyz";
  const D = "23456789";
  const S = "!@#$%*-_";
  const pick = (s: string) => s[Math.floor(Math.random() * s.length)];
  const out = [pick(U), pick(U), pick(U), pick(D), pick(D), pick(S),
    ...Array.from({ length: 7 }, () => pick(L + D))];
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out.join("");
}

export function PasarGuardCreateAdmin() {
  const { toast } = useToast();
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState(genPassword());
  const [roleId, setRoleId] = React.useState("");
  const [dataLimit, setDataLimit] = React.useState("");
  const [note, setNote] = React.useState("");
  const [result, setResult] = React.useState<{ username: string; password: string; panel_url?: string } | null>(null);

  const rolesQ = useQuery({ queryKey: ["pg-roles"], queryFn: () => api.pgRoles() });

  const create = useMutation({
    mutationFn: () =>
      api.pgCreateAdmin({
        username: username.trim(),
        password: password.trim() || undefined,
        role_id: roleId ? Number(roleId) : undefined,
        data_limit_gb: dataLimit ? Number(dataLimit) : undefined,
        note: note.trim() || undefined,
      }),
    onSuccess: (d) => {
      if (!d.ok) {
        toast({ title: "ساخت ناموفق", description: d.error, variant: "error" });
        return;
      }
      setResult({ username: d.username!, password: d.password!, panel_url: d.panel_url });
      toast({ title: "اکانت ادمین ساخته شد", variant: "success" });
      setUsername("");
      setPassword(genPassword());
    },
    onError: (e: Error) => toast({ title: "خطا", description: e.message, variant: "error" }),
  });

  const validUser = username.trim().replace(/[^A-Za-z0-9_]/g, "").length >= 3;
  const validPass = password.trim().length >= 12 && (password.match(/[A-Z]/g)?.length ?? 0) >= 2;

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <UserPlus className="h-5 w-5 text-brand" /> ساخت اکانت ادمین در پنل پاسارگارد
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            یک حساب ادمین با یوزر/پسورد و سطح دسترسی دلخواه در پنل پاسارگارد بساز و به نماینده بده. اگر نقشی انتخاب نکنی،
            نقشِ امنِ پیش‌فرض «نماینده» (کنترل کامل فقط روی کاربرانِ خودش) به‌صورت خودکار ساخته و استفاده می‌شود.
          </p>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>یوزرنیم ادمین</Label>
              <Input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="مثلاً reseller_ali"
                dir="ltr"
              />
              <p className="text-[11px] text-muted-foreground">فقط حروف/عدد/زیرخط، حداقل ۳ کاراکتر.</p>
            </div>

            <div className="space-y-1.5">
              <Label>پسورد</Label>
              <div className="flex gap-2">
                <Input value={password} onChange={(e) => setPassword(e.target.value)} dir="ltr" />
                <Button type="button" variant="subtle" onClick={() => setPassword(genPassword())} title="تولید پسورد">
                  <RefreshCw className="h-4 w-4" />
                </Button>
                <CopyButton value={password} />
              </div>
              <p className={validPass ? "text-[11px] text-emerald-300" : "text-[11px] text-amber-300"}>
                حداقل ۱۲ کاراکتر و ۲ حرف بزرگ (سیاست پنل).
              </p>
            </div>

            <div className="space-y-1.5">
              <Label>سطح دسترسی (نقش)</Label>
              <Select value={roleId} onChange={(e) => setRoleId(e.target.value)}>
                <option value="">نقشِ پیش‌فرضِ نماینده (خودکار)</option>
                {(rolesQ.data?.roles || [])
                  .filter((r) => !r.is_owner)
                  .map((r) => (
                    <option key={r.id} value={r.id}>{r.name}</option>
                  ))}
              </Select>
              <p className="text-[11px] text-muted-foreground">
                نقش‌ها در پنل پاسارگارد تعریف می‌شوند و دسترسی‌ها را مشخص می‌کنند.
              </p>
            </div>

            <div className="space-y-1.5">
              <Label>سقف حجم (گیگ) — اختیاری</Label>
              <Input value={dataLimit} onChange={(e) => setDataLimit(e.target.value.replace(/[^0-9.]/g, ""))} placeholder="خالی = نامحدود" dir="ltr" />
            </div>

            <div className="space-y-1.5 sm:col-span-2">
              <Label>یادداشت — اختیاری</Label>
              <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="مثلاً: نماینده‌ی تهران" />
            </div>
          </div>

          <Button onClick={() => create.mutate()} disabled={!validUser || !validPass || create.isPending}>
            <UserPlus className="h-4 w-4" /> {create.isPending ? "در حال ساخت…" : "ساخت اکانت ادمین"}
          </Button>
        </CardContent>
      </Card>

      {result && (
        <Card className="border-emerald-400/30">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-emerald-200">
              <CheckCircle2 className="h-5 w-5" /> اکانت ادمین ساخته شد — این مشخصات را به نماینده بدهید
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <CredRow label="آدرس پنل" value={result.panel_url || "—"} />
            <CredRow label="یوزرنیم" value={result.username} />
            <CredRow label="پسورد" value={result.password} icon />
            <p className="text-[11px] text-amber-300">⚠️ پسورد فقط همین حالا نمایش داده می‌شود؛ آن را ذخیره کنید.</p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function CredRow({ label, value, icon }: { label: string; value: string; icon?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-border bg-white/[0.02] px-3 py-2.5">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        {icon && <KeyRound className="h-4 w-4" />} {label}
      </div>
      <div className="flex items-center gap-2">
        <span className="font-mono text-sm text-white" dir="ltr">{value}</span>
        <CopyButton value={value} />
      </div>
    </div>
  );
}
