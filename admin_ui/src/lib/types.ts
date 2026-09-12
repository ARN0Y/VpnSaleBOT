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
};

export type DiscountKind = "percent" | "fixed";
export type DiscountAudience = "all" | "users" | "agents" | "new";
export type DiscountApplies = "all" | "purchase" | "renewal";

export interface DiscountCode {
  code: string;
  title: string;
  kind: DiscountKind;
  value: number;
  max_discount_toman: number;
  min_order_toman: number;
  max_order_toman: number;
  /** epoch seconds; 0 = open-ended */
  starts_at: number;
  ends_at: number;
  max_uses: number;
  max_uses_per_user: number;
  audience: DiscountAudience;
  applies_to: DiscountApplies;
  user_ids: number[];
  plan_ids: string[];
  category_ids: string[];
  enabled: boolean;
  note: string;
  used_count: number;
  total_discount_toman: number;
  created_at: number;
  updated_at: number;
}

export interface DiscountRedemption {
  order_id: string;
  user_id: number;
  base_toman: number;
  amount_toman: number;
  order_kind: string;
  status: string;
  created_at: number;
  first_name?: string | null;
  username?: string | null;
}

export interface DiscountDetail extends DiscountCode {
  redemptions: DiscountRedemption[];
  stats: { used: number; buyers: number; given: number; gross: number };
}

export interface DiscountBundle {
  items: DiscountCode[];
  overview: { total: number; active: number; uses: number; given: number };
  plans: { id: string; title: string; category_id: string }[];
  categories: { id: string; title: string; emoji: string }[];
}

export interface BotMessage {
  key: string;
  label: string;
  group: string;
  group_label: string;
  default: string;
  value: string;
  customised: boolean;
  placeholders: string[];
  note: string;
  multiline: boolean;
}

export interface BotButton {
  action: string;
  label: string;
  default: string;
  value: string;
  customised: boolean;
}

export interface ContentBundle {
  groups: { key: string; label: string }[];
  messages: BotMessage[];
  buttons: BotButton[];
}

export interface ResellerPackage {
  id: string;
  title: string;
  traffic_gb: number;
  price: number;
  days: number;
  user_limit: number;
  note: string;
  enabled: boolean;
  sort: number;
}

export interface ResellerSettings {
  enabled: boolean;
  topup_enabled: boolean;
  login_url: string;
  role_name: string;
  username_prefix: string;
}

export interface ResellerBundle {
  settings: ResellerSettings;
  packages: ResellerPackage[];
  problems: Record<string, string[]>;
  overview: { total: number; active: number; sold_bytes: number; used_bytes: number; revenue: number };
  bytes_per_gb: number;
}

export interface SoldPanel {
  panel_id: string;
  user_id: number;
  pg_username: string;
  title: string;
  traffic_bytes: number;
  used_bytes: number;
  price_toman: number;
  status: string;
  created_at: number;
  expires_at: number;
  synced_at: number;
  first_name?: string | null;
  username?: string | null;
}

export interface LoginLook {
  title: string;
  tagline: string;
  image_url: string;
  layout: string;
  overlay: number;
  layouts: { key: string; label: string }[];
}

export interface Appearance {
  banner_enabled: boolean;
  banner_url: string;
  banner_file_id: string;
  button_style: string;
  styles: { key: string; label: string }[];
  preview: Record<string, string>;
  login: LoginLook;
}
