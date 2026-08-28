/**
 * Jalali (Solar Hijri) ↔ Gregorian conversion.
 *
 * The operators of this panel think in Jalali dates, so a filter that asks for
 * Gregorian ones would be filled in wrong. `Intl` can *format* a Jalali date but
 * cannot parse one back, so the arithmetic lives here — it is the well-known
 * Borkowski/jalaali-js algorithm (MIT), inlined to avoid a dependency.
 *
 * Everything here is calendar arithmetic only. Turning a Jalali day into an
 * epoch uses the browser's own timezone, which is also what `jalaliDate()`
 * renders with, so a picked day and a displayed day always agree.
 */

const div = (a: number, b: number) => Math.trunc(a / b);
const mod = (a: number, b: number) => a - Math.trunc(a / b) * b;

const BREAKS = [
  -61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210, 1635, 2060, 2097, 2192,
  2262, 2324, 2394, 2456, 3178,
];

function jalCal(jy: number): { leap: number; gy: number; march: number } {
  const bl = BREAKS.length;
  const gy = jy + 621;
  let leapJ = -14;
  let jp = BREAKS[0];
  let jm = 0;
  let jump = 0;
  if (jy < jp || jy >= BREAKS[bl - 1]) throw new Error(`bad jalali year ${jy}`);
  for (let i = 1; i < bl; i += 1) {
    jm = BREAKS[i];
    jump = jm - jp;
    if (jy < jm) break;
    leapJ = leapJ + div(jump, 33) * 8 + div(mod(jump, 33), 4);
    jp = jm;
  }
  let n = jy - jp;
  leapJ = leapJ + div(n, 33) * 8 + div(mod(n, 33) + 3, 4);
  if (mod(jump, 33) === 4 && jump - n === 4) leapJ += 1;
  const leapG = div(gy, 4) - div((div(gy, 100) + 1) * 3, 4) - 150;
  const march = 20 + leapJ - leapG;
  if (jump - n < 6) n = n - jump + div(jump + 4, 33) * 33;
  let leap = mod(mod(n + 1, 33) - 1, 4);
  if (leap === -1) leap = 4;
  return { leap, gy, march };
}

function g2d(gy: number, gm: number, gd: number): number {
  let d =
    div((gy + div(gm - 8, 6) + 100100) * 1461, 4) +
    div(153 * mod(gm + 9, 12) + 2, 5) +
    gd -
    34840408;
  d = d - div(div(gy + 100100 + div(gm - 8, 6), 100) * 3, 4) + 752;
  return d;
}

function d2g(jdn: number): { gy: number; gm: number; gd: number } {
  let j = 4 * jdn + 139361631;
  j = j + div(div(4 * jdn + 183187720, 146097) * 3, 4) * 4 - 3908;
  const i = div(mod(j, 1461), 4) * 5 + 308;
  const gd = div(mod(i, 153), 5) + 1;
  const gm = mod(div(i, 153), 12) + 1;
  const gy = div(j, 1461) - 100100 + div(8 - gm, 6);
  return { gy, gm, gd };
}

export interface JalaliDate {
  jy: number;
  jm: number;
  jd: number;
}

export function toJalali(date: Date): JalaliDate {
  const jdn = g2d(date.getFullYear(), date.getMonth() + 1, date.getDate());
  const gy = d2g(jdn).gy;
  let jy = gy - 621;
  const r = jalCal(jy);
  const jdn1f = g2d(r.gy, 3, r.march);
  let k = jdn - jdn1f;
  if (k >= 0) {
    if (k <= 185) return { jy, jm: 1 + div(k, 31), jd: mod(k, 31) + 1 };
    k -= 186;
  } else {
    // `r` still describes the year we started from — using the decremented
    // year's leap flag here shifts every pre-Nowruz date by a day.
    jy -= 1;
    k += 179;
    if (r.leap === 1) k += 1;
  }
  return { jy, jm: 7 + div(k, 30), jd: mod(k, 30) + 1 };
}

/** Local midnight at the start of the given Jalali day. */
export function fromJalali({ jy, jm, jd }: JalaliDate): Date {
  const r = jalCal(jy);
  const jdn = g2d(r.gy, 3, r.march) + (jm - 1) * 31 - div(jm, 7) * (jm - 7) + jd - 1;
  const { gy, gm, gd } = d2g(jdn);
  return new Date(gy, gm - 1, gd, 0, 0, 0, 0);
}

export function isJalaliLeap(jy: number): boolean {
  return jalCal(jy).leap === 0;
}

export function jalaliMonthLength(jy: number, jm: number): number {
  if (jm <= 6) return 31;
  if (jm <= 11) return 30;
  return isJalaliLeap(jy) ? 30 : 29;
}

export const JALALI_MONTHS = [
  "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
  "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
];

/** Saturday-first weekday names, matching the grid the picker draws. */
export const JALALI_WEEKDAYS = ["ش", "ی", "د", "س", "چ", "پ", "ج"];

const pad = (n: number) => String(n).padStart(2, "0");

export function formatJalali(date: Date | null): string {
  if (!date) return "";
  const { jy, jm, jd } = toJalali(date);
  return `${jy}/${pad(jm)}/${pad(jd)}`;
}

/** Parse "1404/05/12" (also 1404-5-12). Returns null when it isn't a real day. */
export function parseJalali(text: string): Date | null {
  const m = String(text || "").trim().match(/^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})$/);
  if (!m) return null;
  const jy = Number(m[1]);
  const jm = Number(m[2]);
  const jd = Number(m[3]);
  if (jy < 1300 || jy > 1500 || jm < 1 || jm > 12) return null;
  if (jd < 1 || jd > jalaliMonthLength(jy, jm)) return null;
  return fromJalali({ jy, jm, jd });
}

/** Epoch seconds for local midnight of `date`'s day. */
export function startOfDayTs(date: Date): number {
  const d = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 0, 0, 0, 0);
  return Math.floor(d.getTime() / 1000);
}

/** Epoch seconds for local midnight of the NEXT day — an exclusive range end. */
export function endOfDayTs(date: Date): number {
  const d = new Date(date.getFullYear(), date.getMonth(), date.getDate() + 1, 0, 0, 0, 0);
  return Math.floor(d.getTime() / 1000);
}

export function addJalaliMonths({ jy, jm }: JalaliDate, delta: number): JalaliDate {
  const total = (jy * 12 + (jm - 1)) + delta;
  return { jy: div(total, 12), jm: mod(total, 12) + 1, jd: 1 };
}

/** Weekday index with Saturday = 0, matching the Jalali week. */
export function jalaliWeekday(date: Date): number {
  return (date.getDay() + 1) % 7;
}
