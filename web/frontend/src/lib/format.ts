/**
 * Number / currency / percent / date formatters with A-share color awareness.
 *
 * A-share convention:  red = up, green = down  (opposite of US/EU)
 */

const cny = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  maximumFractionDigits: 0,
});
const cnyCompact = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  notation: "compact",
  maximumFractionDigits: 1,
});

export function fmtMoney(v: number | null | undefined, compact = false): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return (compact ? cnyCompact : cny).format(v);
}

export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${(v * 100).toFixed(digits)}%`;
}

export function fmtPctRaw(v: number | null | undefined, digits = 2): string {
  /** For values already in percent units (not fractions). */
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(digits)}%`;
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtInt(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("zh-CN");
}

export function fmtDate(d: string | null | undefined): string {
  if (!d) return "—";
  // Already YYYY-MM-DD; pass through
  return d;
}

/** Color class for a price-direction value (A-share: red up / green down). */
export function priceColor(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "text-muted";
  if (v > 0) return "text-up";
  if (v < 0) return "text-down";
  return "text-muted";
}

/** Background variant for price tags. */
export function priceBgColor(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "bg-muted/10 text-muted";
  if (v > 0) return "bg-up/10 text-up";
  if (v < 0) return "bg-down/10 text-down";
  return "bg-muted/10 text-muted";
}
