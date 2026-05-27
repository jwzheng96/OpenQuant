"""Fix stock_basic.name by rewriting its underlying Parquet file.

stock_basic is a DuckDB VIEW over data/parquet/stock_basic/year=0/month=00/data.parquet,
so SQL UPDATE doesn't work. We:
  1. Read the parquet into polars.
  2. Pull canonical names via ak.stock_info_a_code_name() + 3 index-constituent lists.
  3. Update name column where blank/equal-to-code.
  4. Append rows for tickers in `daily` but missing from stock_basic.
  5. Write back to the same Parquet path.
"""
from __future__ import annotations

import os
from pathlib import Path

for _k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"

import polars as pl
import requests

_old = requests.Session.__init__
def _new(self, *a, **k):
    _old(self, *a, **k)
    self.trust_env = False
requests.Session.__init__ = _new

import akshare as ak  # noqa: E402

from open_quant.data.api import get_data_api  # noqa: E402
from open_quant.utils import get_logger  # noqa: E402

log = get_logger(__name__)
PQ = Path("data/parquet/stock_basic/year=0/month=00/data.parquet")


def to_ts_code(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("60", "688", "689", "900")):
        return f"{code}.SH"
    if code.startswith(("000", "001", "002", "003", "300", "301", "200")):
        return f"{code}.SZ"
    if code.startswith(("4", "8", "92")):
        return f"{code}.BJ"
    return f"{code}.SH" if code[0] in "56" else f"{code}.SZ"


def build_name_map() -> dict[str, str]:
    names: dict[str, str] = {}
    # Whole-market name table (most comprehensive)
    log.info("pulling stock_info_a_code_name (~5500 rows)…")
    a = ak.stock_info_a_code_name()
    for code, name in zip(a["code"], a["name"]):
        if name:
            names[to_ts_code(code)] = str(name).strip()
    # Index constituents (override with any cleaner names)
    for idx in ("000300", "000905", "000852"):
        log.info(f"pulling {idx} constituents")
        df = ak.index_stock_cons_csindex(symbol=idx)
        for code, name in zip(df["成分券代码"], df["成分券名称"]):
            if name:
                names[to_ts_code(code)] = str(name).strip()
    log.info(f"name map: {len(names)} tickers")
    return names


def main():
    api = get_data_api()
    con = api.query.con

    # Current daily universe
    daily_codes = {r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM daily").fetchall()}
    log.info(f"daily universe: {len(daily_codes)}")

    # Load current Parquet
    df = pl.read_parquet(PQ)
    log.info(f"current stock_basic.parquet: {len(df)} rows / cols={df.columns}")

    name_map = build_name_map()

    # Build a target frame: union(daily_codes ∪ existing_stock_basic)
    existing_codes = set(df["ts_code"].to_list())
    all_codes = sorted(daily_codes | existing_codes)
    log.info(f"target rows: {len(all_codes)}")

    # Rebuild as a fresh DataFrame with corrected names. Preserve other cols
    # for tickers that already had them.
    existing_index = {r["ts_code"]: r for r in df.iter_rows(named=True)}
    rows = []
    for ts in all_codes:
        prior = existing_index.get(ts, {})
        symbol = ts.split(".")[0]
        true_name = name_map.get(ts, "")
        # Use true_name if known, else preserve prior.name if non-empty/non-code,
        # else fall back to symbol.
        prior_name = prior.get("name") or ""
        if true_name:
            name = true_name
        elif prior_name and prior_name != symbol:
            name = prior_name
        else:
            name = symbol
        rows.append({
            "ts_code": ts,
            "symbol": symbol,
            "name": name,
            "area": prior.get("area") or "",
            "industry": prior.get("industry") or "",
            "list_date": prior.get("list_date") or "",
            "delist_date": prior.get("delist_date") or "",
            "market": prior.get("market") or "",
            "month": 0,
            "year": 0,
        })
    new_df = pl.DataFrame(rows)
    log.info(f"new stock_basic frame: {len(new_df)} rows")

    # Write back (same path, same hive partition)
    PQ.parent.mkdir(parents=True, exist_ok=True)
    new_df.write_parquet(PQ)
    log.info(f"wrote {PQ}")

    # Verify by re-querying via DuckDB
    con.execute("DROP VIEW IF EXISTS stock_basic")
    con.execute("""
        CREATE VIEW stock_basic AS
        SELECT * FROM read_parquet('data/parquet/stock_basic/year=*/month=*/data.parquet',
                                    hive_partitioning=1)
    """)
    n_named = con.execute("""
        SELECT COUNT(*) FROM stock_basic WHERE name <> symbol AND name <> ''
    """).fetchone()[0]
    n_total = con.execute("SELECT COUNT(*) FROM stock_basic").fetchone()[0]
    log.info(f"after fix: {n_named}/{n_total} rows have real Chinese name")

    # Spot-check
    for code in ("601318.SH", "601398.SH", "600900.SH", "601012.SH",
                 "600519.SH", "300750.SZ", "000001.SZ"):
        r = con.execute("SELECT name FROM stock_basic WHERE ts_code = ?", [code]).fetchone()
        log.info(f"  {code}: {r[0] if r else '(missing)'}")


if __name__ == "__main__":
    main()
