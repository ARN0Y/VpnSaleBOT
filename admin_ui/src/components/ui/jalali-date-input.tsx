import * as React from "react";
import { Calendar, ChevronLeft, ChevronRight, X } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  JALALI_MONTHS,
  JALALI_WEEKDAYS,
  addJalaliMonths,
  formatJalali,
  fromJalali,
  jalaliMonthLength,
  jalaliWeekday,
  parseJalali,
  toJalali,
} from "@/lib/jalali";

function sameDay(a: Date | null, b: Date | null): boolean {
  if (!a || !b) return false;
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

/**
 * A Jalali day picker that is also a plain text field.
 *
 * Typing beats clicking for an operator who already knows the date, so the
 * input stays editable and the calendar is an assist — the value only changes
 * when what was typed is a real day.
 */
export function JalaliDateInput({
  value,
  onChange,
  placeholder = "۱۴۰۴/۰۱/۰۱",
  label,
  className,
  align = "start",
}: {
  value: Date | null;
  onChange: (next: Date | null) => void;
  placeholder?: string;
  label?: string;
  className?: string;
  align?: "start" | "end";
}) {
  const [open, setOpen] = React.useState(false);
  const [text, setText] = React.useState(() => formatJalali(value));
  const [cursor, setCursor] = React.useState(() => toJalali(value || new Date()));
  const boxRef = React.useRef<HTMLDivElement>(null);

  // The field mirrors the value unless the operator is mid-edit.
  React.useEffect(() => {
    setText(formatJalali(value));
    if (value) setCursor(toJalali(value));
  }, [value]);

  React.useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const commit = (raw: string) => {
    const trimmed = raw.trim();
    if (!trimmed) {
      onChange(null);
      return;
    }
    const parsed = parseJalali(trimmed);
    if (parsed) onChange(parsed);
    else setText(formatJalali(value)); // reject silently, restore the last good value
  };

  const monthStart = fromJalali({ jy: cursor.jy, jm: cursor.jm, jd: 1 });
  const lead = jalaliWeekday(monthStart);
  const length = jalaliMonthLength(cursor.jy, cursor.jm);
  const today = new Date();
  const cells: (number | null)[] = [
    ...Array.from({ length: lead }, () => null),
    ...Array.from({ length }, (_, i) => i + 1),
  ];

  return (
    <div ref={boxRef} className={cn("relative", className)}>
      {label && <div className="mb-1 text-[0.7rem] text-muted-foreground">{label}</div>}
      <div className="relative">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-label="انتخاب تاریخ"
          className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-brand"
        >
          <Calendar className="h-4 w-4" />
        </button>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={(e) => commit(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              commit(text);
              setOpen(false);
            }
          }}
          onFocus={() => setOpen(true)}
          placeholder={placeholder}
          inputMode="numeric"
          className={cn(
            "h-10 w-full rounded-xl border border-input bg-white/[0.02] py-2 pl-8 pr-9 text-sm text-foreground",
            "placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
        />
        {value && (
          <button
            type="button"
            onClick={() => {
              onChange(null);
              setText("");
            }}
            aria-label="پاک کردن"
            className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-destructive"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {open && (
        <div
          className={cn(
            "absolute z-50 mt-2 w-64 rounded-2xl border border-border bg-card p-3 shadow-card",
            align === "end" ? "left-0" : "right-0",
          )}
        >
          <div className="mb-2 flex items-center justify-between">
            <button
              type="button"
              className="rounded-lg p-1 text-muted-foreground hover:bg-white/5 hover:text-white"
              onClick={() => setCursor((c) => ({ ...addJalaliMonths(c, -1), jd: 1 }))}
              aria-label="ماه قبل"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
            <div className="text-sm font-bold text-white">
              {JALALI_MONTHS[cursor.jm - 1]} {cursor.jy}
            </div>
            <button
              type="button"
              className="rounded-lg p-1 text-muted-foreground hover:bg-white/5 hover:text-white"
              onClick={() => setCursor((c) => ({ ...addJalaliMonths(c, 1), jd: 1 }))}
              aria-label="ماه بعد"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
          </div>
          <div className="grid grid-cols-7 gap-1 text-center text-[0.65rem] text-muted-foreground">
            {JALALI_WEEKDAYS.map((d, i) => (
              <span key={i} className="py-1">{d}</span>
            ))}
          </div>
          <div className="grid grid-cols-7 gap-1">
            {cells.map((day, i) => {
              if (day === null) return <span key={`e${i}`} />;
              const date = fromJalali({ jy: cursor.jy, jm: cursor.jm, jd: day });
              const selected = sameDay(date, value);
              const isToday = sameDay(date, today);
              return (
                <button
                  key={day}
                  type="button"
                  onClick={() => {
                    onChange(date);
                    setOpen(false);
                  }}
                  className={cn(
                    "h-8 rounded-lg text-xs transition-colors",
                    selected
                      ? "bg-primary font-bold text-primary-foreground"
                      : isToday
                        ? "border border-brand/40 text-brand hover:bg-white/5"
                        : "text-foreground hover:bg-white/5",
                  )}
                >
                  {day}
                </button>
              );
            })}
          </div>
          <button
            type="button"
            className="mt-2 w-full rounded-lg py-1.5 text-[0.7rem] text-muted-foreground hover:bg-white/5 hover:text-white"
            onClick={() => {
              onChange(new Date());
              setOpen(false);
            }}
          >
            امروز
          </button>
        </div>
      )}
    </div>
  );
}
