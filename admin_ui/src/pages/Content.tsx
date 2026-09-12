import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Eye, MessageSquareText, RotateCcw, Save, Type } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import type { BotMessage, BotButton } from "@/lib/types";

/** The text as the bot would send it: an override if there is one, else the
 *  built-in default. The editor always shows something real. */
function effective(message: BotMessage): string {
  return message.value.trim() ? message.value : message.default;
}

function MessageEditor({
  message, draft, onChange, onReset,
}: {
  message: BotMessage;
  draft: string | undefined;
  onChange: (value: string) => void;
  onReset: () => void;
}) {
  const { toast } = useToast();
  const [preview, setPreview] = React.useState("");
  const value = draft ?? effective(message);
  const dirty = draft !== undefined && draft !== effective(message);

  // Placeholders the bot will not fill. Shown as a warning rather than blocked:
  // the bot renders them literally, so a half-finished edit is never fatal.
  const stray = React.useMemo(() => {
    const allowed = new Set(message.placeholders);
    const found = new Set(
      Array.from(value.matchAll(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g)).map((m) => m[1]),
    );
    return Array.from(found).filter((name) => !allowed.has(name));
  }, [value, message.placeholders]);

  const insert = (name: string) => onChange(`${value}{${name}}`);

  const show = useMutation({
    mutationFn: () => api.previewContent(message.key, value),
    onSuccess: (r) => setPreview(r.rendered),
    onError: (e: Error) => toast({ title: "پیش‌نمایش ناموفق", description: e.message, variant: "error" }),
  });

  return (
    <div className="rounded-2xl border border-border bg-white/[0.02] p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-bold text-white">{message.label}</span>
            {message.customised && <Badge variant="success">سفارشی</Badge>}
            {dirty && <Badge variant="warning">ذخیره نشده</Badge>}
          </div>
          {message.note && (
            <p className="mt-1 text-[0.68rem] leading-6 text-muted-foreground">{message.note}</p>
          )}
        </div>
        <div className="flex items-center gap-1">
          <Button size="sm" variant="ghost" onClick={() => show.mutate()} title="پیش‌نمایش">
            <Eye className="h-4 w-4" /> پیش‌نمایش
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={!message.customised && !dirty}
            onClick={() => { setPreview(""); onReset(); }}
            title="بازگشت به متن پیش‌فرض"
          >
            <RotateCcw className="h-4 w-4" /> پیش‌فرض
          </Button>
        </div>
      </div>

      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={message.multiline ? 7 : 2}
        dir="rtl"
        className="mt-3 w-full rounded-xl border border-input bg-card px-3 py-2 font-mono text-[0.8rem] leading-7 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />

      {message.placeholders.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1">
          <span className="text-[0.65rem] text-muted-foreground">افزودن:</span>
          {message.placeholders.map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => insert(name)}
              className="rounded-md border border-border bg-white/[0.04] px-1.5 py-0.5 font-mono text-[0.65rem] text-brand hover:border-brand/40"
            >
              {"{" + name + "}"}
            </button>
          ))}
        </div>
      )}

      {stray.length > 0 && (
        <div className="mt-2 flex items-start gap-1.5 rounded-lg border border-amber-400/30 bg-amber-400/5 p-2 text-[0.65rem] leading-6 text-amber-200">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            این نام‌ها پر نمی‌شوند و همان‌طور که هستند نمایش داده می‌شوند:{" "}
            <span className="font-mono">{stray.map((s) => `{${s}}`).join(" ")}</span>
          </span>
        </div>
      )}

      {preview && (
        <div className="mt-3 rounded-xl border border-emerald-400/25 bg-emerald-400/[0.04] p-3">
          <div className="mb-1 text-[0.65rem] text-emerald-200/80">پیش‌نمایش در ربات:</div>
          <div className="whitespace-pre-wrap text-[0.8rem] leading-7 text-foreground" dir="rtl">
            {preview}
          </div>
        </div>
      )}
    </div>
  );
}

export function Content() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data, isLoading } = useQuery({ queryKey: ["content"], queryFn: () => api.content() });

  // Only edited entries are held here, so a reload never fights the operator
  // and saving sends exactly what they changed.
  const [messages, setMessages] = React.useState<Record<string, string>>({});
  const [buttons, setButtons] = React.useState<Record<string, string>>({});
  const dirtyCount = Object.keys(messages).length + Object.keys(buttons).length;

  const save = useMutation({
    mutationFn: () => api.saveContent({ messages, buttons }),
    onSuccess: (r) => {
      const stray = Object.values(r.unknown_placeholders || {}).flat();
      toast({
        title: stray.length ? "ذخیره شد — با هشدار" : "ذخیره شد",
        description: stray.length
          ? `این نام‌ها پر نمی‌شوند: ${stray.map((s) => `{${s}}`).join(" ")}`
          : undefined,
        variant: stray.length ? "info" : "success",
      });
      setMessages({});
      setButtons({});
      qc.invalidateQueries({ queryKey: ["content"] });
    },
    onError: (e: Error) => toast({ title: "ذخیره نشد", description: e.message, variant: "error" }),
  });

  if (isLoading || !data) {
    return <div className="space-y-4"><Skeleton className="h-12 w-64" /><Skeleton className="h-72" /></div>;
  }

  const groups = data.groups.filter((g) => data.messages.some((m) => m.group === g.key));

  return (
    <div className="space-y-5">
      <Card className="border-brand/20 bg-gradient-to-br from-brand/[0.07] to-transparent">
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle className="flex items-center gap-2">
                <MessageSquareText className="h-5 w-5 text-brand" /> متن‌ها و دکمه‌های ربات
              </CardTitle>
              <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
                هر پیامی که ربات می‌فرستد و هر دکمه‌ای که نشان می‌دهد، از همین‌جا قابل تغییر است.
                متنِ خالی یعنی «از متن پیش‌فرض استفاده کن» — با دکمه‌ی <b className="text-white">پیش‌فرض</b>
                {" "}هر زمان می‌توانید برگردید.
              </p>
            </div>
            <div className="flex items-center gap-2">
              {dirtyCount > 0 && <Badge variant="warning">{dirtyCount} تغییر ذخیره‌نشده</Badge>}
              <Button disabled={!dirtyCount || save.isPending} onClick={() => save.mutate()}>
                <Save className="h-4 w-4" /> {save.isPending ? "در حال ذخیره…" : "ذخیره"}
              </Button>
            </div>
          </div>
        </CardHeader>
      </Card>

      <Tabs defaultValue={groups[0]?.key ?? "buttons"} className="space-y-5">
        <div className="sticky top-16 z-20 -mx-1 overflow-x-auto pb-1">
          <TabsList className="w-full justify-start">
            {groups.map((g) => (
              <TabsTrigger key={g.key} value={g.key} className="px-4 py-2 text-sm">{g.label}</TabsTrigger>
            ))}
            <TabsTrigger value="buttons" className="px-4 py-2 text-sm">دکمه‌های منو</TabsTrigger>
          </TabsList>
        </div>

        {groups.map((g) => (
          <TabsContent key={g.key} value={g.key} className="space-y-3">
            {data.messages.filter((m) => m.group === g.key).map((message) => (
              <MessageEditor
                key={message.key}
                message={message}
                draft={messages[message.key]}
                onChange={(value) => setMessages((s) => ({ ...s, [message.key]: value }))}
                onReset={() => setMessages((s) => ({ ...s, [message.key]: "" }))}
              />
            ))}
          </TabsContent>
        ))}

        <TabsContent value="buttons" className="space-y-3">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <Type className="h-4 w-4" /> نام دکمه‌های کیبورد ربات
              </CardTitle>
              <p className="text-xs leading-6 text-muted-foreground">
                نام جدید بلافاصله اعمال می‌شود. نام قبلی هم برای کاربرانی که کیبوردشان هنوز
                به‌روز نشده کار می‌کند، پس کسی وسط کار گیر نمی‌افتد.
              </p>
            </CardHeader>
            <CardContent className="grid gap-3 sm:grid-cols-2">
              {data.buttons.map((button: BotButton) => {
                const draft = buttons[button.action];
                const value = draft ?? (button.value || button.default);
                return (
                  <div key={button.action}>
                    <div className="mb-1 flex items-center gap-2">
                      <span className="text-[0.72rem] font-bold text-white">{button.label}</span>
                      {button.customised && <Badge variant="success">سفارشی</Badge>}
                    </div>
                    <div className="flex gap-1">
                      <Input
                        value={value}
                        dir="rtl"
                        onChange={(e) => setButtons((s) => ({ ...s, [button.action]: e.target.value }))}
                      />
                      <Button
                        size="icon"
                        variant="ghost"
                        title="بازگشت به پیش‌فرض"
                        disabled={!button.customised && draft === undefined}
                        onClick={() => setButtons((s) => ({ ...s, [button.action]: "" }))}
                      >
                        <RotateCcw className="h-4 w-4" />
                      </Button>
                    </div>
                    <div className="mt-1 text-[0.62rem] text-muted-foreground">
                      پیش‌فرض: {button.default}
                    </div>
                  </div>
                );
              })}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
