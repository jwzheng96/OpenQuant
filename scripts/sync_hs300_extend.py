"""Extend universe: pull HS300 stocks #51-150 (next 100 after the initial 50)."""

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
            time.sleep(5)
    raise RuntimeError(f"HS300 fetch failed: {last_err}")


def sync_daily(api, ak, ts_codes: list[str]) -> list[str]:
    print(f"[daily] pulling {len(ts_codes)} symbols qfq {START}..{END}", flush=True)
    success: list[str] = []
    frames: list[pl.DataFrame] = []
    t0 = time.time()
    for i, ts_code in enumerate(ts_codes, 1):
        code = ts_code.split(".")[0]
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
            # Force consistent vol dtype across EastMoney/Sina before concat
            df = df.with_columns(pl.col("vol").cast(pl.Int64))
            frames.append(df)
            success.append(ts_code)
            print(f"  [{i:3d}/{len(ts_codes)}] ✅ {ts_code} ({source_used})", flush=True)
        else:
            print(f"  [{i:3d}/{len(ts_codes)}] ⛔ {ts_code}", flush=True)
        time.sleep(0.4)
        if i % 20 == 0:
            print(f"  [pause 5s after {i} requests]", flush=True)
            time.sleep(5)

    if frames:
        n = api.store.write_daily(pl.concat(frames), dataset="daily")
        print(f"[daily] wrote {n} rows ({len(success)}/{len(ts_codes)} symbols) in {time.time()-t0:.1f}s", flush=True)

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
    total = len(ts_codes) * len(indicators)
    idx = 0
    t0 = time.time()

    print(f"[basic] pulling {total} indicator/symbol pairs", flush=True)
    for i, ts_code in enumerate(ts_codes, 1):
        code = ts_code.split(".")[0]
        for cn, our in indicators.items():
            idx += 1
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
        if i % 10 == 0:
            print(f"  [basic {i}/{len(ts_codes)} symbols] pause 4s", flush=True)
            time.sleep(4)

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

    # Get existing symbols
    try:
        existing = set(api.query.con.execute("SELECT DISTINCT symbol FROM daily").pl()["symbol"].to_list())
    except Exception:
        existing = set()
    print(f"existing universe: {len(existing)} symbols", flush=True)

    hs300 = get_hs300_list()
    new_symbols = [s for s in hs300 if s not in existing][:100]
    print(f"adding {len(new_symbols)} new HS300 stocks (next after existing 50)", flush=True)

    success = sync_daily(api, ak, new_symbols)
    if not success:
        print("daily sync produced no data", flush=True)
        sys.exit(1)

    sync_daily_basic(api, success)

    # Update stock_basic + adj_factor
    adj = ak.adj_factor(success, START, END)
    if not adj.is_empty():
        n = api.store.write_daily(adj, dataset="adj_factor")
        print(f"adj_factor: +{n} rows", flush=True)

    all_symbols = sorted(existing | set(success))
    rows = [{"ts_code": s, "symbol": s.split(".")[0], "name": s.split(".")[0],
             "area": "", "industry": "", "list_date": "20100101",
             "delist_date": None, "market": "主板"} for s in all_symbols]
    sb = pl.DataFrame(rows)
    sb_dir = api.store.root / "stock_basic" / "year=0" / "month=00"
    sb_dir.mkdir(parents=True, exist_ok=True)
    sb.write_parquet(sb_dir / "data.parquet")
    print(f"stock_basic: {len(all_symbols)} symbols total", flush=True)

    api.query._register_views()
    print(f"\nDONE — total universe {len(all_symbols)} symbols", flush=True)


if __name__ == "__main__":
    main()
