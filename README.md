# uni-quant — A股工业级量化交易系统

覆盖 **数据 → 因子 → 回测 → 实盘 → 监控** 的中国A股量化全栈框架。
面向 50万–500万 个人资金，支持 多因子日频选股 + CTA趋势 + 事件驱动。

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
uni-quant data init --start 2018-01-01

# 5. 跑一个示例多因子策略回测
uni-quant backtest run --config configs/strategies/mf_value_momentum.yaml

# 6. paper trading
uni-quant live start --mode paper --strategy mf_value_momentum
```

## 架构

见 [`docs/architecture.md`](docs/architecture.md)（暂略，参考 plan 文档）。

## 模块速览

| 模块 | 作用 |
|---|---|
| `uni_quant.data` | 数据采集、复权、标的池、A股日历 |
| `uni_quant.factors` | 因子计算引擎 + 因子库 + IC/IR 评估 |
| `uni_quant.risk` | Barra 风险模型 + 中性化 + 风控限额 |
| `uni_quant.portfolio` | 组合优化器 + 调仓 |
| `uni_quant.strategies` | 多因子 / CTA / 事件驱动 |
| `uni_quant.backtest` | vectorbt 研究层 + 自研 A股精确事件回测 |
| `uni_quant.execution` | OMS + QMT/CTP/Paper broker |
| `uni_quant.monitor` | Prometheus 指标 + 飞书告警 + 日报 |
| `uni_quant.pipelines` | Prefect 调度的盘前/盘中/盘后任务 |

## A股特有约束（已实现）

- T+1
- 涨跌停 (沪深主板/创业板/科创板/北交所/ST 各档)
- 停牌 / 退市
- 复权 (前复权 / 后复权 / 不复权 — 涨跌停判定用不复权)
- 印花税 (卖 0.05%) + 过户费 (双边 0.001%) + 佣金 (可配)
- 集合竞价 vs 连续竞价时间窗
- 滑点 (按成交量比例)

## 法律声明

本项目仅供学习和**自营**资金研究。不构成投资建议，不接受任何形式的代客理财。
