"""Pull historical daily_basic (PE/PB/total_mv) via stock_zh_valuation_baidu.

For each symbol, query 3 indicators (PE_TTM, PB, total_mv) over '近三年' period
and stitch them into a single daily_basic DataFrame.
"""

from __future__ import annotations

import signal
import sys
import time
from datetime import date

import polars as pl

from uni_quant.data.api import get_data_api

START = date(2022, 1, 1)
END = date(2024, 12, 31)
TIMEOUT = 20


class _Timeout(Exception):
    pass


def _handler(s, f):
    raise _Timeout()


def call_with_timeout(fn, *args, **kwargs):
    signal.signal(signal.SIGALRM, _handler)
    signal.alarm(TIMEOUT)
    try:
        return fn(*args, **kwargs)
    finally:
        signal.alarm(0)


def get_synced_symbols(api) -> list[str]:
    """Read symbols from the daily store (only stocks we already have)."""
    df = api.query.con.execute("SELECT DISTINCT symbol FROM daily ORDER BY symbol").pl()
    return df["symbol"].to_list()


def fetch_one(code: str, indicator: str, period: str = "近三年") -> pl.DataFrame:
    import akshare as ak
    df = ak.stock_zh_valuation_baidu(symbol=code, indicator=indicator, period=period)
    if df is None or len(df) == 0:
        return pl.DataFrame()
    return pl.from_pandas(df)


def main():
    api = get_data_api()
    symbols = get_synced_symbols(api)
    print(f"[basic] {len(symbols)} symbols in daily store", flush=True)

    indicators = {
        "市盈率(TTM)": "pe_ttm",
        "市净率": "pb",
        "总市值": "total_mv",
    }
    sd = START.strftime("%Y%m%d")
    ed = END.strftime("%Y%m%d")

    # Layout: per indicator, accumulate {ts_code, trade_date, value} long form.
    all_pieces: dict[str, list[pl.DataFrame]] = {col: [] for col in indicators.values()}
    t0 = time.time()
    total_calls = len(symbols) * len(indicators)
    call_idx = 0

    for i, ts_code in enumerate(symbols, 1):
        code = ts_code.split(".")[0]
        for cn_name, our_name in indicators.items():
            call_idx += 1
            try:
                df = call_with_timeout(fetch_one, code, cn_name)
                if df.is_empty():
                    print(f"  [{call_idx:3d}/{total_calls}] ⚠️  {ts_code} {our_name} empty", flush=True)
                    continue
                # df has columns ['date', 'value']
                df = df.with_columns(
                    pl.col("date").cast(pl.Utf8).str.replace_all("-", "").alias("trade_date"),
                    pl.lit(ts_code).alias("ts_code"),
                ).filter(
                    (pl.col("trade_date") >= sd) & (pl.col("trade_date") <= ed)
                ).select(["ts_code", "trade_date", pl.col("value").alias(our_name)])
                if not df.is_empty():
                    all_pieces[our_name].append(df)
                    print(f"  [{call_idx:3d}/{total_calls}] ✅ {ts_code} {our_name} ({df.height})", flush=True)
            except _Timeout:
                print(f"  [{call_idx:3d}/{total_calls}] ⏱  {ts_code} {our_name}", flush=True)
            except Exception as e:
                print(f"  [{call_idx:3d}/{total_calls}] ❌ {ts_code} {our_name}: {str(e)[:60]}", flush=True)
            time.sleep(0.3)
        if i % 10 == 0:
            print(f"  [pause 4s after {i} symbols]", flush=True)
            time.sleep(4)

    # Combine three indicators into one wide daily_basic table
    print(f"\n[basic] merging indicators...", flush=True)
    combined: pl.DataFrame | None = None
    for col, frames in all_pieces.items():
        if not frames:
            print(f"  no data for {col}", flush=True)
            continue
        piece = pl.concat(frames)
        if combined is None:
            combined = piece
        else:
            combined = combined.join(piece, on=["ts_code", "trade_date"], how="full")
            # outer join may produce ts_code_right / trade_date_right; coalesce
            if "ts_code_right" in combined.columns:
                combined = combined.with_columns(
                    pl.coalesce(["ts_code", "ts_code_right"]).alias("ts_code"),
                    pl.coalesce(["trade_date", "trade_date_right"]).alias("trade_date"),
                ).drop(["ts_code_right", "trade_date_right"])

    if combined is None or combined.is_empty():
        print("[basic] no data collected", flush=True)
        sys.exit(1)

    # Compute ps_ttm and turnover_rate as nulls for backwards compat (some factors need)
    if "ps_ttm" not in combined.columns:
        combined = combined.with_columns(pl.lit(None, dtype=pl.Float64).alias("ps_ttm"))
    if "turnover_rate" not in combined.columns:
        combined = combined.with_columns(pl.lit(None, dtype=pl.Float64).alias("turnover_rate"))
    if "circ_mv" not in combined.columns:
        # Without separate float share data, use total_mv as proxy
        combined = combined.with_columns(pl.col("total_mv").alias("circ_mv"))

    print(f"[basic] wide table: {combined.height} rows, columns={combined.columns}", flush=True)
    n = api.store.write_daily(combined, dataset="daily_basic")
    print(f"[basic] wrote {n} rows in {time.time() - t0:.1f}s", flush=True)
    api.query._register_views()


if __name__ == "__main__":
    main()
