"""open_quant CLI — `open_quant data sync`, `open_quant backtest run`, etc.

Built on typer. Keep commands thin — heavy work happens in the domain modules.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import typer
import yaml
from rich import print
from rich.table import Table

from open_quant.data import get_data_api
from open_quant.utils import get_logger, load_settings

app = typer.Typer(no_args_is_help=True, add_completion=False, help="open_quant — A-share quant CLI")
data_app = typer.Typer(no_args_is_help=True)
backtest_app = typer.Typer(no_args_is_help=True)
factor_app = typer.Typer(no_args_is_help=True)
live_app = typer.Typer(no_args_is_help=True)
agents_app = typer.Typer(no_args_is_help=True, help="LLM agent overlay (TradingAgents-style)")
app.add_typer(data_app, name="data")
app.add_typer(backtest_app, name="backtest")
app.add_typer(factor_app, name="factor")
app.add_typer(live_app, name="live")
app.add_typer(agents_app, name="agents")

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
    from open_quant.factors import default_engine
    eng = default_engine()
    for name in eng.names():
        print(f"  - {name}")


@factor_app.command("eval")
def factor_eval(
    name: str,
    start: str = typer.Option("2020-01-01"),
    end: str = typer.Option(date.today().isoformat()),
) -> None:
    from open_quant.factors import default_engine, evaluate_factor
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
    from open_quant.backtest import BacktestConfig, EventBacktester
    from open_quant.data.universe import annotate_for_backtest
    from open_quant.strategies import FactorWeight, MultiFactorStrategy

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
            from open_quant.agents import QualitativeOverlay
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


# ---------------------------------------------------------------------------- #
# agents — LLM overlay management                                              #
# ---------------------------------------------------------------------------- #


@agents_app.command("config")
def agents_config() -> None:
    """Show the resolved DeepSeek + toolkit configuration."""
    s = load_settings().data_sources.deepseek
    key_masked = f"{s.api_key[:8]}...{s.api_key[-4:]}" if s.api_key and len(s.api_key) > 12 else "(unset)"
    t = Table(title="agents — DeepSeek config")
    t.add_column("key"); t.add_column("value")
    t.add_row("api_key", key_masked)
    t.add_row("model", s.model)
    t.add_row("base_url", s.base_url)
    t.add_row("temperature", str(s.temperature))
    t.add_row("max_tokens", str(s.max_tokens))
    t.add_row("timeout", f"{s.timeout}s")
    print(t)


@agents_app.command("test")
def agents_test(
    symbol: str = typer.Argument(..., help="ts_code e.g. 600519.SH"),
    name: str = typer.Option("", help="Stock name for prompts; auto-fetch if blank"),
    as_of: str = typer.Option(date.today().isoformat()),
    roles: str = typer.Option("fundamentals,news,technical",
                              help="comma-separated agent roles"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Run a single-stock end-to-end overlay evaluation. Useful for debugging."""
    import json as _json
    from open_quant.agents import DeepSeekClient, HybridToolkit
    from open_quant.agents.cache import DecisionCache
    from open_quant.agents.overlay import _safe_parse_json
    from open_quant.agents.prompts import get_prompts

    target_date = _parse_date(as_of)
    tk = HybridToolkit()
    llm = DeepSeekClient()
    cache = DecisionCache(ttl_days=0 if no_cache else 7)

    print(f"[cyan]symbol[/]: {symbol}  [cyan]as_of[/]: {target_date}  [cyan]model[/]: {llm.model}")

    # Fetch
    data = {}
    role_list = [r.strip() for r in roles.split(",")]
    if "fundamentals" in role_list:
        snap = tk.get_fundamentals(symbol, as_of=target_date)
        data["fundamentals"] = snap
        if not name:
            name = snap.name or symbol
        print(f"[dim]fundamentals: name={snap.name!r} industry={snap.industry!r} pe={snap.pe_ttm} pb={snap.pb}[/]")
    if "news" in role_list:
        news = tk.get_news(symbol, days=7, limit=8)
        data["news"] = news
        print(f"[dim]news: {len(news)} items[/]")
    if "technical" in role_list:
        tech = tk.get_technical(symbol, as_of=target_date)
        data["technical"] = tech
        ml = tech.factor_values.get("ml_lgb_strict")
        print(f"[dim]technical: close={tech.close} ml_lgb_strict={ml}[/]")

    # Run each analyst
    outputs = {}
    for role in role_list:
        if role not in data:
            continue
        sys_p, user_fn = get_prompts(role)
        if role == "fundamentals":
            user_prompt = user_fn(data["fundamentals"])
        elif role == "news":
            user_prompt = user_fn(symbol, name or symbol, data["news"])
        elif role == "technical":
            user_prompt = user_fn(data["technical"], name or symbol)
        else:
            continue
        cached = cache.get(symbol, str(target_date), role, user_prompt)
        if cached:
            parsed, tag = cached, "(cache)"
        else:
            resp = llm.chat(system=sys_p, user=user_prompt, temperature=0.3, max_tokens=600)
            parsed = _safe_parse_json(resp.text)
            cache.put(symbol, str(target_date), role, user_prompt, parsed)
            tag = ""
        outputs[role] = parsed
        action = parsed.get("action", "?")
        conf = parsed.get("confidence", 0)
        rat = parsed.get("rationale", "")[:200]
        action_color = {"BUY": "green", "SELL": "red", "HOLD": "yellow"}.get(action, "white")
        print(f"\n[bold]{role}[/] [{action_color}]{action}[/] (conf {conf:.2f}) {tag}")
        print(f"  {rat}")

    # Aggregator
    if len(outputs) >= 2:
        sys_p, user_fn = get_prompts("aggregator")
        agg_user = user_fn(symbol, name or symbol, outputs)
        cached = cache.get(symbol, str(target_date), "aggregator", agg_user)
        if cached:
            final, tag = cached, "(cache)"
        else:
            resp = llm.chat(system=sys_p, user=agg_user, temperature=0.3, max_tokens=600)
            final = _safe_parse_json(resp.text)
            cache.put(symbol, str(target_date), "aggregator", agg_user, final)
            tag = ""
        decision = final.get("decision", "?")
        color = "green" if decision == "KEEP" else "red"
        print(f"\n[bold magenta]aggregator[/] [{color}]{decision}[/] (conf {final.get('confidence', 0):.2f}) {tag}")
        print(f"  {final.get('rationale', '')}")
        if final.get("risk_flags"):
            print(f"  [red]risk_flags[/]: {final['risk_flags']}")

    s = llm.stats()
    cost = s["total_prompt_tokens"] / 1e6 * 1.5 + s["total_completion_tokens"] / 1e6 * 8
    print(f"\n[dim]tokens: prompt={s['total_prompt_tokens']:,}  "
          f"completion={s['total_completion_tokens']:,}  cost ≈ ¥{cost:.4f}[/]")
    llm.close()


@agents_app.command("eval")
def agents_eval(
    config_off: str = typer.Option("configs/strategies/mf_ml_strict.yaml", help="config WITHOUT overlay"),
    config_on: str = typer.Option("configs/strategies/mf_ml_strict_with_agents.yaml", help="config WITH overlay"),
    frm: str = typer.Option(..., "--from", help="YYYY-MM-DD"),
    to: str = typer.Option(..., help="YYYY-MM-DD"),
    out_dir: str = typer.Option("data/eval_agents"),
    reset: bool = typer.Option(False),
) -> None:
    """A/B compare ml_lgb pure vs ml_lgb + LLM overlay."""
    import subprocess, sys as _sys
    args = [
        _sys.executable, "scripts/eval_agents.py",
        "--config-off", config_off, "--config-on", config_on,
        "--from", frm, "--to", to, "--state-root", out_dir,
        "--out", f"{out_dir}/comparison.json",
    ]
    if reset:
        args.append("--reset")
    subprocess.run(args, check=True)


@agents_app.command("cache")
def agents_cache_info(
    show: bool = typer.Option(False, help="list cache entries"),
    clear: bool = typer.Option(False, help="WIPE cache (irreversible)"),
) -> None:
    """Inspect or clear the agents decision cache."""
    from pathlib import Path as _P
    root = _P("data/agents_cache")
    if clear:
        import shutil
        if root.exists():
            shutil.rmtree(root)
            print(f"[red]wiped[/] {root}")
        else:
            print(f"{root} already empty")
        return
    if not root.exists():
        print("(cache empty)")
        return
    files = list(root.rglob("*.json"))
    by_symbol = {}
    for f in files:
        if f.parent.name.startswith("_"):
            continue
        sym = f.parent.name
        by_symbol[sym] = by_symbol.get(sym, 0) + 1
    print(f"[cyan]cache root[/]: {root}")
    print(f"[cyan]total files[/]: {len(files)}  symbols: {len(by_symbol)}")
    if show:
        for sym, n in sorted(by_symbol.items()):
            print(f"  {sym}: {n} entries")


if __name__ == "__main__":
    app()
