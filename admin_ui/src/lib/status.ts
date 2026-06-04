type Variant = "default" | "success" | "warning" | "danger" | "muted";

const MAP: Record<string, { label: string; variant: Variant }> = {
  approved: { label: "تایید شده", variant: "success" },
  pending: { label: "در انتظار", variant: "warning" },
  rejected: { label: "رد شده", variant: "danger" },
  failed: { label: "ناموفق", variant: "danger" },
  reserved: { label: "رزرو", variant: "muted" },
};

export function statusBadge(status: string): { label: string; variant: Variant } {
  return MAP[String(status || "").toLowerCase()] || { label: status || "—", variant: "muted" };
}
