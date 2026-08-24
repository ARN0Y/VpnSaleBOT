export interface DashboardMetrics {
  users: number;
  agents: number;
  open_agents: number;
  closed_agents: number;
  revenue: number;
  traffic_gb: number;
  pending_topups: number;
  pending_agent_requests: number;
}

export interface Order {
  order_id: string;
  user_id: number;
  gb: number;
  qty: number;
  final_price: number;
  status: string;
  created_at: number;
  order_type?: string;
  client_name?: string | null;
  first_name?: string | null;
  username?: string | null;
  subscription_name?: string | null;
  [key: string]: unknown;
}

export interface User {
  user_id: number;
  first_name?: string | null;
  username?: string | null;
  joined_at: number;
  approved_orders: number;
  total_spent: number;
  wallet_balance: number;
  disabled: number;
  access_level: string | null;
  total_gb_purchased: number;
  [key: string]: unknown;
}

export interface DashboardResponse {
  metrics: DashboardMetrics;
  recent_orders: Order[];
}

export interface ListResponse<T> {
  items: T[];
  count: number;
}

export interface Paginated<T> {
  items: T[];
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface UserDetailBundle {
  user: Record<string, unknown>;
  subscriptions: Record<string, unknown>[];
  subs_total: number;
  subs_page: number;
  subs_total_pages: number;
  orders: Record<string, unknown>[];
  topups: Record<string, unknown>[];
  topups_total: number;
  ledger: Record<string, unknown>[];
  agent_24h: Record<string, unknown> | null;
  period: string;
  topup_period: string;
}

// ─────────────────────────── sales catalog ───────────────────────────
// A plan is what you sell; a panel is only where it runs. Two decouplings
// matter: `display.volume_label` overrides what the buyer is told about volume
// (so a fair-usage cap can be advertised as "نامحدود"), and `pricing` is a rule
// rather than a number.

export type PlanTarget =
  | { kind: "pasarguard"; group: string }
  | { kind: "xui"; panel: string }
  | { kind: "athena"; node_id: number; outbound: string };

export type PricingMode = "fixed" | "linear" | "tiered";
export type VolumeMode = "fixed" | "variable";

export type PriceTier = {
  min_gb: number;
  price_per_gb: number;
  agent_price_per_gb: number;
};

export type PlanPricing = {
  mode: PricingMode;
  price: number;
  agent_price: number;
  base: number;
  agent_base: number;
  per_gb: number;
  agent_per_gb: number;
  tiers: PriceTier[];
  round_to: number;
};

export type PlanVolume = {
  mode: VolumeMode;
  gb: number;
  days: number;
  min_gb: number;
  max_gb: number;
  step_gb: number;
};

export type Plan = {
  id: string;
  category_id: string;
  title: string;
  enabled: boolean;
  sort: number;
  target: PlanTarget;
  volume: PlanVolume;
  display: { volume_label: string; hide_volume: boolean; note: string; badge: string };
  pricing: PlanPricing;
};

export type Category = {
  id: string;
  title: string;
  emoji: string;
  description: string;
  enabled: boolean;
  sort: number;
};

export type CatalogData = { version: number; categories: Category[]; plans: Plan[] };

export type CatalogBundle = {
  catalog: CatalogData;
  groups: { id: number; name: string }[];
  groups_error: string;
  panels: { key: string; label: string }[];
  athena: { enabled: boolean; label: string; error: string; nodes: { id: number; name: string }[]; outbounds: { name: string; label: string; status: string }[] };
  problems: Record<string, string[]>;
  migrated_from_packages: boolean;
};
