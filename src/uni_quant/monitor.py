"""Monitoring: Prometheus metrics + Feishu/DingTalk alerts + daily reports.

Three roles:

1. `Metrics` — Prometheus counters/gauges exposed on :9101 for Grafana.
2. `AlertManager` — push to Feishu / DingTalk webhooks (and email as fallback).
3. `daily_report` — render an HTML/PDF summary from backtest or live nav.

This module is intentionally synchronous and small; complex monitoring belongs
in Grafana panels backed by ClickHouse queries, not Python code.
"""

from __future__ import annotations

import json
import smtplib
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx
from prometheus_client import Counter, Gauge, start_http_server

from uni_quant.utils import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------- #
# Metrics                                                                      #
# ---------------------------------------------------------------------------- #


class Metrics:
    """Singleton-ish wrapper around prometheus_client. Start once per process."""

    _started = False

    orders_submitted = Counter("uni_quant_orders_submitted_total", "orders submitted", ["strategy", "side"])
    orders_filled = Counter("uni_quant_orders_filled_total", "orders filled", ["strategy", "side"])
    orders_rejected = Counter("uni_quant_orders_rejected_total", "orders rejected", ["strategy", "reason"])
    nav = Gauge("uni_quant_nav", "current strategy NAV", ["strategy"])
    cash = Gauge("uni_quant_cash", "current cash", ["strategy"])
    position_count = Gauge("uni_quant_position_count", "open positions", ["strategy"])
    daily_pnl = Gauge("uni_quant_daily_pnl", "intraday P&L", ["strategy"])
    drawdown = Gauge("uni_quant_drawdown", "current drawdown from peak", ["strategy"])
    data_sync_lag_sec = Gauge("uni_quant_data_sync_lag_seconds", "data freshness", ["dataset"])
    risk_event = Counter("uni_quant_risk_events_total", "risk events", ["rule", "severity"])

    @classmethod
    def start(cls, port: int = 9101) -> None:
        if cls._started:
            return
        start_http_server(port)
        cls._started = True
        log.info(f"Prometheus metrics on :{port}")


# ---------------------------------------------------------------------------- #
# Alerts                                                                       #
# ---------------------------------------------------------------------------- #


@dataclass
class AlertManager:
    feishu_webhook: str = ""
    dingtalk_webhook: str = ""
    email_cfg: dict[str, Any] | None = None

    def info(self, title: str, body: str) -> None:
        self._send(title, body, level="info")

    def warn(self, title: str, body: str) -> None:
        self._send(title, body, level="warn")

    def critical(self, title: str, body: str) -> None:
        self._send(title, body, level="critical")

    def _send(self, title: str, body: str, level: str) -> None:
        prefix = {"info": "ℹ️", "warn": "⚠️", "critical": "🚨"}.get(level, "")
        text = f"{prefix} {title}\n{body}"
        if self.feishu_webhook:
            self._feishu(text)
        if self.dingtalk_webhook:
            self._dingtalk(text)
        if level == "critical" and self.email_cfg:
            self._email(title, body)

    def _feishu(self, text: str) -> None:
        try:
            httpx.post(self.feishu_webhook, json={"msg_type": "text", "content": {"text": text}}, timeout=5)
        except Exception:
            log.exception("feishu webhook failed")

    def _dingtalk(self, text: str) -> None:
        try:
            httpx.post(self.dingtalk_webhook, json={"msgtype": "text", "text": {"content": text}}, timeout=5)
        except Exception:
            log.exception("dingtalk webhook failed")

    def _email(self, title: str, body: str) -> None:
        cfg = self.email_cfg or {}
        if not cfg.get("smtp") or not cfg.get("to"):
            return
        try:
            msg = EmailMessage()
            msg["Subject"] = f"[uni_quant] {title}"
            msg["From"] = cfg["user"]
            msg["To"] = ", ".join(cfg["to"])
            msg.set_content(body)
            with smtplib.SMTP_SSL(cfg["smtp"]) as s:
                s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
        except Exception:
            log.exception("email send failed")


# ---------------------------------------------------------------------------- #
# Reports                                                                      #
# ---------------------------------------------------------------------------- #


def daily_report(
    nav: list[dict],
    fills: list[dict],
    *,
    strategy: str,
    out_path: Path,
    benchmark: list[dict] | None = None,
    initial_cash: float | None = None,
) -> Path:
    """Render a comprehensive self-contained HTML backtest report.

    `nav` rows: trade_date, nav, daily_ret, cash, market_value
    `fills` rows: trade_date, symbol, side, qty, price, cost
    `benchmark` rows: trade_date, close (optional, e.g. 沪深300 daily)
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not nav:
        out_path.write_text("<html><body>No NAV data</body></html>")
        return out_path

    html = _render_full_report(strategy, nav, fills, benchmark, initial_cash)
    out_path.write_text(html, encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------- #
# Statistics                                                                   #
# ---------------------------------------------------------------------------- #


def _trading_days_between(d1, d2) -> int:
    """Approx trading days using 252 per year."""
    import datetime as _dt
    if isinstance(d1, str):
        d1 = _dt.date.fromisoformat(d1)
    if isinstance(d2, str):
        d2 = _dt.date.fromisoformat(d2)
    days = (d2 - d1).days
    return max(int(days * 252 / 365), 1)


def _compute_stats(nav: list[dict], fills: list[dict], initial_cash: float | None) -> dict:
    """Compute full performance statistics from NAV + fills."""
    import math
    import datetime as _dt
    n0 = nav[0]
    nN = nav[-1]
    start_d = n0["trade_date"]
    end_d = nN["trade_date"]
    if isinstance(start_d, str):
        start_d = _dt.date.fromisoformat(start_d)
    if isinstance(end_d, str):
        end_d = _dt.date.fromisoformat(end_d)
    n_days = (end_d - start_d).days
    trading_days = _trading_days_between(n0["trade_date"], nN["trade_date"])

    init = initial_cash or n0.get("nav", 1.0)
    final = nN.get("nav", init)
    total_ret = final / init - 1
    ann_ret = (1 + total_ret) ** (365 / max(n_days, 1)) - 1 if n_days > 0 else 0.0

    rets = [r.get("daily_ret", 0.0) or 0.0 for r in nav[1:]]
    rets_clean = [r for r in rets if math.isfinite(r)]
    if rets_clean:
        mu = sum(rets_clean) / len(rets_clean)
        var = sum((r - mu) ** 2 for r in rets_clean) / max(len(rets_clean) - 1, 1)
        sd = math.sqrt(var)
        ann_vol = sd * math.sqrt(252)
        sharpe = (mu / sd * math.sqrt(252)) if sd > 0 else 0.0
    else:
        ann_vol = 0.0
        sharpe = 0.0

    # Drawdown
    peak = init
    max_dd = 0.0
    dd_series = []
    for r in nav:
        v = r.get("nav", 0.0)
        peak = max(peak, v)
        dd = (v - peak) / peak if peak > 0 else 0.0
        dd_series.append(dd)
        max_dd = min(max_dd, dd)

    calmar = ann_ret / abs(max_dd) if max_dd < 0 else float("inf") if ann_ret > 0 else 0.0

    pos_days = sum(1 for r in rets_clean if r > 0)
    neg_days = sum(1 for r in rets_clean if r < 0)
    win_rate = pos_days / max(pos_days + neg_days, 1)
    avg_win = sum(r for r in rets_clean if r > 0) / max(pos_days, 1)
    avg_loss = sum(r for r in rets_clean if r < 0) / max(neg_days, 1)
    profit_factor = (sum(r for r in rets_clean if r > 0)
                     / abs(sum(r for r in rets_clean if r < 0))) if neg_days > 0 else float("inf")

    # Fill stats
    buys = [f for f in fills if f.get("side") == "buy"]
    sells = [f for f in fills if f.get("side") == "sell"]
    total_cost = sum(f.get("cost", 0.0) or 0.0 for f in fills)
    buy_notional = sum((f.get("price", 0.0) or 0.0) * (f.get("qty", 0) or 0) for f in buys)
    sell_notional = sum((f.get("price", 0.0) or 0.0) * (f.get("qty", 0) or 0) for f in sells)
    turnover = (buy_notional + sell_notional) / (2 * init) if init > 0 else 0.0

    return {
        "start_date": str(start_d),
        "end_date": str(end_d),
        "calendar_days": n_days,
        "trading_days": trading_days,
        "initial_cash": init,
        "final_nav": final,
        "total_return": total_ret,
        "annualized_return": ann_ret,
        "annualized_vol": ann_vol,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "total_fills": len(fills),
        "buy_fills": len(buys),
        "sell_fills": len(sells),
        "total_cost": total_cost,
        "turnover_ratio": turnover,
        "dd_series": dd_series,
    }


def _monthly_returns(nav: list[dict]) -> list[dict]:
    """Group NAV by year-month and compute monthly return."""
    import datetime as _dt
    groups: dict[str, list[dict]] = {}
    for r in nav:
        td = r.get("trade_date")
        if isinstance(td, str):
            td = _dt.date.fromisoformat(td)
        key = f"{td.year}-{td.month:02d}"
        groups.setdefault(key, []).append(r)
    out = []
    prev_nav = None
    for k in sorted(groups):
        last = groups[k][-1]["nav"]
        if prev_nav is not None:
            ret = last / prev_nav - 1
        else:
            ret = 0.0
        out.append({"month": k, "end_nav": last, "ret": ret})
        prev_nav = last
    return out


def _position_pnl_from_fills(fills: list[dict]) -> list[dict]:
    """Compute per-symbol P&L using FIFO matching (rough — sellable when sold)."""
    book: dict[str, list[tuple[int, float]]] = {}  # symbol -> [(qty, cost)]
    realized: dict[str, float] = {}
    fills_by_time = sorted(fills, key=lambda f: (str(f.get("trade_date")), f.get("symbol", "")))
    for f in fills_by_time:
        s = f.get("symbol", "")
        qty = int(f.get("qty", 0) or 0)
        price = float(f.get("price", 0) or 0)
        if f.get("side") == "buy":
            book.setdefault(s, []).append((qty, price))
        else:  # sell — FIFO match
            remaining = qty
            lots = book.get(s, [])
            pnl = 0.0
            new_lots = []
            for lot_qty, lot_cost in lots:
                if remaining <= 0:
                    new_lots.append((lot_qty, lot_cost))
                    continue
                taken = min(lot_qty, remaining)
                pnl += taken * (price - lot_cost)
                remaining -= taken
                if lot_qty - taken > 0:
                    new_lots.append((lot_qty - taken, lot_cost))
            book[s] = new_lots
            realized[s] = realized.get(s, 0.0) + pnl

    rows = sorted(
        ({"symbol": s, "realized_pnl": v} for s, v in realized.items() if abs(v) > 0.01),
        key=lambda r: -abs(r["realized_pnl"]),
    )
    return rows


# ---------------------------------------------------------------------------- #
# Charts (inline SVG, no external deps)                                        #
# ---------------------------------------------------------------------------- #


def _svg_line_chart(
    series: dict[str, list[tuple[str, float]]],
    title: str,
    width: int = 900,
    height: int = 280,
    y_label: str = "",
    fill_under: str | None = None,
) -> str:
    """Render multiple named series as overlaid SVG line chart.

    `series`: name → [(date_str, value), ...]
    `fill_under`: series name to fill under (for drawdown).
    """
    if not series or not any(series.values()):
        return f"<div>{title}: no data</div>"

    pad_l, pad_r, pad_t, pad_b = 60, 20, 30, 40
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    # Determine global x and y range
    all_pts = [p for pts in series.values() for p in pts]
    if not all_pts:
        return f"<div>{title}: no data</div>"

    # Use index as x (preserves ordering with arbitrary date strings)
    n_max = max(len(pts) for pts in series.values())
    ys_all = [v for _, v in all_pts]
    y_min, y_max = min(ys_all), max(ys_all)
    if y_min == y_max:
        y_min -= 1
        y_max += 1
    y_range = y_max - y_min

    def to_x(i, n):
        return pad_l + (i / max(n - 1, 1)) * plot_w

    def to_y(v):
        return pad_t + plot_h - (v - y_min) / y_range * plot_h

    colors = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c"]
    parts = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
             f'style="background:white;border:1px solid #e5e7eb;border-radius:6px;width:100%">']
    parts.append(f'<text x="{pad_l}" y="{pad_t - 8}" font-size="14" font-weight="600" fill="#1f2937">{title}</text>')

    # Y grid + labels
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = pad_t + plot_h - frac * plot_h
        val = y_min + frac * y_range
        parts.append(f'<line x1="{pad_l}" y1="{y}" x2="{pad_l + plot_w}" y2="{y}" '
                     f'stroke="#e5e7eb" stroke-width="1"/>')
        parts.append(f'<text x="{pad_l - 6}" y="{y + 4}" font-size="11" fill="#6b7280" text-anchor="end">'
                     f'{val:.2f}</text>')

    # X labels (first / middle / last for each series)
    if all_pts:
        labels_idx = [0, n_max // 4, n_max // 2, 3 * n_max // 4, n_max - 1]
        # Use first series for date alignment
        first_series = next(iter(series.values()))
        for idx in labels_idx:
            if idx < len(first_series):
                x = to_x(idx, len(first_series))
                date_label = first_series[idx][0][:10]  # YYYY-MM-DD
                parts.append(f'<text x="{x}" y="{pad_t + plot_h + 20}" font-size="10" '
                             f'fill="#6b7280" text-anchor="middle">{date_label}</text>')

    # Plot each series
    for i, (name, pts) in enumerate(series.items()):
        color = colors[i % len(colors)]
        n = len(pts)
        if n == 0:
            continue
        path_d = "M " + " L ".join(f"{to_x(j, n):.1f} {to_y(v):.1f}" for j, (_, v) in enumerate(pts))
        if fill_under == name:
            # Close path to bottom for fill
            x_first = to_x(0, n)
            x_last = to_x(n - 1, n)
            y_zero = to_y(0)
            fill_path = path_d + f" L {x_last} {y_zero} L {x_first} {y_zero} Z"
            parts.append(f'<path d="{fill_path}" fill="{color}" fill-opacity="0.15"/>')
        parts.append(f'<path d="{path_d}" fill="none" stroke="{color}" stroke-width="1.5"/>')

    # Legend
    leg_x = pad_l + 10
    for i, name in enumerate(series.keys()):
        color = colors[i % len(colors)]
        parts.append(f'<rect x="{leg_x}" y="{pad_t + 4}" width="10" height="10" fill="{color}"/>')
        parts.append(f'<text x="{leg_x + 14}" y="{pad_t + 13}" font-size="11" fill="#1f2937">{name}</text>')
        leg_x += len(name) * 8 + 30

    if y_label:
        parts.append(f'<text x="14" y="{pad_t + plot_h / 2}" font-size="11" fill="#6b7280" '
                     f'transform="rotate(-90 14 {pad_t + plot_h / 2})">{y_label}</text>')

    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------- #
# Full report                                                                  #
# ---------------------------------------------------------------------------- #


def _render_full_report(
    strategy: str,
    nav: list[dict],
    fills: list[dict],
    benchmark: list[dict] | None,
    initial_cash: float | None,
) -> str:
    stats = _compute_stats(nav, fills, initial_cash)
    monthly = _monthly_returns(nav)
    pnl_by_symbol = _position_pnl_from_fills(fills)

    # ---- Charts data ----
    nav_series = [(str(r["trade_date"]), float(r["nav"])) for r in nav]
    init = stats["initial_cash"]
    pct_series = [(d, v / init - 1) for d, v in nav_series]
    chart_data = {"策略累计收益": pct_series}
    if benchmark:
        first_b = benchmark[0]["close"]
        bench_pct = [(str(b["trade_date"]), b["close"] / first_b - 1) for b in benchmark]
        chart_data["沪深300 基准"] = bench_pct

    dd_series = [(str(r["trade_date"]), float(stats["dd_series"][i]))
                 for i, r in enumerate(nav)]

    daily_ret_series = [(str(r["trade_date"]), float(r.get("daily_ret", 0) or 0))
                        for r in nav]

    # ---- Helpers ----
    def pct(x):
        return f"{x * 100:+.2f}%"

    def money(x):
        return f"¥{x:,.2f}"

    def num(x, digits=2):
        return f"{x:.{digits}f}"

    # ---- KPI cards ----
    annual_label = "年化" if stats["calendar_days"] >= 180 else f"{stats['calendar_days']}天"

    kpis = [
        ("累计收益", pct(stats["total_return"]),
         f"{stats['start_date']} → {stats['end_date']} ({stats['calendar_days']}天 / {stats['trading_days']}交易日)",
         "good" if stats["total_return"] > 0 else "bad"),
        ("年化收益", pct(stats["annualized_return"]),
         f"按 365天 折算{annual_label}", "good" if stats["annualized_return"] > 0 else "bad"),
        ("年化波动", pct(stats["annualized_vol"]),
         "日收益标准差 × √252", "neutral"),
        ("Sharpe Ratio", num(stats["sharpe"]),
         "日收益均值/标准差 × √252，>1 为优秀", "good" if stats["sharpe"] > 1 else ("ok" if stats["sharpe"] > 0.5 else "bad")),
        ("最大回撤", pct(stats["max_drawdown"]),
         "NAV 从历史峰值的最大跌幅", "bad"),
        ("Calmar Ratio", num(stats["calmar"]),
         "年化收益 / |最大回撤|", "good" if stats["calmar"] > 1 else "neutral"),
        ("胜率", pct(stats["win_rate"]),
         f"上涨日 / (上涨日 + 下跌日)", "neutral"),
        ("盈亏比", num(stats["profit_factor"]),
         "Σ盈利日收益 / |Σ亏损日收益|", "good" if stats["profit_factor"] > 1.5 else "neutral"),
    ]

    # ---- Tables ----
    monthly_rows = [
        [m["month"], f"¥{m['end_nav']:,.0f}", pct(m["ret"])]
        for m in monthly
    ]

    nav_recent = [
        [str(r["trade_date"]), money(r["nav"]), pct(r.get("daily_ret", 0) or 0),
         money(r.get("cash", 0)), money(r.get("market_value", 0))]
        for r in nav[-30:]
    ]

    top_winners = pnl_by_symbol[:10] if pnl_by_symbol else []
    losers = sorted([p for p in pnl_by_symbol if p["realized_pnl"] < 0],
                    key=lambda p: p["realized_pnl"])[:10]

    fill_rows = [
        [str(f.get("trade_date")), f.get("symbol", ""), f.get("side", ""),
         f"{f.get('qty', 0):,}", f"¥{f.get('price', 0):.2f}",
         f"¥{f.get('cost', 0):.2f}"]
        for f in fills[-30:]
    ]

    # ---- HTML ----
    def tbl(headers, rows, klass=""):
        h = "".join(f"<th>{c}</th>" for c in headers)
        body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
        return f'<table class="{klass}"><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>'

    def kpi_card(label, value, hint, kind):
        color = {"good": "#15803d", "bad": "#b91c1c", "ok": "#1f6feb",
                 "neutral": "#1f2937"}.get(kind, "#1f2937")
        return f"""
        <div class="kpi">
          <div class="kpi-label">{label}</div>
          <div class="kpi-value" style="color:{color}">{value}</div>
          <div class="kpi-hint">{hint}</div>
        </div>
        """

    kpi_html = "".join(kpi_card(*k) for k in kpis)

    equity_chart = _svg_line_chart(chart_data, title="累计收益曲线（vs 基准）", y_label="cumulative return", height=320)
    drawdown_chart = _svg_line_chart({"回撤": dd_series},
                                     title="回撤曲线",
                                     y_label="drawdown", height=220, fill_under="回撤")
    daily_ret_chart = _svg_line_chart({"日收益": daily_ret_series},
                                      title="日收益分布", y_label="daily return", height=200)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>uni-quant 回测报告 — {strategy}</title>
<style>
* {{ box-sizing: border-box; }}
body {{
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", Segoe UI, sans-serif;
  max-width: 1200px; margin: 24px auto; padding: 0 20px;
  color: #1f2937; background: #f9fafb;
}}
h1 {{ font-size: 24px; margin: 0 0 4px; }}
h2 {{ font-size: 18px; margin: 32px 0 12px; padding-bottom: 6px; border-bottom: 2px solid #e5e7eb; }}
.subtitle {{ color: #6b7280; font-size: 14px; margin-bottom: 24px; }}
.kpi-grid {{
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px;
}}
.kpi {{
  background: white; padding: 14px 16px; border-radius: 8px;
  border: 1px solid #e5e7eb;
}}
.kpi-label {{ font-size: 12px; color: #6b7280; margin-bottom: 4px; }}
.kpi-value {{ font-size: 22px; font-weight: 600; margin: 4px 0; }}
.kpi-hint {{ font-size: 11px; color: #9ca3af; line-height: 1.4; }}
.section-grid {{
  display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 16px;
}}
table {{
  width: 100%; border-collapse: collapse; background: white;
  border-radius: 6px; overflow: hidden; font-size: 13px;
}}
th, td {{ border-bottom: 1px solid #f1f5f9; padding: 6px 10px; text-align: right; }}
th:first-child, td:first-child {{ text-align: left; }}
th {{ background: #f8fafc; font-weight: 600; color: #475569; font-size: 12px; }}
tr:hover td {{ background: #f8fafc; }}
.box {{ background: white; padding: 14px 16px; border-radius: 8px; border: 1px solid #e5e7eb; }}
.note {{ background: #fef3c7; border-left: 4px solid #f59e0b; padding: 12px 16px;
        border-radius: 4px; font-size: 13px; color: #78350f; margin: 8px 0; }}
.section-grid table {{ width: 100%; }}
.cost-summary {{ display: flex; gap: 24px; margin-top: 8px; font-size: 13px; }}
.cost-summary div {{ flex: 1; }}
.cost-summary b {{ display: block; font-size: 18px; color: #1f2937; }}
</style>
</head>
<body>

<h1>uni-quant 回测报告 — <span style="color:#2563eb">{strategy}</span></h1>
<div class="subtitle">期间 {stats['start_date']} → {stats['end_date']} ｜
                     初始资金 {money(stats['initial_cash'])} ｜
                     终值 {money(stats['final_nav'])} ｜
                     生成于 {datetime.now():%Y-%m-%d %H:%M}</div>

<div class="note">
  <b>解读提示：</b>累计收益是 <code>(终值 - 初值)/初值</code> 在整个回测期间的总收益（**非年化**）。
  期间 {stats['calendar_days']} 自然日（约 {stats['trading_days']} 个交易日）。
  年化收益按 365 天复利折算。Sharpe Ratio &gt; 1 通常被视为优秀。
</div>

<h2>📊 关键指标</h2>
<div class="kpi-grid">
{kpi_html}
</div>

<h2>📈 累计收益曲线</h2>
<div class="box">{equity_chart}</div>

<h2>📉 回撤曲线 & 日收益</h2>
<div class="section-grid">
<div class="box">{drawdown_chart}</div>
<div class="box">{daily_ret_chart}</div>
</div>

<h2>📅 月度收益分解</h2>
<div class="box">
{tbl(["月份", "月末 NAV", "月收益率"], monthly_rows)}
</div>

<h2>💰 成本与换手</h2>
<div class="box">
  <div class="cost-summary">
    <div><b>{money(stats['total_cost'])}</b>总交易成本（佣金+印花税+过户费+滑点）</div>
    <div><b>{stats['total_fills']}</b>总成交笔数（买 {stats['buy_fills']} / 卖 {stats['sell_fills']}）</div>
    <div><b>{stats['turnover_ratio']:.2f}x</b>单边换手率（NAV倍数）</div>
  </div>
</div>

<h2>🏆 个股盈亏排行（实现 P&L）</h2>
<div class="section-grid">
  <div class="box">
    <h3 style="margin:0 0 8px;font-size:14px;color:#15803d">Top Winners</h3>
    {tbl(["Symbol", "Realized P&L"], [[w["symbol"], f"¥{w['realized_pnl']:+,.2f}"] for w in top_winners])}
  </div>
  <div class="box">
    <h3 style="margin:0 0 8px;font-size:14px;color:#b91c1c">Top Losers</h3>
    {tbl(["Symbol", "Realized P&L"], [[l["symbol"], f"¥{l['realized_pnl']:+,.2f}"] for l in losers])}
  </div>
</div>

<h2>📋 NAV 最近 30 个交易日</h2>
<div class="box">
{tbl(["日期", "NAV", "日收益", "现金", "持仓市值"], nav_recent)}
</div>

<h2>📝 最近 30 笔成交</h2>
<div class="box">
{tbl(["日期", "代码", "方向", "数量", "成交价", "成本"], fill_rows)}
</div>

</body></html>
"""
