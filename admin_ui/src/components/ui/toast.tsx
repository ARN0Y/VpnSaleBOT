import * as React from "react";
import { CheckCircle2, AlertTriangle, Info, X } from "lucide-react";
import { cn } from "@/lib/utils";

type Variant = "success" | "error" | "info";
interface Toast {
  id: number;
  title: string;
  description?: string;
  variant: Variant;
}

interface ToastCtx {
  toast: (t: { title: string; description?: string; variant?: Variant }) => void;
}

const Ctx = React.createContext<ToastCtx | null>(null);

const ICON: Record<Variant, typeof Info> = { success: CheckCircle2, error: AlertTriangle, info: Info };
const COLOR: Record<Variant, string> = {
  success: "border-emerald-400/30 text-emerald-200",
  error: "border-rose-400/30 text-rose-200",
  info: "border-border text-foreground",
};

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = React.useState<Toast[]>([]);
  const idRef = React.useRef(0);

  const remove = React.useCallback((id: number) => setItems((xs) => xs.filter((x) => x.id !== id)), []);
  const toast = React.useCallback<ToastCtx["toast"]>(
    ({ title, description, variant = "info" }) => {
      const id = ++idRef.current;
      setItems((xs) => [...xs, { id, title, description, variant }]);
      setTimeout(() => remove(id), 3200);
    },
    [remove],
  );

  return (
    <Ctx.Provider value={{ toast }}>
      {children}
      <div className="pointer-events-none fixed bottom-4 left-4 z-[100] flex w-[22rem] max-w-[calc(100vw-2rem)] flex-col gap-2">
        {items.map((t) => {
          const Icon = ICON[t.variant];
          return (
            <div
              key={t.id}
              className={cn(
                "pointer-events-auto flex items-start gap-3 rounded-2xl border bg-popover/95 p-3.5 shadow-2xl backdrop-blur-xl",
                COLOR[t.variant],
              )}
            >
              <Icon className="mt-0.5 h-5 w-5 shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-bold text-white">{t.title}</div>
                {t.description && <div className="mt-0.5 text-xs text-muted-foreground">{t.description}</div>}
              </div>
              <button onClick={() => remove(t.id)} className="text-muted-foreground transition hover:text-white">
                <X className="h-4 w-4" />
              </button>
            </div>
          );
        })}
      </div>
    </Ctx.Provider>
  );
}

export function useToast(): ToastCtx {
  const ctx = React.useContext(Ctx);
  // Safe no-op fallback so components never crash if provider is missing.
  return ctx ?? { toast: () => undefined };
}
