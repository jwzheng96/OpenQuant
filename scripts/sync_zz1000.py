"""Extend universe with 中证 1000 (000852) — small/mid cap rank 801-1800.

Combined with existing HS300+ZZ500 → ~1500-1800 stocks covering
all of 上证主板/深市主板/创业板/科创板/北交所.

Period: 2020-01-01 → today (full history).
"""

from __future__ import annotations

import signal
import sys
import time
from datetime import date

import polars as pl

from uni_quant.data.api import get_data_api
from uni_quant.data.sources import AkShareSource

START = date(2020, 1, 1)
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


def _to_ts_code(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("60", "68", "11", "13")):
        return f"{code}.SH"
    if code.startswith(("00", "30", "12", "15", "16")):
        return f"{code}.SZ"
    if code.startswith(("8", "43", "92")):
        return f"{code}.BJ"
    return f"{code}.SH"


def get_zz1000_list() -> list[str]:
    import akshare as ak
    for attempt in range(3):
        try:
            df = ak.index_stock_cons(symbol="000852")
            if df is not None and len(df) > 0:
                codes = sorted({str(c).zfill(6) for c in df["品种代码"]})
                return [_to_ts_code(c) for c in codes]
        except Exception as e:
            print(f"  ZZ1000 attempt {attempt+1}: {e}", flush=True)
            time.sleep(5)
    return []


def sync_daily(api, ak, ts_codes: list[str]) -> list[str]:
    print(f"[daily] {len(ts_codes)} symbols {START}..{END}", flush=True)
    success: list[str] = []
    frames: list[pl.DataFrame] = []
    t0 = time.time()
    for i, ts_code in enumerate(ts_codes, 1):
        code = ts_code.split(".")[0]
        df = pl.DataFrame()
        src = ""
        try:
            df = call_with_timeout(ak._hist_one, code, START, END, "qfq")
            if not df.is_empty():
                src = "EM"
        except (_Timeout, Exception):
            pass
        if df.is_empty():
            try:
                df = call_with_timeout(ak._hist_one_sina, code, START, END, "qfq")
                if not df.is_empty():
                    src = "Sina"
            except (_Timeout, Exception):
                pass
        if not df.is_empty():
            df = df.with_columns(pl.col("vol").cast(pl.Int64))
            frames.append(df)
            success.append(ts_code)
            if i % 25 == 0 or i == len(ts_codes):
                print(f"  [{i:4d}/{len(ts_codes)}] last={ts_code} ({src})", flush=True)
        else:
            if i % 25 == 0:
                print(f"  [{i:4d}/{len(ts_codes)}] ⛔ {ts_code}", flush=True)
        time.sleep(0.35)
        if i % 30 == 0:
            time.sleep(8)
        # incremental write every 100 stocks to avoid losing progress on crash
        if i % 100 == 0 and frames:
            n = api.store.write_daily(pl.concat(frames), dataset="daily")
            print(f"  [partial write] +{n} rows so far", flush=True)
            frames = []

    if frames:
        n = api.store.write_daily(pl.concat(frames), dataset="daily")
        print(f"[daily] final write {n} rows, total time {time.time()-t0:.1f}s", flush=True)
    return success


def fetch_basic_one(code: str, indicator: str) -> pl.DataFrame:
    import akshare as ak
    df = ak.stock_zh_valuation_baidu(symbol=code, indicator=indicator, period="近五年")
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
        # incremental merge+write every 200 stocks
        if i % 200 == 0:
            combined = _merge_basic_pieces(all_pieces)
            if combined is not None and not combined.is_empty():
                n = api.store.write_daily(combined, dataset="daily_basic")
                print(f"  [partial write] basic +{n} rows", flush=True)
            all_pieces = {col: [] for col in indicators.values()}

    combined = _merge_basic_pieces(all_pieces)
    if combined is not None and not combined.is_empty():
        n = api.store.write_daily(combined, dataset="daily_basic")
        print(f"[basic] final write {n} rows in {time.time()-t0:.1f}s", flush=True)


def _merge_basic_pieces(all_pieces):
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
        return combined
    if "ps_ttm" not in combined.columns:
        combined = combined.with_columns(pl.lit(None, dtype=pl.Float64).alias("ps_ttm"))
    if "turnover_rate" not in combined.columns:
        combined = combined.with_columns(pl.lit(None, dtype=pl.Float64).alias("turnover_rate"))
    if "circ_mv" not in combined.columns and "total_mv" in combined.columns:
        combined = combined.with_columns(pl.col("total_mv").alias("circ_mv"))
    return combined


def main():
    api = get_data_api()
    ak = AkShareSource()

    try:
        existing = set(api.query.con.execute("SELECT DISTINCT symbol FROM daily").pl()["symbol"].to_list())
    except Exception:
        existing = set()
    print(f"existing universe: {len(existing)} symbols", flush=True)

    zz1000 = get_zz1000_list()
    if not zz1000:
        print("ZZ1000 fetch failed — aborting", flush=True)
        sys.exit(1)
    new_symbols = sorted(set(zz1000) - existing)
    print(f"ZZ1000 has {len(zz1000)} constituents; {len(new_symbols)} new to add", flush=True)
    if not new_symbols:
        print("nothing to add", flush=True); return

    success = sync_daily(api, ak, new_symbols)
    sync_daily_basic(api, success)

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
    print(f"stock_basic: {len(all_symbols)} symbols", flush=True)

    api.query._register_views()
    print(f"\nDONE — universe now {len(all_symbols)} symbols", flush=True)


if __name__ == "__main__":
    main()
