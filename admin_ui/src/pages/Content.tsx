import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, Eye, Image as ImageIcon, LayoutTemplate, MessageSquareText, Palette,
  RotateCcw, Save, Type,
} from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import type { BotMessage, BotButton, LoginLook } from "@/lib/types";

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

function AppearanceCard() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data } = useQuery({ queryKey: ["appearance"], queryFn: () => api.appearance() });
  const [url, setUrl] = React.useState<string | null>(null);

  const save = useMutation({
    mutationFn: (patch: Record<string, unknown>) => api.saveAppearance(patch),
    onSuccess: () => {
      toast({ title: "ظاهر ربات به‌روزرسانی شد", variant: "success" });
      setUrl(null);
      qc.invalidateQueries({ queryKey: ["appearance"] });
    },
    onError: (e: Error) => toast({ title: "ذخیره نشد", description: e.message, variant: "error" }),
  });

  if (!data) return <Skeleton className="h-64" />;
  const bannerUrl = url ?? data.banner_url;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            <ImageIcon className="h-4 w-4" /> بنر بالای پیام خوش‌آمد
          </CardTitle>
          <p className="text-xs leading-6 text-muted-foreground">
            تصویری که همراه پیام خوش‌آمد فرستاده می‌شود. اگر ارسال تصویر به هر دلیلی
            ناموفق باشد، ربات همان متن را می‌فرستد و کاربر بدون منو نمی‌ماند.
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          <label className="flex items-start gap-2 rounded-xl border border-border bg-white/[0.02] p-3 text-sm">
            <input
              type="checkbox"
              checked={data.banner_enabled}
              onChange={(e) => save.mutate({ banner_enabled: e.target.checked })}
              className="mt-0.5 h-4 w-4 accent-[hsl(var(--brand))]"
            />
            <span className="font-bold text-white">بنر نمایش داده شود</span>
          </label>
          <div className="flex gap-2">
            <Input
              value={bannerUrl}
              dir="ltr"
              placeholder="https://example.com/banner.jpg"
              onChange={(e) => setUrl(e.target.value)}
            />
            <Button disabled={save.isPending} onClick={() => save.mutate({ banner_url: bannerUrl })}>
              <Save className="h-4 w-4" /> ذخیره
            </Button>
          </div>
          {data.banner_file_id && (
            <div className="rounded-xl border border-emerald-400/25 bg-emerald-400/[0.04] p-3 text-[0.68rem] leading-6 text-emerald-100">
              یک تصویر آپلودشده ذخیره شده و به آدرس بالا ارجح است.
              <button
                type="button"
                className="mr-2 underline hover:text-white"
                onClick={() => save.mutate({ banner_file_id: "" })}
              >
                حذف تصویر آپلودشده
              </button>
            </div>
          )}
          {bannerUrl && data.banner_enabled && !data.banner_file_id && (
            <img
              src={bannerUrl}
              alt="پیش‌نمایش بنر"
              className="max-h-48 w-full rounded-xl border border-border object-cover"
              onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
            />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            <Palette className="h-4 w-4" /> ظاهر دکمه‌های منو
          </CardTitle>
          <p className="text-xs leading-6 text-muted-foreground">
            تلگرام اجازه‌ی رنگ‌کردن دکمه‌های کیبورد را به ربات نمی‌دهد؛ کاری که می‌شود
            کرد یک نشانه‌ی ثابت در ابتدای هر دکمه است. نام دکمه‌ها دست‌نخورده می‌ماند و
            با برگشتن به حالت ساده، دقیقاً همان چیزی که نوشته‌اید باقی می‌ماند.
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-2 sm:grid-cols-3">
            {data.styles.map((style) => {
              const active = data.button_style === style.key;
              return (
                <button
                  key={style.key}
                  onClick={() => save.mutate({ button_style: style.key })}
                  className={`card-hover rounded-2xl border p-3 text-right transition ${
                    active ? "border-brand/40 bg-brand/[0.06]" : "border-border hover:border-white/20"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-bold text-white">{style.label}</span>
                    {active && <Badge variant="success">فعال</Badge>}
                  </div>
                </button>
              );
            })}
          </div>
          <div className="rounded-xl border border-border bg-white/[0.02] p-3">
            <div className="mb-2 text-[0.65rem] text-muted-foreground">پیش‌نمایش کیبورد:</div>
            <div className="flex flex-wrap gap-1.5">
              {Object.values(data.preview).map((label, i) => (
                <span key={i} className="rounded-lg border border-border bg-card px-3 py-1.5 text-xs text-foreground">
                  {label}
                </span>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

/** A scaled-down sketch of the sign-in screen, drawn from the same settings the
 *  real page reads — so the operator sees what they are choosing rather than
 *  saving and switching windows to find out. */
function LoginPreview({ look, image }: { look: LoginLook; image: string }) {
  const [failed, setFailed] = React.useState(false);
  React.useEffect(() => setFailed(false), [image]);
  const showImage = Boolean(image) && !failed;

  const art = (
    <div className="relative h-full w-full overflow-hidden bg-black">
      {showImage ? (
        <img src={image} alt="" className="h-full w-full object-cover" onError={() => setFailed(true)} />
      ) : (
        <div className="absolute inset-0 bg-[radial-gradient(120%_100%_at_70%_20%,hsl(var(--brand)/0.35),transparent_60%)]" />
      )}
      <div className="absolute inset-0 bg-background" style={{ opacity: look.overlay / 100 }} />
    </div>
  );

  const form = (
    <div className="flex h-full flex-col justify-center gap-1.5 p-3">
      <div className="text-[0.6rem] font-black text-white">{look.title || "ورود مدیریت"}</div>
      <div className="text-[0.5rem] text-muted-foreground">{look.tagline}</div>
      <div className="mt-1 h-2.5 w-full rounded bg-white/10" />
      <div className="h-2.5 w-full rounded bg-white/10" />
      <div className="mt-0.5 h-2.5 w-full rounded bg-primary/70" />
    </div>
  );

  if (look.layout === "centered") {
    return (
      <div className="relative h-36 overflow-hidden rounded-xl border border-border">
        {art}
        <div className="absolute inset-0 grid place-items-center p-3">
          <div className="w-1/2 rounded-lg border border-border/60 bg-card/85 backdrop-blur-sm">{form}</div>
        </div>
      </div>
    );
  }
  const artFirst = look.layout !== "split-left";
  return (
    <div className="grid h-36 grid-cols-2 overflow-hidden rounded-xl border border-border">
      <div className={artFirst ? "order-2" : "order-1"}>{art}</div>
      <div className={artFirst ? "order-1 bg-card" : "order-2 bg-card"}>{form}</div>
    </div>
  );
}

function LoginLookCard() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data } = useQuery({ queryKey: ["appearance"], queryFn: () => api.appearance() });
  // Held locally while editing so the preview reacts to typing, and only the
  // fields actually touched are sent.
  const [draft, setDraft] = React.useState<Partial<LoginLook> | null>(null);

  const save = useMutation({
    mutationFn: (patch: Record<string, unknown>) => api.saveAppearance(patch),
    onSuccess: () => {
      toast({ title: "ظاهر صفحه‌ی ورود ذخیره شد", variant: "success" });
      setDraft(null);
      qc.invalidateQueries({ queryKey: ["appearance"] });
    },
    onError: (e: Error) => toast({ title: "ذخیره نشد", description: e.message, variant: "error" }),
  });

  if (!data) return <Skeleton className="h-96" />;
  const look: LoginLook = { ...data.login, ...(draft ?? {}) };
  const set = <K extends keyof LoginLook>(k: K, v: LoginLook[K]) =>
    setDraft((d) => ({ ...(d ?? {}), [k]: v }));
  const dirty = draft !== null;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-sm">
              <LayoutTemplate className="h-4 w-4" /> صفحه‌ی ورود پنل
            </CardTitle>
            <p className="mt-1 max-w-2xl text-xs leading-6 text-muted-foreground">
              همان صفحه‌ای که قبل از ورود دیده می‌شود. اگر تصویری نگذارید، یک پس‌زمینه‌ی
              آرام کشیده می‌شود که عمدی به نظر برسد — نه مثل عکسِ گم‌شده.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {dirty && <Badge variant="warning">ذخیره نشده</Badge>}
            <Button
              disabled={!dirty || save.isPending}
              onClick={() => save.mutate({
                login_title: look.title,
                login_tagline: look.tagline,
                login_image_url: look.image_url,
                login_layout: look.layout,
                login_overlay: look.overlay,
              })}
            >
              <Save className="h-4 w-4" /> {save.isPending ? "در حال ذخیره…" : "ذخیره"}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <LoginPreview look={look} image={look.image_url} />

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <div className="mb-1 text-[0.72rem] font-bold text-white">عنوان</div>
            <Input value={look.title} onChange={(e) => set("title", e.target.value)}
                   placeholder="ورود مدیریت" />
          </div>
          <div>
            <div className="mb-1 text-[0.72rem] font-bold text-white">زیرعنوان</div>
            <Input value={look.tagline} onChange={(e) => set("tagline", e.target.value)}
                   placeholder="دسترسی مدیر به پنل فروش" />
          </div>
          <div className="sm:col-span-2">
            <div className="mb-1 text-[0.72rem] font-bold text-white">آدرس تصویر پس‌زمینه</div>
            <Input value={look.image_url} dir="ltr"
                   placeholder="https://example.com/background.jpg"
                   onChange={(e) => set("image_url", e.target.value)} />
            <div className="mt-1 text-[0.62rem] text-muted-foreground">
              خالی بگذارید تا پس‌زمینه‌ی پیش‌فرض استفاده شود. اگر آدرس باز نشود، ربات
              خودکار به همان پیش‌فرض برمی‌گردد.
            </div>
          </div>
        </div>

        <div>
          <div className="mb-2 text-[0.72rem] font-bold text-white">چیدمان</div>
          <div className="grid gap-2 sm:grid-cols-3">
            {look.layouts.map((option) => {
              const active = look.layout === option.key;
              return (
                <button
                  key={option.key}
                  type="button"
                  onClick={() => set("layout", option.key)}
                  className={`card-hover rounded-xl border p-2.5 text-right text-xs transition ${
                    active ? "border-brand/40 bg-brand/[0.06] text-white" : "border-border text-muted-foreground hover:border-white/20"
                  }`}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
        </div>

        <div>
          <div className="mb-1 flex items-center justify-between">
            <span className="text-[0.72rem] font-bold text-white">تیرگی روی تصویر</span>
            <span className="font-mono text-[0.7rem] text-muted-foreground">{look.overlay}٪</span>
          </div>
          <input
            type="range"
            min={0}
            max={90}
            step={5}
            value={look.overlay}
            onChange={(e) => set("overlay", Number(e.target.value))}
            className="w-full accent-[hsl(var(--brand))]"
          />
          <div className="mt-1 text-[0.62rem] leading-5 text-muted-foreground">
            تصویر را تیره می‌کند تا متن روی آن خوانا بماند. تصویر روشن معمولاً به
            تیرگی بیشتری نیاز دارد.
          </div>
        </div>
      </CardContent>
    </Card>
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
            <TabsTrigger value="look" className="px-4 py-2 text-sm">ظاهر ربات</TabsTrigger>
            <TabsTrigger value="panel" className="px-4 py-2 text-sm">ظاهر پنل</TabsTrigger>
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

        <TabsContent value="look" className="space-y-4">
          <AppearanceCard />
        </TabsContent>

        <TabsContent value="panel" className="space-y-4">
          <LoginLookCard />
        </TabsContent>
      </Tabs>
    </div>
  );
}
