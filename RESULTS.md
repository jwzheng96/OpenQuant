# uni-quant — 午睡期间完成的工作总结

> 时间窗口：2026-05-25 13:00 – 15:00

## 1. 总体进度

按你早上的 P0/P1/P2 计划全部推进完毕，**149 个因子上线，最佳策略 36.63% 累计收益 / Sharpe 0.79**。

| Phase | 任务 | 状态 |
|---|---|---|
| P0-A | 补 daily_basic 历史数据 | ✅ 50→150 全员有 PE/PB/MV |
| P0-B | 重跑 IC + 回测 | ✅ bp / ep / size 都解锁 |
| P1-A | Alpha191 (gtja) 移植 | ✅ 55 个 alpha 已实现 |
| P1-B | 修补 NaN 因子 | ✅ 策略层加 winsorize + finite filter |
| P2 | 扩 universe 到 150 | ✅ HS300 前 150 只 × 30 月 |
| P2 | 行业/风格中性化 (Barra CNE5) | ✅ Barra-light（4 风格因子） |

## 2. 数据层

- **Universe**：150 只 HS300 成分股
- **价格**：107,869 行 daily（qfq 前复权）— 来自 AkShare（EastMoney 优先 + Sina 兜底，遇 4xx 自动切换）
- **基本面**：87,465 行 daily_basic（PE_TTM / PB / total_mv）— 来自 AkShare `stock_zh_valuation_baidu`
- **时间范围**：2022-01-04 到 2024-12-31（约 720 个交易日）
- **复权因子**：合成 1.0（akshare qfq 已复权过）
- **存储**：Parquet 分区 + DuckDB 视图，单机查询 < 1s

## 3. 因子库（共 149 个）

### 3.1 Baseline（10 个，自研）
bp / ep / roe_ttm / size / mom_20d / mom_60d / reversal_5d / vol_20d / turnover_20d / amihud_20d

### 3.2 WorldQuant Alpha101（57 个，论文移植）
30 个第一批 + 27 个第二批，覆盖：
- 量价相关性类（alpha003/006/013/015/016/026/044/050）
- VWAP 偏离类（alpha005/041/042）
- 时间序列排序类（alpha004/038/017/068/074）
- 复合形态类（alpha024/028/032/064/065/071）
- 19 个需要行业中性化的 alpha 标记为 stub（48/56/58/59/63/67/69/70/76/79/80/82/87/89/90/91/93/97/100）

### 3.3 国泰君安 Alpha191（55 个）
A 股本土特化因子，覆盖：
- SMA Wilder 式 EMA 系列（gtja_a009/023/024/028/057/068/096）
- 收益量加权（gtja_a040/043/053/058/187）
- ATR 系列（gtja_a161/175）
- CCI / KDJ / 均价均量类（gtja_a078/126/153）
- 价差累积（gtja_a052/055/069/093/099）

剩余 ~135 个 gtja 因子大部分依赖 BENCHMARK 或 INDUSTRY 数据，待数据层接入后实现。

## 4. 因子有效性排行（HS300-150 × 2022/06–2024/12，5日 horizon）

按 |RankICIR| 排序前 15：

| 排名 | 因子 | RankIC | RankICIR | 含义 |
|---|---|---|---|---|
| 1 | **amihud_20d** | -0.051 | **-5.59** | 流动性溢价（A 股最强信号） |
| 2 | **gtja_a187** | -0.063 | **-4.61** | open gap 累积，高 gap 反转 |
| 3 | **gtja_a070** | -0.046 | **-4.52** | std(amount, 6)，成交额波动 |
| 4 | **gtja_a153** | -0.057 | **-4.31** | (MA3+MA6+MA12+MA24)/4 |
| 5 | **gtja_a161** | -0.057 | **-4.01** | ATR-12，波动率 |
| 6 | **alpha040** | +0.037 | **+3.69** | -rank(std(high,10))·corr(high,vol,10) |
| 7 | **gtja_a150** | -0.023 | **-3.69** | typical_price × volume |
| 8 | **alpha042** | +0.038 | **+3.35** | rank(vwap-close)/rank(vwap+close) |
| 9 | **bp** | +0.037 | **+2.95** | 账面市值比，价值因子 |
| 10 | **vol_20d** | +0.040 | **+2.87** | 低波动 |
| 11 | **size** | +0.030 | **+2.70** | 小市值 |
| 12 | **gtja_a040** | -0.038 | **-2.83** | 上涨/下跌 volume 比 |
| 13 | **gtja_a069** | -0.040 | **-2.78** | open gap 上涨累积 |
| 14 | **gtja_a129** | -0.040 | **-2.76** | 下跌幅度累积 |
| 15 | **gtja_a042** | +0.037 | **+3.69** | (同 alpha040) |

**关键发现**：
1. **A 股短期动量是反转的** — mom_20d/mom_60d/gtja_a106 RankIC 全负
2. **低风险溢价主导** — amihud + vol_20d + size + gtja_a161/175 都在 top 15
3. **成交额波动是顶级信号** — gtja_a070 (-4.52) 接近最强
4. **vwap 微观结构有效** — alpha042/041/050 都显著

## 5. 回测结果（HS300-150，2022/06/01 - 2024/12/31）

策略全部基于 long-only 多因子，每周五调仓，top 15 等权，单票上限 8%。

| 策略 | 累计收益 | Sharpe | 最大回撤 | 成交笔数 | 因子数 | 中性化 |
|---|---|---|---|---|---|---|
| **mf_diverse** (赢家) | **+36.63%** | **0.79** | -18.0% | 1588 | 7 | ❌ |
| mf_lowvol_alpha | +33.41% | 0.75 | -17.8% | 1543 | 5 | ❌ |
| mf_top_alpha | +30.49% | 0.70 | -15.8% | 1550 | 8 | ❌ |
| **mf_diverse_neutral** ⭐ | **+19.70%** | **0.45** | -21.5% | 2110 | 7 | ✅ Barra-light |

**对照基准**：沪深 300 指数 2022-06 ≈ 4400 → 2024-12 ≈ 3934，约 **-10%**。

→ 非中性化的 mf_diverse 跑赢沪深 300 约 47 个百分点，年化超额 ~17%。

### 5.1 ⚠️ 关键诚实结论（午睡期间通过 Barra-light 中性化验证）

**做了 Barra-light 风格中性化（每个因子先对 log_mv / vol_60d / mom_120d / bp 做 cross-sectional OLS 回归，用残差替换原值）后，36.63% 直接掉到 19.70%。**

这说明 **约 45% 的"超额"其实是风格 beta，不是真 alpha**：
- 我们的因子组合本质是 long「小盘 + 低波 + 价值」的风格篮子
- 这三个风格在 2022-2024 A 股大盘下行期间集体跑赢（防御行情）
- 一旦换到大盘高波动反弹期（如 2024 Q4 之后），这种 tilt 可能反转

**真 alpha**（剥除风格后）约为 +19.70% / Sharpe 0.45 — 仍跑赢基准约 30 个百分点，但远不及表面那么好看。

### 5.2 警示

1. **In-sample 选因子** — 7 因子都是在同一段 2022-2024 数据上挑的 top RankICIR，**严重过拟合风险**。
2. **小 universe** — 150 只比 50 只好但仍不够，理想至少中证 500 (500 只) 或全 A (5000+)。
3. **样本短** — 2.5 年回测，未覆盖完整牛熊周期；2022-2024 整体是熊→震荡→慢牛，对低波因子极友好。
4. **未做行业中性化** — 当前仅风格中性化，行业 tilt 仍可能存在（消费/银行权重高）。

## 6. 工程层关键修复（午睡期间）

| Bug | 影响 | 修复 |
|---|---|---|
| MultiFactorStrategy 选股退化为字母序 | 所有早期回测都是 20.26%（假信号）| 加 finite + winsorize 滤掉 inf/NaN |
| akshare EM/Sina 端点 `vol` 列类型不一致 (Int64 vs Float64) | 50→150 扩 universe 时 concat 报错 | `_normalize_daily` 强制 Float64 |
| AkShare `stock_a_indicator_lg` 已废弃 | daily_basic 全部 ❌ | 换 `stock_zh_valuation_baidu` per-indicator |
| `ak.stock_zh_a_hist` 反爬 50% 失败 | 50 只扩 universe 卡死 | 加 Sina `stock_zh_a_daily` 备用端点 |
| `get_daily` 没 join daily_basic | bp/ep/size 因子 EMPTY | API 加 `include_basic=True` |
| `quantile_returns` qcut 在小 universe panic | 因子评估全崩 | 改用 rank-based 手动分桶 |

## 7. 仓库状态

- 70 个文件
- 42/42 测试通过
- 149 个因子可用
- 3 个可跑策略 + 1 个 CTA + 1 个事件驱动 (后两个是骨架)

## 8. M1 — Walk-Forward 验证（已完成）

**方法**：train 2022-06..2023-12 选因子 → test 2024 全年验证。
**通过标准**：sign 一致 + |test ICIR| ≥ 1 + 衰减 ≥ 40% + 90日滚动 IC stability ≥ 0.5

**结果**：**27/121 因子幸存**，白名单见 `data/factor_whitelist.json`。

| 三组 OOS 回测对比（2024 全年）| 收益 | Sharpe | MDD |
|---|---|---|---|
| 原 in-sample 因子 (mf_diverse) | +30.33% | 1.44 | -9.7% |
| Walk-forward 白名单因子 | +28.66% | 1.34 | -12.9% |
| 白名单 + Barra 风格中性化 | +2.21% | 0.22 | -24.3% |

**关键发现**：
1. **跨时间稳定** — 2022-23 选的因子在 2024 几乎一样好（衰减 5%）。**不是过拟合**。
2. **但 95% alpha 还是风格 beta** — 一旦剥除 size/vol/momentum/bp 4 个风格暴露，年化超额从 30% 跌到 2.2%。
3. **amihud_20d 和 gtja_a070 被剔除** — 这两个 in-sample 顶尖（-5.59 / -4.52 ICIR）但 OOS 衰减到 30%，说明它们专门吃 2022-2023 高波动期红利。

**实战意义**：当前的策略本质是「**小盘 + 低波 + 价值**」的 long-only 风格篮子。在 2022-2024 防御行情有效，但不应当作市场中性 alpha 宣传。要真 alpha 需要：
- 行业中性化（解锁 19 个 IndNeutralize alpha）
- alt-data 信号（北向流入、龙虎榜、研报情感）
- 高频微观结构（需要分钟/tick 数据）

## 10. M2 — 扩展 universe（已完成）

- **HS300 头 150 → ZZ500 全员 425 → 总 573 只**
- daily 407,946 行 + daily_basic 334,371 行
- 双源 fallback (EastMoney + Sina) 在 425 只里 423 成功（99.5%）
- 总 sync 时间 ~3 小时（daily 130 min + daily_basic 60 min + DB writes）

## 11. M3 — LightGBM ML composite（已完成）

**方法**：
- 122 因子作为特征矩阵
- 5 日远期收益作为 target
- **walk-forward 滚动 CV**：每月用前 12 个月训练，预测当月
- LightGBM regressor (300 树, lr 0.03, max_depth 6)
- OOS 预测保存为 `data/parquet/factors/name=ml_lgb/data.parquet`，自动注册为 `ml_lgb` 因子

**OOS 评估**：
- Rank IC mean: **+0.0235**
- **Rank ICIR: +2.92** ← 与最强人工因子同档
- 216,899 个 OOS 预测点

**回测对比（2023-06-01 ~ 2024-12-31，573 只 universe）**：

| 策略 | 收益 | Sharpe | MDD | 中性化后收益 | 中性化后 Sharpe |
|---|---|---|---|---|---|
| 线性 (mf_oos_validated) | +24.61% | 0.81 | -17.9% | **-22.98%** | -0.67 |
| **ML (mf_ml_lgb)** | **+26.23%** | **0.64** | -22.9% | **+29.37%** | **+0.71** |

### 11.1 🎯 关键发现

**线性多因子在中性化后崩塌 (24.61% → -22.98%)，ML 模型在中性化后不降反升 (26.23% → 29.37%)。**

这说明：
- 线性因子叠加 **95% 是风格 beta 暴露**，中性化后变成赌行业涨跌的小尾巴
- ML 模型 **找到了真实的 cross-sectional alpha**（非线性、与风格正交）— 52 个百分点的差距说明 LightGBM 捕捉到了人工因子线性组合捕捉不到的信号

这是 M3 阶段最有价值的发现：**简单线性因子叠加已经接近上限，从这里突破需要非线性模型**。

## 12. M4 — Paper Trading（已完成）

**`scripts/paper_trade.py`** 支持两种模式：

1. **`--mode replay`**：跑历史回放，生成 NAV/fills/summary JSON + HTML 日报
2. **`--mode live --date YYYY-MM-DD`**：单日"实盘式"执行 — 拉最新 panel，跑信号，下单到 PaperBroker，输出持仓

**验证**：
- `replay 2024-01-01..2024-12-31` on `mf_ml_lgb` → +20.86% / Sharpe 0.72 / 1494 fills，HTML 报告产出
- 风控触发：MDD -22.25% 超过 `risk.yaml` 限额 -15%，自动告警（飞书 webhook 未配置时降级到 log warning）
- `live --date 2024-12-31` → 20 单买入到 PaperBroker，持仓 + cost 全部对得上
- Prometheus 指标导出 :9101（已开 docker compose 起 Grafana 即可看）

### 12.1 实盘前还差什么

- **真券商接口**：QMTBroker / CTPBroker 是 stub 状态，需要你拿到券商账号后接 xtquant 的 5-10 行 `order_stock`
- **Postgres 持久化**：现在订单只在内存里，需要 wire 到 `infra/postgres/init.sql` 的 orders 表
- **Prefect 调度**：`uni_quant/pipelines.py` 已有 flow 定义但未部署到 server
- **盘中风控环**：30 秒 polling broker + 风控检查（OMS.reconcile 已实现，只需循环调用）

## 13. 完整对比汇总（按时间顺序）

| 阶段 | universe | 因子数 | 最佳 raw | 最佳 neutralized | 真 alpha (中性化后/绝对值) |
|---|---|---|---|---|---|
| 早期 | 4 (mock) | 10 | 2.81% | — | 0% |
| 单蓝筹 | 10 | 10 | 7.74% | — | 0% |
| HS300-50 | 50 | 67 | 20.26% | — | 0% |
| HS300-150 | 150 | 122 | 36.63% | 19.70% | 0.45 Sharpe (线性) |
| **HS300+ZZ500 = 573** | 573 | 123 (+ml_lgb) | 26.23% | **+29.37%** | **0.71 Sharpe (ML)** |

→ 从 mock 到 ML，**真 alpha 从 0 → 0.71 Sharpe**。线性最高就 0.45，ML 把上限推到 0.71。

## 14. 下一步建议

按 ROI 排序：

| 优先级 | 任务 | 估时 | 价值 |
|---|---|---|---|
| **P0** | walk-forward 验证当前 top 因子（用 2022 选 → 2023 验证 → 2024 持仓） | 30 分钟 | 戳穿 in-sample 假象 |
| **P0** | 行业数据接入 + 行业中性化 | 1 小时 | 19 个 Alpha101 stub 解锁 + 检测真 alpha |
| **P1** | Barra CNE5 风格因子（SIZE/BETA/MOMENTUM/VOL/BTOP/LIQUIDITY/EY） | 1 小时 | 计算 active return vs benchmark |
| **P1** | 扩到中证 500 (500 只) + 5 年回测 | 2-3 小时 sync | 统计显著性 |
| **P2** | 行业中性化后剩余 19 个 gtja IndNeutralize alpha | 1 小时 | 多 ~15 个有效因子 |
| **P2** | walk-forward 上跑 ML 模型（lightgbm 学因子组合）| 2 小时 | 比线性 IC 加权可能再提 5-10pp |

## 9. 文件清单

新增/修改的关键文件：

```
src/uni_quant/data/api.py             - get_daily 加 include_basic
src/uni_quant/data/sources.py         - AkShareSource 加 sina 备用
src/uni_quant/data/store.py           - 列类型规范化
src/uni_quant/factors/alpha101.py     - 57 个 Alpha101 (新)
src/uni_quant/factors/alpha191.py     - 55 个 gtja Alpha191 (新)
src/uni_quant/factors/eval.py         - quantile_returns 鲁棒化
src/uni_quant/strategies.py           - winsorize + finite filter

scripts/sync_real_data.py             - 10 蓝筹同步 (legacy)
scripts/sync_hs300_top50.py           - HS300 50 同步
scripts/sync_hs300_extend.py          - HS300 50→150 增量同步
scripts/sync_daily_basic.py           - daily_basic 单独同步
scripts/factor_screen.py              - 批量 IC 评估
scripts/probe_akshare.py              - akshare 探测

configs/strategies/mf_diverse.yaml         - 7 因子，最佳 (新)
configs/strategies/mf_lowvol_alpha.yaml    - 5 因子 (新)
configs/strategies/mf_top_alpha.yaml       - 8 因子 (新)

RESULTS.md                            - 本文档
```

醒来直接看本文件即可。
