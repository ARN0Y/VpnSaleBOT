import * as React from "react";
import { Ban, ShieldCheck, Power, PowerOff, TriangleAlert } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { n, s, type UserMutations } from "./helpers";
import type { UserDetailBundle } from "@/lib/types";

export function DangerTab({ data, mutations }: { data: UserDetailBundle; mutations: UserMutations }) {
  const u = data.user;
  const disabled = n(u.user_disabled) === 1;
  const [reason, setReason] = React.useState("");

  return (
    <div className="space-y-5">
      <Card className="border-rose-500/25">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-rose-200">
            <TriangleAlert className="h-4 w-4" /> دسترسی کاربر
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {disabled ? (
            <>
              <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-200">
                این کاربر مسدود است. دلیل: {s(u.disabled_reason) || "ثبت نشده"}
              </div>
              <Button disabled={mutations.unban.isPending} onClick={() => mutations.unban.mutate()}>
                <ShieldCheck className="h-4 w-4" /> فعال‌سازی دوباره
              </Button>
            </>
          ) : (
            <>
              <Field label="دلیل محدودسازی (اختیاری)">
                <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="مثلاً تخلف، اسپم…" />
              </Field>
              <ConfirmDialog
                destructive
                title="مسدودسازی کاربر؟"
                description="کاربر دیگر نمی‌تواند از ربات استفاده کند تا زمانی که دوباره فعال شود."
                confirmLabel="مسدود کن"
                onConfirm={() => mutations.ban.mutate(reason || "از پنل مدیریت")}
                trigger={
                  <Button variant="destructive">
                    <Ban className="h-4 w-4" /> مسدودسازی کاربر
                  </Button>
                }
              />
            </>
          )}
        </CardContent>
      </Card>

      <Card className="border-rose-500/25">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-rose-200">
            <TriangleAlert className="h-4 w-4" /> مدیریت گروهی کانفیگ‌ها
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs leading-6 text-muted-foreground">
            این عملیات فقط وضعیت همه‌ی کانفیگ‌های این کاربر را در پنل 3x-ui تغییر می‌دهد و در background (صف رویدادها) اجرا می‌شود.
            حساب کاربر بن یا تمدید نمی‌شود.
          </p>
          <div className="flex flex-wrap gap-2">
            <ConfirmDialog
              destructive
              title="غیرفعال‌سازی همه‌ی کانفیگ‌ها؟"
              description="تمام کانفیگ‌های این کاربر در 3x-ui غیرفعال می‌شوند."
              confirmLabel="غیرفعال کن"
              onConfirm={() => mutations.bulk.mutate(false)}
              trigger={
                <Button variant="destructive">
                  <PowerOff className="h-4 w-4" /> غیرفعال‌سازی همه
                </Button>
              }
            />
            <ConfirmDialog
              title="فعال‌سازی همه‌ی کانفیگ‌ها؟"
              description="تمام کانفیگ‌های این کاربر در 3x-ui فعال می‌شوند."
              confirmLabel="فعال کن"
              onConfirm={() => mutations.bulk.mutate(true)}
              trigger={
                <Button>
                  <Power className="h-4 w-4" /> فعال‌سازی همه
                </Button>
              }
            />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
