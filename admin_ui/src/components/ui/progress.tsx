import { cn } from "@/lib/utils";

type Tone = "default" | "warning" | "danger" | "success";

const TRACK = "h-2 w-full overflow-hidden rounded-full bg-white/[0.06]";
const FILL: Record<Tone, string> = {
  default: "bg-foreground",
  success: "bg-emerald-400",
  warning: "bg-amber-400",
  danger: "bg-rose-400",
};

export function Progress({
  value,
  tone = "default",
  className,
}: {
  value: number;
  tone?: Tone;
  className?: string;
}) {
  const pct = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  return (
    <div className={cn(TRACK, className)}>
      <div className={cn("h-full rounded-full transition-all duration-500", FILL[tone])} style={{ width: `${pct}%` }} />
    </div>
  );
}

// Circular progress ring (for credit/usage), pure SVG, no deps.
export function Ring({
  value,
  size = 64,
  stroke = 6,
  tone = "default",
  label,
}: {
  value: number;
  size?: number;
  stroke?: number;
  tone?: Tone;
  label?: string;
}) {
  const pct = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const dash = (pct / 100) * c;
  const color =
    tone === "danger" ? "hsl(0 72% 55%)" : tone === "warning" ? "hsl(38 92% 55%)" : tone === "success" ? "hsl(152 60% 50%)" : "hsl(0 0% 92%)";
  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="hsl(240 4% 16%)" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${dash} ${c}`}
          className="transition-all duration-700"
        />
      </svg>
      <span className="absolute text-xs font-black text-white">{label ?? `${Math.round(pct)}%`}</span>
    </div>
  );
}
