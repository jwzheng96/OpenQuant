"""WorldQuant Alpha101 — public formulaic alphas (Kakushadze 2015).

Reference: "101 Formulaic Alphas", Z. Kakushadze, arXiv:1601.00991.
https://arxiv.org/abs/1601.00991

Conventions:
- All factor functions take a long panel with columns:
    symbol, trade_date, open, high, low, close, vol(volume), amount, [vwap]
  and return symbol/trade_date/value.
- `vwap` is synthesized from amount/vol if missing.
- `returns` = close.pct_change().
- `adv{N}` = N-day rolling mean volume.
- `rank(x)` = cross-sectional rank within trade_date, normalized to [0, 1].
- `ts_rank(x, n)` = rank within the most-recent N-day window, last position.

We implement a curated subset of ~30 alphas covering the most-cited / most-effective
ones. The remaining ~70 follow the same pattern and can be added iteratively.
"""

from __future__ import annotations

import polars as pl


# ============================================================================ #
# Helpers — polars expressions                                                 #
# ============================================================================ #


def _add_features(panel: pl.DataFrame) -> pl.DataFrame:
    """Enrich panel with vwap, returns, adv{20,60} once per call.

    Accepts either `vol` (Tushare convention) or `volume` (internal canonical
    after _normalize_daily rename). Normalizes to `vol` for the alpha formulas.
    """
    df = panel.sort(["symbol", "trade_date"])
    if "vol" not in df.columns and "volume" in df.columns:
        df = df.rename({"volume": "vol"})
    exprs = []
    if "vwap" not in df.columns:
        # amount in 千元 (Tushare); vol in 手 (Tushare). vwap ≈ amount*1000/(vol*100) = amount*10/vol
        # For AkShare: amount in 元, vol in 股 → vwap = amount / vol
        # Both expressions give meaningful per-share prices for ratio comparisons.
        exprs.append(
            pl.when(pl.col("vol") > 0)
            .then(pl.col("amount") / pl.col("vol"))
            .otherwise(pl.col("close"))
            .alias("vwap")
        )
    if "returns" not in df.columns:
        exprs.append(pl.col("close").pct_change().over("symbol").alias("returns"))
    if exprs:
        df = df.with_columns(exprs)
    if "adv20" not in df.columns:
        df = df.with_columns(pl.col("vol").rolling_mean(20).over("symbol").alias("adv20"))
    if "adv60" not in df.columns:
        df = df.with_columns(pl.col("vol").rolling_mean(60).over("symbol").alias("adv60"))
    return df


def _cs_rank(col: str) -> pl.Expr:
    """Cross-sectional percent-rank within each trade_date (0..1)."""
    return (pl.col(col).rank(method="average").over("trade_date") - 1) / (
        pl.count().over("trade_date") - 1
    )


def _ts_rank(col: str, n: int) -> pl.Expr:
    """Time-series rank within the last n bars per symbol, returned at last position."""
    return (
        pl.col(col)
        .rolling_map(lambda s: (s.rank(method="average")[-1] - 1) / max(len(s) - 1, 1), n)
        .over("symbol")
    )


def _delta(col: str, n: int) -> pl.Expr:
    return pl.col(col) - pl.col(col).shift(n).over("symbol")


def _delay(col: str, n: int) -> pl.Expr:
    return pl.col(col).shift(n).over("symbol")


def _ts_min(col: str, n: int) -> pl.Expr:
    return pl.col(col).rolling_min(n).over("symbol")


def _ts_max(col: str, n: int) -> pl.Expr:
    return pl.col(col).rolling_max(n).over("symbol")


def _ts_std(col: str, n: int) -> pl.Expr:
    return pl.col(col).rolling_std(n).over("symbol")


def _ts_sum(col: str, n: int) -> pl.Expr:
    return pl.col(col).rolling_sum(n).over("symbol")


def _ts_mean(col: str, n: int) -> pl.Expr:
    return pl.col(col).rolling_mean(n).over("symbol")


def _ts_corr(a: str, b: str, n: int) -> pl.Expr:
    return pl.rolling_corr(pl.col(a), pl.col(b), window_size=n).over("symbol")


def _decay_linear(col: str, n: int) -> pl.Expr:
    """Linearly-weighted moving avg: weights 1..n on most recent n bars."""
    weights = [i + 1 for i in range(n)]
    total = sum(weights)
    norm = [w / total for w in weights]
    return (
        pl.col(col)
        .rolling_map(
            lambda s: float((s.to_numpy()[-n:] * norm[-len(s):]).sum()) if len(s) >= 1 else None,
            n,
        )
        .over("symbol")
    )


def _select(df: pl.DataFrame, value_expr: pl.Expr) -> pl.DataFrame:
    return df.select(["symbol", "trade_date", value_expr.alias("value")])


# ============================================================================ #
# Alphas                                                                       #
# ============================================================================ #


def alpha001(panel: pl.DataFrame) -> pl.DataFrame:
    """rank(ts_argmax(SignedPower(returns<0 ? std(returns,20) : close, 2), 5)) - 0.5"""
    df = _add_features(panel)
    df = df.with_columns(
        pl.when(pl.col("returns") < 0)
        .then(_ts_std("returns", 20))
        .otherwise(pl.col("close"))
        .alias("_sigpow_in")
    ).with_columns((pl.col("_sigpow_in") ** 2).alias("_sigpow"))
    # ts_argmax over 5: position (1..5) of max in last 5 bars
    df = df.with_columns(
        pl.col("_sigpow")
        .rolling_map(lambda s: float(s.to_numpy().argmax() + 1) if len(s) else None, 5)
        .over("symbol")
        .alias("_argmax")
    ).with_columns((_cs_rank("_argmax") - 0.5).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def alpha002(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * correlation(rank(delta(log(volume),2)), rank((close-open)/open), 6)"""
    df = _add_features(panel).with_columns([
        (pl.col("vol").log().diff(2).over("symbol")).alias("_dlogv"),
        ((pl.col("close") - pl.col("open")) / pl.col("open")).alias("_co"),
    ]).with_columns([
        _cs_rank("_dlogv").alias("_rdlogv"),
        _cs_rank("_co").alias("_rco"),
    ])
    df = df.with_columns(
        (-_ts_corr("_rdlogv", "_rco", 6)).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha003(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * correlation(rank(open), rank(volume), 10)"""
    df = _add_features(panel).with_columns([
        _cs_rank("open").alias("_ro"),
        _cs_rank("vol").alias("_rv"),
    ]).with_columns((-_ts_corr("_ro", "_rv", 10)).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def alpha004(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * Ts_Rank(rank(low), 9)"""
    df = _add_features(panel).with_columns(_cs_rank("low").alias("_rl"))
    df = df.with_columns((-_ts_rank("_rl", 9)).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def alpha005(panel: pl.DataFrame) -> pl.DataFrame:
    """rank(open - sum(vwap,10)/10) * (-1 * abs(rank(close-vwap)))"""
    df = _add_features(panel).with_columns([
        (pl.col("open") - _ts_sum("vwap", 10) / 10).alias("_a"),
        (pl.col("close") - pl.col("vwap")).alias("_b"),
    ]).with_columns([
        _cs_rank("_a").alias("_ra"),
        _cs_rank("_b").alias("_rb"),
    ]).with_columns(
        (pl.col("_ra") * (-pl.col("_rb").abs())).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha006(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * correlation(open, volume, 10)"""
    df = _add_features(panel).with_columns(
        (-_ts_corr("open", "vol", 10)).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha007(panel: pl.DataFrame) -> pl.DataFrame:
    """((adv20 < volume) ? ((-1 * ts_rank(abs(delta(close,7)),60)) * sign(delta(close,7))) : -1)"""
    df = _add_features(panel).with_columns([
        _delta("close", 7).alias("_dc"),
    ]).with_columns(
        pl.col("_dc").abs().alias("_adc")
    )
    df = df.with_columns(
        pl.when(pl.col("adv20") < pl.col("vol"))
        .then(-_ts_rank("_adc", 60) * pl.col("_dc").sign())
        .otherwise(-1.0)
        .alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha008(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * rank((sum(open,5) * sum(returns,5)) - delay((sum(open,5)*sum(returns,5)), 10))"""
    df = _add_features(panel).with_columns(
        (_ts_sum("open", 5) * _ts_sum("returns", 5)).alias("_x")
    )
    df = df.with_columns(
        (pl.col("_x") - pl.col("_x").shift(10).over("symbol")).alias("_y")
    )
    df = df.with_columns((-_cs_rank("_y")).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def alpha009(panel: pl.DataFrame) -> pl.DataFrame:
    """if 0 < ts_min(delta(close,1),5) → delta(close,1)
       elif ts_max(delta(close,1),5) < 0 → delta(close,1)
       else → -delta(close,1)"""
    df = _add_features(panel).with_columns(_delta("close", 1).alias("_dc1"))
    df = df.with_columns([
        _ts_min("_dc1", 5).alias("_tmin"),
        _ts_max("_dc1", 5).alias("_tmax"),
    ]).with_columns(
        pl.when(pl.col("_tmin") > 0).then(pl.col("_dc1"))
        .when(pl.col("_tmax") < 0).then(pl.col("_dc1"))
        .otherwise(-pl.col("_dc1"))
        .alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha010(panel: pl.DataFrame) -> pl.DataFrame:
    """rank(alpha009-like with 4-day windows)"""
    df = _add_features(panel).with_columns(_delta("close", 1).alias("_dc1"))
    df = df.with_columns([
        _ts_min("_dc1", 4).alias("_tmin"),
        _ts_max("_dc1", 4).alias("_tmax"),
    ]).with_columns(
        pl.when(pl.col("_tmin") > 0).then(pl.col("_dc1"))
        .when(pl.col("_tmax") < 0).then(pl.col("_dc1"))
        .otherwise(-pl.col("_dc1"))
        .alias("_a10")
    ).with_columns(_cs_rank("_a10").alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def alpha012(panel: pl.DataFrame) -> pl.DataFrame:
    """sign(delta(volume,1)) * (-1 * delta(close,1))"""
    df = _add_features(panel).with_columns(
        (_delta("vol", 1).sign() * (-_delta("close", 1))).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha013(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * rank(covariance(rank(close), rank(volume), 5))"""
    df = _add_features(panel).with_columns([
        _cs_rank("close").alias("_rc"),
        _cs_rank("vol").alias("_rv"),
    ])
    df = df.with_columns(
        pl.rolling_cov(pl.col("_rc"), pl.col("_rv"), window_size=5).over("symbol").alias("_cov")
    ).with_columns((-_cs_rank("_cov")).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def alpha014(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * rank(delta(returns, 3)) * correlation(open, volume, 10)"""
    df = _add_features(panel).with_columns(_delta("returns", 3).alias("_dr"))
    df = df.with_columns([
        (-_cs_rank("_dr")).alias("_rrd"),
        _ts_corr("open", "vol", 10).alias("_corr"),
    ]).with_columns((pl.col("_rrd") * pl.col("_corr")).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def alpha018(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * rank((stddev(abs(close-open), 5) + (close-open)) + correlation(close, open, 10))"""
    df = _add_features(panel).with_columns([
        (pl.col("close") - pl.col("open")).alias("_co"),
        (pl.col("close") - pl.col("open")).abs().alias("_aco"),
    ])
    df = df.with_columns([
        _ts_std("_aco", 5).alias("_std5"),
        _ts_corr("close", "open", 10).alias("_corr"),
    ]).with_columns(
        (pl.col("_std5") + pl.col("_co") + pl.col("_corr")).alias("_x")
    ).with_columns(
        (-_cs_rank("_x")).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha019(panel: pl.DataFrame) -> pl.DataFrame:
    """(-1*sign((close-delay(close,7)) + delta(close,7))) * (1 + rank(1 + sum(returns,250)))"""
    df = _add_features(panel)
    df = df.with_columns([
        (pl.col("close") - _delay("close", 7)).alias("_d1"),
        _delta("close", 7).alias("_d2"),
        _ts_sum("returns", 250).alias("_s250"),
    ]).with_columns(
        ((-((pl.col("_d1") + pl.col("_d2")).sign())) * (1 + _cs_rank(
            (1 + pl.col("_s250")).alias("_one_plus_s250").meta.output_name() if False else "_s250"
        ))).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha023(panel: pl.DataFrame) -> pl.DataFrame:
    """((sum(high,20)/20 < high) ? (-1 * delta(high,2)) : 0)"""
    df = _add_features(panel).with_columns([
        (_ts_sum("high", 20) / 20).alias("_avg_h"),
        _delta("high", 2).alias("_dh"),
    ]).with_columns(
        pl.when(pl.col("_avg_h") < pl.col("high"))
        .then(-pl.col("_dh"))
        .otherwise(0.0)
        .alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha024(panel: pl.DataFrame) -> pl.DataFrame:
    """if ((delta(sum(close,100)/100, 100) / delay(close,100)) <= 0.05)
            → -1 * (close - ts_min(close,100))
       else → -1 * delta(close, 3)
    Simplified: detect long-term low growth → mean-reversion signal."""
    df = _add_features(panel).with_columns([
        (_ts_sum("close", 100) / 100).alias("_avg100"),
    ])
    df = df.with_columns(
        (pl.col("_avg100") - _delay("_avg100", 100)).alias("_d_avg"),
    ).with_columns(
        (pl.col("_d_avg") / _delay("close", 100)).alias("_ratio"),
    ).with_columns(
        pl.when(pl.col("_ratio") <= 0.05)
        .then(-(pl.col("close") - _ts_min("close", 100)))
        .otherwise(-_delta("close", 3))
        .alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha028(panel: pl.DataFrame) -> pl.DataFrame:
    """scale(((correlation(adv20, low, 5) + ((high+low)/2)) - close))"""
    df = _add_features(panel)
    df = df.with_columns([
        _ts_corr("adv20", "low", 5).alias("_corr"),
        ((pl.col("high") + pl.col("low")) / 2).alias("_mid"),
    ]).with_columns(
        (pl.col("_corr") + pl.col("_mid") - pl.col("close")).alias("_x")
    )
    # scale = x / abs(x).sum() within each date
    df = df.with_columns(
        (pl.col("_x") / pl.col("_x").abs().sum().over("trade_date")).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha032(panel: pl.DataFrame) -> pl.DataFrame:
    """scale(sum(close,7)/7 - close) + 20*scale(correlation(vwap, delay(close,5), 230))"""
    df = _add_features(panel)
    df = df.with_columns([
        ((_ts_sum("close", 7) / 7) - pl.col("close")).alias("_a"),
        _ts_corr("vwap", "close", 30).alias("_c30"),  # 230 → 30 for shorter history
    ])
    df = df.with_columns([
        (pl.col("_a") / pl.col("_a").abs().sum().over("trade_date")).alias("_sa"),
        (pl.col("_c30") / pl.col("_c30").abs().sum().over("trade_date")).alias("_sc"),
    ]).with_columns(
        (pl.col("_sa") + 20 * pl.col("_sc")).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha033(panel: pl.DataFrame) -> pl.DataFrame:
    """rank(-(1 - (open/close)))"""
    df = _add_features(panel).with_columns(
        (-(1 - pl.col("open") / pl.col("close"))).alias("_x")
    ).with_columns(_cs_rank("_x").alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def alpha034(panel: pl.DataFrame) -> pl.DataFrame:
    """rank((1 - rank(stddev(returns,2)/stddev(returns,5))) + (1 - rank(delta(close,1))))"""
    df = _add_features(panel).with_columns([
        _ts_std("returns", 2).alias("_s2"),
        _ts_std("returns", 5).alias("_s5"),
        _delta("close", 1).alias("_dc1"),
    ]).with_columns(
        (pl.col("_s2") / pl.col("_s5")).alias("_ratio")
    ).with_columns([
        _cs_rank("_ratio").alias("_r1"),
        _cs_rank("_dc1").alias("_r2"),
    ]).with_columns(
        ((1 - pl.col("_r1")) + (1 - pl.col("_r2"))).alias("_sum")
    ).with_columns(_cs_rank("_sum").alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def alpha038(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * rank(Ts_Rank(close,10)) * rank(close/open)"""
    df = _add_features(panel)
    df = df.with_columns([
        _ts_rank("close", 10).alias("_tr"),
        (pl.col("close") / pl.col("open")).alias("_co"),
    ]).with_columns([
        _cs_rank("_tr").alias("_rt"),
        _cs_rank("_co").alias("_rco"),
    ]).with_columns(
        (-pl.col("_rt") * pl.col("_rco")).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha041(panel: pl.DataFrame) -> pl.DataFrame:
    """sqrt(high*low) - vwap"""
    df = _add_features(panel).with_columns(
        ((pl.col("high") * pl.col("low")).sqrt() - pl.col("vwap")).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha042(panel: pl.DataFrame) -> pl.DataFrame:
    """rank(vwap - close) / rank(vwap + close)"""
    df = _add_features(panel).with_columns([
        (pl.col("vwap") - pl.col("close")).alias("_a"),
        (pl.col("vwap") + pl.col("close")).alias("_b"),
    ]).with_columns([
        _cs_rank("_a").alias("_ra"),
        _cs_rank("_b").alias("_rb"),
    ]).with_columns(
        (pl.col("_ra") / pl.col("_rb")).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha043(panel: pl.DataFrame) -> pl.DataFrame:
    """ts_rank(volume/adv20, 20) * ts_rank(-1 * delta(close,7), 8)"""
    df = _add_features(panel).with_columns([
        (pl.col("vol") / pl.col("adv20")).alias("_va"),
        (-_delta("close", 7)).alias("_ndc"),
    ]).with_columns(
        (_ts_rank("_va", 20) * _ts_rank("_ndc", 8)).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha044(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * correlation(high, rank(volume), 5)"""
    df = _add_features(panel).with_columns(_cs_rank("vol").alias("_rv"))
    df = df.with_columns((-_ts_corr("high", "_rv", 5)).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def alpha046(panel: pl.DataFrame) -> pl.DataFrame:
    """((((delay(close,20)-delay(close,10))/10) - ((delay(close,10)-close)/10) > 0.25) ? -1
       : ((same < -0.25) ? 1 : ((-1) * (close-delay(close,1)))))"""
    df = _add_features(panel)
    df = df.with_columns([
        ((_delay("close", 20) - _delay("close", 10)) / 10).alias("_a"),
        ((_delay("close", 10) - pl.col("close")) / 10).alias("_b"),
    ]).with_columns(
        (pl.col("_a") - pl.col("_b")).alias("_diff")
    ).with_columns(
        pl.when(pl.col("_diff") > 0.25).then(-1.0)
        .when(pl.col("_diff") < -0.25).then(1.0)
        .otherwise(-_delta("close", 1))
        .alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha049(panel: pl.DataFrame) -> pl.DataFrame:
    """((((delay(close,20)-delay(close,10))/10) - ((delay(close,10)-close)/10) < -0.1) ? 1
       : ((-1) * (close-delay(close,1))))"""
    df = _add_features(panel)
    df = df.with_columns([
        ((_delay("close", 20) - _delay("close", 10)) / 10).alias("_a"),
        ((_delay("close", 10) - pl.col("close")) / 10).alias("_b"),
    ]).with_columns(
        (pl.col("_a") - pl.col("_b")).alias("_diff")
    ).with_columns(
        pl.when(pl.col("_diff") < -0.1).then(1.0)
        .otherwise(-_delta("close", 1))
        .alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha051(panel: pl.DataFrame) -> pl.DataFrame:
    """Like alpha049 but threshold -0.05."""
    df = _add_features(panel)
    df = df.with_columns([
        ((_delay("close", 20) - _delay("close", 10)) / 10).alias("_a"),
        ((_delay("close", 10) - pl.col("close")) / 10).alias("_b"),
    ]).with_columns(
        (pl.col("_a") - pl.col("_b")).alias("_diff")
    ).with_columns(
        pl.when(pl.col("_diff") < -0.05).then(1.0)
        .otherwise(-_delta("close", 1))
        .alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha054(panel: pl.DataFrame) -> pl.DataFrame:
    """(-1 * (low - close) * open^5) / ((low - high) * close^5)"""
    df = _add_features(panel).with_columns(
        ((-1) * (pl.col("low") - pl.col("close")) * pl.col("open").pow(5)
         / ((pl.col("low") - pl.col("high")) * pl.col("close").pow(5))).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha101(panel: pl.DataFrame) -> pl.DataFrame:
    """(close - open) / (high - low + 0.001)"""
    df = _add_features(panel).with_columns(
        ((pl.col("close") - pl.col("open"))
         / (pl.col("high") - pl.col("low") + 0.001)).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


# ============================================================================ #
# Batch 2 — 25 more alphas covering different signal patterns                  #
# ============================================================================ #


def alpha011(panel: pl.DataFrame) -> pl.DataFrame:
    """((rank(ts_max(vwap-close,3)) + rank(ts_min(vwap-close,3))) * rank(delta(volume,3)))"""
    df = _add_features(panel).with_columns((pl.col("vwap") - pl.col("close")).alias("_vc"))
    df = df.with_columns([
        _ts_max("_vc", 3).alias("_tmx"),
        _ts_min("_vc", 3).alias("_tmn"),
        _delta("vol", 3).alias("_dv"),
    ]).with_columns([
        _cs_rank("_tmx").alias("_r1"),
        _cs_rank("_tmn").alias("_r2"),
        _cs_rank("_dv").alias("_r3"),
    ]).with_columns(((pl.col("_r1") + pl.col("_r2")) * pl.col("_r3")).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def alpha015(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * sum(rank(correlation(rank(high), rank(volume), 3)), 3)"""
    df = _add_features(panel).with_columns([
        _cs_rank("high").alias("_rh"),
        _cs_rank("vol").alias("_rv"),
    ])
    df = df.with_columns(_ts_corr("_rh", "_rv", 3).alias("_corr"))
    df = df.with_columns(_cs_rank("_corr").alias("_rc"))
    df = df.with_columns((-_ts_sum("_rc", 3)).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def alpha016(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * rank(covariance(rank(high), rank(volume), 5))"""
    df = _add_features(panel).with_columns([
        _cs_rank("high").alias("_rh"),
        _cs_rank("vol").alias("_rv"),
    ])
    df = df.with_columns(
        pl.rolling_cov(pl.col("_rh"), pl.col("_rv"), window_size=5).over("symbol").alias("_cov")
    ).with_columns((-_cs_rank("_cov")).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def alpha017(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * rank(ts_rank(close,10)) * rank(delta(delta(close,1),1)) * rank(ts_rank(volume/adv20, 5))"""
    df = _add_features(panel).with_columns([
        _ts_rank("close", 10).alias("_tr_c"),
        (pl.col("vol") / pl.col("adv20")).alias("_vr"),
    ])
    df = df.with_columns([
        _delta("close", 1).alias("_d1"),
    ]).with_columns(
        _delta("_d1", 1).alias("_d2")
    ).with_columns(_ts_rank("_vr", 5).alias("_tr_v")).with_columns([
        _cs_rank("_tr_c").alias("_r1"),
        _cs_rank("_d2").alias("_r2"),
        _cs_rank("_tr_v").alias("_r3"),
    ]).with_columns(
        (-pl.col("_r1") * pl.col("_r2") * pl.col("_r3")).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha019(panel: pl.DataFrame) -> pl.DataFrame:
    """(-1*sign((close-delay(close,7)) + delta(close,7))) * (1 + rank(1 + sum(returns,250)))"""
    df = _add_features(panel)
    df = df.with_columns([
        (pl.col("close") - _delay("close", 7) + _delta("close", 7)).alias("_a"),
        _ts_sum("returns", 250).alias("_s250"),
    ]).with_columns((1 + pl.col("_s250")).alias("_one_plus")).with_columns(
        _cs_rank("_one_plus").alias("_r")
    ).with_columns(
        ((-pl.col("_a").sign()) * (1 + pl.col("_r"))).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha020(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * rank(open-delay(high,1)) * rank(open-delay(close,1)) * rank(open-delay(low,1))"""
    df = _add_features(panel).with_columns([
        (pl.col("open") - _delay("high", 1)).alias("_a"),
        (pl.col("open") - _delay("close", 1)).alias("_b"),
        (pl.col("open") - _delay("low", 1)).alias("_c"),
    ]).with_columns([
        _cs_rank("_a").alias("_ra"),
        _cs_rank("_b").alias("_rb"),
        _cs_rank("_c").alias("_rc"),
    ]).with_columns((-pl.col("_ra") * pl.col("_rb") * pl.col("_rc")).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def alpha022(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * delta(correlation(high, volume, 5), 5) * rank(stddev(close, 20))"""
    df = _add_features(panel).with_columns(_ts_corr("high", "vol", 5).alias("_corr"))
    df = df.with_columns([
        _delta("_corr", 5).alias("_dc"),
        _ts_std("close", 20).alias("_s20"),
    ]).with_columns(_cs_rank("_s20").alias("_rs")).with_columns(
        (-pl.col("_dc") * pl.col("_rs")).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha025(panel: pl.DataFrame) -> pl.DataFrame:
    """rank((-1 * returns) * adv20 * vwap * (high - close))"""
    df = _add_features(panel).with_columns(
        ((-pl.col("returns")) * pl.col("adv20") * pl.col("vwap") * (pl.col("high") - pl.col("close"))).alias("_x")
    ).with_columns(_cs_rank("_x").alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def alpha026(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * ts_max(correlation(ts_rank(volume,5), ts_rank(high,5), 5), 3)"""
    df = _add_features(panel).with_columns([
        _ts_rank("vol", 5).alias("_trv"),
        _ts_rank("high", 5).alias("_trh"),
    ])
    df = df.with_columns(_ts_corr("_trv", "_trh", 5).alias("_corr"))
    df = df.with_columns((-_ts_max("_corr", 3)).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def alpha027(panel: pl.DataFrame) -> pl.DataFrame:
    """(0.5 < rank(sum(correlation(rank(volume), rank(vwap), 6), 2)/2.0)) ? -1 : 1"""
    df = _add_features(panel).with_columns([
        _cs_rank("vol").alias("_rv"),
        _cs_rank("vwap").alias("_rw"),
    ])
    df = df.with_columns(_ts_corr("_rv", "_rw", 6).alias("_corr"))
    df = df.with_columns((_ts_sum("_corr", 2) / 2.0).alias("_avg"))
    df = df.with_columns(_cs_rank("_avg").alias("_rk"))
    df = df.with_columns(
        pl.when(pl.col("_rk") > 0.5).then(-1.0).otherwise(1.0).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha035(panel: pl.DataFrame) -> pl.DataFrame:
    """ts_rank(volume,32) * (1 - ts_rank(close+high-low,16)) * (1 - ts_rank(returns,32))"""
    df = _add_features(panel).with_columns(
        (pl.col("close") + pl.col("high") - pl.col("low")).alias("_chl")
    )
    df = df.with_columns([
        _ts_rank("vol", 32).alias("_a"),
        _ts_rank("_chl", 16).alias("_b"),
        _ts_rank("returns", 32).alias("_c"),
    ]).with_columns(
        (pl.col("_a") * (1 - pl.col("_b")) * (1 - pl.col("_c"))).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha037(panel: pl.DataFrame) -> pl.DataFrame:
    """rank(correlation(delay(open-close,1), close, 200)) + rank(open-close)"""
    df = _add_features(panel).with_columns(
        (pl.col("open") - pl.col("close")).alias("_oc")
    ).with_columns(_delay("_oc", 1).alias("_oc1"))
    df = df.with_columns(_ts_corr("_oc1", "close", 200).alias("_corr"))
    df = df.with_columns([
        _cs_rank("_corr").alias("_r1"),
        _cs_rank("_oc").alias("_r2"),
    ]).with_columns((pl.col("_r1") + pl.col("_r2")).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def alpha040(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * rank(stddev(high, 10)) * correlation(high, volume, 10)"""
    df = _add_features(panel).with_columns([
        _ts_std("high", 10).alias("_s"),
        _ts_corr("high", "vol", 10).alias("_corr"),
    ]).with_columns(_cs_rank("_s").alias("_rs")).with_columns(
        (-pl.col("_rs") * pl.col("_corr")).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha045(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * rank(sum(delay(close,5), 20)/20) * correlation(close, volume, 2)
            * rank(correlation(sum(close,5), sum(close,20), 2))"""
    df = _add_features(panel)
    df = df.with_columns(_delay("close", 5).alias("_dc5"))
    df = df.with_columns([
        (_ts_sum("_dc5", 20) / 20).alias("_a"),
        _ts_corr("close", "vol", 2).alias("_corr1"),
        _ts_sum("close", 5).alias("_sc5"),
        _ts_sum("close", 20).alias("_sc20"),
    ]).with_columns(_ts_corr("_sc5", "_sc20", 2).alias("_corr2"))
    df = df.with_columns([
        _cs_rank("_a").alias("_r1"),
        _cs_rank("_corr2").alias("_r2"),
    ]).with_columns(
        (-pl.col("_r1") * pl.col("_corr1") * pl.col("_r2")).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha050(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * ts_max(rank(correlation(rank(volume), rank(vwap), 5)), 5)"""
    df = _add_features(panel).with_columns([
        _cs_rank("vol").alias("_rv"),
        _cs_rank("vwap").alias("_rw"),
    ])
    df = df.with_columns(_ts_corr("_rv", "_rw", 5).alias("_corr"))
    df = df.with_columns(_cs_rank("_corr").alias("_rk"))
    df = df.with_columns((-_ts_max("_rk", 5)).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def alpha053(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * delta(((close - low) - (high - close)) / (close - low), 9)"""
    df = _add_features(panel).with_columns(
        (((pl.col("close") - pl.col("low")) - (pl.col("high") - pl.col("close")))
         / (pl.col("close") - pl.col("low") + 1e-8)).alias("_x")
    ).with_columns((-_delta("_x", 9)).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def alpha055(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * correlation(rank((close - ts_min(low,12)) / (ts_max(high,12) - ts_min(low,12))),
                       rank(volume), 6)"""
    df = _add_features(panel).with_columns([
        _ts_min("low", 12).alias("_min"),
        _ts_max("high", 12).alias("_max"),
    ]).with_columns(
        ((pl.col("close") - pl.col("_min")) / (pl.col("_max") - pl.col("_min") + 1e-8)).alias("_norm")
    ).with_columns([
        _cs_rank("_norm").alias("_rn"),
        _cs_rank("vol").alias("_rv"),
    ]).with_columns(
        (-_ts_corr("_rn", "_rv", 6)).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha061(panel: pl.DataFrame) -> pl.DataFrame:
    """rank(vwap - ts_min(vwap,16)) < rank(correlation(vwap, adv180, 18)) ? 1 : 0
       (adv180 → adv60 here since our default panel may not have 180d history)"""
    df = _add_features(panel).with_columns(_ts_min("vwap", 16).alias("_min"))
    df = df.with_columns(
        (pl.col("vwap") - pl.col("_min")).alias("_a")
    ).with_columns(_ts_corr("vwap", "adv60", 18).alias("_corr")).with_columns([
        _cs_rank("_a").alias("_r1"),
        _cs_rank("_corr").alias("_r2"),
    ]).with_columns(
        (pl.col("_r1") < pl.col("_r2")).cast(pl.Float64).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha064(panel: pl.DataFrame) -> pl.DataFrame:
    """((rank(correlation(sum((open*0.18+low*0.82),13), sum(adv60,13), 17))
       < rank(delta((high+low)/2*0.18 + vwap*0.82, 4))) * -1)"""
    df = _add_features(panel).with_columns([
        (pl.col("open") * 0.18 + pl.col("low") * 0.82).alias("_a"),
        ((pl.col("high") + pl.col("low")) / 2 * 0.18 + pl.col("vwap") * 0.82).alias("_b"),
    ]).with_columns([
        _ts_sum("_a", 13).alias("_sa"),
        _ts_sum("adv60", 13).alias("_sadv"),
        _delta("_b", 4).alias("_db"),
    ])
    df = df.with_columns(_ts_corr("_sa", "_sadv", 17).alias("_corr"))
    df = df.with_columns([
        _cs_rank("_corr").alias("_r1"),
        _cs_rank("_db").alias("_r2"),
    ]).with_columns(
        ((pl.col("_r1") < pl.col("_r2")).cast(pl.Float64) * -1.0).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha065(panel: pl.DataFrame) -> pl.DataFrame:
    """((rank(correlation(open*0.0073+vwap*0.9927, sum(adv60,9), 6))
       < rank(open - ts_min(open, 14))) * -1)"""
    df = _add_features(panel).with_columns(
        (pl.col("open") * 0.0073 + pl.col("vwap") * 0.9927).alias("_a")
    ).with_columns([
        _ts_sum("adv60", 9).alias("_sadv"),
        _ts_min("open", 14).alias("_min"),
    ])
    df = df.with_columns([
        _ts_corr("_a", "_sadv", 6).alias("_corr"),
        (pl.col("open") - pl.col("_min")).alias("_b"),
    ]).with_columns([
        _cs_rank("_corr").alias("_r1"),
        _cs_rank("_b").alias("_r2"),
    ]).with_columns(
        ((pl.col("_r1") < pl.col("_r2")).cast(pl.Float64) * -1.0).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha068(panel: pl.DataFrame) -> pl.DataFrame:
    """((Ts_Rank(correlation(rank(high), rank(adv15), 9), 14)
       < rank(delta(close*0.518371+low*0.481629, 1))) * -1)
       (adv15 → adv20 for our schema)"""
    df = _add_features(panel).with_columns(
        pl.col("vol").rolling_mean(15).over("symbol").alias("_adv15")
    )
    df = df.with_columns([
        _cs_rank("high").alias("_rh"),
        _cs_rank("_adv15").alias("_radv"),
    ])
    df = df.with_columns(_ts_corr("_rh", "_radv", 9).alias("_corr"))
    df = df.with_columns(_ts_rank("_corr", 14).alias("_tr"))
    df = df.with_columns(
        (pl.col("close") * 0.518371 + pl.col("low") * 0.481629).alias("_b")
    ).with_columns(_delta("_b", 1).alias("_db"))
    df = df.with_columns([
        _cs_rank("_db").alias("_rb"),
    ]).with_columns(
        ((pl.col("_tr") < pl.col("_rb")).cast(pl.Float64) * -1.0).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha071(panel: pl.DataFrame) -> pl.DataFrame:
    """max(
        Ts_Rank(decay_linear(correlation(Ts_Rank(close,3), Ts_Rank(adv180,12), 18), 4), 16),
        Ts_Rank(decay_linear((rank((low+open) - (vwap+vwap)))^2, 16), 4)
       )"""
    df = _add_features(panel).with_columns([
        _ts_rank("close", 3).alias("_tc"),
        _ts_rank("adv60", 12).alias("_ta"),  # adv180 → adv60 for short history
    ])
    df = df.with_columns(_ts_corr("_tc", "_ta", 18).alias("_corr"))
    df = df.with_columns(_decay_linear("_corr", 4).alias("_dl1"))
    df = df.with_columns(
        ((pl.col("low") + pl.col("open")) - (pl.col("vwap") * 2)).alias("_lov")
    ).with_columns(_cs_rank("_lov").alias("_rl"))
    df = df.with_columns((pl.col("_rl") ** 2).alias("_rl2"))
    df = df.with_columns(_decay_linear("_rl2", 16).alias("_dl2"))
    df = df.with_columns([
        _ts_rank("_dl1", 16).alias("_a"),
        _ts_rank("_dl2", 4).alias("_b"),
    ]).with_columns(
        pl.max_horizontal(["_a", "_b"]).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha074(panel: pl.DataFrame) -> pl.DataFrame:
    """((rank(correlation(close, sum(adv30,37), 15))
       < rank(correlation(rank(high*0.0261+vwap*0.9739), rank(volume), 11))) * -1)"""
    df = _add_features(panel).with_columns(
        pl.col("vol").rolling_mean(30).over("symbol").alias("_adv30")
    )
    df = df.with_columns([
        _ts_sum("_adv30", 37).alias("_sadv"),
        (pl.col("high") * 0.0261 + pl.col("vwap") * 0.9739).alias("_a"),
    ])
    df = df.with_columns(_ts_corr("close", "_sadv", 15).alias("_corr1"))
    df = df.with_columns([
        _cs_rank("_a").alias("_ra"),
        _cs_rank("vol").alias("_rv"),
    ])
    df = df.with_columns(_ts_corr("_ra", "_rv", 11).alias("_corr2"))
    df = df.with_columns([
        _cs_rank("_corr1").alias("_r1"),
        _cs_rank("_corr2").alias("_r2"),
    ]).with_columns(
        ((pl.col("_r1") < pl.col("_r2")).cast(pl.Float64) * -1.0).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha075(panel: pl.DataFrame) -> pl.DataFrame:
    """rank(correlation(vwap, volume, 4)) < rank(correlation(rank(low), rank(adv50), 12))"""
    df = _add_features(panel).with_columns(
        pl.col("vol").rolling_mean(50).over("symbol").alias("_adv50")
    )
    df = df.with_columns([
        _cs_rank("low").alias("_rl"),
        _cs_rank("_adv50").alias("_ra"),
    ])
    df = df.with_columns([
        _ts_corr("vwap", "vol", 4).alias("_corr1"),
        _ts_corr("_rl", "_ra", 12).alias("_corr2"),
    ]).with_columns([
        _cs_rank("_corr1").alias("_r1"),
        _cs_rank("_corr2").alias("_r2"),
    ]).with_columns(
        (pl.col("_r1") < pl.col("_r2")).cast(pl.Float64).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha083(panel: pl.DataFrame) -> pl.DataFrame:
    """((rank(delay((high-low)/(sum(close,5)/5), 2)) * rank(rank(volume)))
       / (((high-low)/(sum(close,5)/5)) / (vwap-close)))"""
    df = _add_features(panel).with_columns(
        ((pl.col("high") - pl.col("low")) / (_ts_sum("close", 5) / 5)).alias("_hlc")
    )
    df = df.with_columns(_delay("_hlc", 2).alias("_dhlc"))
    df = df.with_columns([
        _cs_rank("_dhlc").alias("_r1"),
        _cs_rank("vol").alias("_rv"),
    ]).with_columns(_cs_rank("_rv").alias("_r2"))
    df = df.with_columns(
        ((pl.col("_r1") * pl.col("_r2")) /
         ((pl.col("_hlc")) / (pl.col("vwap") - pl.col("close") + 1e-8))).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha084(panel: pl.DataFrame) -> pl.DataFrame:
    """SignedPower(Ts_Rank(vwap - ts_max(vwap,15), 21), delta(close,5))"""
    df = _add_features(panel).with_columns(_ts_max("vwap", 15).alias("_mx"))
    df = df.with_columns(
        (pl.col("vwap") - pl.col("_mx")).alias("_diff")
    ).with_columns(_ts_rank("_diff", 21).alias("_tr"))
    df = df.with_columns(_delta("close", 5).alias("_dc"))
    # SignedPower(x, p) = sign(x) * |x|^p — guard zero/neg
    df = df.with_columns(
        (pl.col("_tr").sign() * pl.col("_tr").abs().pow(pl.col("_dc").fill_null(0.0))).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def alpha099(panel: pl.DataFrame) -> pl.DataFrame:
    """((rank(correlation(sum((high+low)/2, 19), sum(adv60, 19), 8))
       < rank(correlation(low, volume, 6))) * -1)"""
    df = _add_features(panel).with_columns(
        ((pl.col("high") + pl.col("low")) / 2).alias("_hl")
    )
    df = df.with_columns([
        _ts_sum("_hl", 19).alias("_shl"),
        _ts_sum("adv60", 19).alias("_sadv"),
    ])
    df = df.with_columns([
        _ts_corr("_shl", "_sadv", 8).alias("_c1"),
        _ts_corr("low", "vol", 6).alias("_c2"),
    ]).with_columns([
        _cs_rank("_c1").alias("_r1"),
        _cs_rank("_c2").alias("_r2"),
    ]).with_columns(
        ((pl.col("_r1") < pl.col("_r2")).cast(pl.Float64) * -1.0).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


# ============================================================================ #
# Industry-neutralized alphas (stubs)                                          #
#                                                                              #
# These require IndNeutralize(x, IndClass) — residuals after regressing factor #
# values on industry dummies per date. Need industry classification data on    #
# the panel (e.g. Shenwan/中信 一级行业). Will implement once data layer pulls   #
# industry from Tushare `stock_basic.industry` or AkShare `stock_board_industry`.
# Alphas: 48, 56, 58, 59, 63, 67, 69, 70, 76, 79, 80, 82, 87, 89, 90, 91, 93,  #
#         97, 100                                                              #
# ============================================================================ #


def _industry_neutralized_stub(panel: pl.DataFrame) -> pl.DataFrame:
    raise NotImplementedError(
        "industry-neutralized alpha — requires industry classification on panel"
    )


# ============================================================================ #
# Registration                                                                 #
# ============================================================================ #


_ALPHA_FNS = {
    "alpha001": alpha001,
    "alpha002": alpha002,
    "alpha003": alpha003,
    "alpha004": alpha004,
    "alpha005": alpha005,
    "alpha006": alpha006,
    "alpha007": alpha007,
    "alpha008": alpha008,
    "alpha009": alpha009,
    "alpha010": alpha010,
    "alpha012": alpha012,
    "alpha013": alpha013,
    "alpha014": alpha014,
    "alpha018": alpha018,
    "alpha023": alpha023,
    "alpha024": alpha024,
    "alpha028": alpha028,
    "alpha032": alpha032,
    "alpha033": alpha033,
    "alpha034": alpha034,
    "alpha038": alpha038,
    "alpha041": alpha041,
    "alpha042": alpha042,
    "alpha043": alpha043,
    "alpha044": alpha044,
    "alpha046": alpha046,
    "alpha049": alpha049,
    "alpha051": alpha051,
    "alpha054": alpha054,
    "alpha101": alpha101,
    # Batch 2
    "alpha011": alpha011,
    "alpha015": alpha015,
    "alpha016": alpha016,
    "alpha017": alpha017,
    "alpha019": alpha019,
    "alpha020": alpha020,
    "alpha022": alpha022,
    "alpha025": alpha025,
    "alpha026": alpha026,
    "alpha027": alpha027,
    "alpha035": alpha035,
    "alpha037": alpha037,
    "alpha040": alpha040,
    "alpha045": alpha045,
    "alpha050": alpha050,
    "alpha053": alpha053,
    "alpha055": alpha055,
    "alpha061": alpha061,
    "alpha064": alpha064,
    "alpha065": alpha065,
    "alpha068": alpha068,
    "alpha071": alpha071,
    "alpha074": alpha074,
    "alpha075": alpha075,
    "alpha083": alpha083,
    "alpha084": alpha084,
    "alpha099": alpha099,
}

# Names known to require IndNeutralize — exposed for documentation
INDUSTRY_NEUTRALIZED_ALPHAS = [
    "alpha048", "alpha056", "alpha058", "alpha059", "alpha063", "alpha067",
    "alpha069", "alpha070", "alpha076", "alpha079", "alpha080", "alpha082",
    "alpha087", "alpha089", "alpha090", "alpha091", "alpha093", "alpha097",
    "alpha100",
]


def register_alpha101(engine) -> None:
    for name, fn in _ALPHA_FNS.items():
        try:
            engine.register(name, fn)
        except ValueError:
            # Already registered (re-registration during dev) — skip
            pass


def all_alpha_names() -> list[str]:
    return sorted(_ALPHA_FNS)
