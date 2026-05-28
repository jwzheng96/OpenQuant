"""Factor research workbench.

Provides per-factor analytics:
  - IC / Rank IC time series (daily cross-sectional)
  - Annualized ICIR
  - 5-quintile equal-weighted portfolio cumulative returns
  - Decay over 1 / 5 / 10 / 20 trading days

Heavy ops (full IC time series) are cached on-disk as parquet under
`data/parquet/factor_analytics/<factor>/`. First request computes; subsequent
requests just read.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import polars as pl

from app.core.config import get_settings


# ---------------------------------------------------------------------------- #
# Factor inventory                                                              #
# ---------------------------------------------------------------------------- #


def _factors_root() -> Path:
    return get_settings().open_quant_root / "data" / "parquet" / "factors"


def _analytics_root() -> Path:
    p = get_settings().open_quant_root / "data" / "parquet" / "factor_analytics"
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class FactorMeta:
    name: str
    has_cache: bool


def list_factors() -> list[FactorMeta]:
    root = _factors_root()
    if not root.exists():
        return []
    out: list[FactorMeta] = []
    for d in sorted(root.iterdir()):
        if not (d.is_dir() and d.name.startswith("name=")):
            continue
        name = d.name.removeprefix("name=")
        cache_dir = _analytics_root() / name
        has_cache = (cache_dir / "ic.parquet").exists()
        out.append(FactorMeta(name=name, has_cache=has_cache))
    return out


# ---------------------------------------------------------------------------- #
# Internal — load factor + price panel                                          #
# ---------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def _load_daily_panel() -> pl.DataFrame:
    """Cache the daily close panel. Lazy — only built once per process."""
    from app.services.quotes import _api
    api = _api()
    df = api.query.con.execute(
        "SELECT symbol, trade_date, close FROM daily WHERE trade_date >= '2020-01-01'"
    ).pl()
    return df.sort(["symbol", "trade_date"])


def _load_factor_panel(name: str) -> pl.DataFrame | None:
    p = _factors_root() / f"name={name}" / "data.parquet"
    if not p.exists():
        return None
    df = pl.read_parquet(p)
    if "value" not in df.columns:
        return None
    return df.select(["symbol", "trade_date", "value"])


def _build_panel_with_fwd(factor_name: str, horizon: int = 5) -> pl.DataFrame | None:
    """Join factor + forward N-day return."""
    f = _load_factor_panel(factor_name)
    if f is None or f.is_empty():
        return None
    px = _load_daily_panel()
    # Compute forward return per symbol
    fwd = (
        px.sort(["symbol", "trade_date"])
        .with_columns(
            (pl.col("close").shift(-horizon).over("symbol") / pl.col("close") - 1)
            .alias(f"fwd_{horizon}d")
        )
        .select(["symbol", "trade_date", f"fwd_{horizon}d"])
    )
    return f.join(fwd, on=["symbol", "trade_date"], how="inner").drop_nulls()


# ---------------------------------------------------------------------------- #
# IC time series                                                                #
# ---------------------------------------------------------------------------- #


def _compute_ic_series(factor_name: str, horizon: int = 5) -> pl.DataFrame:
    """Per-date cross-sectional Pearson + rank correlation between factor and fwd_ret.
    Output: trade_date, ic, rank_ic, n_obs.
    """
    panel = _build_panel_with_fwd(factor_name, horizon=horizon)
    if panel is None or panel.is_empty():
        return pl.DataFrame()

    fwd_col = f"fwd_{horizon}d"
    # Group by date and compute corr — manually using polars (pl.corr exists)
    rows = []
    for d, g in panel.group_by("trade_date"):
        if g.height < 20:
            continue
        v = g["value"].to_numpy()
        f = g[fwd_col].to_numpy()
        mask = np.isfinite(v) & np.isfinite(f)
        if mask.sum() < 20:
            continue
        v = v[mask]; f = f[mask]
        if np.std(v) == 0 or np.std(f) == 0:
            continue
        ic = float(np.corrcoef(v, f)[0, 1])
        # Rank IC
        rv = v.argsort().argsort()
        rf = f.argsort().argsort()
        rank_ic = float(np.corrcoef(rv, rf)[0, 1])
        # Group_by gives d as a Series of length 1 (or in newer polars a scalar tuple)
        d_val = d[0] if isinstance(d, tuple) else d
        rows.append({
            "trade_date": d_val,
            "ic": ic,
            "rank_ic": rank_ic,
            "n_obs": int(mask.sum()),
        })

    return pl.DataFrame(rows).sort("trade_date") if rows else pl.DataFrame()


def get_ic_series(factor_name: str, horizon: int = 5,
                  use_cache: bool = True) -> pl.DataFrame:
    """Return cached IC series; compute + save on first call."""
    cache_dir = _analytics_root() / factor_name
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"ic_h{horizon}.parquet"
    if use_cache and cache_file.exists():
        return pl.read_parquet(cache_file)
    df = _compute_ic_series(factor_name, horizon=horizon)
    if not df.is_empty():
        df.write_parquet(cache_file)
    return df


# ---------------------------------------------------------------------------- #
# Quintile portfolios                                                           #
# ---------------------------------------------------------------------------- #


def get_quintile_returns(factor_name: str, horizon: int = 5,
                         n_buckets: int = 5,
                         use_cache: bool = True) -> pl.DataFrame:
    """5-bucket equal-weighted portfolio cum return curve.
    Output: trade_date, q1..q5, top_minus_bottom (cum).
    """
    cache_file = _analytics_root() / factor_name / f"quintile_h{horizon}_b{n_buckets}.parquet"
    if use_cache and cache_file.exists():
        return pl.read_parquet(cache_file)

    panel = _build_panel_with_fwd(factor_name, horizon=horizon)
    if panel is None or panel.is_empty():
        return pl.DataFrame()
    fwd_col = f"fwd_{horizon}d"

    rows = []
    for d, g in panel.group_by("trade_date"):
        if g.height < n_buckets * 5:
            continue
        # rank within day, bucket by rank percentile
        g2 = g.with_columns(
            pl.col("value").rank(method="ordinal").alias("rk")
        )
        n = g2.height
        d_val = d[0] if isinstance(d, tuple) else d
        bucket_size = n / n_buckets
        row: dict = {"trade_date": d_val}
        for b in range(n_buckets):
            lo = int(b * bucket_size) + 1
            hi = int((b + 1) * bucket_size)
            mean_fwd = float(
                g2.filter((pl.col("rk") >= lo) & (pl.col("rk") <= hi))[fwd_col].mean() or 0.0
            )
            row[f"q{b+1}"] = mean_fwd
        rows.append(row)

    if not rows:
        return pl.DataFrame()

    df = pl.DataFrame(rows).sort("trade_date")
    # Cumulative product of (1 + daily ret) — but our fwd is N-day. Use as a daily proxy.
    for b in range(1, n_buckets + 1):
        df = df.with_columns(((1 + pl.col(f"q{b}")).cum_prod() - 1).alias(f"cum_q{b}"))
    df = df.with_columns(
        (pl.col(f"q{n_buckets}") - pl.col("q1")).alias("top_minus_bottom"),
    ).with_columns(
        ((1 + pl.col("top_minus_bottom")).cum_prod() - 1).alias("cum_top_minus_bottom")
    )

    if not df.is_empty():
        df.write_parquet(cache_file)
    return df


# ---------------------------------------------------------------------------- #
# Aggregates                                                                    #
# ---------------------------------------------------------------------------- #


def get_factor_summary(factor_name: str, horizon: int = 5) -> dict:
    """Aggregate stats for the factor list page."""
    ic = get_ic_series(factor_name, horizon=horizon)
    if ic.is_empty():
        return {"name": factor_name, "available": False}
    rank_ic = ic["rank_ic"].to_numpy()
    rank_ic = rank_ic[np.isfinite(rank_ic)]
    mean_ic = float(np.mean(rank_ic)) if len(rank_ic) else 0.0
    std_ic = float(np.std(rank_ic, ddof=1)) if len(rank_ic) > 1 else 0.0
    icir = mean_ic / std_ic * np.sqrt(252) if std_ic > 0 else 0.0
    return {
        "name": factor_name,
        "available": True,
        "mean_rank_ic": mean_ic,
        "icir": icir,
        "n_days": len(rank_ic),
        "pos_days_pct": float((rank_ic > 0).mean()) if len(rank_ic) else 0.0,
        "first_date": ic["trade_date"][0].isoformat() if hasattr(ic["trade_date"][0], "isoformat") else str(ic["trade_date"][0]),
        "last_date": ic["trade_date"][-1].isoformat() if hasattr(ic["trade_date"][-1], "isoformat") else str(ic["trade_date"][-1]),
    }


def get_decay_curve(factor_name: str, horizons: list[int] | None = None) -> list[dict]:
    """IC at multiple forward horizons → decay curve."""
    horizons = horizons or [1, 3, 5, 10, 20]
    out = []
    for h in horizons:
        ic = get_ic_series(factor_name, horizon=h)
        if ic.is_empty():
            continue
        v = ic["rank_ic"].to_numpy()
        v = v[np.isfinite(v)]
        out.append({
            "horizon": h,
            "mean_rank_ic": float(np.mean(v)) if len(v) else 0.0,
            "icir": float(np.mean(v) / np.std(v, ddof=1) * np.sqrt(252)) if len(v) > 1 and np.std(v) > 0 else 0.0,
        })
    return out
