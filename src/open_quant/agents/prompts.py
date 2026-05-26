"""中文 prompt 模板 — 4 个 A 股分析师 agent.

Design principles:
  1. system prompt 给角色定位 + 输出格式硬约束（强制 JSON-like）
  2. user prompt 注入结构化数据 (不是裸文本)
  3. 输出限制 200 字内（控制成本 + 减少幻觉）
  4. A 股语境：用人民币、市值用"亿元"、谈集采/北向/龙虎榜等本土概念
"""

from __future__ import annotations

from textwrap import dedent

from open_quant.agents.toolkit import (
    FundamentalSnapshot,
    NewsItem,
    SentimentSnapshot,
    TechnicalSnapshot,
)


# ============================================================================ #
# Common system prefix                                                         #
# ============================================================================ #


_OUTPUT_SCHEMA = dedent("""
请严格按如下 JSON 格式输出，不要有任何额外文字：
{
  "action": "BUY" | "HOLD" | "SELL",
  "confidence": 0.0 到 1.0 之间的小数,
  "key_points": ["要点1", "要点2", "要点3"],
  "rationale": "不超过 150 字的核心理由"
}
""").strip()


# ============================================================================ #
# Fundamentals Analyst                                                          #
# ============================================================================ #


FUNDAMENTALS_SYSTEM = dedent("""
你是中国 A 股基本面分析师。你的任务是基于给定的财务和估值数据，
评估该股票当前的基本面健康度，给出 BUY/HOLD/SELL 判断。

评估维度：
1. 估值合理性 — PE/PB 是否处于行业合理区间
2. 盈利质量 — 营收增长、利润增长、ROE 是否健康
3. 财务安全 — 资产负债率、毛利率趋势
4. 行业景气 — 该公司所在行业的政策风险（如医药集采、新能源补贴）

注意 A 股特有风险：商誉减值、关联交易、大股东减持、退市新规。

{output_schema}
""").strip()


def fundamentals_user(snap: FundamentalSnapshot) -> str:
    return dedent(f"""
    股票代码：{snap.ts_code}
    公司名称：{snap.name or '(未知)'}
    行业：{snap.industry or '(未提供)'}
    评估日期：{snap.as_of}

    【估值】
    - 市盈率(TTM)：{_fmt(snap.pe_ttm)}
    - 市净率：{_fmt(snap.pb)}
    - 市销率(TTM)：{_fmt(snap.ps_ttm)}
    - 总市值：{_fmt_yi(snap.total_mv)} 亿元

    【盈利】
    - 营业总收入：{_fmt_yi(snap.revenue)} 亿元
    - 归母净利润：{_fmt_yi(snap.net_profit)} 亿元
    - 营收同比：{_fmt_pct(snap.revenue_growth_yoy)}
    - 净利润同比：{_fmt_pct(snap.profit_growth_yoy)}
    - ROE(TTM)：{_fmt_pct(snap.roe_ttm)}
    - 毛利率：{_fmt_pct(snap.gross_margin)}

    【偿债】
    - 资产负债率：{_fmt_pct(snap.debt_ratio)}

    请评估并按格式输出。
    """).strip()


# ============================================================================ #
# News Analyst                                                                 #
# ============================================================================ #


NEWS_SYSTEM = dedent("""
你是 A 股新闻分析师。基于最近 7 天的新闻头条，判断该股票是否有重大利好/利空。

特别警惕以下类型：
- 业绩暴雷、监管处罚、ST 风险预警
- 大股东减持公告、限售解禁
- 行业政策（集采、补贴、关税）
- 商誉减值、违规担保、财务造假调查
- 重大资产重组、并购预案
- 北向资金大幅流入/流出（影响短期情绪）

如果新闻条数较少或都是中性消息（如日常公告），给 HOLD。
出现明确利空（监管/退市/减持/暴雷）应给 SELL。

{output_schema}
""").strip()


def news_user(ts_code: str, name: str, news: list[NewsItem]) -> str:
    if not news:
        return f"股票：{ts_code} {name}\n\n最近 7 天无重要新闻。请输出 HOLD。"
    lines = [f"股票：{ts_code} {name}", "", f"最近新闻 {len(news)} 条："]
    for i, n in enumerate(news, 1):
        lines.append(f"\n[{i}] {n.timestamp[:16]}  {n.title}")
        if n.summary:
            lines.append(f"    {n.summary[:200]}")
    lines.append("\n请基于以上新闻综合判断。")
    return "\n".join(lines)


# ============================================================================ #
# Technical Analyst                                                            #
# ============================================================================ #


TECHNICAL_SYSTEM = dedent("""
你是 A 股技术分析师。基于价格走势、量化因子值评估短期趋势。

关注：
1. 短中期收益（5/20/60 日）— 是否处于强势/弱势状态
2. 均线位置 — 站上/跌破 MA20、MA60
3. ml_lgb_strict 因子值 — 这是 LightGBM ML 模型对未来 5 日收益的预测
   - 越高代表越看好（值通常在 -0.05 到 +0.05 之间，> 0.01 即较强信号）

如果短期回调过深（5日跌 > 8%）但 ML 信号仍强 → 可能是 BUY 机会
如果连续上涨（20日涨 > 30%）且 ML 信号转弱 → SELL 警告

{output_schema}
""").strip()


def technical_user(snap: TechnicalSnapshot, name: str) -> str:
    return dedent(f"""
    股票：{snap.ts_code} {name}
    评估日期：{snap.as_of}
    收盘价：{_fmt(snap.close)} 元

    【收益率】
    - 5 日收益：{_fmt_pct(_x100(snap.return_5d))}
    - 20 日收益：{_fmt_pct(_x100(snap.return_20d))}
    - 60 日收益：{_fmt_pct(_x100(snap.return_60d))}

    【均线位置】
    - 站上 MA20：{'是' if snap.above_ma20 else '否'}
    - 站上 MA60：{'是' if snap.above_ma60 else '否'}

    【量化因子】
    {_fmt_factors(snap.factor_values)}

    请综合短期价格行为和量化信号判断。
    """).strip()


# ============================================================================ #
# Aggregator prompt — combines all analyst outputs into final KEEP/DROP        #
# ============================================================================ #


AGGREGATOR_SYSTEM = dedent("""
你是组合经理。多个分析师对同一只股票给出了独立判断，请综合输出最终决策。

决策规则（参考但可微调）：
- 任何 analyst 给 SELL 且 confidence > 0.7  → DROP（强否决）
- ≥ 2 个 analyst 给 BUY                    → KEEP
- 全部 HOLD 或 mixed                       → KEEP（保留量化模型决定）
- 1 个 SELL + 其他 BUY                     → KEEP，但记录该风险

请严格按以下 JSON 格式输出：
{
  "decision": "KEEP" | "DROP",
  "confidence": 0.0 到 1.0,
  "risk_flags": ["如果有 SELL 信号，列出风险点"],
  "rationale": "不超过 100 字的综合理由"
}
""").strip()


def aggregator_user(ts_code: str, name: str, analyst_outputs: dict[str, dict]) -> str:
    parts = [f"股票：{ts_code} {name}", "", "各分析师判断："]
    for role, out in analyst_outputs.items():
        if not out:
            continue
        action = out.get("action", "?")
        conf = out.get("confidence", 0)
        rat = out.get("rationale", "")
        parts.append(f"\n[{role}] {action} (置信度 {conf:.2f})")
        parts.append(f"  {rat}")
    parts.append("\n请综合判断 KEEP / DROP。")
    return "\n".join(parts)


# ============================================================================ #
# Helpers                                                                      #
# ============================================================================ #


def _fmt(x):
    if x is None or (isinstance(x, float) and x != x):
        return "N/A"
    return f"{x:.2f}"


def _fmt_pct(x):
    if x is None or (isinstance(x, float) and x != x):
        return "N/A"
    return f"{x:+.2f}%"


def _fmt_yi(x):
    """Format value in 亿元 (1e8 元). Input is 万元 in Tushare conventions."""
    if x is None or (isinstance(x, float) and x != x):
        return "N/A"
    # heuristic: if value > 1e8 it's already in 元 → convert to 亿
    # if 1e4 < x < 1e8 it's probably 万元 → divide by 1e4 to get 亿
    # if x < 1e4 it's already in 亿元
    if abs(x) > 1e8:
        return f"{x / 1e8:.2f}"
    if abs(x) > 1e4:
        return f"{x / 1e4:.2f}"
    return f"{x:.2f}"


def _x100(x):
    return None if x is None else x * 100


def _fmt_factors(d: dict) -> str:
    if not d:
        return "  (无可用因子值)"
    return "\n".join(f"  - {k}: {v:+.4f}" for k, v in d.items())


# Public — used by analysts.py
def get_prompts(role: str) -> tuple[str, callable]:
    """Return (system_prompt, user_prompt_fn) for a given analyst role."""
    schema = _OUTPUT_SCHEMA
    if role == "fundamentals":
        return FUNDAMENTALS_SYSTEM.replace("{output_schema}", schema), fundamentals_user
    if role == "news":
        return NEWS_SYSTEM.replace("{output_schema}", schema), news_user
    if role == "technical":
        return TECHNICAL_SYSTEM.replace("{output_schema}", schema), technical_user
    if role == "aggregator":
        return AGGREGATOR_SYSTEM, aggregator_user
    raise ValueError(f"unknown role: {role}")
