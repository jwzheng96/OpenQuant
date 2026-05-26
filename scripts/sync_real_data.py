"""Pull real A-share daily prices from AkShare and persist via DataAPI.

Hardcoded blue-chip universe. Each call has its own timeout to prevent hangs.
"""

from __future__ import annotations

import signal
import sys
import time
from datetime import date

import polars as pl

from open_quant.data.api import get_data_api
from open_quant.data.sources import AkShareSource

# 10 大蓝筹 (减半，避免后端节流)
BLUE_CHIPS = [
    "600519.SH",  # 贵州茅台
    "601318.SH",  # 中国平安
    "600036.SH",  # 招商银行
    "000001.SZ",  # 平安银行
    "000333.SZ",  # 美的集团
    "000858.SZ",  # 五粮液
    "002594.SZ",  # 比亚迪
    "300750.SZ",  # 宁德时代
    "601398.SH",  # 工商银行
    "600276.SH",  # 恒瑞医药
]

START = date(2022, 1, 1)
END = date(2024, 12, 31)
PER_CALL_TIMEOUT = 30   # seconds per akshare call


class TimeoutError_(Exception):
    pass


def _timeout_handler(signum, frame):
    raise TimeoutError_(f"call exceeded {PER_CALL_TIMEOUT}s")


def call_with_timeout(fn, *args, **kwargs):
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(PER_CALL_TIMEOUT)
    try:
        return fn(*args, **kwargs)
    finally:
        signal.alarm(0)


def main() -> None:
    api = get_data_api()
    ak = AkShareSource()
    print(f"[sync] {len(BLUE_CHIPS)} blue-chips {START}..{END}", flush=True)
    t0 = time.time()

    success_symbols: list[str] = []
    all_frames: list[pl.DataFrame] = []
    for ts_code in BLUE_CHIPS:
        code = ts_code.split(".")[0]
        got = False
        for attempt in range(3):
            try:
                df = call_with_timeout(ak._hist_one, code, START, END, "qfq")
                if not df.is_empty():
                    all_frames.append(df)
                    success_symbols.append(ts_code)
                    print(f"  ✅ {ts_code} ({df.height} rows)", flush=True)
                    got = True
                    break
                else:
                    print(f"  ⚠️  {ts_code} empty (attempt {attempt + 1})", flush=True)
            except TimeoutError_ as e:
                print(f"  ⏱  {ts_code} timeout (attempt {attempt + 1})", flush=True)
            except Exception as e:
                msg = str(e)[:80]
                print(f"  ❌ {ts_code} (attempt {attempt + 1}): {msg}", flush=True)
            time.sleep(2)
        if not got:
            print(f"  ⛔ {ts_code} skipped after 3 attempts", flush=True)
        time.sleep(0.5)

    if not all_frames:
        print("[sync] FAILURE: no data pulled", flush=True)
        sys.exit(1)

    daily = pl.concat(all_frames)
    n = api.store.write_daily(daily, dataset="daily")
    print(f"[sync] wrote {n} rows of daily", flush=True)

    adj = ak.adj_factor(success_symbols, START, END)
    if not adj.is_empty():
        n2 = api.store.write_daily(adj, dataset="adj_factor")
        print(f"[sync] wrote {n2} rows of adj_factor", flush=True)

    # daily_basic from spot is unreliable when EastMoney throttles; skip if hangs
    try:
        basic = call_with_timeout(ak.daily_basic, success_symbols, START, END)
        if not basic.is_empty():
            n3 = api.store.write_daily(basic, dataset="daily_basic")
            print(f"[sync] wrote {n3} rows of daily_basic", flush=True)
    except (TimeoutError_, Exception) as e:
        print(f"[sync] daily_basic skipped: {str(e)[:80]}", flush=True)

    stock_basic_rows = [
        {"ts_code": s, "symbol": s.split(".")[0], "name": s.split(".")[0],
         "area": "", "industry": "", "list_date": "20100101",
         "delist_date": None, "market": "主板"}
        for s in success_symbols
    ]
    sb = pl.DataFrame(stock_basic_rows)
    sb_dir = api.store.root / "stock_basic" / "year=0" / "month=00"
    sb_dir.mkdir(parents=True, exist_ok=True)
    sb.write_parquet(sb_dir / "data.parquet")
    print(f"[sync] wrote stock_basic for {len(success_symbols)} symbols", flush=True)

    api.query._register_views()
    print(f"\n[sync] DONE in {time.time() - t0:.1f}s — {len(success_symbols)}/{len(BLUE_CHIPS)} symbols", flush=True)


if __name__ == "__main__":
    main()
