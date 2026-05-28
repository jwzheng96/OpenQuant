/** Number with A-share color (red up / green down) and optional sign. */
import { priceColor, fmtPct, fmtMoney } from "@/lib/format";

export function PriceDelta({
  value,
  format = "pct",
  digits = 2,
  raw = false,
}: {
  value: number | null | undefined;
  format?: "pct" | "money";
  digits?: number;
  /** When true, the value is treated as a raw % (e.g. 12.34 means 12.34%, not 1234%). */
  raw?: boolean;
}) {
  const cls = priceColor(value);
  let text = "—";
  if (value != null && Number.isFinite(value)) {
    if (format === "pct") {
      const v = raw ? value / 100 : value;
      text = fmtPct(v, digits);
    } else {
      text = (value > 0 ? "+" : "") + fmtMoney(value);
    }
  }
  return <span className={`tabular font-mono ${cls}`}>{text}</span>;
}
