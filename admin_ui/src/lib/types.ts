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
