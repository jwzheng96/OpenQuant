"""Sync today's daily K via direct EastMoney API — bypasses AkShare's broken session.

The AkShare requests session inherits macOS-level proxy settings even when we
unset HTTPS_PROXY + set trust_env=False on a new session. So we go direct:
build the same URL AkShare would build, but with our own clean session.

Run: python scripts/sync_today_em.py [--date YYYY-MM-DD]
Defaults to today.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime

# Disable env-level proxy BEFORE importing anything HTTP
for _k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import polars as pl
import requests

from open_quant.data.api import get_data_api
from open_quant.utils import get_logger

log = get_logger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0"
EM_KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def _make_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update({"User-Agent": UA})
    return s


def _secid(ts_code: str) -> str:
    code, ex = ts_code.split(".")
    market = "1" if ex == "SH" else "0"  # SH→1, SZ/BJ→0
    return f"{market}.{code}"


def fetch_one(session: requests.Session, ts_code: str, start: str, end: str,
              adjust: int = 1) -> pl.DataFrame:
    """Pull single-stock daily K. start/end are 'YYYYMMDD'. adjust: 0=raw 1=qfq 2=hfq."""
    params = {
        "secid": _secid(ts_code),
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": 101,    # daily
        "fqt": adjust,
        "beg": start,
        "end": end,
    }
    try:
        r = session.get(EM_KLINE, params=params,
                        proxies={"http": None, "https": None}, timeout=8)
        if r.status_code != 200:
            return pl.DataFrame()
        data = r.json().get("data") or {}
        klines = data.get("klines") or []
        if not klines:
            return pl.DataFrame()
    except Exception:
        return pl.DataFrame()

    rows = []
    for line in klines:
        # date,open,close,high,low,vol,amount,amplitude,pct_chg,price_chg,turnover
        parts = line.split(",")
        if len(parts) < 7:
            continue
        d = parts[0].replace("-", "")
        rows.append({
            "ts_code": ts_code,
            "trade_date": d,
            "open": float(parts[1]),
            "close": float(parts[2]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            "vol": int(float(parts[5])),
            "amount": float(parts[6]),
        })
    if not rows:
        return pl.DataFrame()
    df = pl.DataFrame(rows)
    # pre_close = previous row's close (sorted ascending by date)
    df = df.sort("trade_date").with_columns(
        pl.col("close").shift(1).alias("pre_close")
    )
    return df.select(["ts_code", "trade_date", "open", "high", "low", "close",
                      "pre_close", "vol", "amount"])


def sync(target_date: str | None = None) -> None:
    if target_date is None:
        target_date = date.today().strftime("%Y%m%d")
    else:
        target_date = target_date.replace("-", "")

    api = get_data_api()

    # Get existing universe
    universe = api.query.con.execute(
        "SELECT DISTINCT symbol FROM daily ORDER BY symbol"
    ).fetchall()
    ts_codes = [row[0] for row in universe]
    log.info(f"target_date={target_date}  universe={len(ts_codes)} stocks")

    # Only sync stocks missing target_date
    have = api.query.con.execute(
        "SELECT DISTINCT symbol FROM daily WHERE trade_date = ?",
        [datetime.strptime(target_date, "%Y%m%d").date()],
    ).fetchall()
    have_set = {row[0] for row in have}
    to_pull = [c for c in ts_codes if c not in have_set]
    log.info(f"  already have: {len(have_set)}  to pull: {len(to_pull)}")

    if not to_pull:
        log.info("nothing to do")
        return

    session = _make_session()
    frames: list[pl.DataFrame] = []
    ok = 0
    fail = []
    t0 = time.time()

    # 拉 [target_date - 3 day, target_date] 以便 pre_close 也 OK
    start_window = (datetime.strptime(target_date, "%Y%m%d").date()).strftime("%Y%m%d")
    # 简化：拉单日（DB 已有历史，今天补一行即可）
    for i, code in enumerate(to_pull, 1):
        df = fetch_one(session, code, start_window, target_date, adjust=1)
        if df.is_empty():
            fail.append(code)
        else:
            # Filter to just target_date row(s)
            df = df.filter(pl.col("trade_date") == target_date)
            if not df.is_empty():
                frames.append(df)
                ok += 1
        if i % 50 == 0 or i == len(to_pull):
            elapsed = time.time() - t0
            rate = i / elapsed
            eta = (len(to_pull) - i) / rate if rate > 0 else 0
            log.info(f"  [{i:3d}/{len(to_pull)}] ok={ok} fail={len(fail)} "
                     f"elapsed={elapsed:.1f}s eta={eta:.0f}s")
        time.sleep(0.1)  # gentle rate limit

    if frames:
        all_df = pl.concat(frames)
        n_before = api.query.con.execute("SELECT COUNT(*) FROM daily").fetchone()[0]
        api.store.write_daily(all_df, dataset="daily")
        n_after = api.query.con.execute("SELECT COUNT(*) FROM daily").fetchone()[0]
        log.info(f"wrote: {n_after - n_before} new rows ({n_before} → {n_after})")

    log.info(f"\n done: {ok}/{len(to_pull)} success, {len(fail)} fail")
    if fail and len(fail) <= 30:
        log.info(f"  failed: {fail}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD or YYYYMMDD; default today")
    args = ap.parse_args()
    sync(args.date)


if __name__ == "__main__":
    main()
