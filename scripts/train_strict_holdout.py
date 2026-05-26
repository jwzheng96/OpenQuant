"""Strict train/test holdout — addresses 'training and evaluating on same data' concern.

Methodology (avoids look-ahead leakage):
  1. Train LightGBM on a fixed window (2020-01 to 2023-12)
  2. Apply 5-day **embargo** at end of training: drop last 5 rows per symbol so
     5-day forward return target can't peek into the held-out test window
  3. Predict on a held-out test window (2024-01 to 2026-05) — model NEVER sees this
  4. Compare:
     - In-sample (IS) fit on training window
     - Out-of-sample (OOS) fit on test window
     - Gap between the two = overfitting magnitude
  5. Backtest OOS predictions
  6. Also benchmark a random-noise factor for "null" baseline

This is a stricter test than rolling walk-forward because there's one model and
one held-out span — much less room for subtle leakage.
"""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl

from uni_quant.data.api import get_data_api
from uni_quant.data.universe import annotate_for_backtest
from uni_quant.factors import default_engine

# Training window (no peek at 2024+)
TRAIN_START = date(2020, 1, 1)
TRAIN_END = date(2023, 12, 31)
# Test window — model never sees this data during training
TEST_START = date(2024, 1, 1)
TEST_END = date(2026, 5, 25)
EMBARGO_DAYS = 5      # drop last N days of training so fwd_ret target doesn't peek
TARGET_HORIZON = 5    # 5-day forward return


def build_features(panel: pl.DataFrame, eng) -> tuple[pl.DataFrame, list[str]]:
    print(f"computing {len(eng.names())} factors on {panel.height}-row panel...", flush=True)
    feats: list[pl.DataFrame] = []
    valid: list[str] = []
    for i, name in enumerate(sorted(eng.names()), 1):
        if name == "ml_lgb":     # skip — that's a derived factor
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
    fwd = (
        panel.sort(["symbol", "trade_date"])
        .with_columns(
            (pl.col("close").shift(-TARGET_HORIZON).over("symbol") / pl.col("close") - 1)
            .alias("fwd_ret")
        )
        .select(["symbol", "trade_date", "fwd_ret"])
    )
    return features.join(fwd, on=["symbol", "trade_date"], how="inner")


def _ic(arr_x: np.ndarray, arr_y: np.ndarray) -> tuple[float, float]:
    """Pearson IC + rank IC."""
    if len(arr_x) < 10:
        return float("nan"), float("nan")
    pearson = float(np.corrcoef(arr_x, arr_y)[0, 1])
    rx = np.argsort(np.argsort(arr_x))
    ry = np.argsort(np.argsort(arr_y))
    rank = float(np.corrcoef(rx, ry)[0, 1])
    return pearson, rank


def _per_date_rank_ic(df: pl.DataFrame, pred_col: str, target_col: str) -> tuple[float, float]:
    """Per-date rank IC: mean across dates, ICIR = mean / std * sqrt(252)."""
    ics = []
    for _, g in df.group_by("trade_date"):
        if g.height < 10:
            continue
        rx = g[pred_col].rank().to_numpy()
        ry = g[target_col].rank().to_numpy()
        if len(rx) > 1 and np.std(rx) > 0 and np.std(ry) > 0:
            ics.append(float(np.corrcoef(rx, ry)[0, 1]))
    if not ics:
        return float("nan"), 0.0
    m = float(np.mean(ics))
    sd = float(np.std(ics, ddof=1)) if len(ics) > 1 else 0.0
    ir = m / sd * np.sqrt(252) if sd > 0 else 0.0
    return m, ir


def main():
    api = get_data_api()
    # Pull a larger panel — extra 250 days before train_start for factor warm-up
    panel_start = TRAIN_START - timedelta(days=400)
    panel = api.get_daily(None, panel_start, TEST_END, adjust="fwd")
    print(f"panel: {panel.height} rows, {panel['symbol'].n_unique()} symbols "
          f"{panel['trade_date'].min()} → {panel['trade_date'].max()}", flush=True)

    sb = api.query.con.execute("SELECT * FROM stock_basic").pl()
    panel = annotate_for_backtest(panel, sb)

    eng = default_engine()
    features, factor_cols = build_features(panel, eng)
    features = add_target(features, panel)
    features = features.filter(
        (pl.col("trade_date") >= TRAIN_START) & (pl.col("trade_date") <= TEST_END)
    )
    print(f"feature matrix: {features.height} rows × {len(factor_cols)} factors", flush=True)

    # Split with embargo, then strip nulls + NaN target rows
    embargo_cutoff = TRAIN_END - timedelta(days=EMBARGO_DAYS * 2)
    train = features.filter(
        (pl.col("trade_date") <= embargo_cutoff)
        & pl.col("fwd_ret").is_not_null()
        & pl.col("fwd_ret").is_finite()
    )
    embargo = features.filter(
        (pl.col("trade_date") > embargo_cutoff) & (pl.col("trade_date") <= TRAIN_END)
    )
    test = features.filter(
        (pl.col("trade_date") >= TEST_START)
        & pl.col("fwd_ret").is_not_null()
        & pl.col("fwd_ret").is_finite()
    )
    print(f"  train: {train.height} rows ({train['trade_date'].min()} → {train['trade_date'].max()})")
    print(f"  embargo (excluded): {embargo.height} rows")
    print(f"  test:  {test.height} rows ({test['trade_date'].min()} → {test['trade_date'].max()})", flush=True)

    X_train = train.select(factor_cols).fill_null(0.0).fill_nan(0.0).to_numpy()
    y_train = train["fwd_ret"].to_numpy().astype(np.float64)
    X_test = test.select(factor_cols).fill_null(0.0).fill_nan(0.0).to_numpy()
    y_test = test["fwd_ret"].to_numpy().astype(np.float64)

    print(f"\nfitting LightGBM on train ({X_train.shape[0]:,} samples)...", flush=True)
    t0 = time.time()
    model = lgb.LGBMRegressor(
        n_estimators=300, learning_rate=0.03, max_depth=6,
        num_leaves=31, min_child_samples=100,
        reg_alpha=0.1, reg_lambda=0.1,
        subsample=0.8, colsample_bytree=0.8,
        verbose=-1,
    )
    model.fit(X_train, y_train)
    print(f"  fit done in {time.time()-t0:.1f}s", flush=True)

    pred_train = model.predict(X_train)
    pred_test = model.predict(X_test)

    # Per-date rank IC: the right metric for cross-sectional strategy
    train_with_pred = train.with_columns(pl.Series("pred", pred_train))
    test_with_pred = test.with_columns(pl.Series("pred", pred_test))
    ic_train_pearson, _ = _ic(pred_train, y_train)
    ic_test_pearson, _ = _ic(pred_test, y_test)
    rank_ic_train_mean, icir_train = _per_date_rank_ic(train_with_pred, "pred", "fwd_ret")
    rank_ic_test_mean, icir_test = _per_date_rank_ic(test_with_pred, "pred", "fwd_ret")

    # Random baseline as null benchmark
    rng = np.random.default_rng(42)
    rand_test = rng.standard_normal(len(pred_test))
    test_with_rand = test.with_columns(pl.Series("rand", rand_test))
    rand_pearson, _ = _ic(rand_test, y_test)
    rand_rank_ic, rand_icir = _per_date_rank_ic(test_with_rand, "rand", "fwd_ret")

    # Print comparison
    print("\n" + "=" * 70)
    print(f"{'':30s} {'TRAIN (IS)':>15} {'TEST (OOS)':>15} {'RANDOM':>15}")
    print(f"{'-' * 70}")
    print(f"{'Pearson IC':30s} {ic_train_pearson:+15.4f} {ic_test_pearson:+15.4f} {rand_pearson:+15.4f}")
    print(f"{'Rank IC (per-date mean)':30s} {rank_ic_train_mean:+15.4f} {rank_ic_test_mean:+15.4f} {rand_rank_ic:+15.4f}")
    print(f"{'Rank ICIR (annualized)':30s} {icir_train:+15.2f} {icir_test:+15.2f} {rand_icir:+15.2f}")
    print(f"{'samples':30s} {X_train.shape[0]:>15,} {X_test.shape[0]:>15,} {len(rand_test):>15,}")
    print("=" * 70)

    is_oos_gap_rank_ic = abs(rank_ic_train_mean - rank_ic_test_mean)
    decay_pct = (rank_ic_test_mean / rank_ic_train_mean * 100) if rank_ic_train_mean > 0 else 0
    print(f"\n📊 OOS vs IS analysis:")
    print(f"  IS  Rank IC = {rank_ic_train_mean:+.4f}  ICIR={icir_train:+.2f}")
    print(f"  OOS Rank IC = {rank_ic_test_mean:+.4f}  ICIR={icir_test:+.2f}")
    print(f"  IS→OOS decay: {decay_pct:.1f}% retained ({is_oos_gap_rank_ic:+.4f} absolute gap)")
    print(f"  random baseline (no signal): Rank IC {rand_rank_ic:+.4f} (should be ~0)")

    if decay_pct < 30:
        verdict = "❌ 严重过拟合 — OOS 信号衰减 >70%"
    elif decay_pct < 60:
        verdict = "⚠️  中度过拟合 — OOS 信号衰减 40-70%"
    elif decay_pct < 100:
        verdict = "✅ 轻度衰减 (正常) — OOS 信号保留 >60%"
    else:
        verdict = "🎯 OOS > IS — 反过拟合迹象，但样本可能偏差"
    print(f"\n  Verdict: {verdict}")

    # Save OOS predictions as ml_lgb_strict factor
    preds_df = test_with_pred.select(["symbol", "trade_date", pl.col("pred").alias("value")])
    target_dir = Path("data/parquet/factors/name=ml_lgb_strict")
    target_dir.mkdir(parents=True, exist_ok=True)
    preds_df.write_parquet(target_dir / "data.parquet")
    print(f"\nsaved factor `ml_lgb_strict` to {target_dir / 'data.parquet'}", flush=True)

    # Top features
    importance = list(zip(factor_cols, model.feature_importances_))
    importance.sort(key=lambda x: -x[1])
    print("\nTop 15 features by importance:")
    for name, imp in importance[:15]:
        print(f"  {name:20s} {imp}")

    # Save report
    out = {
        "method": "strict_holdout",
        "train_start": str(TRAIN_START), "train_end": str(TRAIN_END),
        "test_start": str(TEST_START), "test_end": str(TEST_END),
        "embargo_days": EMBARGO_DAYS,
        "train_samples": int(X_train.shape[0]),
        "test_samples": int(X_test.shape[0]),
        "is_pearson_ic": ic_train_pearson, "oos_pearson_ic": ic_test_pearson,
        "is_rank_ic": rank_ic_train_mean, "oos_rank_ic": rank_ic_test_mean,
        "is_rank_icir": icir_train, "oos_rank_icir": icir_test,
        "random_rank_ic": rand_rank_ic, "random_rank_icir": rand_icir,
        "is_to_oos_decay_pct": decay_pct,
        "verdict": verdict,
        "top_features": [(n, int(i)) for n, i in importance[:30]],
    }
    Path("data/strict_holdout_report.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nfull report → data/strict_holdout_report.json", flush=True)


if __name__ == "__main__":
    main()
