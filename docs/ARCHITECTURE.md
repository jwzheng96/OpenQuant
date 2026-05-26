# OpenQuant 架构说明

> 工业级 A 股量化系统，分层清晰、每层可独立测试 / 替换。
> 本文档配合源码阅读，所有流程图用 ```` ```mermaid ```` 围栏，GitHub 原生渲染。

## 目录

1. [系统全景](#1-系统全景)
2. [数据层](#2-数据层)
3. [因子层](#3-因子层)
4. [ML 训练流水线](#4-ml-训练流水线)
5. [策略与回测](#5-策略与回测)
6. [Paper Trading 状态机](#6-paper-trading-状态机)
7. [LLM Agent Overlay](#7-llm-agent-overlay)
8. [目录布局](#8-目录布局)
9. [扩展点](#9-扩展点)

---

## 1. 系统全景

OpenQuant 是一个**分层、可替换**的量化系统，每一层只依赖下层的稳定接口：

```mermaid
flowchart TB
    subgraph L1 [① 数据层 data/]
        direction LR
        AK["AkShare<br/>akshare>=1.13"]
        TS["Tushare<br/>tushare>=1.4"]
        PARQ[("Parquet<br/>分区存储")]
        DUCK[("DuckDB<br/>查询引擎")]
        AK --> PARQ
        TS --> PARQ
        PARQ --> DUCK
    end

    subgraph L2 [② 因子层 factors/]
        direction LR
        BL[Baseline 10]
        A101[Alpha101 57]
        A191[GTJA Alpha191 55]
        ENG[FactorEngine]
        BL --> ENG
        A101 --> ENG
        A191 --> ENG
    end

    subgraph L3 [③ ML 层 ml/]
        direction LR
        LGB[LightGBM<br/>walk-forward]
        STR[strict holdout]
        LGB --> STR
    end

    subgraph L4 [④ 策略层 strategies/]
        MFS[MultiFactorStrategy]
        CTA[CTA Dual Thrust]
        EVT[Event-driven]
    end

    subgraph L5 [⑤ 组合 / 回测 portfolio + backtest/]
        OPT[Optimizer cvxpy]
        BAR[Barra 风险模型]
        BT[EventBacktester<br/>A股规则]
    end

    subgraph L6 [⑥ 执行 execution/]
        OMS[OMS 订单机]
        PAPER[PaperBroker]
        QMT[QMT 桥]
        CTP[CTP 桥]
    end

    subgraph L7 [⑦ Agent Overlay 可选 agents/]
        TK[Toolkit<br/>多源新闻 + 财务]
        AN[Analysts × 4]
        AGG[Aggregator]
        TK --> AN --> AGG
    end

    subgraph L8 [⑧ 监控 monitor/]
        PROM[Prometheus]
        FEISHU[飞书告警]
        REP[HTML 日报]
    end

    L1 --> L2 --> L3 --> L4
    L4 --> L5 --> L6
    L4 -.可选二审.-> L7 -.KEEP/DROP.-> L4
    L6 --> L8
```

每层的边界都是**纯 Python 函数 / Pydantic 数据类**，所以可以单测、可以替换实现（比如把 Tushare 换成 Wind 不影响上面）。

---

## 2. 数据层

### 2.1 数据源 & 存储

```mermaid
flowchart LR
    subgraph SRC [数据源]
        AK[AkShare]
        TS[Tushare Pro]
    end

    subgraph FN [data/sources.py]
        FN_DAILY["fetch_daily()"]
        FN_VAL["fetch_valuation()"]
        FN_UNI["fetch_universe()"]
        FN_NEWS["fetch_news()"]
    end

    AK --> FN_DAILY
    AK --> FN_VAL
    AK --> FN_UNI
    AK --> FN_NEWS
    TS --> FN_DAILY

    FN_DAILY --> STORE["data/store.py<br/>write_parquet()<br/>分区: year=YYYY/month=MM"]
    FN_VAL --> STORE
    FN_UNI --> STORE

    STORE --> P[("data/parquet/<br/>daily/, valuation/, universe/")]
    P --> API["data/api.py<br/>get_panel(), get_universe()"]
    API --> CALLER[上层调用者<br/>factors / strategies / backtest]
```

### 2.2 A股微观摩擦 — 都封装在 `backtest/ashare_rules.py`

复权三种模式都保留：

| 用途 | 模式 | 为什么 |
|---|---|---|
| 实盘下单 | 不复权 | 涨跌停按**当日实际价**判定 |
| 研究 / 回测信号 | 前复权 | 避免历史除权造成的价跳变 |
| 长期累计收益 | 后复权 | 与净值曲线一致 |

涨跌停规则按板块自动选：

| 板块 | 涨跌幅限制 |
|---|---|
| 沪深主板 | ±10% |
| 创业板 / 科创板 | ±20% |
| 北交所 | ±30% |
| ST / *ST | ±5% |

### 2.3 数据完整性自检

```mermaid
sequenceDiagram
    autonumber
    participant CLI as open-quant data check
    participant API as data.api
    participant DUCK as DuckDB
    participant TS as Tushare 日历

    CLI->>API: get_panel(2020-01-01..今天)
    API->>DUCK: SELECT trade_date,symbol,close...
    DUCK-->>API: panel
    CLI->>TS: trade_cal()
    TS-->>CLI: 预期交易日列表
    CLI->>CLI: diff(预期日 vs 实际日)
    CLI->>CLI: 检查复权因子单调性
    CLI->>CLI: 检查停牌符号
    Note over CLI: 任何异常 → 飞书告警
```

---

## 3. 因子层

125 个因子按来源分库；引擎统一从面板 (DataFrame) 计算到面板 (DataFrame)：

```mermaid
flowchart LR
    PANEL[("DuckDB Panel<br/>574 stk × 6.4 年")] --> ENG[FactorEngine]

    subgraph LIB [factors/library/]
        BASE[baseline.py<br/>10 因子]
        ALPHA101[alpha101.py<br/>57 因子]
        ALPHA191[alpha191.py<br/>55 因子]
        ML[ml_lgb_*.py<br/>3 模型]
    end

    LIB --> ENG
    ENG --> EVAL["eval.py<br/>IC / RankIC / IR / 衰减"]
    ENG --> CACHE[("factors/<br/>cached parquet")]
    CACHE --> STRAT[策略读取]
    EVAL --> RPT[HTML 因子报告]
```

注册新因子只需在 `factors/library/` 加文件 + 在 `factors/__init__.py` 暴露名字 — `default_engine()` 自动收录。

---

## 4. ML 训练流水线

`ml_lgb_strict` 是当前最关键的 composite：

```mermaid
flowchart TB
    PANEL[("Panel 2020-2023<br/>训练区间")] --> FEAT[特征矩阵<br/>122 base factors]
    FEAT --> LBL[label = next_5d_return]
    LBL --> WF[Walk-forward CV<br/>train 12m → predict 1m]

    subgraph WF
        direction LR
        F1[Fold 1] --> F2[Fold 2] --> F3[...] --> FN[Fold N]
    end

    WF --> LGB[LightGBM<br/>num_leaves=31<br/>learning_rate=0.05]
    LGB --> OOS[OOS 预测<br/>每月 rolling]
    OOS --> CACHE[("factors/ml_lgb_strict.parquet")]

    HOLD[("Holdout 2024-2026<br/>模型从未见过")] -.作为因子使用.-> STRAT[ml_lgb_strict 策略]
    CACHE -.同上.-> STRAT
```

**严格性**：模型只在 2020-2023 训练，2024-2026 是真 OOS。这就是为什么 README 里 +100% / +228% 的数字是诚实的 — 数据未来从未泄漏。

---

## 5. 策略与回测

策略输出"目标权重"，回测引擎按 A 股微观规则模拟撮合：

```mermaid
flowchart LR
    CFG[configs/strategies/<br/>*.yaml] --> STR[MultiFactorStrategy]
    PANEL[(Panel + 因子)] --> STR
    STR --> RANK[因子加权打分]
    RANK --> SEL[top_n 选股]
    SEL --> OPT["组合优化器<br/>cvxpy<br/>max_position_weight, 行业暴露"]
    OPT --> W[目标权重]

    W --> BT[EventBacktester]

    subgraph BT [事件回测]
        direction TB
        T1[load 当日 panel] --> T2[计算 target weight]
        T2 --> T3[T+1 check]
        T3 --> T4[涨跌停 check]
        T4 --> T5[停牌 check]
        T5 --> T6[生成订单]
        T6 --> T7["成本扣除<br/>佣金 + 印花税 + 过户费 + 滑点"]
        T7 --> T8[MTM + 记录 NAV]
    end

    BT --> RPT[HTML 报告<br/>累计收益 + Sharpe + MDD + 归因]
```

事件回测 (`backtest/event_backtester.py`) 和 paper trading 共用同一份 `ashare_rules.py` — 这是"回测-实盘不偏离"的核心保证。

---

## 6. Paper Trading 状态机

`scripts/paper_daily.py` 每天前进一步，状态全部 JSON 持久化在 `data/paper_state/<strategy>/`：

```mermaid
sequenceDiagram
    autonumber
    participant Cron as 调度 / CLI
    participant State as PaperState (JSON)
    participant Strat as Strategy
    participant Mkt as 市场数据
    participant Bk as PaperBroker

    Cron->>State: load 昨日 positions + pending_orders
    Mkt-->>Cron: 今日开盘价
    Cron->>Bk: fill pending_orders @ open
    Bk-->>State: 更新 positions, cash
    Note over State: T+1 锁 unlock
    Mkt-->>Cron: 今日收盘价
    Cron->>State: MTM + record NAV
    Cron->>Strat: panel through today close
    Strat-->>Cron: 明日 target weights
    Cron->>State: persist pending_orders (明日 open 撮合)
    Cron->>State: save state.json + report.html
```

**为什么需要状态机**：A股 T+1 让"昨日买、今日不能卖"变成强约束，必须有状态来记录每只票的可卖份额。状态机让 paper trading 可断点续跑 — 中断了不丢数据。

---

## 7. LLM Agent Overlay

可选的 LLM 二审层。在量化选出 top-N 后，对每只候选股做"质量门"评估：

```mermaid
flowchart TB
    Q[ml_lgb top-30<br/>候选股] --> PF{pre_filter}

    PF -->|蓝筹<br/>大市值/低 PE/<br/>主板/无 ST| KEEP_AUTO[auto-KEEP<br/>不调 LLM]

    PF -->|高风险<br/>小盘 / 高 PE /<br/>创业板 / ST 历史 /<br/>20日涨跌&gt;30%| EVAL[多 Agent 评估]

    subgraph EVAL [Agent Evaluation 并行]
        direction TB
        TK[Toolkit<br/>parallel data fetch]

        TK --> N1[新闻 CLS 财联社]
        TK --> N2[新闻 财新]
        TK --> N3[公告 巨潮 CNINFO]
        TK --> FUND[财务<br/>PE/PB/ROE/营收增速]
        TK --> TECH[技术<br/>已禁用 ❌]

        N1 --> NEWS_A[News Analyst]
        N2 --> NEWS_A
        N3 --> NEWS_A
        FUND --> FUND_A[Fundamentals Analyst]

        NEWS_A --> AGG{Aggregator}
        FUND_A --> AGG
    end

    AGG -->|conf ≥ 0.85<br/>SELL| VETO[强 DROP]
    AGG -->|否则| MODE{decision.mode}
    MODE -->|filter| KD["KEEP / DROP"]
    MODE -->|weight| WT[权重 multiplier<br/>0.5x ~ 1.5x]

    KEEP_AUTO --> FINAL[最终组合]
    KD --> FINAL
    WT --> FINAL
    VETO --> FINAL
```

### 7.1 多源新闻的设计动机

A 股暴雷往往**先在财联社快讯出现**（"立案调查"、"财务差错更正"等关键词），主流财经媒体要慢半天到一天。所以新闻 toolkit 同时拉 3 个源：

| 来源 | AkShare endpoint | 内容侧重 | 实测延迟 |
|---|---|---|---|
| 财联社全球资讯 | `stock_info_global_cls` | 实时市场快讯，立案 / 停牌 / 重大事项 | ~分钟级 |
| 财新主新闻 | `stock_news_main_cx` | 主流财经，深度分析 | ~小时级 |
| 巨潮公告 | `stock_zh_a_disclosure_report_cninfo` | 财务差错更正、公司公告 | T+1 |

market-wide 拉一次缓存 60 分钟，单股按 `stock_news_em` 兜底。

### 7.2 实测对比（4 轮 A/B，2024-01-02 → 2026-05-25）

| 配置 | 累计收益 | 对 OFF 差距 | Sharpe |
|---|---|---|---|
| OFF (纯量化基线) | +3.71% | — | 5.86 |
| v1 broken news | +2.63% | -1.08pp | — |
| v3 multi-source news | +3.03% | -0.69pp | 6.53 |
| v4 deepseek-v4-pro | +2.74% | -0.98pp | 8.49 |
| **vB no-technical + filter** | **+3.26%** | **-0.45pp** | — |
| vC no-technical + weight | +3.71% | 0pp（太软）| — |

**结论**：technical agent 关掉效果最好 — ml_lgb 已经吃透量价信息，LLM 看 K 线反而误杀小盘 winner。

详细方法学：见 [RESULTS.md](../RESULTS.md) 第 12 节。

---

## 8. 目录布局

```
chuanye/                                # 历史仓库名，包名是 open_quant
├── src/open_quant/
│   ├── data/          # ① 数据层 (sources, store, calendar, adjust, universe, api)
│   ├── factors/       # ② 因子层 (engine, library/, eval)
│   ├── ml/            # ③ ML 训练 (composite, walkforward)
│   ├── strategies/    # ④ 策略 (multi_factor, cta, event_driven)
│   ├── portfolio/     # ⑤ 组合优化 + Barra 风险
│   ├── backtest/      # ⑤ 事件回测 + 成本模型 + A股规则
│   ├── execution/     # ⑥ OMS + 各类 broker
│   ├── monitor/       # ⑧ 指标 + 告警 + 报告
│   ├── agents/        # ⑦ LLM 二审 (toolkit, overlay, prompts, aggregator)
│   ├── paper_state/   # paper trading 状态机
│   ├── cli.py         # `open-quant ...` 入口
│   └── config.py
├── configs/strategies/   # 每个策略一份 YAML
├── tests/                # 74 个测试 (data/factors/backtest/agents)
├── scripts/              # sync_*.py / train_*.py / paper_daily.py
├── notebooks/            # 01_quickstart.ipynb
└── docs/                 # 当前文档
```

---

## 9. 扩展点

| 想做什么 | 改哪 | 测试在哪 |
|---|---|---|
| 加一个新因子 | `src/open_quant/factors/library/` 新增文件 → 在 `__init__.py` 暴露 | `tests/factors/` |
| 加一个数据源 | `src/open_quant/data/sources.py` 新增 fetcher 函数 | `tests/data/` |
| 加一类 broker | `src/open_quant/execution/brokers/` 实现 `BrokerBase` 接口 | `tests/execution/` |
| 改 A 股规则 | `src/open_quant/backtest/ashare_rules.py`（事件回测和 paper 共享）| `tests/backtest/` |
| 加一个 Analyst | `src/open_quant/agents/analysts/` 新增 + 在 overlay 注册 | `tests/agents/` |
| 接 Wind 数据 | 实现 `data/sources/wind.py`，配置切到它 | `tests/data/` |

每个扩展点都有现成的兄弟实现可参考；接口稳定，加 feature 不会动到其他层。

---

## License

[Apache License 2.0](../LICENSE)
