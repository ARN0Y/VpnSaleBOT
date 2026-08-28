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

const PAYMENT_LABELS: Record<string, string> = {
  rial: "کارت به کارت",
  wallet: "کیف پول",
  agent_wallet: "کیف پول نماینده",
  agent_open: "حساب باز نماینده",
  test_rial: "تست",
  crypto: "کریپتو",
};

export function paymentLabel(method: string): string {
  const key = String(method || "").toLowerCase();
  return PAYMENT_LABELS[key] || (key ? key : "—");
}

export const PAYMENT_METHODS = Object.keys(PAYMENT_LABELS);
