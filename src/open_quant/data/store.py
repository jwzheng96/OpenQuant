"""Storage layer: Parquet (cold) + DuckDB (query) + Postgres (metadata).

The hot path is:
  1. Source adapter returns polars DataFrame.
  2. Writer normalizes schema (rename columns, parse dates) and appends to
     partitioned Parquet (`year=YYYY/month=MM/<dataset>.parquet`).
  3. DuckDB views are registered over the Parquet root so SQL queries see
     fresh data without re-import.

Postgres holds operational metadata (sync state, orders, P&L) — see init.sql.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable

import duckdb
import polars as pl

from open_quant.utils import get_logger

log = get_logger(__name__)

# Tushare-style column names → our internal canonical names
DAILY_RENAME = {"ts_code": "symbol", "vol": "volume", "amount": "amount"}


def _normalize_daily(df: pl.DataFrame) -> pl.DataFrame:
    if df.is_empty():
        return df
    rename = {k: v for k, v in DAILY_RENAME.items() if k in df.columns}
    df = df.rename(rename)
    if "trade_date" in df.columns and df["trade_date"].dtype == pl.Utf8:
        df = df.with_columns(
            pl.col("trade_date").str.strptime(pl.Date, "%Y%m%d", strict=False)
        )
    # Force consistent dtypes — EastMoney vs Sina differ on vol (int vs float)
    casts = []
    if "volume" in df.columns:
        casts.append(pl.col("volume").cast(pl.Float64))
    if "amount" in df.columns:
        casts.append(pl.col("amount").cast(pl.Float64))
    if casts:
        df = df.with_columns(casts)
    return df.with_columns([
        pl.col("trade_date").dt.year().alias("_year"),
        pl.col("trade_date").dt.month().alias("_month"),
    ])


class ParquetStore:
    """Append-only Parquet store partitioned by (year, month)."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, dataset: str, year: int, month: int) -> Path:
        p = self.root / dataset / f"year={year}" / f"month={month:02d}"
        p.mkdir(parents=True, exist_ok=True)
        return p / "data.parquet"

    def write_daily(self, df: pl.DataFrame, dataset: str = "daily") -> int:
        if df.is_empty():
            return 0
        df = _normalize_daily(df)
        written = 0
        for (y, m), part in df.group_by(["_year", "_month"]):
            part = part.drop(["_year", "_month"])
            target = self.path(dataset, int(y), int(m))
            if target.exists():
                old = pl.read_parquet(target)
                if "symbol" in part.columns and "trade_date" in part.columns:
                    merged = pl.concat([old, part]).unique(subset=["symbol", "trade_date"],
                                                          keep="last")
                else:
                    merged = pl.concat([old, part])
                merged.write_parquet(target)
            else:
                part.write_parquet(target)
            written += len(part)
        log.info(f"parquet write {dataset}: {written} rows")
        return written

    def glob(self, dataset: str) -> str:
        return str(self.root / dataset / "year=*" / "month=*" / "data.parquet")


class DuckDBQuery:
    """Read-side: DuckDB views over Parquet roots."""

    def __init__(self, db_path: str | Path, parquet_root: str | Path):
        self.db_path = str(db_path)
        self.parquet_root = Path(parquet_root)
        self._con: duckdb.DuckDBPyConnection | None = None

    @property
    def con(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            self._con = duckdb.connect(self.db_path)
            self._register_views()
        return self._con

    def _register_views(self) -> None:
        if self._con is None:
            self._con = duckdb.connect(self.db_path)
        if not self.parquet_root.exists():
            return
        for dataset_dir in self.parquet_root.iterdir():
            if not dataset_dir.is_dir():
                continue
            ds = dataset_dir.name
            glob = str(dataset_dir / "year=*" / "month=*" / "data.parquet")
            try:
                self._con.execute(
                    f"CREATE OR REPLACE VIEW {ds} AS SELECT * FROM read_parquet('{glob}', hive_partitioning=1)"
                )
            except duckdb.IOException:
                # No files yet for this dataset — view skipped
                continue

    def sql(self, query: str) -> pl.DataFrame:
        return self.con.execute(query).pl()

    def daily(
        self,
        symbols: Iterable[str] | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> pl.DataFrame:
        where = []
        params: list = []
        if symbols is not None:
            syms = list(symbols)
            placeholders = ", ".join(["?"] * len(syms))
            where.append(f"symbol IN ({placeholders})")
            params.extend(syms)
        if start:
            where.append("trade_date >= ?")
            params.append(start)
        if end:
            where.append("trade_date <= ?")
            params.append(end)
        sql = "SELECT * FROM daily"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY symbol, trade_date"
        return self.con.execute(sql, params).pl()
