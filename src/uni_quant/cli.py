"""uni_quant CLI — `uni_quant data sync`, `uni_quant backtest run`, etc.

Built on typer. Keep commands thin — heavy work happens in the domain modules.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import typer
import yaml
from rich import print
from rich.table import Table

from uni_quant.data import get_data_api
from uni_quant.utils import get_logger, load_settings

app = typer.Typer(no_args_is_help=True, add_completion=False, help="uni_quant — A-share quant CLI")
data_app = typer.Typer(no_args_is_help=True)
backtest_app = typer.Typer(no_args_is_help=True)
factor_app = typer.Typer(no_args_is_help=True)
live_app = typer.Typer(no_args_is_help=True)
app.add_typer(data_app, name="data")
app.add_typer(backtest_app, name="backtest")
app.add_typer(factor_app, name="factor")
app.add_typer(live_app, name="live")

log = get_logger(__name__)


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


# ---------------------------------------------------------------------------- #
# data                                                                          #
# ---------------------------------------------------------------------------- #


@data_app.command("sync")
def data_sync(
    dataset: str = typer.Option("daily", help="daily | adj | basic | stock_basic"),
    start: str = typer.Option("2018-01-01"),
    end: str = typer.Option(date.today().isoformat()),
) -> None:
    """Sync a dataset from the configured source into Parquet."""
    api = get_data_api()
    s, e = _parse_date(start), _parse_date(end)
    if dataset == "daily":
        n = api.sync_daily(s, e)
    elif dataset == "adj":
        n = api.sync_adj_factor(s, e)
    elif dataset == "basic":
        n = api.sync_daily_basic(s, e)
    elif dataset == "stock_basic":
        n = api.sync_stock_basic()
    else:
        raise typer.BadParameter(f"unknown dataset: {dataset}")
    print(f"[green]synced[/] {dataset}: {n} rows")


@data_app.command("init")
def data_init(start: str = typer.Option("2018-01-01")) -> None:
    """Bootstrap: pull stock_basic + daily + adj + daily_basic from `start` to today."""
    api = get_data_api()
    s, e = _parse_date(start), date.today()
    print(f"bootstrapping {s}..{e}")
    api.sync_stock_basic()
    api.sync_daily(s, e)
    api.sync_adj_factor(s, e)
    api.sync_daily_basic(s, e)
    print("[green]done[/]")


@data_app.command("check")
def data_check(
    start: str = typer.Option((date.today() - timedelta(days=30)).isoformat()),
    end: str = typer.Option(date.today().isoformat()),
) -> None:
    """Quick data-quality check on the daily dataset."""
    api = get_data_api()
    df = api.query.daily(None, _parse_date(start), _parse_date(end))
    if df.is_empty():
        print("[red]no data[/]")
        raise typer.Exit(1)
    t = Table(title="data check")
    t.add_column("metric"); t.add_column("value")
    t.add_row("rows", f"{df.height:,}")
    t.add_row("symbols", f"{df['symbol'].n_unique():,}")
    t.add_row("date range", f"{df['trade_date'].min()} .. {df['trade_date'].max()}")
    nulls = sum(df[c].null_count() for c in df.columns)
    t.add_row("total nulls", f"{nulls:,}")
    print(t)


# ---------------------------------------------------------------------------- #
# factor                                                                        #
# ---------------------------------------------------------------------------- #


@factor_app.command("list")
def factor_list() -> None:
    from uni_quant.factors import default_engine
    eng = default_engine()
    for name in eng.names():
        print(f"  - {name}")


@factor_app.command("eval")
def factor_eval(
    name: str,
    start: str = typer.Option("2020-01-01"),
    end: str = typer.Option(date.today().isoformat()),
) -> None:
    from uni_quant.factors import default_engine, evaluate_factor
    api = get_data_api()
    panel = api.get_daily(None, _parse_date(start), _parse_date(end), adjust="fwd")
    if panel.is_empty():
        print("[red]no panel data[/]")
        raise typer.Exit(1)
    eng = default_engine()
    r = eng.compute(name, panel)
    if r.data.is_empty():
        print(f"[red]factor {name} produced no data[/]")
        raise typer.Exit(1)
    ev = evaluate_factor(r.data, panel, name=name)
    t = Table(title=f"factor eval: {name}")
    t.add_column("metric"); t.add_column("value")
    for k, v in ev.summary().items():
        t.add_row(k, f"{v:.4f}")
    print(t)


# ---------------------------------------------------------------------------- #
# backtest                                                                      #
# ---------------------------------------------------------------------------- #


@backtest_app.command("run")
def backtest_run(config: Path = typer.Option(..., exists=True, dir_okay=False)) -> None:
    cfg = yaml.safe_load(config.read_text())
    from uni_quant.backtest import BacktestConfig, EventBacktester
    from uni_quant.data.universe import annotate_for_backtest
    from uni_quant.strategies import FactorWeight, MultiFactorStrategy

    api = get_data_api()
    bcfg = cfg.get("backtest", {})
    start = _parse_date(bcfg.get("start", "2020-01-01"))
    end = _parse_date(bcfg.get("end", date.today().isoformat()))
    panel = api.get_daily(None, start, end, adjust="fwd")
    try:
        stock_basic = api.query.con.execute("SELECT * FROM stock_basic").pl()
    except Exception:
        stock_basic = panel.select(["symbol"]).unique().with_columns(pl_alias_name())
    panel = annotate_for_backtest(panel, stock_basic)

    strategy_kind = cfg.get("type", "multi_factor")
    if strategy_kind == "multi_factor":
        factors = [FactorWeight(**f) for f in cfg.get("factors", [])]
        overlay = None
        overlay_cfg = cfg.get("qualitative_overlay") or {}
        if overlay_cfg.get("enabled"):
            from uni_quant.agents import QualitativeOverlay
            overlay = QualitativeOverlay.from_config(overlay_cfg)
            print(f"[overlay enabled — agents: "
                  f"{[k for k, v in overlay_cfg.get('agents', {}).items() if v]}]")
        strat = MultiFactorStrategy(
            factors=factors,
            top_n=cfg.get("selection", {}).get("top_n", 30),
            rebalance_freq={"D": "D", "W-FRI": "W-FRI", "M-END": "M-END"}.get(
                cfg.get("rebalance", {}).get("frequency", "W-FRI"), "W-FRI"
            ),
            max_weight=cfg.get("risk_overrides", {}).get("max_position_weight", 0.05),
            neutralize_styles=cfg.get("neutralize", {}).get("enabled", False),
            qualitative_overlay=overlay,
        )
    else:
        raise typer.BadParameter(f"unsupported strategy type in CLI: {strategy_kind}")

    bt = EventBacktester(BacktestConfig(start=start, end=end,
                                        initial_cash=bcfg.get("initial_cash", 1_000_000)))
    res = bt.run(panel, strat)
    t = Table(title=f"backtest: {cfg.get('name')}")
    t.add_column("metric"); t.add_column("value")
    for k, v in res.summary().items():
        t.add_row(k, f"{v:.4f}")
    print(t)


def pl_alias_name():
    import polars as pl
    return pl.lit("UNKNOWN").alias("name")


# ---------------------------------------------------------------------------- #
# live                                                                          #
# ---------------------------------------------------------------------------- #


@live_app.command("start")
def live_start(
    mode: str = typer.Option("paper", help="paper | live"),
    broker: str = typer.Option("paper"),
    strategy: str = typer.Option(...),
) -> None:
    print(f"[yellow]live start: mode={mode} broker={broker} strategy={strategy}[/]")
    print("[dim]live loop not implemented — wire pipelines.signal_gen + OMS here[/]")


if __name__ == "__main__":
    app()
