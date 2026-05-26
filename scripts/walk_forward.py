"""Walk-forward + rolling IC stability — separate signal from in-sample overfit.

Two analyses:

1. **Train/Test split** — IC on 2022-06..2023-12 vs IC on 2024 (OOS).
   Factors that ranked high in-sample but drop dramatically OOS are likely
   overfit; factors with stable IC across both halves are more trustworthy.

2. **Rolling 90-day IC** — sliding-window IC over 30 months. Factors with
   stable monthly IC (low std relative to mean) are more reliable than ones
   driven by one regime.

Outputs `data/factor_whitelist.json` for the next strategy config.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from uni_quant.data.api import get_data_api
from uni_quant.factors import default_engine
from uni_quant.factors.eval import ic_series

TRAIN_START = date(2022, 6, 1)
TRAIN_END = date(2023, 12, 31)
TEST_START = date(2024, 1, 1)
TEST_END = date(2024, 12, 31)


def split_ic(eng, panel, factor_name: str) -> dict:
    """Compute train vs test IC for one factor."""
    try:
        r = eng.compute(factor_name, panel)
    except Exception as e:
        return {"factor": factor_name, "error": str(e)[:60]}
    if r.data.is_empty():
        return {"factor": factor_name, "error": "empty"}

    ics_train = ic_series(r.data.filter(pl.col("trade_date") <= TRAIN_END),
                          panel.filter(pl.col("trade_date") <= TRAIN_END),
                          horizon=5, method="spearman")
    ics_test = ic_series(r.data.filter(pl.col("trade_date") >= TEST_START),
                         panel.filter(pl.col("trade_date") >= TEST_START),
                         horizon=5, method="spearman")

    def _stat(s: pl.Series) -> tuple[float, float, float]:
        arr = s.drop_nulls().to_numpy()
        arr = arr[np.isfinite(arr)]
        if len(arr) < 5:
            return float("nan"), float("nan"), float("nan")
        m = float(np.mean(arr))
        sd = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
        ir = m / sd * np.sqrt(252) if sd > 0 else 0.0
        return m, sd, ir

    train_m, train_s, train_ir = _stat(ics_train["ic"])
    test_m, test_s, test_ir = _stat(ics_test["ic"])

    # Sign consistency: did the direction flip OOS?
    sign_match = (np.sign(train_m) == np.sign(test_m)) if np.isfinite(train_m) and np.isfinite(test_m) else False

    return {
        "factor": factor_name,
        "train_ic": train_m, "train_icir": train_ir,
        "test_ic": test_m, "test_icir": test_ir,
        "sign_match": bool(sign_match),
        # decay: how much IC magnitude dropped
        "ic_decay_ratio": (abs(test_m) / abs(train_m)) if abs(train_m) > 1e-6 else 0.0,
    }


def rolling_ic(eng, panel, factor_name: str, window_days: int = 90) -> dict:
    """Rolling-window IC mean and std → stability score."""
    try:
        r = eng.compute(factor_name, panel)
    except Exception:
        return {"factor": factor_name, "stability": float("nan")}
    if r.data.is_empty():
        return {"factor": factor_name, "stability": float("nan")}

    ics = ic_series(r.data, panel, horizon=5, method="spearman")
    if ics.is_empty():
        return {"factor": factor_name, "stability": float("nan")}

    arr = ics["ic"].drop_nulls().to_numpy()
    arr = arr[np.isfinite(arr)]
    if len(arr) < window_days // 5:
        return {"factor": factor_name, "stability": float("nan")}

    # Split into 90-day chunks (~ 60 trading days)
    chunk = 60
    means = []
    for i in range(0, len(arr) - chunk + 1, chunk // 2):
        c = arr[i:i + chunk]
        if len(c) >= 30:
            means.append(float(np.mean(c)))
    if len(means) < 3:
        return {"factor": factor_name, "stability": float("nan")}

    m = float(np.mean(means))
    sd = float(np.std(means, ddof=1))
    stability = abs(m) / sd if sd > 0 else 0.0  # higher = more stable

    # Positive months ratio
    pos_ratio = float(np.mean([1.0 if x > 0 else 0.0 for x in means]))

    return {
        "factor": factor_name,
        "n_windows": len(means),
        "rolling_mean_ic": m,
        "rolling_std_ic": sd,
        "stability_ratio": stability,
        "positive_window_pct": pos_ratio,
    }


def main():
    api = get_data_api()
    panel = api.get_daily(None, TRAIN_START, TEST_END, adjust="fwd")
    print(f"panel: {panel.height} rows, {panel['symbol'].n_unique()} symbols", flush=True)
    print(f"  train: {TRAIN_START}..{TRAIN_END}", flush=True)
    print(f"  test:  {TEST_START}..{TEST_END}", flush=True)

    # Annotate
    from uni_quant.data.universe import annotate_for_backtest
    sb = api.query.con.execute("SELECT * FROM stock_basic").pl()
    panel = annotate_for_backtest(panel, sb)

    eng = default_engine()
    names = eng.names()
    print(f"\nevaluating {len(names)} factors over train/test split...", flush=True)

    split_results = []
    rolling_results = []
    for i, name in enumerate(names, 1):
        s = split_ic(eng, panel, name)
        split_results.append(s)
        r = rolling_ic(eng, panel, name)
        rolling_results.append(r)
        if i % 20 == 0:
            print(f"  [{i}/{len(names)}]", flush=True)

    # Compose
    split_df = pl.DataFrame([{k: v for k, v in s.items() if k != "error"}
                             for s in split_results if "error" not in s])
    rolling_df = pl.DataFrame([r for r in rolling_results if "stability_ratio" in r])

    if split_df.is_empty():
        print("no split results", flush=True)
        sys.exit(1)

    # Combine
    full = split_df.join(rolling_df, on="factor", how="left")

    # White list criteria (the "trustworthy" filter):
    #   1. sign_match = True (OOS direction agrees with IS)
    #   2. abs(test_icir) >= 1.0
    #   3. ic_decay_ratio >= 0.4 (OOS keeps ≥40% of in-sample magnitude)
    #   4. positive_window_pct in [0.3, 0.7] for consistently-signed factors
    full = full.with_columns([
        (pl.col("test_icir").abs() >= 1.0).alias("_ok_icir"),
        (pl.col("ic_decay_ratio") >= 0.4).alias("_ok_decay"),
        (pl.col("stability_ratio") >= 0.5).alias("_ok_stable"),
    ])
    full = full.with_columns(
        (pl.col("sign_match") & pl.col("_ok_icir") & pl.col("_ok_decay") & pl.col("_ok_stable"))
        .alias("trustworthy")
    )

    # Sort by absolute test_icir for display
    full = full.with_columns(pl.col("test_icir").abs().alias("_abs_test_icir")).sort(
        ["trustworthy", "_abs_test_icir"], descending=[True, True]
    )

    print(f"\n=== walk-forward results ===")
    print(full.select([
        "factor", "train_ic", "test_ic", "ic_decay_ratio",
        "sign_match", "stability_ratio", "positive_window_pct", "trustworthy"
    ]).head(30))

    n_trust = int(full["trustworthy"].sum())
    print(f"\ntrustworthy factors: {n_trust} / {full.height}")

    # Save whitelist
    whitelist = full.filter(pl.col("trustworthy")).select([
        "factor", "train_ic", "test_ic", "test_icir", "stability_ratio"
    ])
    print("\n=== WHITE LIST ===")
    print(whitelist)

    out_path = Path("data/factor_whitelist.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "train_start": str(TRAIN_START), "train_end": str(TRAIN_END),
        "test_start": str(TEST_START), "test_end": str(TEST_END),
        "criteria": {
            "sign_match": True,
            "min_test_icir_abs": 1.0,
            "min_ic_decay_ratio": 0.4,
            "min_stability_ratio": 0.5,
        },
        "n_total": full.height,
        "n_trustworthy": n_trust,
        "factors": whitelist.to_dicts(),
        "all_results": full.drop("_abs_test_icir", "_ok_icir", "_ok_decay", "_ok_stable").to_dicts(),
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\nwhitelist saved to {out_path}", flush=True)


if __name__ == "__main__":
    main()
