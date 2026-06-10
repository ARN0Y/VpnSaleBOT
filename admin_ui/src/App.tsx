import * as React from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { AppShell } from "@/components/layout/AppShell";
import { Login } from "@/pages/Login";
import { Skeleton } from "@/components/ui/skeleton";

// Lazy-loaded pages → smaller initial bundle, each page fetched on demand.
const Dashboard = React.lazy(() => import("@/pages/Dashboard").then((m) => ({ default: m.Dashboard })));
const Orders = React.lazy(() => import("@/pages/Orders").then((m) => ({ default: m.Orders })));
const Users = React.lazy(() => import("@/pages/Users").then((m) => ({ default: m.Users })));
const UserDetail = React.lazy(() => import("@/pages/UserDetail").then((m) => ({ default: m.UserDetail })));
const Topups = React.lazy(() => import("@/pages/Topups").then((m) => ({ default: m.Topups })));
const AgentRequests = React.lazy(() => import("@/pages/AgentRequests").then((m) => ({ default: m.AgentRequests })));
const Subscriptions = React.lazy(() => import("@/pages/Subscriptions").then((m) => ({ default: m.Subscriptions })));
const SubscriptionDetail = React.lazy(() => import("@/pages/SubscriptionDetail").then((m) => ({ default: m.SubscriptionDetail })));
const Broadcast = React.lazy(() => import("@/pages/Broadcast").then((m) => ({ default: m.Broadcast })));
const Events = React.lazy(() => import("@/pages/Events").then((m) => ({ default: m.Events })));
const Settings = React.lazy(() => import("@/pages/Settings").then((m) => ({ default: m.Settings })));

function PageFallback() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-24" />
      <Skeleton className="h-64" />
    </div>
  );
}

export function App() {
  const { username, loading } = useAuth();

  if (loading) {
    return <div className="grid min-h-screen place-items-center text-muted-foreground">در حال بارگذاری…</div>;
  }
  if (!username) return <Login />;

  return (
    <BrowserRouter basename="/admin">
      <Routes>
        <Route element={<AppShell />}>
          <Route
            element={
              <React.Suspense fallback={<PageFallback />}>
                <Outlet />
              </React.Suspense>
            }
          >
            <Route index element={<Dashboard />} />
            <Route path="orders" element={<Orders />} />
            <Route path="users" element={<Users />} />
            <Route path="users/:userId" element={<UserDetail />} />
            <Route path="topups" element={<Topups />} />
            <Route path="agent-requests" element={<AgentRequests />} />
            <Route path="subscriptions" element={<Subscriptions />} />
            <Route path="subscriptions/:subId" element={<SubscriptionDetail />} />
            <Route path="broadcast" element={<Broadcast />} />
            <Route path="events" element={<Events />} />
            <Route path="settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
