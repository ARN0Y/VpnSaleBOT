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

export interface OrderFilters {
  q?: string;
  /** Inclusive start, epoch seconds. */
  from?: number;
  /** Exclusive end, epoch seconds. */
  to?: number;
  status?: string;
  type?: string;
  method?: string;
  user_id?: number;
  min_amount?: number;
  max_amount?: number;
  sort?: string;
}

export interface OrderTopBuyer {
  user_id: number;
  first_name?: string | null;
  username?: string | null;
  orders: number;
  approved_orders: number;
  spent: number;
  gb: number;
}

export interface OrdersSummary {
  total_count: number;
  buyers: number;
  approved_count: number;
  pending_count: number;
  rejected_count: number;
  approved_amount: number;
  total_amount: number;
  discount_amount: number;
  approved_gb: number;
  approved_qty: number;
  avg_order_value: number;
  first_at: number;
  last_at: number;
  top_buyers: OrderTopBuyer[];
  by_payment_method: { payment_method: string; orders: number; amount: number }[];
}

export interface OrdersReport {
  items: Order[];
  summary: OrdersSummary;
  page: number;
  page_size: number;
  has_more: boolean;
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

export type PlanTarget =
  | { kind: "pasarguard"; group: string }
  | { kind: "xui"; panel: string };

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
  /** Let the buyer type an exact volume between min_gb and max_gb. */
  custom: boolean;
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
  problems: Record<string, string[]>;
  migrated_from_packages: boolean;
};
