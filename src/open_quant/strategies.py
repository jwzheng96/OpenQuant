"""Strategy implementations — all three flavors in one file.

Each strategy implements `on_date(d, panel, positions, cash) -> dict[symbol, weight]`
matching `open_quant.backtest.event_engine.TargetWeightStrategy`.

  - `MultiFactorStrategy`: daily/weekly equal-weighted top-N from blended factors.
  - `DualThrustCTA`: classic intraday breakout (here applied to daily bars for
    股指期货 — a real CTA build should consume 1m bars).
  - `EarningsDriftStrategy`: holds the top-quintile YoY-net-income-growth names
    for N days post-announcement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import polars as pl

from open_quant.factors import default_engine
from open_quant.portfolio import OptimizerConstraints, optimize_target_weights


# ---------------------------------------------------------------------------- #
# Multi-factor                                                                 #
# ---------------------------------------------------------------------------- #


@dataclass
class FactorWeight:
    name: str
    weight: float
    direction: int = 1


@dataclass
class MultiFactorStrategy:
    factors: list[FactorWeight]
    top_n: int = 30
    rebalance_freq: str = "W-FRI"               # 'D' | 'W-FRI' | 'M-END'
    max_weight: float = 0.05
    universe_filter: callable = field(default=lambda panel, d: panel)
    # Barra-light neutralization: regress each factor on (log_mv, vol_60d, mom_120d, bp)
    # cross-sectionally per date, use residuals. Strips style beta from alpha.
    neutralize_styles: bool = False
    # Optional qualitative overlay (agent-based KEEP/DROP). When set, the
    # top-N quant picks are filtered through LLM analysts before final
    # portfolio construction. Set to None to keep pure-quant behavior.
    qualitative_overlay: object | None = None
    # Hook fired after each overlay evaluation with the per-symbol decisions.
    # Useful for logging / inspection / paper trading state attribution.
    on_overlay_decisions: callable | None = None

    def __post_init__(self):
        self._engine = default_engine()
        self._last_rebalance: date | None = None
        self._exposures_cache: pl.DataFrame | None = None

    @staticmethod
    def _neutralize_one_date(factor_today: pl.DataFrame, exposures_today: pl.DataFrame) -> pl.DataFrame:
        """Cross-sectional OLS residuals on a single trade date.

        factor_today / exposures_today both have `symbol` + columns. Returns
        a new DataFrame `symbol, value` where value is the residual.
        """
        import numpy as np
        merged = factor_today.join(exposures_today, on="symbol", how="inner")
        merged = merged.filter(pl.col("value").is_finite())
        if merged.height < 5:
            return factor_today
        candidate_cols = [c for c in ("log_mv", "vol_60d", "mom_120d", "bp") if c in merged.columns]
        cols = [c for c in candidate_cols if merged[c].null_count() < merged.height]
        if not cols:
            return factor_today
        y = merged["value"].to_numpy().astype(np.float64)
        X = np.column_stack([
            np.ones(merged.height),
            *(np.nan_to_num(merged[c].to_numpy().astype(np.float64), nan=0.0) for c in cols),
        ])
        try:
            beta, *_ = np.linalg.lstsq(X, y, rcond=None)
            resid = y - X @ beta
        except np.linalg.LinAlgError:
            return factor_today
        return pl.DataFrame({"symbol": merged["symbol"], "value": resid})

    def _should_rebalance(self, d: date) -> bool:
        if self._last_rebalance is None:
            return True
        if self.rebalance_freq == "D":
            return d != self._last_rebalance
        if self.rebalance_freq == "W-FRI":
            return d.weekday() == 4 and d != self._last_rebalance
        if self.rebalance_freq == "M-END":
            from calendar import monthrange
            last_day = monthrange(d.year, d.month)[1]
            return d.day == last_day and d != self._last_rebalance
        return True

    def on_date(self, d, panel: pl.DataFrame, positions, cash) -> dict[str, float] | None:
        if not self._should_rebalance(d):
            return None
        snapshot = self.universe_filter(panel, d)
        snapshot = snapshot.filter(pl.col("trade_date") <= d)
        if snapshot.is_empty():
            return None

        # Optional: prepare style exposures snapshot for neutralization
        exposures_today = None
        if self.neutralize_styles:
            from open_quant.portfolio import style_exposures
            exps = style_exposures(snapshot)
            exposures_today = exps.filter(pl.col("trade_date") == d).drop("trade_date")
            if exposures_today.is_empty():
                # Fallback to latest available
                max_d = exps["trade_date"].max()
                exposures_today = exps.filter(pl.col("trade_date") == max_d).drop("trade_date")

        # Score = Σ w_i * direction_i * z_score(factor_i), winsorized + finite-only
        import math
        contribs: dict[str, float] = {}
        for fw in self.factors:
            r = self._engine.compute(fw.name, snapshot).data
            if r.is_empty():
                continue
            today = r.filter(pl.col("trade_date") == d).select(["symbol", "value"]).drop_nulls()
            if today.is_empty():
                max_d = r["trade_date"].max()
                today = r.filter(pl.col("trade_date") == max_d).select(["symbol", "value"]).drop_nulls()
            if today.is_empty():
                continue
            # Drop non-finite (inf/-inf/NaN) — defective factors otherwise poison the composite
            today = today.filter(pl.col("value").is_finite())
            if not today.is_empty() and exposures_today is not None and not exposures_today.is_empty():
                today = self._neutralize_one_date(today, exposures_today)
            if today.is_empty():
                continue
            # Winsorize at 5/95 percentiles to tame outliers
            lo, hi = today["value"].quantile(0.05), today["value"].quantile(0.95)
            if lo is not None and hi is not None and lo < hi:
                today = today.with_columns(pl.col("value").clip(lo, hi))
            mu = today["value"].mean()
            sd = today["value"].std()
            mu = 0.0 if mu is None or not math.isfinite(mu) else mu
            sd = 1.0 if sd is None or not math.isfinite(sd) or sd == 0 else sd
            today = today.with_columns(
                ((pl.col("value") - mu) / sd * (fw.direction * fw.weight)).alias("contrib")
            )
            for row in today.iter_rows(named=True):
                c = float(row["contrib"])
                if math.isfinite(c):
                    contribs[row["symbol"]] = contribs.get(row["symbol"], 0.0) + c
        if not contribs:
            return None

        ranked = sorted(contribs.items(), key=lambda kv: -kv[1])[: self.top_n]
        alpha = {sym: c for sym, c in ranked}

        # ---- Qualitative overlay (optional, agent-based filter) ----
        if self.qualitative_overlay is not None and alpha:
            try:
                decisions = self.qualitative_overlay.evaluate(list(alpha.keys()), as_of=d)
                if self.on_overlay_decisions is not None:
                    self.on_overlay_decisions(d, decisions)
                # Apply decisions: DROP filters out, KEEP optionally scales
                filtered: dict[str, float] = {}
                for sym, contrib in alpha.items():
                    dec = decisions.get(sym)
                    if dec is None or dec.action == "KEEP":
                        mult = getattr(dec, "weight_multiplier", 1.0) if dec else 1.0
                        filtered[sym] = contrib * mult
                # If everything was dropped, fall back to unfiltered (fail-safe)
                if filtered:
                    alpha = filtered
                else:
                    import logging
                    logging.getLogger(__name__).warning(
                        f"overlay dropped ALL {len(alpha)} symbols on {d}; falling back to unfiltered"
                    )
            except Exception as e:
                import logging
                logging.getLogger(__name__).exception(f"overlay failed: {e}; using raw quant alpha")

        weights = optimize_target_weights(
            alpha,
            current={s: 0.0 for s in alpha},
            constraints=OptimizerConstraints(max_weight=self.max_weight, gross_target=0.95),
        )
        self._last_rebalance = d
        return weights


# ---------------------------------------------------------------------------- #
# CTA — Dual Thrust (daily bars, single instrument)                            #
# ---------------------------------------------------------------------------- #


@dataclass
class DualThrustCTA:
    symbol: str
    lookback: int = 20
    k_upper: float = 0.5
    k_lower: float = 0.5
    fixed_lots: int = 1

    def on_date(self, d, panel: pl.DataFrame, positions, cash) -> dict[str, float] | None:
        history = panel.filter((pl.col("symbol") == self.symbol) & (pl.col("trade_date") <= d))
        if history.height < self.lookback + 1:
            return None
        window = history.tail(self.lookback)
        hh = window["high"].max()
        lc = window["close"].min()
        hc = window["close"].max()
        ll = window["low"].min()
        rng = max(hh - lc, hc - ll)
        today_open = history.tail(1)["open"][0]
        upper = today_open + self.k_upper * rng
        lower = today_open - self.k_lower * rng
        today_close = history.tail(1)["close"][0]

        cur_pos = positions.get(self.symbol)
        cur_qty = cur_pos.qty if cur_pos else 0
        if today_close > upper and cur_qty <= 0:
            return {self.symbol: 1.0}     # full long
        if today_close < lower and cur_qty >= 0:
            return {}                     # flatten (no shorting in spot stock; CTA needs futures broker)
        return None


# ---------------------------------------------------------------------------- #
# Earnings drift                                                                #
# ---------------------------------------------------------------------------- #


@dataclass
class EarningsDriftStrategy:
    """Hold top-quintile YoY net-income-growth names for `hold_days`.

    Requires `income` data joined onto the panel with a `yoy_netprofit` column.
    Falls back to a no-op if missing.
    """

    hold_days: int = 20
    max_concurrent: int = 20

    def __post_init__(self):
        self._entries: dict[str, date] = {}

    def on_date(self, d, panel: pl.DataFrame, positions, cash) -> dict[str, float] | None:
        if "yoy_netprofit" not in panel.columns:
            return None
        # Pop expired entries
        expired = [s for s, ed in self._entries.items()
                   if (d - ed).days > self.hold_days * 1.5]
        for s in expired:
            self._entries.pop(s, None)

        today = panel.filter(pl.col("trade_date") == d).drop_nulls("yoy_netprofit")
        if today.is_empty():
            return None
        # Top quintile by surprise
        threshold = today["yoy_netprofit"].quantile(0.8) or 0.0
        winners = today.filter(pl.col("yoy_netprofit") >= threshold)["symbol"].to_list()
        for w in winners:
            self._entries.setdefault(w, d)

        holds = list(self._entries)[: self.max_concurrent]
        if not holds:
            return None
        w = 0.95 / len(holds)
        return {s: w for s in holds}
