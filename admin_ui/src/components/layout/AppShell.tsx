import { NavLink, Outlet } from "react-router-dom";
import { Rail } from "./Rail";
import { Topbar } from "./Topbar";
import { NAV } from "@/lib/nav";
import { cn } from "@/lib/utils";

function MobileNav() {
  return (
    <nav className="flex gap-1 overflow-x-auto border-b border-border bg-card/30 px-3 py-2 lg:hidden">
      {NAV.map((n) => (
        <NavLink
          key={n.to}
          to={n.to}
          end={n.end}
          className={({ isActive }) =>
            cn(
              "flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-bold transition-colors",
              isActive ? "bg-white/[0.08] text-white" : "text-muted-foreground",
            )
          }
        >
          <n.icon className="h-4 w-4" />
          {n.label}
        </NavLink>
      ))}
    </nav>
  );
}

export function AppShell() {
  return (
    <div className="min-h-screen">
      <Rail />
      <div className="lg:mr-16">
        <Topbar />
        <MobileNav />
        <main className="mx-auto max-w-7xl p-4 lg:p-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
