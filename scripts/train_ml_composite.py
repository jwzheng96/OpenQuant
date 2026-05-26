"""M3 — LightGBM composite trained on all 122 factors with time-series CV.

Pipeline:
  1. Load panel; compute all factors → wide feature matrix.
  2. Target: 5-day forward log return per symbol.
  3. Rolling CV: for each month M in test range, train on the prior 12 months.
  4. LightGBM regressor with reasonable defaults.
  5. Aggregate OOS predictions into a long DataFrame `(symbol, trade_date, ml_score)`.
  6. Persist as `data/parquet/factors/name=ml_lgb/data.parquet` so the engine can load it.
  7. Print OOS IC vs target.

Run after walk-forward; can be re-run anytime on the latest panel.
"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl
from dateutil.relativedelta import relativedelta

from uni_quant.data.api import get_data_api
from uni_quant.data.universe import annotate_for_backtest
from uni_quant.factors import default_engine

TRAIN_LOOKBACK_MONTHS = 12
TARGET_HORIZON_DAYS = 5
MIN_TRAIN_ROWS = 5000


def build_feature_matrix(panel: pl.DataFrame, eng) -> tuple[pl.DataFrame, list[str]]:
    """Compute all engine factors and pivot into a wide (symbol,trade_date) × factor matrix."""
    print(f"computing {len(eng.names())} factors on {panel.height}-row panel...", flush=True)
    feats: list[pl.DataFrame] = []
    valid_names: list[str] = []
    for i, name in enumerate(sorted(eng.names()), 1):
        try:
            r = eng.compute(name, panel)
        except Exception as e:
            print(f"  ⚠️  {name}: {str(e)[:60]}", flush=True)
            continue
        if r.data.is_empty():
            continue
        feats.append(r.data.rename({"value": name}))
        valid_names.append(name)
        if i % 20 == 0:
            print(f"  [{i}/{len(eng.names())}] {len(valid_names)} valid", flush=True)
    if not feats:
        raise RuntimeError("no factors produced data")

    print(f"merging {len(feats)} factor frames...", flush=True)
    out = feats[0]
    for f in feats[1:]:
        out = out.join(f, on=["symbol", "trade_date"], how="full")
        # outer join may add _right cols
        for c in list(out.columns):
            if c.endswith("_right"):
                out = out.drop(c)
    return out, valid_names


def add_target(features: pl.DataFrame, panel: pl.DataFrame) -> pl.DataFrame:
    fwd = (
        panel.sort(["symbol", "trade_date"])
        .with_columns(
            (pl.col("close").shift(-TARGET_HORIZON_DAYS).over("symbol") / pl.col("close") - 1)
            .alias("fwd_ret")
        )
        .select(["symbol", "trade_date", "fwd_ret"])
    )
    return features.join(fwd, on=["symbol", "trade_date"], how="inner")


def walk_forward_predict(
    feats: pl.DataFrame, factor_cols: list[str]
) -> tuple[pl.DataFrame, dict]:
    """Time-series rolling: each test month uses the previous 12 months as train."""
    # Determine month boundaries
    feats = feats.with_columns(pl.col("trade_date").dt.month_start().alias("_month"))
    months = sorted(feats["_month"].unique().to_list())
    n = len(months)
    print(f"walk-forward: {n} months; rolling train={TRAIN_LOOKBACK_MONTHS}m → test=1m", flush=True)

    all_preds = []
    metrics_per_fold = []
    t0 = time.time()
    for i in range(TRAIN_LOOKBACK_MONTHS, n):
        train_months = months[i - TRAIN_LOOKBACK_MONTHS:i]
        test_month = months[i]
        train_df = feats.filter(pl.col("_month").is_in(train_months))
        test_df = feats.filter(pl.col("_month") == test_month)

        train_df = train_df.drop_nulls(subset=["fwd_ret"])
        if train_df.height < MIN_TRAIN_ROWS or test_df.is_empty():
            continue

        X_train = train_df.select(factor_cols).fill_null(0.0).fill_nan(0.0).to_numpy()
        y_train = train_df["fwd_ret"].to_numpy().astype(np.float64)
        X_test = test_df.select(factor_cols).fill_null(0.0).fill_nan(0.0).to_numpy()

        model = lgb.LGBMRegressor(
            n_estimators=300,
            learning_rate=0.03,
            max_depth=6,
            num_leaves=31,
            min_child_samples=50,
            reg_alpha=0.1,
            reg_lambda=0.1,
            subsample=0.8,
            colsample_bytree=0.8,
            verbose=-1,
        )
        model.fit(X_train, y_train)
        y_pred = model.predict(X_test)

        pred_df = test_df.select(["symbol", "trade_date"]).with_columns(
            pl.Series(name="ml_score", values=y_pred)
        )
        all_preds.append(pred_df)

        # OOS IC
        try:
            test_y = test_df["fwd_ret"].drop_nulls().to_numpy()
            if len(test_y) == len(y_pred):
                ic = float(np.corrcoef(y_pred, test_df["fwd_ret"].to_numpy())[0, 1])
            else:
                ic = float("nan")
        except Exception:
            ic = float("nan")
        metrics_per_fold.append({"month": test_month, "n_train": train_df.height,
                                 "n_test": test_df.height, "oos_ic": ic})
        print(f"  [{i - TRAIN_LOOKBACK_MONTHS + 1}/{n - TRAIN_LOOKBACK_MONTHS}] {test_month}: "
              f"train={train_df.height} test={test_df.height} OOS_IC={ic:+.4f}", flush=True)

    print(f"walk-forward done in {time.time()-t0:.1f}s", flush=True)
    if not all_preds:
        raise RuntimeError("no OOS predictions produced")
    return pl.concat(all_preds), {"folds": metrics_per_fold}


def save_as_factor(preds: pl.DataFrame, name: str = "ml_lgb") -> Path:
    """Persist OOS predictions as a factor parquet."""
    api = get_data_api()
    target_dir = api.store.root / "factors" / f"name={name}"
    target_dir.mkdir(parents=True, exist_ok=True)
    out_path = target_dir / "data.parquet"
    preds.rename({"ml_score": "value"}).write_parquet(out_path)
    return out_path


def main():
    api = get_data_api()
    # Train on full available history; OOS predictions cover everything after 12m warmup
    panel = api.get_daily(None, date(2022, 1, 1), date(2026, 5, 25), adjust="fwd")
    print(f"panel: {panel.height} rows, {panel['symbol'].n_unique()} symbols", flush=True)

    sb = api.query.con.execute("SELECT * FROM stock_basic").pl()
    panel = annotate_for_backtest(panel, sb)

    eng = default_engine()
    features, factor_cols = build_feature_matrix(panel, eng)
    print(f"feature matrix: {features.height} × {len(factor_cols)} factors", flush=True)

    features = add_target(features, panel)
    print(f"after target join: {features.height} rows", flush=True)

    preds, info = walk_forward_predict(features, factor_cols)
    print(f"\nOOS predictions: {preds.height} rows", flush=True)

    # Overall OOS IC
    fwd = (
        panel.sort(["symbol", "trade_date"])
        .with_columns(
            (pl.col("close").shift(-TARGET_HORIZON_DAYS).over("symbol") / pl.col("close") - 1)
            .alias("fwd_ret")
        )
        .select(["symbol", "trade_date", "fwd_ret"])
    )
    merged = preds.join(fwd, on=["symbol", "trade_date"], how="inner").drop_nulls()
    arr_pred = merged["ml_score"].to_numpy()
    arr_y = merged["fwd_ret"].to_numpy()
    pearson_ic = float(np.corrcoef(arr_pred, arr_y)[0, 1])

    # Per-date rank IC
    rank_ics = []
    for td, g in merged.group_by("trade_date"):
        if g.height < 10:
            continue
        rank_ics.append(float(np.corrcoef(
            g["ml_score"].rank().to_numpy(),
            g["fwd_ret"].rank().to_numpy(),
        )[0, 1]))
    rank_ic_mean = float(np.mean(rank_ics)) if rank_ics else float("nan")
    rank_ic_ir = (rank_ic_mean / np.std(rank_ics) * np.sqrt(252)) if rank_ics and np.std(rank_ics) > 0 else 0.0

    print(f"\n=== ML composite OOS metrics ===", flush=True)
    print(f"  pearson IC:    {pearson_ic:+.4f}", flush=True)
    print(f"  mean rank IC:  {rank_ic_mean:+.4f}", flush=True)
    print(f"  rank ICIR:     {rank_ic_ir:+.2f}", flush=True)
    print(f"  n predictions: {merged.height}", flush=True)

    out_path = save_as_factor(preds)
    print(f"\nsaved factor `ml_lgb` to {out_path}", flush=True)


if __name__ == "__main__":
    main()
