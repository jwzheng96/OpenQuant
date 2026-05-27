"""Refresh universe to the canonical HS300 + CSI500 + CSI1000 (~1800 stocks).

Does three things in one pass:
  1. Pull real-time HS300/CSI500/CSI1000 constituents from AkShare.
  2. Diff vs current DB universe — find missing tickers.
  3. Backfill 2020-01-01 → today for the missing tickers via EM kline.
  4. Refresh stock_basic.name from ak.stock_info_a_code_name() so the
     metadata table actually has Chinese names (currently all blank).

Run: python scripts/refresh_universe.py
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date

# Disable proxy BEFORE any HTTP import
for _k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import polars as pl
import requests

# Monkey-patch Session before akshare imports it
_old_session_init = requests.Session.__init__
def _new_session_init(self, *a, **k):
    _old_session_init(self, *a, **k)
    self.trust_env = False
requests.Session.__init__ = _new_session_init

import akshare as ak  # noqa: E402

from open_quant.data.api import get_data_api  # noqa: E402
from open_quant.utils import get_logger  # noqa: E402

# Reuse fetch_one from the sync script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_today_em import _make_session, fetch_one  # noqa: E402

log = get_logger(__name__)

HISTORY_START = "20200101"
HISTORY_END = date.today().strftime("%Y%m%d")


def to_ts_code(code: str) -> str:
    """6-digit code → ts_code with .SH/.SZ/.BJ suffix."""
    code = str(code).zfill(6)
    if code.startswith(("60", "688", "689", "900")):
        return f"{code}.SH"
    if code.startswith(("000", "001", "002", "003", "300", "301", "200")):
        return f"{code}.SZ"
    if code.startswith(("4", "8", "92")):
        return f"{code}.BJ"
    # Fallback: assume SH for 5-6 prefix, SZ otherwise
    return f"{code}.SH" if code[0] in "56" else f"{code}.SZ"


def fetch_target_universe() -> tuple[set[str], dict[str, str]]:
    """Pull HS300 + CSI500 + CSI1000 constituents. Return (ts_codes set, name map)."""
    target: set[str] = set()
    names: dict[str, str] = {}

    for idx_code, label in [("000300", "HS300"), ("000905", "CSI500"), ("000852", "CSI1000")]:
        t = time.time()
        df = ak.index_stock_cons_csindex(symbol=idx_code)
        added = 0
        for code, name in zip(df["成分券代码"], df["成分券名称"]):
            ts = to_ts_code(code)
            target.add(ts)
            if name and not names.get(ts):
                names[ts] = str(name).strip()
            added += 1
        log.info(f"{label}: {added} constituents ({time.time()-t:.1f}s)")

    # Also pull whole-market name table (fills in non-index names)
    t = time.time()
    a_names = ak.stock_info_a_code_name()
    for code, name in zip(a_names["code"], a_names["name"]):
        ts = to_ts_code(code)
        if name and not names.get(ts):
            names[ts] = str(name).strip()
    log.info(f"stock_info_a_code_name: {len(a_names)} rows ({time.time()-t:.1f}s)")

    return target, names


def backfill_history(missing: list[str], session) -> int:
    """For each new ticker, pull 2020-01-01 → today via EM kline."""
    if not missing:
        return 0

    api = get_data_api()
    frames: list[pl.DataFrame] = []
    ok = fail = 0
    t0 = time.time()

    for i, ts_code in enumerate(missing, 1):
        df = fetch_one(session, ts_code, HISTORY_START, HISTORY_END, adjust=1)
        if df.is_empty():
            fail += 1
        else:
            frames.append(df)
            ok += 1
        if i % 50 == 0 or i == len(missing):
            elapsed = time.time() - t0
            rate = i / max(elapsed, 0.01)
            eta = (len(missing) - i) / max(rate, 0.01)
            log.info(f"  [{i:4d}/{len(missing)}] ok={ok} fail={fail} "
                     f"elapsed={elapsed:.0f}s eta={eta:.0f}s")
        time.sleep(0.1)

    if frames:
        all_df = pl.concat(frames)
        n_before = api.query.con.execute("SELECT COUNT(*) FROM daily").fetchone()[0]
        api.store.write_daily(all_df, dataset="daily")
        n_after = api.query.con.execute("SELECT COUNT(*) FROM daily").fetchone()[0]
        log.info(f"backfill wrote: {n_after - n_before} new rows ({n_before} → {n_after})")

    return ok


def refresh_stock_basic(names: dict[str, str]) -> int:
    """Update stock_basic.name from the constituent + whole-market name dump.

    Only updates rows where name is currently blank or equals symbol.
    Also inserts new rows for any ts_code we have daily data for but no
    stock_basic entry.
    """
    api = get_data_api()
    con = api.query.con

    # current state
    existing = {r[0]: r[1] for r in con.execute(
        "SELECT ts_code, name FROM stock_basic").fetchall()}
    have_daily = {r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM daily").fetchall()}

    to_update: list[tuple[str, str]] = []
    to_insert: list[tuple[str, str, str]] = []

    for ts_code in have_daily:
        true_name = names.get(ts_code, "")
        if not true_name:
            continue
        if ts_code in existing:
            cur = existing[ts_code] or ""
            # blank, equal to bare 6-digit code, or differs from true → update
            if not cur or cur == ts_code.split(".")[0] or cur != true_name:
                to_update.append((true_name, ts_code))
        else:
            symbol = ts_code.split(".")[0]
            to_insert.append((ts_code, symbol, true_name))

    log.info(f"stock_basic: {len(to_update)} to update, {len(to_insert)} to insert")

    if to_update:
        con.executemany("UPDATE stock_basic SET name = ? WHERE ts_code = ?", to_update)
    if to_insert:
        con.executemany(
            "INSERT INTO stock_basic (ts_code, symbol, name) VALUES (?, ?, ?)",
            to_insert)
    return len(to_update) + len(to_insert)


def main():
    api = get_data_api()
    log.info("=== Step 1: fetch target universe (HS300 + CSI500 + CSI1000) ===")
    target, names = fetch_target_universe()
    log.info(f"target universe size: {len(target)} (deduped)")

    current = {r[0] for r in api.query.con.execute(
        "SELECT DISTINCT symbol FROM daily").fetchall()}
    log.info(f"current DB universe: {len(current)}")

    missing = sorted(target - current)
    extra = sorted(current - target)
    log.info(f"missing (target but not in DB): {len(missing)}")
    log.info(f"extra (in DB but not in target): {len(extra)} "
             f"— kept as historical legacy")

    if missing:
        log.info(f"\n=== Step 2: backfill {len(missing)} new tickers' history ===")
        session = _make_session()
        backfill_history(missing, session)

    log.info("\n=== Step 3: refresh stock_basic.name ===")
    n_changed = refresh_stock_basic(names)
    log.info(f"stock_basic mutations: {n_changed}")

    # Final verification
    log.info("\n=== Final state ===")
    final = api.query.con.execute("SELECT COUNT(DISTINCT symbol) FROM daily").fetchone()[0]
    log.info(f"DB universe now: {final} stocks")
    canonical_covered = len(target & {r[0] for r in api.query.con.execute(
        "SELECT DISTINCT symbol FROM daily").fetchall()})
    log.info(f"HS300+CSI500+CSI1000 coverage: {canonical_covered}/{len(target)} "
             f"({canonical_covered/len(target)*100:.1f}%)")

    # Spot-check the big names that were missing before
    for code, expected in [
        ("601318.SH", "中国平安"),
        ("601398.SH", "工商银行"),
        ("600900.SH", "长江电力"),
        ("601012.SH", "隆基绿能"),
    ]:
        in_daily = api.query.con.execute(
            "SELECT COUNT(*) FROM daily WHERE symbol = ?", [code]).fetchone()[0]
        in_basic = api.query.con.execute(
            "SELECT name FROM stock_basic WHERE ts_code = ?", [code]).fetchone()
        nm = in_basic[0] if in_basic else "(no row)"
        flag = "✅" if in_daily > 0 else "❌"
        log.info(f"  {flag} {code} daily_rows={in_daily} name='{nm}' (expected {expected})")


if __name__ == "__main__":
    main()
