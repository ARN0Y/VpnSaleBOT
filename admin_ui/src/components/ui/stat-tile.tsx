import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export function StatTile({
  icon: Icon,
  label,
  value,
  sub,
  className,
}: {
  icon?: LucideIcon;
  label: string;
  value: string;
  sub?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "card-hover group relative overflow-hidden rounded-2xl border border-border bg-gradient-to-b from-card/80 to-card/40 p-4 shadow-card",
        className,
      )}
    >
      {/* faint top sheen */}
      <span className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-l from-transparent via-white/15 to-transparent" />
      <div className="flex items-center justify-between">
        <span className="text-[0.7rem] font-medium text-muted-foreground">{label}</span>
        {Icon && (
          <span className="flex h-8 w-8 items-center justify-center rounded-xl border border-border bg-white/[0.03] text-muted-foreground transition-colors group-hover:border-brand/30 group-hover:text-brand">
            <Icon className="h-4 w-4" />
          </span>
        )}
      </div>
      <div className="mt-2 text-xl font-black tracking-tight text-white">{value}</div>
      {sub && <div className="mt-0.5 text-[0.65rem] text-muted-foreground">{sub}</div>}
    </div>
  );
}
