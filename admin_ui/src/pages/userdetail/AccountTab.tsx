import * as React from "react";
import { Wallet, Send, Receipt } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Field } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { StatTile } from "@/components/ui/stat-tile";
import { CreditCard, Coins } from "lucide-react";
import { PeriodTabs } from "./parts";
import { toman, jalaliDate } from "@/lib/utils";
import { n, s, type UserMutations } from "./helpers";
import { statusBadge } from "@/lib/status";
import type { UserDetailBundle } from "@/lib/types";

export function AccountTab({
  data,
  mutations,
  topupPeriod,
  setTopupPeriod,
}: {
  data: UserDetailBundle;
  mutations: UserMutations;
  topupPeriod: string;
  setTopupPeriod: (v: string) => void;
}) {
  const u = data.user;
  const [wallet, setWallet] = React.useState(String(n(u.wallet_balance)));
  const [msg, setMsg] = React.useState("");

  const cardTotal = data.topups.filter((t) => t.method !== "crypto").reduce((a, t) => a + n(t.amount_toman), 0);
  const cryptoTotal = data.topups.filter((t) => t.method === "crypto").reduce((a, t) => a + n(t.amount_toman), 0);
  const pendingCount = data.topups.filter((t) => s(t.status) === "pending").length;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile icon={Receipt} label="تعداد شارژ (بازه)" value={toman(data.topups.length)} sub={`${toman(pendingCount)} در انتظار`} />
        <StatTile icon={CreditCard} label="مجموع کارت‌به‌کارت" value={toman(cardTotal)} sub="تومان" />
        <StatTile icon={Coins} label="مجموع رمزارز" value={toman(cryptoTotal)} sub="تومان" />
        <StatTile icon={Wallet} label="موجودی فعلی" value={toman(u.wallet_balance)} sub="تومان" />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Wallet className="h-4 w-4" /> ویرایش کیف پول</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="rounded-xl border border-border bg-white/[0.02] p-3 text-sm text-muted-foreground">
              موجودی فعلی: <b className="text-white">{toman(u.wallet_balance)}</b> تومان
            </div>
            <Field label="موجودی جدید (تومان)">
              <Input value={wallet} onChange={(e) => setWallet(e.target.value)} inputMode="numeric" />
            </Field>
            <Button className="w-full" disabled={mutations.wallet.isPending} onClick={() => mutations.wallet.mutate(Number(wallet) || 0)}>
              ذخیره و اطلاع‌رسانی به کاربر
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Send className="h-4 w-4" /> پیام شخصی</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <Textarea rows={5} value={msg} onChange={(e) => setMsg(e.target.value)} placeholder="متن پیام مدیریت برای این کاربر…" />
            <Button
              className="w-full"
              disabled={mutations.message.isPending || !msg.trim()}
              onClick={() => mutations.message.mutate(msg, { onSuccess: () => setMsg("") })}
            >
              <Send className="h-4 w-4" /> ارسال پیام در تلگرام
            </Button>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle>
              تاریخچه شارژ — مجموع تاییدشده: <span className="text-emerald-300">{toman(data.topups_total)}</span> ت
            </CardTitle>
            <PeriodTabs value={topupPeriod} onChange={setTopupPeriod} />
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {data.topups.length === 0 ? (
            <EmptyState icon={Receipt} title="شارژی ثبت نشده" hint="در این بازه‌ی زمانی شارژی وجود ندارد." />
          ) : (
            data.topups.map((t, i) => {
              const st = statusBadge(s(t.status));
              return (
                <div key={i} className="rounded-2xl border border-border bg-white/[0.02] p-4">
                  <div className="flex items-center justify-between gap-2">
                    <b className="text-white">{toman(t.amount_toman)} تومان</b>
                    <div className="flex items-center gap-2">
                      <Badge variant={st.variant}>{st.label}</Badge>
                      <span className="text-xs text-muted-foreground">{jalaliDate(n(t.created_at))}</span>
                    </div>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    روش: {t.method === "crypto" ? "رمزارز (تتر)" : "کارت به کارت"}
                    {t.txid ? ` • TXID: ${s(t.txid)}` : ""}
                  </div>
                  {!!t.receipt_file_id && (
                    <a href={`/admin/file/${s(t.receipt_file_id)}`} target="_blank" rel="noreferrer">
                      <img src={`/admin/file/${s(t.receipt_file_id)}`} loading="lazy" className="mt-3 max-h-96 w-full rounded-xl border border-border object-contain" alt="رسید پرداخت" />
                    </a>
                  )}
                </div>
              );
            })
          )}
        </CardContent>
      </Card>
    </div>
  );
}
