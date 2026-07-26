import { ShieldCheck, Server } from "lucide-react";
import { cn } from "@/lib/utils";
import { BACKEND_LABEL, backendOf, type Backend } from "@/lib/backend";

/**
 * Shows which panel a config was created on. Pass either the subscription's
 * `inbound_id` or an explicit backend (orders carry a `backend` field).
 */
export function PanelBadge({
  inboundId,
  backend,
  className,
  size = "sm",
}: {
  inboundId?: unknown;
  backend?: Backend;
  className?: string;
  size?: "sm" | "xs";
}) {
  const kind: Backend = backend ?? backendOf(inboundId);
  const pg = kind === "pasarguard";
  const Icon = pg ? ShieldCheck : Server;
  return (
    <span
      title={pg ? "این سرویس روی پنل PasarGuard ساخته شده است" : "این سرویس روی پنل 3x-ui ساخته شده است"}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border font-bold",
        size === "xs" ? "px-1.5 py-0.5 text-[0.62rem]" : "px-2 py-0.5 text-[0.68rem]",
        pg
          ? "border-brand/35 bg-brand/10 text-brand"
          : "border-sky-400/30 bg-sky-400/10 text-sky-200",
        className,
      )}
    >
      <Icon className={size === "xs" ? "h-2.5 w-2.5" : "h-3 w-3"} />
      {BACKEND_LABEL[kind]}
    </span>
  );
}
