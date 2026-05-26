"""国泰君安 Alpha191 — A股本土量化因子库。

Source: 国泰君安证券研究所 *短周期价量特征 Alpha 因子* (Liang Yuanqing, 2017).
A subset of 191 公式化 alpha factors specifically tuned for A-share markets.

Compared to WorldQuant Alpha101, Alpha191:
  - More heavily uses `SMA(x, n, m)` — Wilder-style EMA with custom α=m/n.
  - More volume-weighted forms (vwap variations).
  - Some use `BENCHMARK*` (Index price) — we skip these unless 沪深300 is loaded.
  - Some use `INDUSTRY` — we skip these until industry data is available.

We implement ~40 of the most-cited / most-distinct alphas that need only
OHLCV + vwap + market_cap + turnover_rate. Remaining ones can be added.
"""

from __future__ import annotations

import polars as pl


# ============================================================================ #
# Helpers                                                                      #
# ============================================================================ #


def _add_features(panel: pl.DataFrame) -> pl.DataFrame:
    df = panel.sort(["symbol", "trade_date"])
    if "vol" not in df.columns and "volume" in df.columns:
        df = df.rename({"volume": "vol"})
    exprs = []
    if "vwap" not in df.columns:
        exprs.append(
            pl.when(pl.col("vol") > 0).then(pl.col("amount") / pl.col("vol"))
            .otherwise(pl.col("close")).alias("vwap")
        )
    if "returns" not in df.columns:
        exprs.append(pl.col("close").pct_change().over("symbol").alias("returns"))
    if exprs:
        df = df.with_columns(exprs)
    return df


def _cs_rank(col: str) -> pl.Expr:
    return (pl.col(col).rank(method="average").over("trade_date") - 1) / (
        pl.count().over("trade_date") - 1
    )


def _ts_rank(col: str, n: int) -> pl.Expr:
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


def _ts_cov(a: str, b: str, n: int) -> pl.Expr:
    return pl.rolling_cov(pl.col(a), pl.col(b), window_size=n).over("symbol")


def _count_cond(cond_expr: pl.Expr, n: int) -> pl.Expr:
    """COUNT(cond, n) — number of true conditions in last n bars."""
    return cond_expr.cast(pl.Int32).rolling_sum(n).over("symbol")


def _sma_wilder(col: str, n: int, m: int = 1) -> pl.Expr:
    """gtja SMA: weighted EMA where alpha = m/n.

    SMA[t] = (m * X[t] + (n - m) * SMA[t-1]) / n
    Equivalent to ewm with adjust=False, alpha=m/n. Polars `ewm_mean` has this signature.
    """
    return pl.col(col).ewm_mean(alpha=m / n, adjust=False).over("symbol")


# ============================================================================ #
# Alphas — implementing 40 of the 191                                          #
# ============================================================================ #


def gtja_alpha001(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * CORR(RANK(DELTA(LOG(VOLUME), 1)), RANK((CLOSE-OPEN)/OPEN), 6)"""
    df = _add_features(panel).with_columns([
        pl.col("vol").log().diff(1).over("symbol").alias("_dlv"),
        ((pl.col("close") - pl.col("open")) / pl.col("open")).alias("_co"),
    ]).with_columns([
        _cs_rank("_dlv").alias("_rd"),
        _cs_rank("_co").alias("_rc"),
    ]).with_columns(
        (-_ts_corr("_rd", "_rc", 6)).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha002(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * DELTA( ((CLOSE-LOW) - (HIGH-CLOSE)) / (HIGH-LOW), 1 )"""
    df = _add_features(panel).with_columns(
        (((pl.col("close") - pl.col("low")) - (pl.col("high") - pl.col("close")))
         / (pl.col("high") - pl.col("low") + 1e-8)).alias("_x")
    ).with_columns((-_delta("_x", 1)).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha005(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * TSMAX(CORR(TSRANK(VOLUME, 5), TSRANK(HIGH, 5), 5), 3)"""
    df = _add_features(panel).with_columns([
        _ts_rank("vol", 5).alias("_trv"),
        _ts_rank("high", 5).alias("_trh"),
    ])
    df = df.with_columns(_ts_corr("_trv", "_trh", 5).alias("_corr"))
    df = df.with_columns((-_ts_max("_corr", 3)).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha006(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * RANK(SIGN(DELTA(OPEN*0.85 + HIGH*0.15, 4)))"""
    df = _add_features(panel).with_columns(
        (pl.col("open") * 0.85 + pl.col("high") * 0.15).alias("_x")
    ).with_columns(_delta("_x", 4).sign().alias("_sx"))
    df = df.with_columns((-_cs_rank("_sx")).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha007(panel: pl.DataFrame) -> pl.DataFrame:
    """(RANK(MAX(VWAP-CLOSE, 3)) + RANK(MIN(VWAP-CLOSE, 3))) * RANK(DELTA(VOLUME, 3))"""
    df = _add_features(panel).with_columns((pl.col("vwap") - pl.col("close")).alias("_vc"))
    df = df.with_columns([
        _ts_max("_vc", 3).alias("_mx"),
        _ts_min("_vc", 3).alias("_mn"),
        _delta("vol", 3).alias("_dv"),
    ]).with_columns([
        _cs_rank("_mx").alias("_r1"),
        _cs_rank("_mn").alias("_r2"),
        _cs_rank("_dv").alias("_r3"),
    ]).with_columns(((pl.col("_r1") + pl.col("_r2")) * pl.col("_r3")).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha008(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * RANK(DELTA((HIGH+LOW)/2*0.2 + VWAP*0.8, 4))"""
    df = _add_features(panel).with_columns(
        ((pl.col("high") + pl.col("low")) / 2 * 0.2 + pl.col("vwap") * 0.8).alias("_x")
    ).with_columns(_delta("_x", 4).alias("_dx"))
    df = df.with_columns((-_cs_rank("_dx")).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha009(panel: pl.DataFrame) -> pl.DataFrame:
    """SMA( ((HIGH+LOW)/2 - (DELAY(HIGH,1)+DELAY(LOW,1))/2) * (HIGH-LOW)/VOLUME, 7, 2 )"""
    df = _add_features(panel).with_columns([
        ((pl.col("high") + pl.col("low")) / 2).alias("_mid"),
        ((_delay("high", 1) + _delay("low", 1)) / 2).alias("_dmid"),
    ]).with_columns(
        ((pl.col("_mid") - pl.col("_dmid")) * (pl.col("high") - pl.col("low"))
         / (pl.col("vol") + 1)).alias("_x")
    ).with_columns(_sma_wilder("_x", 7, 2).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha010(panel: pl.DataFrame) -> pl.DataFrame:
    """RANK( MAX( ((RET < 0) ? STD(RET,20) : CLOSE) ^ 2, 5 ) )"""
    df = _add_features(panel).with_columns(
        pl.when(pl.col("returns") < 0)
        .then(_ts_std("returns", 20))
        .otherwise(pl.col("close"))
        .alias("_in")
    ).with_columns((pl.col("_in") ** 2).alias("_sq"))
    df = df.with_columns(_ts_max("_sq", 5).alias("_mx"))
    df = df.with_columns(_cs_rank("_mx").alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha011(panel: pl.DataFrame) -> pl.DataFrame:
    """SUM(((CLOSE-LOW) - (HIGH-CLOSE)) / (HIGH-LOW) * VOLUME, 6)"""
    df = _add_features(panel).with_columns(
        (((pl.col("close") - pl.col("low")) - (pl.col("high") - pl.col("close")))
         / (pl.col("high") - pl.col("low") + 1e-8) * pl.col("vol")).alias("_x")
    ).with_columns(_ts_sum("_x", 6).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha012(panel: pl.DataFrame) -> pl.DataFrame:
    """RANK(OPEN - SUM(VWAP,10)/10) * -1 * RANK(ABS(CLOSE-VWAP))"""
    df = _add_features(panel).with_columns([
        (pl.col("open") - _ts_sum("vwap", 10) / 10).alias("_a"),
        (pl.col("close") - pl.col("vwap")).abs().alias("_b"),
    ]).with_columns([
        _cs_rank("_a").alias("_ra"),
        _cs_rank("_b").alias("_rb"),
    ]).with_columns((pl.col("_ra") * (-pl.col("_rb"))).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha013(panel: pl.DataFrame) -> pl.DataFrame:
    """SQRT(HIGH*LOW) - VWAP"""
    df = _add_features(panel).with_columns(
        ((pl.col("high") * pl.col("low")).sqrt() - pl.col("vwap")).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha014(panel: pl.DataFrame) -> pl.DataFrame:
    """CLOSE - DELAY(CLOSE, 5)"""
    df = _add_features(panel).with_columns(_delta("close", 5).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha015(panel: pl.DataFrame) -> pl.DataFrame:
    """OPEN / DELAY(CLOSE, 1) - 1"""
    df = _add_features(panel).with_columns(
        (pl.col("open") / _delay("close", 1) - 1).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha017(panel: pl.DataFrame) -> pl.DataFrame:
    """RANK(VWAP - TSMAX(VWAP, 15)) ^ DELTA(CLOSE, 5)"""
    df = _add_features(panel).with_columns(_ts_max("vwap", 15).alias("_mx"))
    df = df.with_columns(
        (pl.col("vwap") - pl.col("_mx")).alias("_a")
    ).with_columns(_cs_rank("_a").alias("_ra"))
    df = df.with_columns(_delta("close", 5).alias("_dc"))
    df = df.with_columns(
        (pl.col("_ra").sign() * pl.col("_ra").abs().pow(pl.col("_dc").fill_null(0.0))).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha018(panel: pl.DataFrame) -> pl.DataFrame:
    """CLOSE / DELAY(CLOSE, 5)"""
    df = _add_features(panel).with_columns(
        (pl.col("close") / _delay("close", 5)).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha019(panel: pl.DataFrame) -> pl.DataFrame:
    """if (CLOSE < DELAY(CLOSE,5))   → (CLOSE - DELAY(CLOSE,5)) / DELAY(CLOSE,5)
       elif (CLOSE = DELAY(CLOSE,5)) → 0
       else                          → (CLOSE - DELAY(CLOSE,5)) / CLOSE"""
    df = _add_features(panel).with_columns(_delay("close", 5).alias("_d5"))
    df = df.with_columns(
        pl.when(pl.col("close") < pl.col("_d5"))
        .then((pl.col("close") - pl.col("_d5")) / (pl.col("_d5") + 1e-8))
        .when(pl.col("close") == pl.col("_d5"))
        .then(0.0)
        .otherwise((pl.col("close") - pl.col("_d5")) / (pl.col("close") + 1e-8))
        .alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha020(panel: pl.DataFrame) -> pl.DataFrame:
    """(CLOSE - DELAY(CLOSE, 6)) / DELAY(CLOSE, 6) * 100"""
    df = _add_features(panel).with_columns(
        ((pl.col("close") / _delay("close", 6) - 1) * 100).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha023(panel: pl.DataFrame) -> pl.DataFrame:
    """SMA( (CLOSE > DELAY(CLOSE,1) ? STD(CLOSE,20) : 0), 20, 1 )
       / (SMA( STD(CLOSE,20), 20, 1 ) + SMA( CLOSE > DELAY(CLOSE,1) ? 0 : STD(CLOSE,20), 20, 1 )) * 100"""
    df = _add_features(panel).with_columns([
        _ts_std("close", 20).alias("_s20"),
        _delay("close", 1).alias("_d1"),
    ])
    df = df.with_columns([
        pl.when(pl.col("close") > pl.col("_d1")).then(pl.col("_s20")).otherwise(0.0).alias("_up"),
        pl.when(pl.col("close") > pl.col("_d1")).then(0.0).otherwise(pl.col("_s20")).alias("_dn"),
    ])
    df = df.with_columns([
        _sma_wilder("_up", 20, 1).alias("_sup"),
        _sma_wilder("_dn", 20, 1).alias("_sdn"),
    ]).with_columns(
        (pl.col("_sup") / (pl.col("_sup") + pl.col("_sdn") + 1e-8) * 100).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha024(panel: pl.DataFrame) -> pl.DataFrame:
    """SMA(CLOSE - DELAY(CLOSE, 5), 5, 1)"""
    df = _add_features(panel).with_columns(_delta("close", 5).alias("_dc"))
    df = df.with_columns(_sma_wilder("_dc", 5, 1).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha028(panel: pl.DataFrame) -> pl.DataFrame:
    """3 * SMA( (CLOSE-MIN(LOW,9)) / (MAX(HIGH,9)-MIN(LOW,9)) * 100, 3, 1 )
       - 2 * SMA( SMA( (CLOSE-MIN(LOW,9)) / (MAX(HIGH,9)-MIN(LOW,9)) * 100, 3, 1 ), 3, 1 )"""
    df = _add_features(panel).with_columns([
        _ts_min("low", 9).alias("_ll"),
        _ts_max("high", 9).alias("_hh"),
    ])
    df = df.with_columns(
        ((pl.col("close") - pl.col("_ll")) / (pl.col("_hh") - pl.col("_ll") + 1e-8) * 100).alias("_k")
    ).with_columns(_sma_wilder("_k", 3, 1).alias("_sk"))
    df = df.with_columns(_sma_wilder("_sk", 3, 1).alias("_ssk")).with_columns(
        (3 * pl.col("_sk") - 2 * pl.col("_ssk")).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha029(panel: pl.DataFrame) -> pl.DataFrame:
    """(CLOSE - DELAY(CLOSE, 6)) / DELAY(CLOSE, 6) * VOLUME"""
    df = _add_features(panel).with_columns(
        ((pl.col("close") / _delay("close", 6) - 1) * pl.col("vol")).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha031(panel: pl.DataFrame) -> pl.DataFrame:
    """(CLOSE - MEAN(CLOSE, 12)) / MEAN(CLOSE, 12) * 100"""
    df = _add_features(panel).with_columns(
        ((pl.col("close") / _ts_mean("close", 12) - 1) * 100).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha032(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * SUM(RANK(CORR(RANK(HIGH), RANK(VOLUME), 3)), 3)"""
    df = _add_features(panel).with_columns([
        _cs_rank("high").alias("_rh"),
        _cs_rank("vol").alias("_rv"),
    ])
    df = df.with_columns(_ts_corr("_rh", "_rv", 3).alias("_corr"))
    df = df.with_columns(_cs_rank("_corr").alias("_rc"))
    df = df.with_columns((-_ts_sum("_rc", 3)).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha034(panel: pl.DataFrame) -> pl.DataFrame:
    """MEAN(CLOSE, 12) / CLOSE"""
    df = _add_features(panel).with_columns(
        (_ts_mean("close", 12) / pl.col("close")).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha038(panel: pl.DataFrame) -> pl.DataFrame:
    """(SUM(HIGH,20)/20 < HIGH) ? -1 * DELTA(HIGH,2) : 0"""
    df = _add_features(panel).with_columns([
        (_ts_sum("high", 20) / 20).alias("_ah"),
        _delta("high", 2).alias("_dh"),
    ]).with_columns(
        pl.when(pl.col("_ah") < pl.col("high")).then(-pl.col("_dh")).otherwise(0.0).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha040(panel: pl.DataFrame) -> pl.DataFrame:
    """SUM(CLOSE>DELAY(CLOSE,1) ? VOLUME : 0, 26)
       / SUM(CLOSE<=DELAY(CLOSE,1) ? VOLUME : 0, 26) * 100"""
    df = _add_features(panel).with_columns(_delay("close", 1).alias("_d1"))
    df = df.with_columns([
        pl.when(pl.col("close") > pl.col("_d1")).then(pl.col("vol")).otherwise(0.0).alias("_up"),
        pl.when(pl.col("close") <= pl.col("_d1")).then(pl.col("vol")).otherwise(0.0).alias("_dn"),
    ]).with_columns([
        _ts_sum("_up", 26).alias("_su"),
        _ts_sum("_dn", 26).alias("_sd"),
    ]).with_columns(
        (pl.col("_su") / (pl.col("_sd") + 1) * 100).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha042(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * RANK(STD(HIGH, 10)) * CORR(HIGH, VOLUME, 10)"""
    df = _add_features(panel).with_columns([
        _ts_std("high", 10).alias("_s"),
        _ts_corr("high", "vol", 10).alias("_c"),
    ]).with_columns(_cs_rank("_s").alias("_rs")).with_columns(
        (-pl.col("_rs") * pl.col("_c")).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha043(panel: pl.DataFrame) -> pl.DataFrame:
    """SUM( (CLOSE > DELAY(CLOSE,1)) ? VOLUME
          : ((CLOSE < DELAY(CLOSE,1)) ? -VOLUME : 0), 6 )"""
    df = _add_features(panel).with_columns(_delay("close", 1).alias("_d1"))
    df = df.with_columns(
        pl.when(pl.col("close") > pl.col("_d1")).then(pl.col("vol"))
        .when(pl.col("close") < pl.col("_d1")).then(-pl.col("vol"))
        .otherwise(0.0)
        .alias("_x")
    ).with_columns(_ts_sum("_x", 6).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha046(panel: pl.DataFrame) -> pl.DataFrame:
    """(MEAN(CLOSE,3) + MEAN(CLOSE,6) + MEAN(CLOSE,12) + MEAN(CLOSE,24)) / (4 * CLOSE)"""
    df = _add_features(panel).with_columns(
        ((_ts_mean("close", 3) + _ts_mean("close", 6) + _ts_mean("close", 12) + _ts_mean("close", 24))
         / (4 * pl.col("close"))).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha052(panel: pl.DataFrame) -> pl.DataFrame:
    """SUM(MAX(0, HIGH - DELAY((HIGH+LOW+CLOSE)/3, 1)), 26)
       / SUM(MAX(0, DELAY((HIGH+LOW+CLOSE)/3, 1) - LOW), 26) * 100"""
    df = _add_features(panel).with_columns(
        ((pl.col("high") + pl.col("low") + pl.col("close")) / 3).alias("_tp")
    ).with_columns(_delay("_tp", 1).alias("_dtp"))
    df = df.with_columns([
        pl.max_horizontal([pl.col("high") - pl.col("_dtp"), pl.lit(0.0)]).alias("_up"),
        pl.max_horizontal([pl.col("_dtp") - pl.col("low"), pl.lit(0.0)]).alias("_dn"),
    ]).with_columns([
        _ts_sum("_up", 26).alias("_su"),
        _ts_sum("_dn", 26).alias("_sd"),
    ]).with_columns(
        (pl.col("_su") / (pl.col("_sd") + 1e-8) * 100).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha053(panel: pl.DataFrame) -> pl.DataFrame:
    """COUNT(CLOSE > DELAY(CLOSE, 1), 12) / 12 * 100"""
    df = _add_features(panel).with_columns(_delay("close", 1).alias("_d1"))
    df = df.with_columns(
        (_count_cond(pl.col("close") > pl.col("_d1"), 12) / 12.0 * 100).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha055(panel: pl.DataFrame) -> pl.DataFrame:
    """SUM( 16 * (CLOSE - DELAY(CLOSE,1) + (CLOSE-OPEN)/2 + DELAY(CLOSE,1) - DELAY(OPEN,1))
          / max( |HIGH - DELAY(CLOSE,1)|, |LOW - DELAY(CLOSE,1)| ), 20 )

    Simplified — gtja's original uses ABS comparison branches."""
    df = _add_features(panel).with_columns([
        _delay("close", 1).alias("_dc1"),
        _delay("open", 1).alias("_do1"),
    ])
    df = df.with_columns(
        ((pl.col("close") - pl.col("_dc1")) + (pl.col("close") - pl.col("open")) / 2
         + (pl.col("_dc1") - pl.col("_do1"))).alias("_num")
    ).with_columns([
        (pl.col("high") - pl.col("_dc1")).abs().alias("_hd"),
        (pl.col("low") - pl.col("_dc1")).abs().alias("_ld"),
    ]).with_columns(
        pl.max_horizontal(["_hd", "_ld"]).alias("_den")
    ).with_columns(
        (16 * pl.col("_num") / (pl.col("_den") + 1e-8)).alias("_x")
    ).with_columns(_ts_sum("_x", 20).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha057(panel: pl.DataFrame) -> pl.DataFrame:
    """SMA( (CLOSE - TSMIN(LOW, 9)) / (TSMAX(HIGH, 9) - TSMIN(LOW, 9)) * 100, 3, 1 )"""
    df = _add_features(panel).with_columns([
        _ts_min("low", 9).alias("_ll"),
        _ts_max("high", 9).alias("_hh"),
    ])
    df = df.with_columns(
        ((pl.col("close") - pl.col("_ll")) / (pl.col("_hh") - pl.col("_ll") + 1e-8) * 100).alias("_k")
    ).with_columns(_sma_wilder("_k", 3, 1).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha058(panel: pl.DataFrame) -> pl.DataFrame:
    """COUNT(CLOSE > DELAY(CLOSE, 1), 20) / 20 * 100"""
    df = _add_features(panel).with_columns(_delay("close", 1).alias("_d1"))
    df = df.with_columns(
        (_count_cond(pl.col("close") > pl.col("_d1"), 20) / 20.0 * 100).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha066(panel: pl.DataFrame) -> pl.DataFrame:
    """(CLOSE - MEAN(CLOSE, 6)) / MEAN(CLOSE, 6) * 100"""
    df = _add_features(panel).with_columns(
        ((pl.col("close") / _ts_mean("close", 6) - 1) * 100).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha068(panel: pl.DataFrame) -> pl.DataFrame:
    """SMA( ((HIGH+LOW)/2 - (DELAY(HIGH,1)+DELAY(LOW,1))/2) * (HIGH-LOW)/VOLUME, 15, 2 )"""
    df = _add_features(panel).with_columns([
        ((pl.col("high") + pl.col("low")) / 2).alias("_mid"),
        ((_delay("high", 1) + _delay("low", 1)) / 2).alias("_dmid"),
    ]).with_columns(
        ((pl.col("_mid") - pl.col("_dmid")) * (pl.col("high") - pl.col("low"))
         / (pl.col("vol") + 1)).alias("_x")
    ).with_columns(_sma_wilder("_x", 15, 2).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha069(panel: pl.DataFrame) -> pl.DataFrame:
    """SUM(MAX(0, OPEN - DELAY(OPEN, 1)), 20)
       / SUM(MAX(0, DELAY(OPEN, 1) - OPEN), 20)"""
    df = _add_features(panel).with_columns([
        pl.max_horizontal([pl.col("open") - _delay("open", 1), pl.lit(0.0)]).alias("_up"),
        pl.max_horizontal([_delay("open", 1) - pl.col("open"), pl.lit(0.0)]).alias("_dn"),
    ]).with_columns([
        _ts_sum("_up", 20).alias("_su"),
        _ts_sum("_dn", 20).alias("_sd"),
    ]).with_columns((pl.col("_su") / (pl.col("_sd") + 1e-8)).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha070(panel: pl.DataFrame) -> pl.DataFrame:
    """STD(AMOUNT, 6)"""
    df = _add_features(panel).with_columns(_ts_std("amount", 6).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha071(panel: pl.DataFrame) -> pl.DataFrame:
    """(CLOSE - MEAN(CLOSE, 24)) / MEAN(CLOSE, 24) * 100"""
    df = _add_features(panel).with_columns(
        ((pl.col("close") / _ts_mean("close", 24) - 1) * 100).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha078(panel: pl.DataFrame) -> pl.DataFrame:
    """((HIGH+LOW+CLOSE)/3 - MEAN((HIGH+LOW+CLOSE)/3, 12))
       / (0.015 * MEAN(ABS(CLOSE - MEAN((HIGH+LOW+CLOSE)/3, 12)), 12))  — CCI"""
    df = _add_features(panel).with_columns(
        ((pl.col("high") + pl.col("low") + pl.col("close")) / 3).alias("_tp")
    ).with_columns(_ts_mean("_tp", 12).alias("_mtp"))
    df = df.with_columns(
        (pl.col("close") - pl.col("_mtp")).abs().alias("_dev")
    ).with_columns(_ts_mean("_dev", 12).alias("_mdev"))
    df = df.with_columns(
        ((pl.col("_tp") - pl.col("_mtp")) / (0.015 * pl.col("_mdev") + 1e-8)).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha088(panel: pl.DataFrame) -> pl.DataFrame:
    """(CLOSE - DELAY(CLOSE, 20)) / DELAY(CLOSE, 20) * 100"""
    df = _add_features(panel).with_columns(
        ((pl.col("close") / _delay("close", 20) - 1) * 100).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha093(panel: pl.DataFrame) -> pl.DataFrame:
    """SUM( (OPEN >= DELAY(OPEN,1)) ? 0 : MAX(OPEN - LOW, OPEN - DELAY(OPEN, 1)), 20 )"""
    df = _add_features(panel).with_columns(_delay("open", 1).alias("_do1"))
    df = df.with_columns(
        pl.max_horizontal([
            pl.col("open") - pl.col("low"),
            pl.col("open") - pl.col("_do1"),
        ]).alias("_mx")
    ).with_columns(
        pl.when(pl.col("open") >= pl.col("_do1")).then(0.0).otherwise(pl.col("_mx")).alias("_x")
    ).with_columns(_ts_sum("_x", 20).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha096(panel: pl.DataFrame) -> pl.DataFrame:
    """SMA( SMA( (CLOSE - TSMIN(LOW,9)) / (TSMAX(HIGH,9) - TSMIN(LOW,9)) * 100, 3, 1 ), 3, 1 )"""
    df = _add_features(panel).with_columns([
        _ts_min("low", 9).alias("_ll"),
        _ts_max("high", 9).alias("_hh"),
    ])
    df = df.with_columns(
        ((pl.col("close") - pl.col("_ll")) / (pl.col("_hh") - pl.col("_ll") + 1e-8) * 100).alias("_k")
    ).with_columns(_sma_wilder("_k", 3, 1).alias("_sk"))
    df = df.with_columns(_sma_wilder("_sk", 3, 1).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha101(panel: pl.DataFrame) -> pl.DataFrame:
    """(RANK(CORR(CLOSE, SUM(MEAN(VOLUME, 30), 37), 15)) < RANK(CORR(RANK((HIGH*0.1+VWAP*0.9)), RANK(VOLUME), 11))) * -1"""
    df = _add_features(panel).with_columns([
        _ts_mean("vol", 30).alias("_mv30"),
        (pl.col("high") * 0.1 + pl.col("vwap") * 0.9).alias("_a"),
    ])
    df = df.with_columns(_ts_sum("_mv30", 37).alias("_smv"))
    df = df.with_columns(_ts_corr("close", "_smv", 15).alias("_c1"))
    df = df.with_columns([
        _cs_rank("_a").alias("_ra"),
        _cs_rank("vol").alias("_rv"),
    ])
    df = df.with_columns(_ts_corr("_ra", "_rv", 11).alias("_c2"))
    df = df.with_columns([
        _cs_rank("_c1").alias("_r1"),
        _cs_rank("_c2").alias("_r2"),
    ]).with_columns(((pl.col("_r1") < pl.col("_r2")).cast(pl.Float64) * -1.0).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha104(panel: pl.DataFrame) -> pl.DataFrame:
    """-1 * (DELTA(CORR(HIGH, VOLUME, 5), 5) * RANK(STD(CLOSE, 20)))"""
    df = _add_features(panel).with_columns(_ts_corr("high", "vol", 5).alias("_corr"))
    df = df.with_columns([
        _delta("_corr", 5).alias("_dc"),
        _ts_std("close", 20).alias("_s"),
    ]).with_columns(_cs_rank("_s").alias("_rs")).with_columns(
        (-pl.col("_dc") * pl.col("_rs")).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha106(panel: pl.DataFrame) -> pl.DataFrame:
    """CLOSE - DELAY(CLOSE, 20)"""
    df = _add_features(panel).with_columns(_delta("close", 20).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha109(panel: pl.DataFrame) -> pl.DataFrame:
    """SMA(HIGH - LOW, 10, 2) / SMA(SMA(HIGH - LOW, 10, 2), 10, 2)"""
    df = _add_features(panel).with_columns(
        (pl.col("high") - pl.col("low")).alias("_hl")
    ).with_columns(_sma_wilder("_hl", 10, 2).alias("_s1"))
    df = df.with_columns(_sma_wilder("_s1", 10, 2).alias("_s2")).with_columns(
        (pl.col("_s1") / (pl.col("_s2") + 1e-8)).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha117(panel: pl.DataFrame) -> pl.DataFrame:
    """TSRANK(VOLUME, 32) * (1 - TSRANK(CLOSE+HIGH-LOW, 16)) * (1 - TSRANK(RET, 32))"""
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


def gtja_alpha126(panel: pl.DataFrame) -> pl.DataFrame:
    """(CLOSE + HIGH + LOW) / 3"""
    df = _add_features(panel).with_columns(
        ((pl.col("close") + pl.col("high") + pl.col("low")) / 3).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha129(panel: pl.DataFrame) -> pl.DataFrame:
    """SUM( CLOSE < DELAY(CLOSE,1) ? ABS(CLOSE - DELAY(CLOSE,1)) : 0, 12 )"""
    df = _add_features(panel).with_columns(_delay("close", 1).alias("_d1"))
    df = df.with_columns(
        pl.when(pl.col("close") < pl.col("_d1"))
        .then((pl.col("close") - pl.col("_d1")).abs())
        .otherwise(0.0)
        .alias("_x")
    ).with_columns(_ts_sum("_x", 12).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha150(panel: pl.DataFrame) -> pl.DataFrame:
    """((HIGH+LOW+CLOSE)/3 * VOLUME)"""
    df = _add_features(panel).with_columns(
        ((pl.col("high") + pl.col("low") + pl.col("close")) / 3 * pl.col("vol")).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha153(panel: pl.DataFrame) -> pl.DataFrame:
    """(MEAN(CLOSE, 3) + MEAN(CLOSE, 6) + MEAN(CLOSE, 12) + MEAN(CLOSE, 24)) / 4"""
    df = _add_features(panel).with_columns(
        ((_ts_mean("close", 3) + _ts_mean("close", 6) + _ts_mean("close", 12) + _ts_mean("close", 24))
         / 4).alias("value")
    )
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha161(panel: pl.DataFrame) -> pl.DataFrame:
    """MEAN( MAX( MAX(HIGH-LOW, ABS(DELAY(CLOSE,1) - HIGH)), ABS(DELAY(CLOSE,1) - LOW) ), 12 )  — ATR"""
    df = _add_features(panel).with_columns(_delay("close", 1).alias("_dc"))
    df = df.with_columns([
        (pl.col("high") - pl.col("low")).alias("_hl"),
        (pl.col("_dc") - pl.col("high")).abs().alias("_dch"),
        (pl.col("_dc") - pl.col("low")).abs().alias("_dcl"),
    ]).with_columns(
        pl.max_horizontal(["_hl", "_dch", "_dcl"]).alias("_tr")
    ).with_columns(_ts_mean("_tr", 12).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha175(panel: pl.DataFrame) -> pl.DataFrame:
    """MEAN( MAX( MAX(HIGH-LOW, ABS(DELAY(CLOSE,1) - HIGH)), ABS(DELAY(CLOSE,1) - LOW) ), 6 )"""
    df = _add_features(panel).with_columns(_delay("close", 1).alias("_dc"))
    df = df.with_columns([
        (pl.col("high") - pl.col("low")).alias("_hl"),
        (pl.col("_dc") - pl.col("high")).abs().alias("_dch"),
        (pl.col("_dc") - pl.col("low")).abs().alias("_dcl"),
    ]).with_columns(
        pl.max_horizontal(["_hl", "_dch", "_dcl"]).alias("_tr")
    ).with_columns(_ts_mean("_tr", 6).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


def gtja_alpha187(panel: pl.DataFrame) -> pl.DataFrame:
    """SUM( OPEN <= DELAY(OPEN, 1) ? 0 : MAX(HIGH - OPEN, OPEN - DELAY(OPEN, 1)), 20 )"""
    df = _add_features(panel).with_columns(_delay("open", 1).alias("_do"))
    df = df.with_columns(
        pl.max_horizontal([
            pl.col("high") - pl.col("open"),
            pl.col("open") - pl.col("_do"),
        ]).alias("_mx")
    ).with_columns(
        pl.when(pl.col("open") <= pl.col("_do")).then(0.0).otherwise(pl.col("_mx")).alias("_x")
    ).with_columns(_ts_sum("_x", 20).alias("value"))
    return df.select(["symbol", "trade_date", "value"])


# ============================================================================ #
# Registration                                                                 #
# ============================================================================ #


_GTJA_FNS = {
    "gtja_a001": gtja_alpha001, "gtja_a002": gtja_alpha002,
    "gtja_a005": gtja_alpha005, "gtja_a006": gtja_alpha006,
    "gtja_a007": gtja_alpha007, "gtja_a008": gtja_alpha008,
    "gtja_a009": gtja_alpha009, "gtja_a010": gtja_alpha010,
    "gtja_a011": gtja_alpha011, "gtja_a012": gtja_alpha012,
    "gtja_a013": gtja_alpha013, "gtja_a014": gtja_alpha014,
    "gtja_a015": gtja_alpha015, "gtja_a017": gtja_alpha017,
    "gtja_a018": gtja_alpha018, "gtja_a019": gtja_alpha019,
    "gtja_a020": gtja_alpha020, "gtja_a023": gtja_alpha023,
    "gtja_a024": gtja_alpha024, "gtja_a028": gtja_alpha028,
    "gtja_a029": gtja_alpha029, "gtja_a031": gtja_alpha031,
    "gtja_a032": gtja_alpha032, "gtja_a034": gtja_alpha034,
    "gtja_a038": gtja_alpha038, "gtja_a040": gtja_alpha040,
    "gtja_a042": gtja_alpha042, "gtja_a043": gtja_alpha043,
    "gtja_a046": gtja_alpha046, "gtja_a052": gtja_alpha052,
    "gtja_a053": gtja_alpha053, "gtja_a055": gtja_alpha055,
    "gtja_a057": gtja_alpha057, "gtja_a058": gtja_alpha058,
    "gtja_a066": gtja_alpha066, "gtja_a068": gtja_alpha068,
    "gtja_a069": gtja_alpha069, "gtja_a070": gtja_alpha070,
    "gtja_a071": gtja_alpha071, "gtja_a078": gtja_alpha078,
    "gtja_a088": gtja_alpha088, "gtja_a093": gtja_alpha093,
    "gtja_a096": gtja_alpha096, "gtja_a101": gtja_alpha101,
    "gtja_a104": gtja_alpha104, "gtja_a106": gtja_alpha106,
    "gtja_a109": gtja_alpha109, "gtja_a117": gtja_alpha117,
    "gtja_a126": gtja_alpha126, "gtja_a129": gtja_alpha129,
    "gtja_a150": gtja_alpha150, "gtja_a153": gtja_alpha153,
    "gtja_a161": gtja_alpha161, "gtja_a175": gtja_alpha175,
    "gtja_a187": gtja_alpha187,
}


def register_alpha191(engine) -> None:
    for name, fn in _GTJA_FNS.items():
        try:
            engine.register(name, fn)
        except ValueError:
            pass


def all_alpha_names() -> list[str]:
    return sorted(_GTJA_FNS)
