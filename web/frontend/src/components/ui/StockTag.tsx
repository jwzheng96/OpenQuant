/** Unified <symbol + name> display chip. */
export function StockTag({
  symbol,
  name,
  size = "sm",
}: {
  symbol: string;
  name?: string;
  size?: "xs" | "sm" | "md";
}) {
  const sizeCls =
    size === "xs" ? "text-xs"
      : size === "md" ? "text-sm"
      : "text-xs";
  return (
    <span className={`inline-flex items-center gap-1.5 ${sizeCls}`}>
      <span className="font-mono tabular text-muted">{symbol}</span>
      {name && name !== symbol && (
        <span className="text-foreground">{name}</span>
      )}
    </span>
  );
}
