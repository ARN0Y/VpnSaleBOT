import * as React from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Send, Megaphone } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

const AUDIENCES = [
  { key: "all", label: "همه کاربران" },
  { key: "agents", label: "فقط نماینده‌ها" },
  { key: "customers", label: "فقط کاربران عادی" },
];

export function Broadcast() {
  const [audience, setAudience] = React.useState("all");
  const [message, setMessage] = React.useState("");
  const [done, setDone] = React.useState<string | null>(null);

  const { data } = useQuery({
    queryKey: ["broadcast-info", audience],
    queryFn: () => api.broadcastInfo(audience),
  });

  const send = useMutation({
    mutationFn: () => api.sendBroadcast(audience, message),
    onSuccess: (r) => {
      if (r.ok) {
        setDone(String(r.event_id || ""));
        setMessage("");
      }
    },
  });

  return (
    <Card className="mx-auto max-w-2xl">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Megaphone className="h-5 w-5" /> پیام همگانی
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          پیام در صف ارسال (background) قرار می‌گیرد و با محدودکننده‌ی نرخ تلگرام ارسال می‌شود.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex gap-1 rounded-xl border border-border bg-white/[0.02] p-1">
          {AUDIENCES.map((a) => (
            <Button key={a.key} size="sm" variant={audience === a.key ? "default" : "ghost"} onClick={() => setAudience(a.key)}>
              {a.label}
            </Button>
          ))}
        </div>
        <div className="rounded-xl border border-border bg-white/[0.02] p-3 text-sm text-muted-foreground">
          مخاطبان این گروه: <b className="text-white">{data ? data.target_count : "…"}</b> کاربر
        </div>
        <Textarea rows={7} value={message} onChange={(e) => setMessage(e.target.value)} placeholder="متن پیام همگانی (HTML مجاز است)…" />
        {done !== null && (
          <div className="rounded-xl border border-emerald-400/20 bg-emerald-400/10 p-3 text-sm font-bold text-emerald-200">
            پیام در صف رویدادها قرار گرفت. پیشرفت را در صفحه‌ی «رویدادها» ببینید.
          </div>
        )}
        <Button className="w-full" disabled={send.isPending || !message.trim()} onClick={() => send.mutate()}>
          <Send className="h-4 w-4" /> ارسال به {AUDIENCES.find((a) => a.key === audience)?.label}
        </Button>
      </CardContent>
    </Card>
  );
}
