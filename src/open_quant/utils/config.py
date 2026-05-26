"""Centralized configuration loading. YAML on disk → pydantic models."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = ROOT / "configs"


class TushareConfig(BaseModel):
    token: str = ""
    rate_limit_per_minute: int = 500
    retry: dict[str, Any] = Field(default_factory=lambda: {"attempts": 5, "backoff_seconds": 2})


class AkShareConfig(BaseModel):
    rate_limit_per_minute: int = 200


class DeepSeekConfig(BaseModel):
    api_key: str = ""
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com/v1"
    temperature: float = 0.3
    max_tokens: int = 1500
    timeout: float = 60.0


class StorageConfig(BaseModel):
    parquet_root: str = "./data/parquet"
    duckdb_path: str = "./data/open_quant.duckdb"
    clickhouse: dict[str, Any] = Field(default_factory=dict)
    postgres: dict[str, Any] = Field(default_factory=dict)


class DataSourcesConfig(BaseModel):
    tushare: TushareConfig = Field(default_factory=TushareConfig)
    akshare: AkShareConfig = Field(default_factory=AkShareConfig)
    deepseek: DeepSeekConfig = Field(default_factory=DeepSeekConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    datasets: dict[str, Any] = Field(default_factory=dict)


class UniverseRule(BaseModel):
    exchanges: list[str] = Field(default_factory=lambda: ["SSE", "SZSE"])
    exclude_st: bool = True
    exclude_suspended: bool = True
    exclude_new_listings_days: int = 60
    exclude_will_delist: bool = True
    min_market_cap: float = 2.0e9
    min_avg_turnover_20d: float = 5.0e7
    blacklist: list[str] = Field(default_factory=list)


class RiskConfig(BaseModel):
    strategy_level: dict[str, Any] = Field(default_factory=dict)
    portfolio_level: dict[str, Any] = Field(default_factory=dict)
    account_level: dict[str, Any] = Field(default_factory=dict)


class TradingConfig(BaseModel):
    mode: str = "paper"
    stock: dict[str, Any] = Field(default_factory=dict)
    futures: dict[str, Any] = Field(default_factory=dict)
    slippage: dict[str, Any] = Field(default_factory=dict)
    notification: dict[str, Any] = Field(default_factory=dict)


class Settings(BaseModel):
    """Top-level settings, lazy-loaded sub-configs."""

    data_sources: DataSourcesConfig = Field(default_factory=DataSourcesConfig)
    universe: dict[str, UniverseRule] = Field(default_factory=dict)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    trading: TradingConfig = Field(default_factory=TradingConfig)


def _read_yaml(path: Path, *, fallback: Path | None = None) -> dict[str, Any]:
    if not path.exists() and fallback and fallback.exists():
        path = fallback
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def load_settings(config_dir: Path | None = None) -> Settings:
    cd = config_dir or CONFIG_DIR
    data_sources = _read_yaml(cd / "data_sources.yaml", fallback=cd / "data_sources.example.yaml")
    universe = _read_yaml(cd / "universe.yaml")
    risk = _read_yaml(cd / "risk.yaml")
    trading = _read_yaml(cd / "trading.yaml", fallback=cd / "trading.example.yaml")
    return Settings(
        data_sources=DataSourcesConfig(**data_sources),
        universe={k: UniverseRule(**v) for k, v in (universe or {}).items()},
        risk=RiskConfig(**risk),
        trading=TradingConfig(**trading),
    )
