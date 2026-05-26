"""A-share trading calendar.

The authoritative source is Tushare's `trade_cal`. We cache it locally in a
Parquet file and expose query helpers. When the cache is missing, we fall back
to a pure-Python weekday rule (Mon–Fri excluding well-known holidays) for tests
and offline development.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd
import polars as pl

# Session-time bookkeeping (continuous trading; pre-open auction handled separately)
MORNING_OPEN = time(9, 30)
MORNING_CLOSE = time(11, 30)
AFTERNOON_OPEN = time(13, 0)
AFTERNOON_CLOSE = time(15, 0)
PRE_OPEN_AUCTION = (time(9, 15), time(9, 25))
CLOSING_AUCTION = (time(14, 57), time(15, 0))   # 主板/创科板规则一致


class AShareCalendar:
    """Wrap a sorted list of trade dates with convenient lookups."""

    def __init__(self, trade_dates: list[date]):
        if not trade_dates:
            raise ValueError("Empty calendar")
        self._dates = sorted(set(trade_dates))
        self._set = set(self._dates)
        self._index = {d: i for i, d in enumerate(self._dates)}

    # -- queries -----------------------------------------------------------------

    def is_trading_day(self, d: date) -> bool:
        return d in self._set

    def next_trading_day(self, d: date, n: int = 1) -> date:
        """Return the n-th trading day strictly after `d`."""
        idx = self._search_idx(d)
        target = idx + n if d in self._set else idx + n  # _search_idx returns next-or-equal
        if d not in self._set:
            target = idx + n - 1
        if target >= len(self._dates):
            raise IndexError(f"calendar exhausted for next_trading_day({d!r}, n={n})")
        return self._dates[target]

    def prev_trading_day(self, d: date, n: int = 1) -> date:
        idx = self._search_idx(d)
        if d in self._set:
            target = idx - n
        else:
            target = idx - n
        if target < 0:
            raise IndexError(f"calendar exhausted for prev_trading_day({d!r}, n={n})")
        return self._dates[target]

    def range(self, start: date, end: date) -> list[date]:
        return [d for d in self._dates if start <= d <= end]

    def count_between(self, start: date, end: date) -> int:
        return sum(1 for d in self._dates if start <= d <= end)

    # -- session helpers ---------------------------------------------------------

    @staticmethod
    def in_continuous_session(ts: datetime) -> bool:
        t = ts.time()
        return (MORNING_OPEN <= t < MORNING_CLOSE) or (AFTERNOON_OPEN <= t < AFTERNOON_CLOSE)

    @staticmethod
    def in_pre_open_auction(ts: datetime) -> bool:
        return PRE_OPEN_AUCTION[0] <= ts.time() < PRE_OPEN_AUCTION[1]

    @staticmethod
    def in_closing_auction(ts: datetime) -> bool:
        return CLOSING_AUCTION[0] <= ts.time() < CLOSING_AUCTION[1]

    # -- internals ---------------------------------------------------------------

    def _search_idx(self, d: date) -> int:
        """Return the index of d if present, else the smallest i with dates[i] > d."""
        lo, hi = 0, len(self._dates)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._dates[mid] < d:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def to_polars(self) -> pl.DataFrame:
        return pl.DataFrame({"trade_date": self._dates})


# ---------------------------------------------------------------------------- #
# Loading                                                                      #
# ---------------------------------------------------------------------------- #


def _builtin_holidays_2015_2026() -> set[date]:
    """Hard-coded major holidays for the offline-fallback calendar.

    Used only when no cached calendar exists. Real deployments must sync from
    Tushare `trade_cal` to capture 调休 (workday substitution) which the simple
    weekday rule misses.
    """
    # Spring Festival / National Day windows for 2015–2026; not exhaustive but
    # sufficient to make backtest dates plausible.
    raw = [
        # 2015 春节
        "2015-02-18", "2015-02-19", "2015-02-20", "2015-02-23", "2015-02-24",
        # 2024 春节
        "2024-02-09", "2024-02-12", "2024-02-13", "2024-02-14", "2024-02-15", "2024-02-16",
        # 2024 国庆
        "2024-10-01", "2024-10-02", "2024-10-03", "2024-10-04", "2024-10-07",
        # 2025 春节
        "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31", "2025-02-03", "2025-02-04",
        # 2025 国庆
        "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-06", "2025-10-07", "2025-10-08",
        # 2026 春节 (approx)
        "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    ]
    return {datetime.strptime(s, "%Y-%m-%d").date() for s in raw}


def _build_offline_calendar(start: date, end: date) -> AShareCalendar:
    holidays = _builtin_holidays_2015_2026()
    dates = []
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in holidays:
            dates.append(d)
        d += timedelta(days=1)
    return AShareCalendar(dates)


@lru_cache(maxsize=4)
def get_calendar(
    cache_path: str | None = None,
    *,
    fallback_start: date = date(2015, 1, 1),
    fallback_end: date = date(2026, 12, 31),
) -> AShareCalendar:
    """Load the calendar from parquet, or build an offline fallback."""
    if cache_path:
        p = Path(cache_path)
        if p.exists():
            df = pd.read_parquet(p)
            return AShareCalendar([d.date() if hasattr(d, "date") else d for d in df["trade_date"]])
    return _build_offline_calendar(fallback_start, fallback_end)
