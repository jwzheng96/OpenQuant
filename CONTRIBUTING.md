# Contributing to OpenQuant

感谢有兴趣贡献 OpenQuant！下面是基本指引。

## 环境准备

```bash
# 推荐 uv
uv venv -p 3.11
source .venv/bin/activate
uv pip install -e ".[dev]"

# 数据源配置（可选 — 仅 paper/live + 真实回测需要）
cp configs/data_sources.example.yaml configs/data_sources.yaml
# 编辑填入你的 Tushare token / DeepSeek api_key
```

跑测试确认环境就绪：

```bash
pytest -v       # 应该看到 66 passed
open-quant --help
```

## 开发流程

1. **Fork 仓库** → clone 你的 fork
2. **新分支** — 别在 `main` 上直接改：
   ```bash
   git checkout -b feat/your-feature
   ```
3. **改代码** — 确保：
   - 新增功能配套加单元测试（参考 `tests/test_open_quant.py` / `tests/test_agents.py`）
   - 改动不破坏现有 66 个测试
   - 涉及 LLM / 外部 API 的代码必须有 mock 测试，不依赖真实凭证
4. **本地验证**：
   ```bash
   pytest -v
   python -m compileall -q src tests scripts   # 语法检查
   ruff check src --select E9,F82,F63          # 严重错误检查
   ```
5. **Commit 信息** — 用 `[type]: subject` 格式，type 选：
   - `feat:` 新功能
   - `fix:` bug 修复
   - `agents:` agent overlay 相关
   - `data:` 数据层 (sources/store/api)
   - `factors:` 因子库
   - `backtest:` 回测引擎
   - `test:` 新增/改测试
   - `docs:` 仅文档
   - `chore:` CI / 配置 / 杂项
6. **推到你的 fork** → 开 PR 到 `main`

PR 描述里清楚写：
- **改动动机**（解决什么问题？）
- **改动范围**（哪些文件 / 哪些行为变了？）
- **测试覆盖**（新加了什么测试？）
- **回测影响**（如果涉及策略改动，附上 before/after 的数字对比）

## 代码风格

- Python 3.11+，鼓励用 type hints
- 用 polars 替代 pandas（性能 + 一致性）
- A 股相关常量集中在 `backtest/ashare_rules.py`
- 因子函数签名统一：`(panel: pl.DataFrame) -> pl.DataFrame`，返回 `symbol/trade_date/value`
- LLM agent 输出必须严格 JSON（schema 见 `agents/prompts.py`）

## 测试要求

| 类型 | 要求 |
|---|---|
| 新因子 | 在 `tests/test_open_quant.py::TestFactorEngine` 加一个断言 |
| 新 agent / 新 prompt | 在 `tests/test_agents.py` 加 MockLLM 测试 |
| 新数据源 | 加 MockSource 风格的 fixture，不能依赖网络 |
| 回测逻辑改动 | 必须跑完整 `pytest` 不退步 |

CI 会在 PR 自动跑 Python 3.11 + 3.12 双版本 pytest。**红了不能合**。

## 数据 / 模型贡献

如果你做的是新因子 / 新模型 / 新策略，请在 PR 描述里附上：

- 因子的 **IC / RankIC / ICIR**（用 `evaluate_factor` 算）
- 回测的 **累计收益 / Sharpe / MDD**（用 `EventBacktester` 跑）
- 真实 A 股 vs 合成数据的对比（合成数据 IC ~0 是正常）
- **walk-forward 或 strict-holdout OOS 验证结果**（防过拟合）

不带 OOS 验证的策略 PR 一般不接受。`RESULTS.md` 里有完整的分析框架范例。

## 报 issue

发现 bug？建议优先：
1. 先在 issues 里搜一下，避免重复
2. 用 minimal reproducible example（复制 `tests/test_*.py` 的写法）
3. 写清楚 Python 版本 + OS

新特性请求也欢迎，但请先描述**问题场景**，再描述提议的解法。

## 风险声明

OpenQuant 是研究和实验项目。**不构成投资建议**。任何在实盘使用本系统造成的资金损失，**贡献者与维护者不承担任何责任**（详见 LICENSE 第 7-8 条）。

谢谢你的贡献！
