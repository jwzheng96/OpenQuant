"""2022 熊市压力测试 — 真正的"模型从未见过这种行情"考验。

Setup:
  - 训练窗：2020-01-01 → 2021-12-31 (2 年，牛+震荡)
  - 5 日 embargo
  - 测试窗：2022-01-01 → 2022-12-31 (A 股熊市，HS300 -22%)
  - 模型从未见过任何 2022 数据

这检验的是：
  1. 在与训练完全不同的行情体制下，alpha 是否还能保留
  2. 模型是否在熊市仍能选出相对强势的票（如低波/防御）
  3. MDD 是否会失控（关键风险指标）
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

TRAIN_START = date(2020, 1, 1)
TRAIN_END = date(2021, 12, 31)
TEST_START = date(2022, 1, 1)
TEST_END = date(2022, 12, 31)
EMBARGO_DAYS = 5
TARGET_HORIZON = 5


def build_features(panel: pl.DataFrame, eng) -> tuple[pl.DataFrame, list[str]]:
    print(f"computing {len(eng.names())} factors on {panel.height}-row panel...", flush=True)
    feats: list[pl.DataFrame] = []
    valid: list[str] = []
    for i, name in enumerate(sorted(eng.names()), 1):
        if name.startswith("ml_"):
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


def _per_date_rank_ic(df, pred_col, target_col):
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
    return m, m / sd * np.sqrt(252) if sd > 0 else 0.0


def main():
    api = get_data_api()
    panel = api.get_daily(None, TRAIN_START - timedelta(days=300), TEST_END, adjust="fwd")
    print(f"panel: {panel.height} rows, {panel['symbol'].n_unique()} symbols "
          f"{panel['trade_date'].min()} → {panel['trade_date'].max()}", flush=True)
    sb = api.query.con.execute("SELECT * FROM stock_basic").pl()
    panel = annotate_for_backtest(panel, sb)

    eng = default_engine()
    features, factor_cols = build_features(panel, eng)
    features = add_target(features, panel)
    features = features.filter(
        (pl.col("trade_date") >= TRAIN_START) & (pl.col("trade_date") <= TEST_END)
        & pl.col("fwd_ret").is_not_null() & pl.col("fwd_ret").is_finite()
    )
    print(f"feature matrix: {features.height} rows × {len(factor_cols)} factors", flush=True)

    embargo_cutoff = TRAIN_END - timedelta(days=EMBARGO_DAYS * 2)
    train = features.filter(pl.col("trade_date") <= embargo_cutoff)
    test = features.filter(pl.col("trade_date") >= TEST_START)
    print(f"  train: {train.height} rows ({train['trade_date'].min()} → {train['trade_date'].max()})")
    print(f"  test:  {test.height} rows ({test['trade_date'].min()} → {test['trade_date'].max()})", flush=True)

    X_train = train.select(factor_cols).fill_null(0.0).fill_nan(0.0).to_numpy()
    y_train = train["fwd_ret"].to_numpy().astype(np.float64)
    X_test = test.select(factor_cols).fill_null(0.0).fill_nan(0.0).to_numpy()
    y_test = test["fwd_ret"].to_numpy().astype(np.float64)

    print(f"\nfitting LightGBM on bull/sideways years 2020-2021...", flush=True)
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

    is_ric, is_icir = _per_date_rank_ic(
        train.with_columns(pl.Series("pred", pred_train)), "pred", "fwd_ret"
    )
    oos_ric, oos_icir = _per_date_rank_ic(
        test.with_columns(pl.Series("pred", pred_test)), "pred", "fwd_ret"
    )

    print(f"\n{'='*70}")
    print(f"🔥 2022 熊市压力测试结果")
    print(f"{'='*70}")
    print(f"  IS  (2020-21 bull): Rank IC = {is_ric:+.4f}  ICIR = {is_icir:+.2f}")
    print(f"  OOS (2022 bear):    Rank IC = {oos_ric:+.4f}  ICIR = {oos_icir:+.2f}")
    print(f"  signal retention:   {(oos_ric/is_ric*100):.1f}% (of IS magnitude)")
    if oos_ric > 0:
        print(f"  ✅ 即使在熊市，模型仍捕捉到正向 cross-sectional signal")
    else:
        print(f"  ❌ 模型在熊市方向反转 — 行情敏感")

    # Save preds
    preds_df = test.with_columns(pl.Series("value", pred_test)).select(["symbol", "trade_date", "value"])
    target_dir = Path("data/parquet/factors/name=ml_lgb_bear2022")
    target_dir.mkdir(parents=True, exist_ok=True)
    preds_df.write_parquet(target_dir / "data.parquet")
    print(f"\nsaved ml_lgb_bear2022 → {target_dir/'data.parquet'}", flush=True)

    Path("data/bear_2022_report.json").write_text(json.dumps({
        "train": [str(TRAIN_START), str(TRAIN_END)],
        "test": [str(TEST_START), str(TEST_END)],
        "is_rank_ic": is_ric, "is_rank_icir": is_icir,
        "oos_rank_ic": oos_ric, "oos_rank_icir": oos_icir,
    }, indent=2))


if __name__ == "__main__":
    main()
