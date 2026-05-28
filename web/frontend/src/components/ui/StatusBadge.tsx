/** Pill for status enums: filled / rejected / pending / etc. */
export type Tone = "success" | "danger" | "warning" | "muted" | "accent";

const TONE_BG: Record<Tone, string> = {
  success: "bg-success/15 text-success ring-success/30",
  danger:  "bg-danger/15 text-danger ring-danger/30",
  warning: "bg-warning/15 text-warning ring-warning/30",
  muted:   "bg-muted/10 text-muted ring-muted/30",
  accent:  "bg-accent/15 text-accent ring-accent/30",
};

export function StatusBadge({
  text,
  tone = "muted",
}: {
  text: string;
  tone?: Tone;
}) {
  return (
    <span
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ring-1 ${TONE_BG[tone]}`}
    >
      {text}
    </span>
  );
}

/** Convenience helpers for common A-share/quant statuses. */
export function OrderStatusBadge({ status }: { status: string }) {
  const tone: Tone =
    status === "filled" ? "success"
      : status === "rejected" ? "danger"
      : status === "pending" || status === "partial" ? "warning"
      : "muted";
  return <StatusBadge text={status} tone={tone} />;
}

export function SideBadge({ side }: { side: string }) {
  // A-share convention — buy = red (上涨), sell = green (下跌)
  if (side === "buy") return <StatusBadge text="买入" tone="danger" />;
  if (side === "sell") return <StatusBadge text="卖出" tone="success" />;
  return <StatusBadge text={side} tone="muted" />;
}
