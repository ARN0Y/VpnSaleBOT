import * as React from "react";
import { api, ApiError, setCsrf } from "@/lib/api";

interface AuthState {
  username: string | null;
  loading: boolean;
  login: (u: string, p: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = React.createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [username, setUsername] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    api
      .me()
      .then((res) => {
        setCsrf(res.csrf);
        setUsername(res.username || null);
      })
      .catch((err) => {
        if (!(err instanceof ApiError) || err.status !== 401) console.error(err);
        setUsername(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const login = React.useCallback(async (u: string, p: string) => {
    const res = await api.login(u, p);
    setCsrf(res.csrf);
    setUsername(res.username);
  }, []);

  const logout = React.useCallback(async () => {
    await api.logout().catch(() => undefined);
    setCsrf("");
    setUsername(null);
  }, []);

  return (
    <AuthContext.Provider value={{ username, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = React.useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
