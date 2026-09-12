import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Loader2 } from "lucide-react";
import { api, ApiError, setCsrf } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/**
 * The sign-in screen.
 *
 * Its appearance comes from a public endpoint, because this page renders before
 * anyone has a session and so cannot authenticate to fetch its own decoration.
 * The whole component is built around that request failing: FALLBACK is a
 * complete, presentable configuration, so a server mid-restart shows a plain
 * panel rather than an empty one.
 *
 * It also handles the first run, where no administrator exists yet: the panel
 * asks for the one-time code from its own service log instead of a password
 * that has not been chosen.
 */

interface Branding {
  title: string;
  tagline: string;
  image_url: string;
  layout: string;
  overlay: number;
}

const FALLBACK: Branding = {
  title: "ورود مدیریت",
  tagline: "دسترسی مدیر به پنل فروش",
  image_url: "",
  layout: "split-right",
  overlay: 55,
};

function Mark() {
  return (
    <div className="flex items-center gap-2.5">
      <span className="relative flex h-7 w-7 items-center justify-center">
        <span className="absolute inset-0 rotate-45 rounded-[7px] border border-white/25" />
        <span className="absolute inset-[9px] rotate-45 rounded-[2px] bg-white/80" />
      </span>
      <span className="text-[12px] font-bold tracking-[0.2em] text-white/90">PANEL</span>
    </div>
  );
}

/** The artwork panel, and the backdrop for the centred layout. */
function Artwork({ brand, className = "" }: { brand: Branding; className?: string }) {
  // Tracked so a broken address falls back to the gradient rather than leaving
  // a dead image frame on the page.
  const [failed, setFailed] = React.useState(false);
  React.useEffect(() => setFailed(false), [brand.image_url]);
  const showImage = Boolean(brand.image_url) && !failed;

  return (
    <div className={`overflow-hidden bg-black ${className}`}>
      {showImage ? (
        <img
          src={brand.image_url}
          alt=""
          aria-hidden="true"
          onError={() => setFailed(true)}
          className="h-full w-full object-cover"
        />
      ) : (
        // The built-in look, used when no artwork is set. Deliberately quiet:
        // it should read as intentional, not as a missing image.
        <div className="absolute inset-0 bg-[radial-gradient(120%_100%_at_70%_20%,hsl(var(--brand)/0.35),transparent_60%),radial-gradient(90%_80%_at_20%_90%,hsl(var(--primary)/0.18),transparent_55%)]">
          <div
            className="absolute inset-0 opacity-40"
            style={{
              backgroundImage: "radial-gradient(rgba(255,255,255,0.10) 1px, transparent 1px)",
              backgroundSize: "26px 26px",
            }}
          />
        </div>
      )}
      {/* Dim layer. The operator picks the picture, so the page cannot assume
          it is dark enough for white text — this is what keeps it readable. */}
      <div className="absolute inset-0 bg-background" style={{ opacity: brand.overlay / 100 }} />
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-l from-background/70 via-transparent to-transparent" />
    </div>
  );
}

export function Login({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [code, setCode] = React.useState("");
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const { data: look } = useQuery({
    queryKey: ["branding"],
    queryFn: () => api.branding(),
    staleTime: 5 * 60 * 1000,
    retry: false, // never leave the form waiting on decoration
  });
  const { data: setup } = useQuery({
    queryKey: ["setup-status"],
    queryFn: () => api.setupStatus(),
    retry: false,
  });
  const brand: Branding = { ...FALLBACK, ...(look ?? {}) };
  const needsSetup = setup?.needs_setup === true;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const r = needsSetup
        ? await api.completeSetup(code, username, password)
        : await api.login(username, password);
      setCsrf(r.csrf);
      onDone();
    } catch (err) {
      const known: Record<string, string> = {
        invalid_credentials: "نام کاربری یا رمز عبور نادرست است.",
        too_many_attempts: "تلاش‌های ناموفق زیاد بوده. چند دقیقه بعد دوباره امتحان کنید.",
        bad_setup_code: "کد راه‌اندازی نادرست است. کد را از لاگ سرویس پنل بردارید.",
        already_configured: "این پنل قبلاً راه‌اندازی شده؛ صفحه را تازه کنید و وارد شوید.",
        needs_setup: "این پنل هنوز راه‌اندازی نشده. صفحه را تازه کنید.",
      };
      const raw = err instanceof ApiError ? err.message : "";
      setError(known[raw] || raw || "ورود ناموفق بود.");
    } finally {
      setBusy(false);
    }
  };

  const card = (
    <div className="w-full max-w-[400px] rounded-2xl border border-border/60 bg-card/85 p-7 shadow-2xl shadow-black/40 backdrop-blur-md">
      <h1 className="text-[26px] font-black tracking-tight text-white">
        {needsSetup ? "راه‌اندازی اولیه" : brand.title}
      </h1>
      <p className="mt-1.5 text-sm leading-6 text-muted-foreground">
        {needsSetup
          ? "هنوز مدیری تعریف نشده. کد راه‌اندازی را از لاگ سرویس پنل بردارید و حساب مدیر را بسازید."
          : brand.tagline}
      </p>

      <form onSubmit={submit} className="mt-7 space-y-4">
        {needsSetup && (
          <div className="space-y-2">
            <label className="block text-[10px] font-bold tracking-[0.14em] text-muted-foreground">
              کد راه‌اندازی
            </label>
            <Input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              required
              autoFocus
              dir="ltr"
              className="h-11 font-mono"
              placeholder="از journalctl -u wolfpanel"
            />
          </div>
        )}
        <div className="space-y-2">
          <label className="block text-[10px] font-bold tracking-[0.14em] text-muted-foreground">
            نام کاربری
          </label>
          <Input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            autoFocus={!needsSetup}
            autoComplete="username"
            dir="ltr"
            className="h-11"
            placeholder="admin"
          />
        </div>
        <div className="space-y-2">
          <label className="block text-[10px] font-bold tracking-[0.14em] text-muted-foreground">
            {needsSetup ? "رمز عبور جدید" : "رمز عبور"}
          </label>
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete={needsSetup ? "new-password" : "current-password"}
            dir="ltr"
            className="h-11 tracking-[0.18em]"
            placeholder="••••••••"
          />
          {needsSetup && (
            <p className="text-[0.65rem] text-muted-foreground">حداقل ۱۰ کاراکتر.</p>
          )}
        </div>

        {error && (
          <div className="rounded-xl border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs leading-6 text-destructive">
            {error}
          </div>
        )}

        <Button type="submit" disabled={busy} className="group mt-1 h-11 w-full">
          {busy ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <>
              {needsSetup ? "ساخت حساب مدیر" : "ورود"}
              <ArrowLeft className="h-4 w-4 transition-transform group-hover:-translate-x-1" />
            </>
          )}
        </Button>
      </form>
    </div>
  );

  if (brand.layout === "centered") {
    return (
      <div className="relative flex min-h-screen items-center justify-center overflow-hidden p-6">
        <Artwork brand={brand} className="absolute inset-0" />
        <div className="relative flex w-full max-w-[400px] flex-col items-center">
          <div className="mb-8"><Mark /></div>
          {card}
        </div>
      </div>
    );
  }

  // Two columns. In an RTL page the artwork sits on the left by default, so
  // "split-left" is the one that moves it across.
  const artFirst = brand.layout !== "split-left";
  return (
    <div className="relative min-h-screen bg-background">
      <div className={`grid min-h-screen lg:grid-cols-2 ${artFirst ? "" : "lg:[&>*:last-child]:order-first"}`}>
        <div className="relative flex flex-col justify-between px-6 py-10 sm:px-12 lg:px-16">
          <Mark />
          <div className="flex flex-1 items-center py-12">{card}</div>
          <div className="text-[0.65rem] text-muted-foreground">پنل مدیریت فروش</div>
        </div>
        {/* Hidden on narrow screens: a cropped sliver of a wallpaper above a
            form is worse than no wallpaper at all. */}
        <Artwork brand={brand} className="relative hidden lg:block" />
      </div>
    </div>
  );
}
