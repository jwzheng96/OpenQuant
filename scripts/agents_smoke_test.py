"""End-to-end smoke test for the agent overlay layer.

Pipeline:
  1. Build toolkit (Hybrid → Tushare income + AkShare news/spot)
  2. Pull fundamentals + news + technical for a single stock
  3. Build DeepSeek client (from configs/data_sources.yaml)
  4. Run 3 analysts (fundamentals / news / technical) in sequence
  5. Print each analyst's JSON output

Used to validate Stage 1 + 2 before plugging into the strategy.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date

from open_quant.agents.cache import DecisionCache
from open_quant.agents.llm_client import DeepSeekClient
from open_quant.agents.prompts import get_prompts
from open_quant.agents.toolkit import HybridToolkit
from open_quant.utils import get_logger

log = get_logger(__name__)


def _safe_parse_json(text: str) -> dict:
    """LLM sometimes wraps JSON in ```json fences or adds preamble. Extract robustly."""
    text = text.strip()
    if text.startswith("```"):
        # ```json\n...\n```
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()
    # Find first { ... } block
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        return {"_parse_error": str(e), "_raw": text[:500]}


def run_analyst(role: str, llm: DeepSeekClient, system_prompt: str, user_prompt: str,
                cache: DecisionCache, symbol: str, as_of: str) -> tuple[dict, float]:
    # cache check
    cached = cache.get(symbol, as_of, role, user_prompt)
    if cached is not None:
        return cached, 0.0
    t0 = time.time()
    resp = llm.chat(system=system_prompt, user=user_prompt, temperature=0.3, max_tokens=600)
    elapsed = time.time() - t0
    parsed = _safe_parse_json(resp.text)
    parsed["_meta"] = {
        "tokens": {"prompt": resp.prompt_tokens, "completion": resp.completion_tokens},
        "elapsed_sec": round(elapsed, 2),
        "model": resp.model,
        "finish_reason": resp.finish_reason,
    }
    cache.put(symbol, as_of, role, user_prompt, parsed)
    return parsed, elapsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="600519.SH", help="ts_code (e.g. 600519.SH 茅台)")
    ap.add_argument("--name", default="贵州茅台", help="股票中文名称")
    ap.add_argument("--as-of", default=str(date.today()), help="YYYY-MM-DD")
    ap.add_argument("--roles", default="fundamentals,news,technical")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    as_of = date.fromisoformat(args.as_of)
    roles = [r.strip() for r in args.roles.split(",")]

    print(f"=== Agent smoke test: {args.symbol} {args.name} as of {as_of} ===\n", flush=True)

    # ---- Toolkit ----
    print("[1/3] Building toolkit...", flush=True)
    tk = HybridToolkit()
    print(f"  toolkit: hybrid (tushare available: {tk._ts is not None})", flush=True)

    # ---- Fetch all data first ----
    print("\n[2/3] Fetching data...", flush=True)
    data = {}
    if "fundamentals" in roles:
        t0 = time.time()
        data["fundamentals"] = tk.get_fundamentals(args.symbol, as_of=as_of)
        print(f"  fundamentals: ok ({time.time()-t0:.1f}s) — name={data['fundamentals'].name}", flush=True)
    if "news" in roles:
        t0 = time.time()
        data["news"] = tk.get_news(args.symbol, days=7, limit=8)
        print(f"  news: {len(data['news'])} items ({time.time()-t0:.1f}s)", flush=True)
    if "technical" in roles:
        t0 = time.time()
        data["technical"] = tk.get_technical(args.symbol, as_of=as_of)
        print(f"  technical: close={data['technical'].close}  ml_score={data['technical'].factor_values.get('ml_lgb_strict')}", flush=True)

    # ---- LLM ----
    print("\n[3/3] Calling DeepSeek for analyst reviews...", flush=True)
    llm = DeepSeekClient()
    print(f"  model: {llm.model}", flush=True)

    cache = DecisionCache(ttl_days=7)
    if args.no_cache:
        cache.ttl_days = 0

    outputs = {}
    for role in roles:
        sys_prompt, user_fn = get_prompts(role)
        if role == "fundamentals":
            user_prompt = user_fn(data["fundamentals"])
        elif role == "news":
            user_prompt = user_fn(args.symbol, args.name, data["news"])
        elif role == "technical":
            user_prompt = user_fn(data["technical"], args.name)
        else:
            continue

        try:
            output, elapsed = run_analyst(role, llm, sys_prompt, user_prompt, cache,
                                          args.symbol, str(as_of))
            outputs[role] = output
            cached_tag = " (cache)" if elapsed == 0 else f" ({elapsed:.1f}s)"
            print(f"\n  [{role}]{cached_tag}", flush=True)
            print(f"    action: {output.get('action')}  confidence: {output.get('confidence')}", flush=True)
            print(f"    rationale: {output.get('rationale', '')[:200]}", flush=True)
            if "key_points" in output:
                for kp in output["key_points"][:3]:
                    print(f"      • {kp}", flush=True)
            if "_parse_error" in output:
                print(f"    ⚠️  JSON parse error: {output['_parse_error']}", flush=True)
                print(f"        raw: {output.get('_raw', '')[:200]}", flush=True)
        except Exception as e:
            print(f"\n  [{role}] ❌ {e}", flush=True)
            outputs[role] = {"action": "ERROR", "error": str(e)}

    # ---- Aggregator ----
    if len(outputs) >= 2:
        print(f"\n=== Aggregator: combining {len(outputs)} analyst views ===", flush=True)
        sys_p, user_fn = get_prompts("aggregator")
        agg_user = user_fn(args.symbol, args.name, outputs)
        try:
            final, elapsed = run_analyst("aggregator", llm, sys_p, agg_user, cache,
                                         args.symbol, str(as_of))
            print(f"  decision: {final.get('decision')}  confidence: {final.get('confidence')}", flush=True)
            print(f"  rationale: {final.get('rationale')}", flush=True)
            if final.get("risk_flags"):
                print(f"  risk flags: {final['risk_flags']}", flush=True)
        except Exception as e:
            print(f"  aggregator ❌ {e}", flush=True)

    # ---- Stats ----
    s = llm.stats()
    cost_per_1m_in = 1.5   # 估算: DeepSeek-V3 输入 ¥1.5/1M tokens
    cost_per_1m_out = 8.0  # 输出 ¥8/1M tokens
    est_cost = (s["total_prompt_tokens"] / 1e6 * cost_per_1m_in
                + s["total_completion_tokens"] / 1e6 * cost_per_1m_out)
    print(f"\n=== Token usage ===", flush=True)
    print(f"  calls: {s['n_calls']}", flush=True)
    print(f"  prompt tokens: {s['total_prompt_tokens']:,}", flush=True)
    print(f"  completion tokens: {s['total_completion_tokens']:,}", flush=True)
    print(f"  estimated cost (DeepSeek 标价): ¥{est_cost:.4f}", flush=True)
    llm.close()


if __name__ == "__main__":
    sys.exit(main())
