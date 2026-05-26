"""Pull a 50-stock universe from HS300 + their daily prices + historical daily_basic.

Network plan:
  1. Get HS300 constituent list (fast).
  2. Select top 50 (alphabetical for determinism).
  3. Pull daily (qfq) per stock (~3s each × 50 = 2.5 min).
  4. Pull stock_a_indicator_lg per stock for historical PE/PB/PS (~5s × 50 = 4 min).
  5. Write all to parquet store.
"""

from __future__ import annotations

import signal
import sys
import time
from datetime import date

import polars as pl

from uni_quant.data.api import get_data_api
from uni_quant.data.sources import AkShareSource

START = date(2022, 1, 1)
END = date(2024, 12, 31)
N_STOCKS = 50
TIMEOUT_PER_CALL = 20


class _Timeout(Exception):
    pass


def _handler(s, f):
    raise _Timeout()


def call_with_timeout(fn, *args, **kwargs):
    signal.signal(signal.SIGALRM, _handler)
    signal.alarm(TIMEOUT_PER_CALL)
    try:
        return fn(*args, **kwargs)
    finally:
        signal.alarm(0)


def _to_ts_code(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("60", "68", "11", "13")):
        return f"{code}.SH"
    if code.startswith(("00", "30", "12", "15", "16")):
        return f"{code}.SZ"
    return f"{code}.SH"


def get_hs300_list() -> list[str]:
    import akshare as ak
    last_err = None
    for attempt in range(3):
        try:
            df = ak.index_stock_cons(symbol="000300")
            if df is not None and len(df) > 0:
                codes = sorted({str(c).zfill(6) for c in df["品种代码"]})
                return [_to_ts_code(c) for c in codes]
        except Exception as e:
            last_err = e
            print(f"  HS300 fetch attempt {attempt + 1} failed: {str(e)[:80]}", flush=True)
            time.sleep(5)
    raise RuntimeError(f"HS300 fetch failed after 3 attempts: {last_err}")


def sync_daily(api, ak, ts_codes: list[str]) -> list[str]:
    print(f"[daily] pulling {len(ts_codes)} symbols qfq {START}..{END}", flush=True)
    success: list[str] = []
    frames: list[pl.DataFrame] = []
    t0 = time.time()
    for i, ts_code in enumerate(ts_codes, 1):
        code = ts_code.split(".")[0]
        # Two-source fallback: EastMoney first, Sina if 4xx/proxy
        df = pl.DataFrame()
        source_used = ""
        try:
            df = call_with_timeout(ak._hist_one, code, START, END, "qfq")
            if not df.is_empty():
                source_used = "EM"
        except (_Timeout, Exception):
            pass
        if df.is_empty():
            try:
                df = call_with_timeout(ak._hist_one_sina, code, START, END, "qfq")
                if not df.is_empty():
                    source_used = "Sina"
            except (_Timeout, Exception):
                pass
        if not df.is_empty():
            frames.append(df)
            success.append(ts_code)
            print(f"  [{i:3d}/{len(ts_codes)}] ✅ {ts_code} ({source_used}, {df.height} rows)", flush=True)
        else:
            print(f"  [{i:3d}/{len(ts_codes)}] ⛔ {ts_code}", flush=True)
        time.sleep(0.4)
        if i % 20 == 0:
            print(f"  [pause 5s after {i} requests to avoid rate limit]", flush=True)
            time.sleep(5)

    if frames:
        n = api.store.write_daily(pl.concat(frames), dataset="daily")
        print(f"[daily] wrote {n} rows ({len(success)}/{len(ts_codes)} symbols) in {time.time()-t0:.1f}s", flush=True)

    return success


def sync_daily_basic(api, ts_codes: list[str]) -> None:
    """Historical PE/PB/PS via akshare stock_a_indicator_lg — per-stock, slow."""
    import akshare as ak
    print(f"[basic] pulling daily_basic for {len(ts_codes)} symbols", flush=True)
    frames: list[pl.DataFrame] = []
    t0 = time.time()
    for i, ts_code in enumerate(ts_codes, 1):
        code = ts_code.split(".")[0]
        try:
            df = call_with_timeout(ak.stock_a_indicator_lg, symbol=code)
            if df is None or len(df) == 0:
                print(f"  [{i:3d}/{len(ts_codes)}] ⚠️  {ts_code} empty", flush=True)
                continue
            df = pl.from_pandas(df)
            # akshare columns: trade_date(date), pe, pe_ttm, pb, ps, ps_ttm, dv_ratio, dv_ttm, total_mv
            df = df.rename({c: c for c in df.columns if c in df.columns})
            df = df.with_columns(
                pl.col("trade_date").cast(pl.Utf8).str.replace_all("-", "").alias("trade_date"),
                pl.lit(ts_code).alias("ts_code"),
            )
            sd, ed = START.strftime("%Y%m%d"), END.strftime("%Y%m%d")
            df = df.filter((pl.col("trade_date") >= sd) & (pl.col("trade_date") <= ed))
            keep = ["ts_code", "trade_date", "pe", "pe_ttm", "pb", "ps", "ps_ttm", "total_mv"]
            df = df.select([c for c in keep if c in df.columns])
            if not df.is_empty():
                frames.append(df)
                print(f"  [{i:3d}/{len(ts_codes)}] ✅ {ts_code} ({df.height} rows)", flush=True)
            else:
                print(f"  [{i:3d}/{len(ts_codes)}] ⚠️  {ts_code} no rows in range", flush=True)
        except _Timeout:
            print(f"  [{i:3d}/{len(ts_codes)}] ⏱  {ts_code} timeout", flush=True)
        except Exception as e:
            print(f"  [{i:3d}/{len(ts_codes)}] ❌ {ts_code}: {str(e)[:60]}", flush=True)
        time.sleep(0.3)

    if frames:
        n = api.store.write_daily(pl.concat(frames), dataset="daily_basic")
        print(f"[basic] wrote {n} rows in {time.time()-t0:.1f}s", flush=True)


def write_stock_basic(api, ts_codes: list[str]) -> None:
    rows = [{"ts_code": s, "symbol": s.split(".")[0], "name": s.split(".")[0],
             "area": "", "industry": "", "list_date": "20100101",
             "delist_date": None, "market": "主板"} for s in ts_codes]
    sb = pl.DataFrame(rows)
    sb_dir = api.store.root / "stock_basic" / "year=0" / "month=00"
    sb_dir.mkdir(parents=True, exist_ok=True)
    sb.write_parquet(sb_dir / "data.parquet")
    print(f"[stock_basic] wrote {len(ts_codes)} symbols", flush=True)


def main():
    api = get_data_api()
    ak = AkShareSource()

    print("[1/4] fetching HS300 constituents...", flush=True)
    try:
        hs300 = get_hs300_list()
    except _Timeout:
        print("HS300 fetch timed out", flush=True)
        sys.exit(1)
    if not hs300:
        print("no HS300 list", flush=True)
        sys.exit(1)
    sample = hs300[:N_STOCKS]
    print(f"[1/4] HS300 has {len(hs300)} stocks; sampling first {N_STOCKS}: {sample[:5]}...", flush=True)

    print(f"\n[2/4] sync daily for {N_STOCKS} stocks", flush=True)
    success = sync_daily(api, ak, sample)
    if not success:
        print("daily sync produced no data", flush=True)
        sys.exit(1)

    print(f"\n[3/4] sync daily_basic for {len(success)} stocks", flush=True)
    sync_daily_basic(api, success)

    print(f"\n[4/4] adj_factor + stock_basic", flush=True)
    adj = ak.adj_factor(success, START, END)
    if not adj.is_empty():
        n = api.store.write_daily(adj, dataset="adj_factor")
        print(f"  adj_factor: {n} rows", flush=True)
    write_stock_basic(api, success)

    api.query._register_views()
    print(f"\nDONE — {len(success)} symbols", flush=True)


if __name__ == "__main__":
    main()
