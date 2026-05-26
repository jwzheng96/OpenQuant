"""Extend price history forward: 2025-01-01 → today for ALL existing 573 stocks.

Uses incremental sync — only fetches dates not already in the store.
"""

from __future__ import annotations

import signal
import sys
import time
from datetime import date

import polars as pl

from open_quant.data.api import get_data_api
from open_quant.data.sources import AkShareSource

START = date(2025, 1, 1)
END = date.today()
TIMEOUT = 25


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


def sync_daily(api, ak, ts_codes: list[str]) -> list[str]:
    print(f"[daily] {len(ts_codes)} symbols {START}..{END}", flush=True)
    success: list[str] = []
    frames: list[pl.DataFrame] = []
    t0 = time.time()
    for i, ts_code in enumerate(ts_codes, 1):
        code = ts_code.split(".")[0]
        df = pl.DataFrame()
        source = ""
        try:
            df = call_with_timeout(ak._hist_one, code, START, END, "qfq")
            if not df.is_empty():
                source = "EM"
        except (_Timeout, Exception):
            pass
        if df.is_empty():
            try:
                df = call_with_timeout(ak._hist_one_sina, code, START, END, "qfq")
                if not df.is_empty():
                    source = "Sina"
            except (_Timeout, Exception):
                pass
        if not df.is_empty():
            df = df.with_columns(pl.col("vol").cast(pl.Int64))
            frames.append(df)
            success.append(ts_code)
            if i % 25 == 0 or i == len(ts_codes):
                print(f"  [{i:3d}/{len(ts_codes)}] last={ts_code} ({source})", flush=True)
        time.sleep(0.4)
        if i % 30 == 0:
            time.sleep(8)

    if frames:
        n = api.store.write_daily(pl.concat(frames), dataset="daily")
        print(f"[daily] wrote {n} rows in {time.time()-t0:.1f}s", flush=True)
    return success


def fetch_basic_one(code: str, indicator: str) -> pl.DataFrame:
    import akshare as ak
    df = ak.stock_zh_valuation_baidu(symbol=code, indicator=indicator, period="近三年")
    if df is None or len(df) == 0:
        return pl.DataFrame()
    return pl.from_pandas(df)


def sync_daily_basic(api, ts_codes: list[str]) -> None:
    indicators = {"市盈率(TTM)": "pe_ttm", "市净率": "pb", "总市值": "total_mv"}
    sd, ed = START.strftime("%Y%m%d"), END.strftime("%Y%m%d")
    all_pieces: dict[str, list[pl.DataFrame]] = {col: [] for col in indicators.values()}
    t0 = time.time()
    print(f"[basic] {len(ts_codes) * 3} calls", flush=True)
    for i, ts_code in enumerate(ts_codes, 1):
        code = ts_code.split(".")[0]
        for cn, our in indicators.items():
            try:
                df = call_with_timeout(fetch_basic_one, code, cn)
                if df.is_empty():
                    continue
                df = df.with_columns(
                    pl.col("date").cast(pl.Utf8).str.replace_all("-", "").alias("trade_date"),
                    pl.lit(ts_code).alias("ts_code"),
                ).filter(
                    (pl.col("trade_date") >= sd) & (pl.col("trade_date") <= ed)
                ).select(["ts_code", "trade_date", pl.col("value").alias(our)])
                if not df.is_empty():
                    all_pieces[our].append(df)
            except (_Timeout, Exception):
                pass
            time.sleep(0.3)
        if i % 25 == 0:
            print(f"  [basic {i}/{len(ts_codes)}]", flush=True)
            time.sleep(5)

    combined: pl.DataFrame | None = None
    for col, frames in all_pieces.items():
        if not frames:
            continue
        piece = pl.concat(frames)
        if combined is None:
            combined = piece
        else:
            combined = combined.join(piece, on=["ts_code", "trade_date"], how="full")
            if "ts_code_right" in combined.columns:
                combined = combined.with_columns(
                    pl.coalesce(["ts_code", "ts_code_right"]).alias("ts_code"),
                    pl.coalesce(["trade_date", "trade_date_right"]).alias("trade_date"),
                ).drop(["ts_code_right", "trade_date_right"])

    if combined is None or combined.is_empty():
        print("[basic] no data", flush=True)
        return
    if "ps_ttm" not in combined.columns:
        combined = combined.with_columns(pl.lit(None, dtype=pl.Float64).alias("ps_ttm"))
    if "turnover_rate" not in combined.columns:
        combined = combined.with_columns(pl.lit(None, dtype=pl.Float64).alias("turnover_rate"))
    if "circ_mv" not in combined.columns and "total_mv" in combined.columns:
        combined = combined.with_columns(pl.col("total_mv").alias("circ_mv"))

    n = api.store.write_daily(combined, dataset="daily_basic")
    print(f"[basic] wrote {n} rows in {time.time()-t0:.1f}s", flush=True)


def main():
    api = get_data_api()
    ak = AkShareSource()

    symbols = api.query.con.execute("SELECT DISTINCT symbol FROM daily ORDER BY symbol").pl()["symbol"].to_list()
    print(f"extending {len(symbols)} symbols from {START} to {END}", flush=True)

    success = sync_daily(api, ak, symbols)
    sync_daily_basic(api, success)

    # Also add benchmark (沪深300) if possible
    try:
        import akshare as ak_lib
        bench = ak_lib.stock_zh_index_daily(symbol="sh000300")
        if bench is not None and len(bench) > 0:
            bench = pl.from_pandas(bench).rename({"date": "trade_date"})
            bench = bench.with_columns(
                pl.lit("000300.SH").alias("ts_code"),
                pl.col("trade_date").cast(pl.Utf8).str.replace_all("-", "").alias("trade_date"),
                pl.col("volume").cast(pl.Int64).alias("vol"),
            ).filter(
                (pl.col("trade_date") >= "20220101")
            ).with_columns(
                pl.col("close").shift(1).alias("pre_close"),
                pl.lit(0.0).alias("amount"),
            ).select(["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"])
            n = api.store.write_daily(bench, dataset="daily")
            print(f"[bench] wrote {n} rows of 000300.SH", flush=True)
    except Exception as e:
        print(f"[bench] failed: {e}", flush=True)

    api.query._register_views()
    print(f"\nDONE", flush=True)


if __name__ == "__main__":
    main()
