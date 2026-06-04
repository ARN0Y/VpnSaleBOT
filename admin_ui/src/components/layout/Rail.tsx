import { NavLink } from "react-router-dom";
import { Hexagon, LogOut } from "lucide-react";
import { NAV } from "@/lib/nav";
import { cn } from "@/lib/utils";
import { useAuth } from "@/auth/AuthContext";

function RailLink({ to, label, icon: Icon, end }: (typeof NAV)[number]) {
  return (
    <NavLink to={to} end={end} className="group relative flex items-center justify-center">
      {({ isActive }) => (
        <>
          {/* active indicator bar on the inner edge (RTL → right) */}
          <span
            className={cn(
              "absolute right-0 h-6 w-[3px] rounded-full transition-all",
              isActive ? "bg-foreground opacity-100" : "opacity-0",
            )}
          />
          <span
            className={cn(
              "flex h-11 w-11 items-center justify-center rounded-xl border transition-colors",
              isActive
                ? "border-white/15 bg-white/[0.08] text-white"
                : "border-transparent text-muted-foreground hover:bg-white/[0.04] hover:text-white",
            )}
          >
            <Icon className="h-5 w-5" />
          </span>
          {/* hover tooltip */}
          <span className="pointer-events-none absolute right-14 z-50 whitespace-nowrap rounded-lg border border-border bg-popover px-2.5 py-1.5 text-xs font-bold text-white opacity-0 shadow-xl transition-opacity group-hover:opacity-100">
            {label}
          </span>
        </>
      )}
    </NavLink>
  );
}

export function Rail() {
  const { logout } = useAuth();
  const groups: (typeof NAV)[number]["group"][] = ["main", "ops", "system"];
  return (
    <aside className="fixed inset-y-0 right-0 z-40 hidden w-16 flex-col items-center border-l border-border bg-card/30 py-4 backdrop-blur-xl lg:flex">
      <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-border bg-white/[0.05] text-foreground">
        <Hexagon className="h-6 w-6" />
      </div>
      <nav className="mt-6 flex flex-1 flex-col items-center gap-1.5">
        {groups.map((g, gi) => (
          <div key={g} className="flex flex-col items-center gap-1.5">
            {gi > 0 && <span className="my-1.5 h-px w-7 bg-border" />}
            {NAV.filter((n) => n.group === g).map((n) => (
              <RailLink key={n.to} {...n} />
            ))}
          </div>
        ))}
      </nav>
      <button
        onClick={() => void logout()}
        className="group relative flex h-11 w-11 items-center justify-center rounded-xl text-muted-foreground transition-colors hover:bg-rose-500/10 hover:text-rose-300"
        title="خروج"
      >
        <LogOut className="h-5 w-5" />
      </button>
    </aside>
  );
}
