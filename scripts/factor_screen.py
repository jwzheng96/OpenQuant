"""Batch-evaluate every registered factor on the stored panel, sort by RankICIR."""

from __future__ import annotations

import sys
from datetime import date

import numpy as np
import polars as pl

from open_quant.data.api import get_data_api
from open_quant.factors import default_engine, evaluate_factor

START = date(2022, 6, 1)
END = date(2024, 12, 31)


def main():
    api = get_data_api()
    panel = api.get_daily(None, START, END, adjust="fwd")
    if panel.is_empty():
        print("no panel data", flush=True)
        sys.exit(1)
    print(f"panel: {panel.height} rows, {panel['symbol'].n_unique()} symbols, {START}..{END}", flush=True)

    eng = default_engine()
    results = []
    for name in sorted(eng.names()):
        try:
            r = eng.compute(name, panel)
            if r.data.is_empty():
                print(f"  {name:14s} EMPTY", flush=True)
                continue
            ev = evaluate_factor(r.data, panel, name=name, primary_horizon=5)
            s = ev.summary()
            results.append({"factor": name, **s})
            print(f"  {name:14s} IC={s['ic']:+.4f}  RankIC={s['rank_ic']:+.4f}  RankICIR={s['rank_icir']:+.3f}", flush=True)
        except Exception as e:
            print(f"  {name:14s} ERROR: {str(e)[:60]}", flush=True)

    if not results:
        return
    df = pl.DataFrame(results).sort("rank_icir", descending=True)
    print("\n=== TOP 15 by |RankICIR| (5-day horizon) ===", flush=True)
    df_abs = df.with_columns(pl.col("rank_icir").abs().alias("abs_ir")).sort("abs_ir", descending=True)
    print(df_abs.select(["factor", "ic", "rank_ic", "rank_icir", "turnover"]).head(15))


if __name__ == "__main__":
    main()
