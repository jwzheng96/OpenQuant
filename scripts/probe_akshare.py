"""Probe AkShare: pull stock_basic + daily for 5 blue-chips."""
import sys
import polars as pl
from datetime import date

from open_quant.data.sources import AkShareSource

ak = AkShareSource()
sys.stdout.write("=== stock_basic ===\n"); sys.stdout.flush()
sb = ak.stock_basic()
sys.stdout.write(f"  total A-share symbols: {len(sb)}\n")
sys.stdout.write(str(sb.head(5)) + "\n")
sys.stdout.flush()

test_symbols = ["600519.SH", "000001.SZ", "300750.SZ", "601318.SH", "600036.SH"]
sys.stdout.write(f"\n=== daily for {test_symbols} (2024) ===\n"); sys.stdout.flush()
df = ak.daily(test_symbols, date(2024, 1, 2), date(2024, 12, 31))
sys.stdout.write(f"  rows: {df.height}, symbols: {df['ts_code'].n_unique()}\n")
sys.stdout.write(str(df.head(3)) + "\n")
sys.stdout.write(str(df.tail(3)) + "\n")

mt = df.filter(pl.col("ts_code") == "600519.SH")
sys.stdout.write(f"\n=== 600519.SH 2024 ===\n")
sys.stdout.write(f"  rows: {mt.height}, open range: {mt['open'].min():.2f}..{mt['open'].max():.2f}\n")
sys.stdout.write(f"  close last: {mt.tail(1)['close'][0]:.2f}\n")
sys.stdout.flush()
