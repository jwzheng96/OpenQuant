/** Unified <symbol + name> display chip. Optionally links to /stock/:symbol. */
import { Link } from "@tanstack/react-router";

export function StockTag({
  symbol,
  name,
  size = "sm",
  linkable = true,
}: {
  symbol: string;
  name?: string;
  size?: "xs" | "sm" | "md";
  /** When true, wraps the chip in a router Link to /stock/:symbol. */
  linkable?: boolean;
}) {
  const sizeCls =
    size === "xs" ? "text-xs"
      : size === "md" ? "text-sm"
      : "text-xs";
  const body = (
    <span className={`inline-flex items-center gap-1.5 ${sizeCls}`}>
      <span className="font-mono tabular text-muted">{symbol}</span>
      {name && name !== symbol && (
        <span className="text-foreground">{name}</span>
      )}
    </span>
  );
  if (!linkable) return body;
  return (
    <Link
      to="/stock/$symbol"
      params={{ symbol }}
      className="hover:underline"
      onClick={(e) => e.stopPropagation()}
    >
      {body}
    </Link>
  );
}
