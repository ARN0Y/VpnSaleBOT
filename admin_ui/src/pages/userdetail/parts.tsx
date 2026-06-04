import { Button } from "@/components/ui/button";
import { PERIODS } from "./helpers";

export function PeriodTabs({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex gap-1 rounded-xl border border-border bg-white/[0.02] p-1">
      {PERIODS.map(([k, label]) => (
        <Button key={k} size="sm" variant={value === k ? "default" : "ghost"} onClick={() => onChange(k)}>
          {label}
        </Button>
      ))}
    </div>
  );
}

export function InfoRow({ k, v, accent }: { k: string; v: string; accent?: "emerald" | "white" }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border/60 py-2 last:border-0">
      <dt className="text-sm text-muted-foreground">{k}</dt>
      <dd className={accent === "emerald" ? "font-bold text-emerald-300" : "font-bold text-white"}>{v}</dd>
    </div>
  );
}
