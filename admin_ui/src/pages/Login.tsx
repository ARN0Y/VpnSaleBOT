import * as React from "react";
import { Hexagon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card } from "@/components/ui/card";
import { useAuth } from "@/auth/AuthContext";
import { BRAND } from "@/brand";

export function Login() {
  const { login } = useAuth();
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(username.trim(), password);
    } catch {
      setError("نام کاربری یا رمز عبور نادرست است.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative grid min-h-screen place-items-center overflow-hidden px-4">
      {/* ambient brand glow behind the card */}
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-1/2 h-[34rem] w-[34rem] -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand/10 blur-[120px]"
      />
      <Card className="relative z-10 w-full max-w-md p-7 shadow-glow">
        <div className="mb-6 text-center">
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl border border-brand/25 bg-brand/10 text-brand shadow-brand-glow">
            <Hexagon className="h-8 w-8" />
          </div>
          <p className="text-[0.68rem] font-black uppercase tracking-wide text-muted-foreground">
            {BRAND.console}
          </p>
          <h1 className="mt-2 text-2xl font-black text-white">ورود مدیریت {BRAND.emoji}</h1>
          <p className="mt-2 text-sm leading-7 text-muted-foreground">
            اطلاعات ورود تعریف‌شده در فایل محیطی را وارد کنید.
          </p>
        </div>
        {error && (
          <div className="mb-5 rounded-xl border border-rose-400/20 bg-rose-400/10 p-3 text-sm font-bold text-rose-100">
            {error}
          </div>
        )}
        <form onSubmit={onSubmit} className="space-y-4">
          <div className="space-y-2">
            <label className="text-xs font-bold text-muted-foreground">نام کاربری</label>
            <Input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required />
          </div>
          <div className="space-y-2">
            <label className="text-xs font-bold text-muted-foreground">رمز عبور</label>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          <Button type="submit" size="lg" className="w-full" disabled={busy}>
            {busy ? "در حال ورود…" : "ورود به پنل"}
          </Button>
        </form>
      </Card>
    </div>
  );
}
