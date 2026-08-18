// Tiny fetch wrapper around the /admin/api/v1 JSON API.
// Sends the session cookie automatically (same-origin) and attaches the CSRF
// header on mutations. A 401 throws ApiError so the auth layer can react.

const BASE = "/admin/api/v1";

let csrfToken = "";
export function setCsrf(token: string) {
  csrfToken = token || "";
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method || "GET").toUpperCase();
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (method !== "GET" && method !== "HEAD") {
    headers["Content-Type"] = "application/json";
    if (csrfToken) headers["x-csrf-token"] = csrfToken;
  }
  const res = await fetch(`${BASE}${path}`, {
    credentials: "same-origin",
    ...options,
    headers,
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      message = body?.error || message;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, message);
  }
  return (await res.json()) as T;
}

type Row = Record<string, unknown>;
type Ok = { ok: boolean; [k: string]: unknown };

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });
}

export const api = {
  me: () => request<{ username: string; csrf: string }>("/me"),
  login: (username: string, password: string) =>
    post<{ ok: boolean; username: string; csrf: string }>("/login", { username, password }),
  logout: () => post<{ ok: boolean }>("/logout"),

  dashboard: (q = "") =>
    request<import("./types").DashboardResponse>(`/dashboard?q=${encodeURIComponent(q)}`),
  orders: (q = "", period = "all", page = 1) =>
    request<import("./types").Paginated<import("./types").Order>>(
      `/orders?q=${encodeURIComponent(q)}&period=${period}&page=${page}`,
    ),
  users: (q = "", filter = "all", page = 1) =>
    request<import("./types").Paginated<import("./types").User>>(
      `/users?q=${encodeURIComponent(q)}&filter=${filter}&page=${page}`,
    ),
  topups: (status = "pending") =>
    request<import("./types").ListResponse<Row>>(`/topups?status=${status}`),
  agentRequests: (status = "pending") =>
    request<import("./types").ListResponse<Row>>(`/agent-requests?status=${status}`),
  subscriptions: (q = "", page = 1) =>
    request<{ items: Row[]; total: number; page: number; page_size: number }>(
      `/subscriptions?q=${encodeURIComponent(q)}&page=${page}`,
    ),
  settings: () => request<{ items: Record<string, string> }>("/settings"),

  userDetail: (id: number, period = "all", topupPeriod = "all", subsPage = 1) =>
    request<import("./types").UserDetailBundle>(
      `/users/${id}/detail?period=${period}&topup_period=${topupPeriod}&subs_page=${subsPage}`,
    ),
  subscriptionDetail: (subId: string) =>
    request<Row>(`/subscriptions/${encodeURIComponent(subId)}`),
  broadcastInfo: (audience: string) =>
    request<{ audience: string; target_count: number }>(`/broadcast?audience=${audience}`),
  events: (status = "active") =>
    request<import("./types").ListResponse<Row>>(`/events?status=${status}`),

  // mutations
  approveTopup: (id: string) => post<Ok>(`/topups/${encodeURIComponent(id)}/approve`),
  rejectTopup: (id: string) => post<Ok>(`/topups/${encodeURIComponent(id)}/reject`),
  approveAgentRequest: (
    id: string,
    payload: { price_per_gb: number },
  ) => post<Ok>(`/agent-requests/${encodeURIComponent(id)}/approve`, payload),
  rejectAgentRequest: (id: string) => post<Ok>(`/agent-requests/${encodeURIComponent(id)}/reject`),
  banUser: (id: number, reason: string) => post<Ok>(`/users/${id}/ban`, { reason }),
  unbanUser: (id: number) => post<Ok>(`/users/${id}/unban`),
  setWallet: (id: number, wallet_balance: number) => post<Ok>(`/users/${id}/wallet`, { wallet_balance }),
  setSubEnabled: (subId: string, enabled: boolean) =>
    post<Ok>(`/subscriptions/${encodeURIComponent(subId)}/enabled`, { enabled }),
  syncSub: (subId: string) => post<Ok>(`/subscriptions/${encodeURIComponent(subId)}/sync`),
  setSubVolume: (subId: string, total_gb: number) =>
    post<Ok>(`/subscriptions/${encodeURIComponent(subId)}/volume`, { total_gb }),
  setSales: (audience: "all" | "user" | "agent", sales_status: "open" | "closed") =>
    post<Ok>("/sales", { audience, sales_status }),
  setUiMode: (mode: "modern" | "classic") => post<Ok>("/ui-mode", { mode }),
  setPaymentCards: (cards: { number: string; name: string }[]) => post<Ok>("/payment-cards", { cards }),
  setInfinite: (p: { enabled: boolean; cap_gb: number; duration_days: number; price: number }) =>
    post<Ok>("/infinite-package", p),
  setPriceTiers: (tiers: { min_gb: number; price_per_gb: number }[]) =>
    post<Ok>("/price-tiers", { tiers }),
  setPanelPrimary: (enabled: boolean) => post<Ok>("/panel-primary", { enabled }),
  setPanelPackages: (
    panel: "1" | "2" | "pg",
    packages: { kind: "volume" | "unlimited"; title: string; gb: number; days: number; price: number; agent_price: number }[],
  ) => post<Ok>("/panel-packages", { panel, packages }),
  setTexts: (p: { welcome_text?: string; labels?: Record<string, string> }) => post<Ok>("/texts", p),
  setPanel2: (p: {
    enabled: boolean;
    label: string;
    base_url: string;
    username: string;
    password: string;
    inbound_id: number;
    sub_link_base: string;
    use_proxy: "" | "true" | "false";
    proxy_url: string;
    price_per_gb: number;
  }) => post<Ok>("/panel2", p),

  // PasarGuard backend (navid: package pricing)
  setPrimaryBackend: (backend: "xui" | "pasarguard") => post<Ok>("/primary-backend", { backend }),
  setPasarGuard: (p: {
    enabled: boolean;
    label: string;
    base_url: string;
    username: string;
    password: string;
    group: string;
    verify_tls: boolean;
    default_days: number;
  }) => post<Ok>("/pasarguard", p),
  testPasarGuard: (p: { base_url?: string; username?: string; password?: string; verify_tls?: boolean }) =>
    post<{ ok: boolean; error?: string; admin_username?: string; is_owner?: boolean; panel_version?: string; groups?: { id: number; name: string; inbound_tags?: string[] }[] }>(
      "/pasarguard/test",
      p,
    ),
  pgAdmins: () =>
    request<{ ok: boolean; error?: string; admins: Row[]; total?: number }>("/pasarguard/admins"),
  pgAdminUsers: (username: string, offset = 0, limit = 25, search = "") =>
    request<{ ok: boolean; error?: string; admin: Row; users: Row[]; total: number; offset: number; limit: number }>(
      `/pasarguard/admins/${encodeURIComponent(username)}/users?offset=${offset}&limit=${limit}&search=${encodeURIComponent(search)}`,
    ),
  pgAdminStats: (username: string) =>
    request<{ ok: boolean; error?: string; total: number; used: number; allocated: number; created_24h_count: number; created_24h_data: number; capped: boolean }>(
      `/pasarguard/admins/${encodeURIComponent(username)}/stats`,
    ),
  pgRoles: () =>
    request<{ ok: boolean; error?: string; roles: { id: number; name: string; is_owner: boolean }[] }>(
      "/pasarguard/roles",
    ),
  pgCreateAdmin: (p: { username: string; password?: string; role_id?: number; data_limit_gb?: number; note?: string }) =>
    post<{ ok: boolean; error?: string; username?: string; password?: string; role_id?: number; panel_url?: string }>(
      "/pasarguard/admins",
      p,
    ),
  pgDeleteAdmin: (username: string) =>
    post<Ok>(`/pasarguard/admins/${encodeURIComponent(username)}/delete`),
  pgSetAdminStatus: (username: string, status: "active" | "disabled") =>
    post<Ok>(`/pasarguard/admins/${encodeURIComponent(username)}/status`, { status }),
  pgCreateAdminForReseller: (userId: number) =>
    post<{ ok: boolean; error?: string; exists?: boolean; username?: string; password?: string; panel_url?: string }>(
      `/users/${userId}/pasarguard-admin`,
    ),

  updateAgent: (
    id: number,
    p: { price_per_gb: number; daily_test_limit: number },
  ) => post<Ok>(`/users/${id}/agent`, p),
  messageUser: (id: number, message: string) => post<Ok>(`/users/${id}/message`, { message }),
  syncUserSubs: (id: number) => post<Ok>(`/users/${id}/sync-subscriptions`),
  bulkUserSubs: (id: number, enabled: boolean) => post<Ok>(`/users/${id}/subscriptions/bulk`, { enabled }),

  sendBroadcast: (audience: string, message: string) => post<Ok>("/broadcast", { audience, message }),
  dismissEvent: (id: string) => post<Ok>(`/events/${encodeURIComponent(id)}/dismiss`),
  updateSettings: (values: Record<string, string>) => post<Ok>("/settings", values),
  catalog: () => request<import("./types").CatalogBundle>("/catalog"),
  saveCatalog: (catalog: { categories: import("./types").Category[]; plans: import("./types").Plan[] }) =>
    post<{ ok: boolean; catalog: import("./types").CatalogData; problems: Record<string, string[]> }>("/catalog", { catalog }),
  runBackup: () =>
    post<{
      ok: boolean;
      error?: string;
      mode?: string;
      file?: string;
      status?: string;
      delivered?: boolean;
      errors?: string[];
      pg?: { included: boolean; mode: string; db_mb: number; restorable: boolean };
    }>("/backup/run", {}),
};
