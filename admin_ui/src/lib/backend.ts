// Which panel a config lives on. Subscriptions created on PasarGuard are tagged
// with this sentinel inbound_id (see async_storefront/models.py); everything else
// is a normal 3x-ui inbound.
export const PG_INBOUND_SENTINEL = -100;

export type Backend = "xui" | "pasarguard";

export const BACKEND_LABEL: Record<Backend, string> = {
  xui: "3x-ui",
  pasarguard: "PasarGuard",
};

/** Resolve the backend from a subscription row's inbound_id. */
export function backendOf(inboundId: unknown): Backend {
  return Number(inboundId ?? 0) === PG_INBOUND_SENTINEL ? "pasarguard" : "xui";
}

export function isPasarGuard(inboundId: unknown): boolean {
  return backendOf(inboundId) === "pasarguard";
}
