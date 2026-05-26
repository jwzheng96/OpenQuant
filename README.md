# OpenQuant — A股工业级量化交易系统

[![tests](https://github.com/jwzheng96/OpenQuant/actions/workflows/tests.yml/badge.svg)](https://github.com/jwzheng96/OpenQuant/actions/workflows/tests.yml)
[![codecov](https://codecov.io/gh/jwzheng96/OpenQuant/branch/main/graph/badge.svg)](https://codecov.io/gh/jwzheng96/OpenQuant)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache--2.0-green.svg)](LICENSE)

覆盖 **数据 → 因子 → 回测 → 实盘 → 监控** 的中国A股量化全栈框架。
面向 50万–500万 个人资金，支持 多因子日频选股 + CTA趋势 + 事件驱动。
内置可选的 LLM agent overlay（TradingAgents-style）作为量化选股后的"质量门"。

> ⚠️ **本系统不承诺盈利**。它提供的是稳健的工程基础设施和正确的 A股微观摩擦建模，alpha 仍需自行研究。

## 快速开始

```bash
# 1. 装环境 (推荐 uv)
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# 2. 起依赖服务 (ClickHouse + Postgres + Grafana + Prefect)
docker compose up -d

# 3. 配置数据源
cp configs/data_sources.example.yaml configs/data_sources.yaml
# 编辑填入 Tushare token

# 4. 初始化数据 (拉历史)
open-quant data init --start 2018-01-01

# 5. 跑一个示例多因子策略回测
open-quant backtest run --config configs/strategies/mf_value_momentum.yaml

# 6. paper trading
open-quant live start --mode paper --strategy mf_value_momentum
```

## 架构

见 [`docs/architecture.md`](docs/architecture.md)（暂略，参考 plan 文档）。

## 模块速览

| 模块 | 作用 |
|---|---|
| `open_quant.data` | 数据采集、复权、标的池、A股日历 |
| `open_quant.factors` | 因子计算引擎 + 因子库 + IC/IR 评估 |
| `open_quant.risk` | Barra 风险模型 + 中性化 + 风控限额 |
| `open_quant.portfolio` | 组合优化器 + 调仓 |
| `open_quant.strategies` | 多因子 / CTA / 事件驱动 |
| `open_quant.backtest` | vectorbt 研究层 + 自研 A股精确事件回测 |
| `open_quant.execution` | OMS + QMT/CTP/Paper broker |
| `open_quant.monitor` | Prometheus 指标 + 飞书告警 + 日报 |
| `open_quant.pipelines` | Prefect 调度的盘前/盘中/盘后任务 |

## A股特有约束（已实现）

- T+1
- 涨跌停 (沪深主板/创业板/科创板/北交所/ST 各档)
- 停牌 / 退市
- 复权 (前复权 / 后复权 / 不复权 — 涨跌停判定用不复权)
- 印花税 (卖 0.05%) + 过户费 (双边 0.001%) + 佣金 (可配)
- 集合竞价 vs 连续竞价时间窗
- 滑点 (按成交量比例)

## Agent Overlay (可选功能 — feat/trading-agents 分支)

把 LLM 当成"量化选股后的二审"，过滤掉有暴雷新闻/基本面崩坏/技术形态破位的票：

```yaml
# configs/strategies/your_strategy.yaml
qualitative_overlay:
  enabled: true                # 一键开关
  agents:
    fundamentals: true         # 基本面分析师
    news: true                 # 新闻分析师
    technical: true            # 技术分析师
  llm:
    provider: deepseek         # 用 DeepSeek-V4-flash (~¥0.003/股/天)
  decision:
    veto_threshold: 0.7        # 任何 agent SELL 且 conf ≥ 0.7 → 强否决
```

CLI 工具：
```bash
open-quant agents config              # 查看 LLM 配置
open-quant agents test 600519.SH      # 单股 4-agent 评估（debug 用）
open-quant agents eval --from 2025-08-01 --to 2025-09-30  # A/B 量化 vs +LLM
open-quant agents cache               # 查看 / 清理决策缓存
```

成本估算（DeepSeek-V4-flash 公开价格）：
- 单股单日 4 个 agent：~¥0.013
- 30 股池 × 252 交易日：**~¥98/年**

详见 `src/open_quant/agents/`。

## 法律声明

本项目仅供学习和**自营**资金研究。不构成投资建议，不接受任何形式的代客理财。
