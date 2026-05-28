"""DuckDB queries — latest prices, history, stock names.

Wraps `open_quant.data.api.get_data_api()` for the web layer. Keeps a
single shared DataAPI instance per process (DuckDB single-writer).
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

import polars as pl


@lru_cache(maxsize=1)
def _api():
    from open_quant.data.api import get_data_api
    return get_data_api()


# ---------------------------------------------------------------------------- #
# Names                                                                         #
# ---------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def _name_map() -> dict[str, str]:
    """symbol → 中文名. Cached once per process (refresh by restarting)."""
    try:
        df = _api().query.con.execute(
            "SELECT ts_code, name FROM stock_basic"
        ).fetchall()
        return {row[0]: row[1] or row[0] for row in df}
    except Exception:
        return {}


def name_of(symbol: str) -> str:
    return _name_map().get(symbol, symbol)


def names_of(symbols: list[str]) -> dict[str, str]:
    m = _name_map()
    return {s: m.get(s, s) for s in symbols}


# ---------------------------------------------------------------------------- #
# Prices                                                                        #
# ---------------------------------------------------------------------------- #


def latest_close(symbols: list[str]) -> dict[str, float | None]:
    """Most-recent close per symbol. None if symbol absent."""
    if not symbols:
        return {}
    syms = ",".join(repr(s) for s in symbols)
    rows = _api().query.con.execute(f"""
        SELECT symbol, close
        FROM (
            SELECT symbol, close, trade_date,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn
            FROM daily
            WHERE symbol IN ({syms})
        ) t
        WHERE rn = 1
    """).fetchall()
    out: dict[str, float | None] = {s: None for s in symbols}
    for sym, c in rows:
        out[sym] = float(c) if c is not None else None
    return out


def latest_trade_date() -> date | None:
    r = _api().query.con.execute("SELECT MAX(trade_date) FROM daily").fetchone()
    return r[0] if r and r[0] else None


def history(symbol: str, days: int = 60) -> list[dict]:
    """Recent N daily bars: [{trade_date, open, high, low, close, vol}, ...]."""
    rows = _api().query.con.execute("""
        SELECT trade_date, open, high, low, close, volume
        FROM daily
        WHERE symbol = ?
        ORDER BY trade_date DESC
        LIMIT ?
    """, [symbol, days]).fetchall()
    out = []
    for r in reversed(rows):
        out.append({
            "trade_date": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
            "open": float(r[1]) if r[1] is not None else None,
            "high": float(r[2]) if r[2] is not None else None,
            "low": float(r[3]) if r[3] is not None else None,
            "close": float(r[4]) if r[4] is not None else None,
            "vol": int(r[5]) if r[5] is not None else None,
        })
    return out


def benchmark_nav(symbol: str = "000300.SH", start: date | None = None,
                  end: date | None = None) -> list[dict]:
    """Return cumulative-return-style series rebased to 1.0 at start."""
    start = start or date(2024, 1, 1)
    end = end or date.today()
    rows = _api().query.con.execute("""
        SELECT trade_date, close
        FROM daily
        WHERE symbol = ? AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date
    """, [symbol, start, end]).fetchall()
    if not rows:
        return []
    base = float(rows[0][1])
    out = []
    for r in rows:
        d, c = r[0], float(r[1]) if r[1] is not None else None
        out.append({
            "trade_date": d.isoformat() if hasattr(d, "isoformat") else str(d),
            "nav": (c / base) if c else None,
        })
    return out


def factor_value(symbol: str, factor: str, days: int = 30) -> list[dict]:
    """Recent factor values for a symbol — used by the stock detail side panel."""
    try:
        df = pl.read_parquet(
            f"data/parquet/factors/name={factor}/data.parquet"
        )
    except Exception:
        return []
    sub = (df.filter(pl.col("symbol") == symbol)
             .sort("trade_date")
             .tail(days)
             .select(["trade_date", "value"]))
    return [
        {
            "trade_date": (d.isoformat() if hasattr(d, "isoformat") else str(d)),
            "value": float(v) if v is not None else None,
        }
        for d, v in zip(sub["trade_date"], sub["value"])
    ]
