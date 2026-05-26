"""QualitativeOverlay — orchestrates analyst agents into KEEP/DROP decisions.

Public API:

    overlay = QualitativeOverlay.from_config({
        "agents": {"fundamentals": True, "news": True, "technical": True},
        "llm": {"provider": "deepseek", "model": "deepseek-v4-flash"},
        "decision": {"mode": "filter", "veto_threshold": 0.7},
        "cache": {"dir": "data/agents_cache", "ttl_days": 7},
    })
    decisions = overlay.evaluate(["600519.SH", "000333.SZ"], as_of=date(2026, 5, 25))
    # decisions: {symbol: OverlayDecision(action="KEEP"|"DROP", confidence, risk_flags, rationale)}

When `decision.mode == "filter"`, returns a binary KEEP/DROP per symbol.
When `decision.mode == "weight"`, returns a continuous multiplier in [0, 1.5].

Optimization:
- Parallel data fetch + LLM calls per symbol (ThreadPoolExecutor)
- Disk cache keyed by (symbol, as_of, role, prompt_hash) — TTL controlled
- Token usage + cost tracking
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from uni_quant.agents.cache import DecisionCache
from uni_quant.agents.llm_client import DeepSeekClient, LLMClient
from uni_quant.agents.prompts import get_prompts
from uni_quant.agents.toolkit import (
    AkShareToolkit,
    FundamentalSnapshot,
    HybridToolkit,
    NewsItem,
    TechnicalSnapshot,
    Toolkit,
    TushareToolkit,
)
from uni_quant.utils import get_logger

log = get_logger(__name__)


@dataclass
class OverlayDecision:
    symbol: str
    as_of: str
    action: Literal["KEEP", "DROP"]
    confidence: float
    weight_multiplier: float = 1.0  # for weight mode
    risk_flags: list[str] = field(default_factory=list)
    rationale: str = ""
    analyst_outputs: dict[str, dict] = field(default_factory=dict)


@dataclass
class OverlayStats:
    n_evaluated: int = 0
    n_kept: int = 0
    n_dropped: int = 0
    n_errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_yuan: float = 0.0
    elapsed_sec: float = 0.0


def _safe_parse_json(text: str) -> dict:
    """LLM sometimes wraps JSON in ```json fences. Extract robustly."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        return {"_parse_error": str(e), "_raw": text[:300]}


class QualitativeOverlay:
    """Orchestrates analyst agents into per-symbol KEEP/DROP decisions."""

    def __init__(
        self,
        toolkit: Toolkit | None = None,
        llm: LLMClient | None = None,
        cache: DecisionCache | None = None,
        agents_enabled: dict[str, bool] | None = None,
        veto_threshold: float = 0.7,
        decision_mode: Literal["filter", "weight"] = "filter",
        max_workers: int = 4,
        name_lookup: dict[str, str] | None = None,
        cost_per_1m_in: float = 1.5,   # DeepSeek-V3 input price (CNY / 1M tokens)
        cost_per_1m_out: float = 8.0,  # DeepSeek-V3 output price
    ):
        self.toolkit = toolkit or HybridToolkit()
        self.llm = llm or DeepSeekClient()
        self.cache = cache or DecisionCache()
        self.agents_enabled = agents_enabled or {
            "fundamentals": True, "news": True, "technical": True, "sentiment": False,
        }
        self.veto_threshold = veto_threshold
        self.decision_mode = decision_mode
        self.max_workers = max_workers
        self.name_lookup = name_lookup or {}
        self.cost_per_1m_in = cost_per_1m_in
        self.cost_per_1m_out = cost_per_1m_out
        self._stats = OverlayStats()

    # ---- public API --------------------------------------------------------

    def evaluate(self, symbols: list[str], as_of: date) -> dict[str, OverlayDecision]:
        """Evaluate multiple symbols in parallel; return KEEP/DROP per symbol."""
        import time
        t0 = time.time()
        decisions: dict[str, OverlayDecision] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            future_to_symbol = {
                pool.submit(self._evaluate_one, s, as_of): s for s in symbols
            }
            for fut in as_completed(future_to_symbol):
                s = future_to_symbol[fut]
                try:
                    decisions[s] = fut.result()
                except Exception as e:
                    log.exception(f"overlay eval failed {s}")
                    self._stats.n_errors += 1
                    decisions[s] = OverlayDecision(
                        symbol=s, as_of=as_of.isoformat(),
                        action="KEEP",  # fail-safe: keep on error (don't kill the quant pick)
                        confidence=0.0, rationale=f"agent error: {e}",
                    )
        self._stats.elapsed_sec += time.time() - t0
        # Update cost
        s = self.llm.stats() if hasattr(self.llm, "stats") else {}
        self._stats.prompt_tokens = s.get("total_prompt_tokens", 0)
        self._stats.completion_tokens = s.get("total_completion_tokens", 0)
        self._stats.cost_yuan = (
            self._stats.prompt_tokens / 1e6 * self.cost_per_1m_in
            + self._stats.completion_tokens / 1e6 * self.cost_per_1m_out
        )
        return decisions

    def stats(self) -> OverlayStats:
        return self._stats

    # ---- per-symbol pipeline -----------------------------------------------

    def _evaluate_one(self, symbol: str, as_of: date) -> OverlayDecision:
        self._stats.n_evaluated += 1
        as_of_str = as_of.isoformat()
        name = self.name_lookup.get(symbol, symbol)
        analyst_outputs: dict[str, dict] = {}

        # ---- Parallel data fetch (3 toolkit calls concurrently, ~3x speedup) ----
        data: dict[str, object] = {}
        with ThreadPoolExecutor(max_workers=3) as fetch_pool:
            futures = {}
            if self.agents_enabled.get("fundamentals"):
                futures[fetch_pool.submit(self.toolkit.get_fundamentals, symbol, as_of=as_of)] = "fundamentals"
            if self.agents_enabled.get("news"):
                futures[fetch_pool.submit(self.toolkit.get_news, symbol, days=7, limit=8)] = "news"
            if self.agents_enabled.get("technical"):
                futures[fetch_pool.submit(self.toolkit.get_technical, symbol, as_of=as_of)] = "technical"
            for fut in as_completed(futures):
                kind = futures[fut]
                try:
                    data[kind] = fut.result()
                except Exception as e:
                    log.warning(f"{symbol} {kind} fetch: {e}")

        # Use the fundamentals snapshot to backfill `name` if we didn't have it
        if name == symbol and "fundamentals" in data:
            f = data["fundamentals"]
            if getattr(f, "name", None):
                name = f.name

        # ---- Run analysts (sequential to avoid LLM rate-limit) ----
        if "fundamentals" in data:
            analyst_outputs["fundamentals"] = self._call_analyst(
                "fundamentals", symbol, as_of_str,
                lambda fn, d=data["fundamentals"]: fn(d),
            )
        if "news" in data:
            analyst_outputs["news"] = self._call_analyst(
                "news", symbol, as_of_str,
                lambda fn, items=data["news"]: fn(symbol, name, items),
            )
        if "technical" in data:
            analyst_outputs["technical"] = self._call_analyst(
                "technical", symbol, as_of_str,
                lambda fn, t=data["technical"]: fn(t, name),
            )

        # ---- Aggregator ----
        if not analyst_outputs:
            # No analyst ran — fail-safe KEEP
            return OverlayDecision(
                symbol=symbol, as_of=as_of_str, action="KEEP",
                confidence=0.0, rationale="no analyst available",
            )

        # Veto rule shortcut: any SELL with confidence > veto_threshold → DROP
        for role, out in analyst_outputs.items():
            if not isinstance(out, dict):
                continue
            if out.get("action") == "SELL" and float(out.get("confidence", 0)) >= self.veto_threshold:
                return OverlayDecision(
                    symbol=symbol, as_of=as_of_str, action="DROP",
                    confidence=float(out.get("confidence", 0)),
                    risk_flags=[f"{role}_strong_sell"],
                    rationale=f"[{role}] {out.get('rationale', '')[:150]}",
                    analyst_outputs=analyst_outputs,
                )

        agg = self._call_analyst("aggregator", symbol, as_of_str,
                                 lambda fn: fn(symbol, name, analyst_outputs))
        decision = (agg.get("decision") or "KEEP").upper()
        conf = float(agg.get("confidence", 0.5))
        flags = agg.get("risk_flags") or []
        rationale = agg.get("rationale", "")
        action: Literal["KEEP", "DROP"] = "DROP" if decision == "DROP" else "KEEP"
        if action == "KEEP":
            self._stats.n_kept += 1
        else:
            self._stats.n_dropped += 1

        # Weight multiplier (for weight mode)
        weight_mult = 1.0
        if self.decision_mode == "weight" and action == "KEEP":
            # boost or penalize based on aggregator confidence
            # high-confidence KEEP → 1.5x, low-confidence KEEP → 0.5x
            weight_mult = 0.5 + conf

        return OverlayDecision(
            symbol=symbol, as_of=as_of_str, action=action,
            confidence=conf, weight_multiplier=weight_mult,
            risk_flags=flags, rationale=rationale,
            analyst_outputs=analyst_outputs,
        )

    # ---- helpers -----------------------------------------------------------

    def _call_analyst(self, role: str, symbol: str, as_of_str: str, build_user) -> dict:
        sys_prompt, user_fn = get_prompts(role)
        user_prompt = build_user(user_fn)

        cached = self.cache.get(symbol, as_of_str, role, user_prompt)
        if cached is not None:
            return cached

        try:
            resp = self.llm.chat(
                system=sys_prompt, user=user_prompt,
                temperature=0.3, max_tokens=600,
            )
        except Exception as e:
            log.warning(f"llm {role} {symbol}: {e}")
            return {"action": "HOLD", "confidence": 0.0, "rationale": f"llm error: {e}"}

        parsed = _safe_parse_json(resp.text)
        if "_parse_error" not in parsed:
            self.cache.put(symbol, as_of_str, role, user_prompt, parsed)
        return parsed

    # ---- factory -----------------------------------------------------------

    @classmethod
    def from_config(cls, cfg: dict, *, name_lookup: dict[str, str] | None = None) -> "QualitativeOverlay":
        """Build from a dict (typically from strategy yaml's `qualitative_overlay` section)."""
        agents = cfg.get("agents") or {}
        decision = cfg.get("decision") or {}
        llm_cfg = cfg.get("llm") or {}
        cache_cfg = cfg.get("cache") or {}
        tk_cfg = cfg.get("toolkit") or {}

        # Toolkit choice
        tk_kind = tk_cfg.get("kind", "hybrid")
        if tk_kind == "akshare":
            toolkit = AkShareToolkit()
        elif tk_kind == "tushare":
            toolkit = TushareToolkit()
        else:
            toolkit = HybridToolkit()

        llm = DeepSeekClient(
            api_key=llm_cfg.get("api_key"),
            model=llm_cfg.get("model"),
            base_url=llm_cfg.get("base_url"),
            timeout=llm_cfg.get("timeout"),
        )

        cache = DecisionCache(
            root=cache_cfg.get("dir", "data/agents_cache"),
            ttl_days=int(cache_cfg.get("ttl_days", 7)),
        )

        return cls(
            toolkit=toolkit,
            llm=llm,
            cache=cache,
            agents_enabled=agents,
            veto_threshold=float(decision.get("veto_threshold", 0.7)),
            decision_mode=decision.get("mode", "filter"),
            max_workers=int(cfg.get("max_workers", 4)),
            name_lookup=name_lookup or {},
        )
