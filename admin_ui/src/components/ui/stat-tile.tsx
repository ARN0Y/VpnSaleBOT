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
    <div className={cn("rounded-2xl border border-border bg-card/60 p-4", className)}>
      <div className="flex items-center justify-between">
        <span className="text-[0.7rem] font-medium text-muted-foreground">{label}</span>
        {Icon && (
          <span className="flex h-7 w-7 items-center justify-center rounded-lg border border-border bg-white/[0.03] text-foreground">
            <Icon className="h-4 w-4" />
          </span>
        )}
      </div>
      <div className="mt-2 text-xl font-black tracking-tight text-white">{value}</div>
      {sub && <div className="mt-0.5 text-[0.65rem] text-muted-foreground">{sub}</div>}
    </div>
  );
}
