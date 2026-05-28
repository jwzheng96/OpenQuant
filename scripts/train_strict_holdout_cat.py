"""Strict holdout — CatBoost variant.

CatBoost differs from LGB/XGB:
  - Ordered boosting (less prediction shift / less overfit)
  - Oblivious (symmetric) decision trees — different tree topology
  - Should produce predictions less correlated with LGB/XGB

Hyperparameters (intentionally different from LGB and XGB for ensemble decorrelation):
  - iterations=400, learning_rate=0.025, depth=6
  - l2_leaf_reg=3.0, rsm=0.8, subsample=0.8
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
from catboost import CatBoostRegressor

from open_quant.data.api import get_data_api
from open_quant.data.universe import annotate_for_backtest
from open_quant.factors import default_engine

TRAIN_START = date(2020, 1, 1)
TRAIN_END = date(2023, 12, 31)
TEST_START = date(2024, 1, 1)
TEST_END = date(2026, 5, 27)
EMBARGO_DAYS = 5
TARGET_HORIZON = 5


def build_features(panel: pl.DataFrame, eng) -> tuple[pl.DataFrame, list[str]]:
    print(f"computing {len(eng.names())} factors on {panel.height}-row panel...", flush=True)
    feats: list[pl.DataFrame] = []
    valid: list[str] = []
    for i, name in enumerate(sorted(eng.names()), 1):
        if name in ("ml_lgb", "ml_xgb_strict", "ml_lgb_strict", "ml_cat_strict",
                    "lhb_signal", "earnings_pead", "earnings_yjyg"):
            continue
        try:
            r = eng.compute(name, panel)
        except Exception:
            continue
        if r.data.is_empty():
            continue
        feats.append(r.data.rename({"value": name}))
        valid.append(name)
        if i % 30 == 0:
            print(f"  [{i}/{len(eng.names())}] {len(valid)} valid", flush=True)
    out = feats[0]
    for f in feats[1:]:
        out = out.join(f, on=["symbol", "trade_date"], how="full")
        for c in list(out.columns):
            if c.endswith("_right"):
                out = out.drop(c)
    return out, valid


def add_target(features, panel):
    fwd = (panel.sort(["symbol", "trade_date"])
           .with_columns((pl.col("close").shift(-TARGET_HORIZON).over("symbol") / pl.col("close") - 1)
                         .alias("fwd_ret"))
           .select(["symbol", "trade_date", "fwd_ret"]))
    return features.join(fwd, on=["symbol", "trade_date"], how="left")


def _ic(x, y):
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 10: return float("nan"), 0.0
    return float(np.corrcoef(x[mask], y[mask])[0, 1]), mask.sum()


def _per_date_rank_ic(df, pred_col, target_col):
    ics = []
    for d, g in df.group_by("trade_date"):
        rx = g[pred_col].rank().to_numpy()
        ry = g[target_col].rank().to_numpy()
        if len(rx) > 1 and np.std(rx) > 0 and np.std(ry) > 0:
            ics.append(float(np.corrcoef(rx, ry)[0, 1]))
    if not ics:
        return float("nan"), 0.0
    m, sd = float(np.mean(ics)), float(np.std(ics, ddof=1)) if len(ics) > 1 else 0.0
    ir = m / sd * np.sqrt(252) if sd > 0 else 0.0
    return m, ir


def main():
    api = get_data_api()
    panel_start = TRAIN_START - timedelta(days=400)
    panel = api.get_daily(None, panel_start, TEST_END, adjust="fwd")
    print(f"panel: {panel.height} rows, {panel['symbol'].n_unique()} symbols", flush=True)

    sb = api.query.con.execute("SELECT * FROM stock_basic").pl()
    panel = annotate_for_backtest(panel, sb)

    eng = default_engine()
    features, factor_cols = build_features(panel, eng)
    features = add_target(features, panel)
    features = features.filter(
        (pl.col("trade_date") >= TRAIN_START) & (pl.col("trade_date") <= TEST_END)
    )
    print(f"feature matrix: {features.height} rows × {len(factor_cols)} factors", flush=True)

    embargo_cutoff = TRAIN_END - timedelta(days=EMBARGO_DAYS * 2)
    train = features.filter(
        (pl.col("trade_date") <= embargo_cutoff)
        & pl.col("fwd_ret").is_not_null() & pl.col("fwd_ret").is_finite()
    )
    test = features.filter(
        (pl.col("trade_date") >= TEST_START)
        & pl.col("fwd_ret").is_not_null() & pl.col("fwd_ret").is_finite()
    )
    print(f"  train: {train.height} rows  test: {test.height} rows", flush=True)

    X_train = train.select(factor_cols).fill_null(0.0).fill_nan(0.0).to_numpy()
    y_train = train["fwd_ret"].to_numpy().astype(np.float64)
    X_test = test.select(factor_cols).fill_null(0.0).fill_nan(0.0).to_numpy()
    y_test = test["fwd_ret"].to_numpy().astype(np.float64)

    # CatBoost also strict about extremes; same defensive cleanup as XGB
    X_train = np.nan_to_num(X_train, nan=0.0, posinf=0.0, neginf=0.0)
    X_test = np.nan_to_num(X_test, nan=0.0, posinf=0.0, neginf=0.0)
    X_train = np.clip(X_train, -1e6, 1e6).astype(np.float32)
    X_test = np.clip(X_test, -1e6, 1e6).astype(np.float32)

    print(f"\nfitting CatBoost ({X_train.shape[0]:,} samples)...", flush=True)
    t0 = time.time()
    model = CatBoostRegressor(
        iterations=400,
        learning_rate=0.025,
        depth=6,
        l2_leaf_reg=3.0,
        rsm=0.8,
        subsample=0.8,
        bootstrap_type="Bernoulli",
        random_seed=42,
        verbose=False,
    )
    model.fit(X_train, y_train)
    print(f"  fit done in {time.time()-t0:.1f}s", flush=True)

    pred_train = model.predict(X_train)
    pred_test = model.predict(X_test)

    train_p = train.with_columns(pl.Series("pred", pred_train))
    test_p = test.with_columns(pl.Series("pred", pred_test))
    ic_train, _ = _ic(pred_train, y_train)
    ic_test, _ = _ic(pred_test, y_test)
    rank_ic_train, icir_train = _per_date_rank_ic(train_p, "pred", "fwd_ret")
    rank_ic_test, icir_test = _per_date_rank_ic(test_p, "pred", "fwd_ret")

    rng = np.random.default_rng(42)
    rand_test = rng.standard_normal(len(pred_test))
    test_r = test.with_columns(pl.Series("rand", rand_test))
    rand_pearson, _ = _ic(rand_test, y_test)
    rand_rank, rand_icir = _per_date_rank_ic(test_r, "rand", "fwd_ret")

    print("\n" + "=" * 70)
    print(f"{'':30s} {'TRAIN (IS)':>15} {'TEST (OOS)':>15} {'RANDOM':>15}")
    print(f"{'Pearson IC':30s} {ic_train:+15.4f} {ic_test:+15.4f} {rand_pearson:+15.4f}")
    print(f"{'Rank IC':30s} {rank_ic_train:+15.4f} {rank_ic_test:+15.4f} {rand_rank:+15.4f}")
    print(f"{'Rank ICIR':30s} {icir_train:+15.2f} {icir_test:+15.2f} {rand_icir:+15.2f}")
    print("=" * 70)

    preds_df = test_p.select(["symbol", "trade_date", pl.col("pred").alias("value")])
    out_dir = Path("data/parquet/factors/name=ml_cat_strict")
    out_dir.mkdir(parents=True, exist_ok=True)
    preds_df.write_parquet(out_dir / "data.parquet")
    print(f"\nsaved factor `ml_cat_strict` to {out_dir/'data.parquet'}", flush=True)

    # Importance (CatBoost specific API)
    importance = list(zip(factor_cols, model.feature_importances_))
    importance.sort(key=lambda x: -x[1])
    print("\nTop 15 features:")
    for n, s in importance[:15]:
        print(f"  {n:20s} {s:.4f}")

    report = {
        "method": "strict_holdout_catboost",
        "train_samples": X_train.shape[0], "test_samples": X_test.shape[0],
        "is_pearson_ic": ic_train, "oos_pearson_ic": ic_test,
        "is_rank_ic": rank_ic_train, "oos_rank_ic": rank_ic_test,
        "is_rank_icir": icir_train, "oos_rank_icir": icir_test,
        "random_rank_ic": rand_rank, "random_rank_icir": rand_icir,
        "top_features": [[n, float(s)] for n, s in importance[:30]],
    }
    Path("data/strict_holdout_cat_report.json").write_text(
        json.dumps(report, indent=2, default=str))
    print(f"\nreport → data/strict_holdout_cat_report.json")


if __name__ == "__main__":
    main()
