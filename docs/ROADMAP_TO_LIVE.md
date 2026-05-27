# 上实盘路线图

> **当前位置**：研究 + 历史 paper trading 平台
> **目标**：个人 50–500 万级别 A 股自营资金日频选股实盘
> **预估时长**：6–10 周（已开 QMT 账号的话），不含小资金灰度期

⚠️ **个人量化的现实警告**

- 实盘 = 不可逆。每个 Phase 必须**全过**才能进下一个 — 不要为了赶进度跳过 paper 验证期
- "回测亮眼"和"实盘赚钱"是两件事 — 实盘会暴露你回测里所有看不见的偏差（撮合延迟、数据延迟、行情断流、撤单失败）
- README 里 +228% / +100% 是诚实的 OOS 数字，但**那是单次历史回放**。实盘退化是常态 — 接受 30-50% 的退化作为基线预期

---

## Phase A — 接入（2–3 周）

### A.1 开 QMT 账号

**MiniQMT 个人版可申请券商**（截至 2026 年）：

| 券商 | 资产门槛 | 备注 |
|---|---|---|
| 国金证券 | 50 万 | 个人开通最容易 |
| 华泰证券 | 50 万 | 申请审批 1-2 周 |
| 东兴证券 | 30 万 | 比较友好 |
| 方正证券 | 50 万 | |
| 中信建投 | 50 万 + 6 个月交易 | 审核严格 |
| 海通证券 | 50 万 | |
| 国信证券 | 50 万 | |

**开户流程**：

1. **选券商**（推荐国金或东兴 — 门槛低、审批快、佣金可议价）
2. **手机 App 开户**：身份证 + 银行卡，30 分钟在线办完。注意：
   - 选 "**普通账户**" 不要"信用账户"（融资融券初期不需要）
   - **风险测评选 C4 或 C5**（积极型），低于 C3 不能交易创业板/科创板
   - 三方存管银行选你常用的
3. **资金过户**：转至少 50 万到这个证券账户（部分券商支持开户 5 个工作日后申请）
4. **打电话给客户经理**（开户后会有人主动联系），明确要求：
   - "我要开通 **QMT / MiniQMT 量化交易权限**"
   - 部分券商让你签电子表格，部分要求线下面签
   - 申请通过后通常 **1-2 周** 拿到登录账号
5. **下载 QMT 客户端**：
   - 客户经理给你下载链接（每家券商定制版略不同）
   - Windows-only — 这是硬性约束，没 Mac 版
   - 启动客户端登录后会在本地解压一份 `userdata_mini/` 目录
   - 关键路径：`userdata_mini/bin64/`（xtquant Python 模块在这里）

**注意事项**：

- **门槛是资产 50 万但不需要常驻** — 开通后可以转出，留个 5-10 万验证就行。具体规则问客户经理
- **佣金可谈** — 默认万 2.5 + 5 元起，量化加入"低频高金额"协议常能压到万 1.5
- **不要在主交易日开通** — 周末/盘后开户最快
- **MiniQMT vs QMT**：MiniQMT 是简化版，只有 Python API，**对个人足够**；完整版 QMT 带可视化界面，机构用
- **xtquant 不上 PyPI** — 它是 QMT 客户端附带的，永远从客户端目录加 `sys.path`

### A.2 Wire `QMTBroker`

参考 `src/open_quant/execution.py:226-270`，需要完成的部分：

```python
def connect(self) -> None:
    from xtquant import xttrader, xtdata
    self._xt = xttrader.XtQuantTrader(self.qmt_path, int(time.time()))
    self._xt.start()
    self._xt.connect()
    # 注册回调
    self._xt.register_callback(self._make_callback())
    # 订阅账号
    self._account = xttrader.StockAccount(self.account_id)

def submit(self, order: Order) -> str:
    from xtquant import xttrader
    side = xttrader.STOCK_BUY if order.side == "buy" else xttrader.STOCK_SELL
    price_type = (xttrader.LATEST_PRICE if order.order_type == OrderType.MARKET
                  else xttrader.FIX_PRICE)
    seq_id = self._xt.order_stock(
        account=self._account, stock_code=order.symbol,
        order_type=side, order_volume=order.qty,
        price_type=price_type, price=order.price or 0,
        strategy_name=order.strategy, order_remark=order.client_id,
    )
    if seq_id < 0:
        raise RuntimeError(f"order_stock returned {seq_id}")
    return str(seq_id)

def cancel(self, broker_id: str) -> None:
    self._xt.cancel_order_stock(self._account, int(broker_id))

def query_order(self, broker_id: str) -> Order | None:
    orders = self._xt.query_stock_orders(self._account)
    for o in orders:
        if str(o.order_id) == broker_id:
            return _convert_xt_order(o)
    return None

def query_positions(self) -> dict[str, dict]:
    positions = self._xt.query_stock_positions(self._account)
    return {p.stock_code: {"qty": p.volume, "avg_cost": p.open_price,
                          "sellable": p.can_use_volume} for p in positions}

def _make_callback(self):
    class CB(xttrader.XtQuantTraderCallback):
        def on_stock_trade(inner_self, trade):
            # 把成交回报转成 Order 推到 OMS
            fill = _xt_trade_to_order(trade)
            if self._fill_cb:
                self._fill_cb(fill)
        def on_order_error(inner_self, err):
            log.error(f"QMT order error: {err.error_msg}")
        def on_disconnected(inner_self):
            log.error("QMT disconnected — alert and attempt reconnect")
    return CB()
```

**测试方法**：QMT 模拟环境 — 客户端有"仿真交易"选项，账户初始资金通常 100 万。在仿真账户上跑通 submit/cancel/query 三件套 + 收到 `on_stock_trade` 回调后才能切真账户。

### A.3 实时行情订阅

xtdata 内置，不需要单独申请：

```python
from xtquant import xtdata

# 订阅当日分钟 K
xtdata.subscribe_quote(stock_code, period="1m", count=-1, callback=on_bar)

# 拉历史日线（也走 xtdata，避免和 AkShare 数据源对不上）
xtdata.get_market_data(
    field_list=["open", "high", "low", "close", "volume"],
    stock_list=universe,
    period="1d",
    start_time="20240101",
    end_time="20260526",
    dividend_type="front",
)
```

**关键决策**：实盘行情用 QMT 自带还是继续 AkShare？建议 — **回测继续用 AkShare（成本 0），实盘订阅 QMT 行情（最低延迟）**，但保留 AkShare 作为兜底。

### A.4 OMS 持久化

当前 `OrderManagementSystem._orders` 是 `dict[str, Order]` in-memory。进程重启 → 全丢。

加 Postgres 持久化：

```python
class PostgresOrderListener:
    def __init__(self, conn): self.conn = conn
    def __call__(self, o: Order):
        self.conn.execute("""
            INSERT INTO orders (client_id, strategy, symbol, side, qty, ...)
            VALUES (...)
            ON CONFLICT (client_id) DO UPDATE SET ...
        """, (...))

oms = OrderManagementSystem(broker=qmt, risk_check=...)
oms.add_listener(PostgresOrderListener(pg_conn))
```

### A.5 每分钟对账

```python
import schedule
schedule.every(1).minutes.do(oms.reconcile)
```

对账时检查：
- 本地状态 vs `query_order` 返回的 broker 状态
- 本地持仓 vs `query_positions` 返回的 broker 持仓
- 任何 diff > 0 → 飞书告警 + 等下个周期再确认 → 还在 diff → 暂停下单

---

## Phase B — 实时 paper trading（2–4 周硬性要求）

### B.1 数据 sync — `scripts/sync_today_em.py`

A 股 EOD 数据 sync 关键组件。直连东方财富 `push2his.eastmoney.com/api/qt/stock/kline/get`，绕开 AkShare 内部 broken session（在有 Clash/Shadowsocks 系统代理时 AkShare 的 requests.Session 即使 `trust_env=False` 仍会 ConnectionError）。

性能：**单日 574 票 ~104 秒**（vs AkShare 4 小时）。

使用：
```bash
# 默认 today
python scripts/sync_today_em.py

# 指定某天补数据
python scripts/sync_today_em.py --date 2026-05-26
```

幂等增量：只补 DB 缺的 symbol。

### B.2 改造 `paper_daily.py` 实时模式

当前是"每天一次的离线回放"。组合 sync + paper 成实时模式：
- 盘前 08:30：跑前一日 paper → 生成今天 target_weights → 写 `pending_orders.json`
- 盘后 17:00：`sync_today_em.py` → `paper_daily.py --once today` → 撮合 + MTM + NAV
- 周末：跑周报 + 因子衰减检查

**注意**：A 股 15:00 收盘，EOD 数据 17:00 后稳定。盘中跑 paper 拿到的是 intraday 价 snapshot（close 字段实际是当前价），用于 demo 可，做正式 NAV 必须 17:00 后跑。

### B.3 自动化 — macOS launchd

参考 `scripts/daily_paper_cron.sh` + `~/Library/LaunchAgents/com.openquant.daily.plist` —— 每天 17:00 自动跑。

### B.4 验证 checklist

### B.5 验证 checklist

每项都要"刻意触发"测试，不能等真出问题：

- [ ] **T+1 锁仓**：手工买入一只票，当天尝试卖出 → OMS 应拒
- [ ] **涨跌停拒单**：选一只开盘涨停的票下单 → OMS 应拒
- [ ] **停牌处理**：选一只停牌票 → 持仓 MTM 按停牌前最后价
- [ ] **资金不足拒单**：余额 1 万的情况下下 10 万单 → OMS 应拒
- [ ] **熔断演练**：手工把单日亏损阈值调到 0.1% → 触发后撤所有订单 + 飞书告警 < 1 分钟
- [ ] **OMS 恢复**：杀掉 paper 进程 → 重启 → 状态恢复正确
- [ ] **AkShare 限流**：连续刷新 1000 次 → 系统不崩，自动 backoff
- [ ] **OOS 退化**：连跑 4 周后实时表现 vs 你 README 里的回测数字偏离应 < 100bps/月

通过 8/8 才能进 Phase C。

---

## Phase C — 灰度小资金（4 周）

- [ ] 真实账户，**总资金 10%** — 50 万账户用 5 万；500 万账户用 50 万
- [ ] 单笔订单上限：账户 2%
- [ ] 单日亏损止损：1.5%
- [ ] 月度 NAV vs 回测预期 — 偏离 > 1% 立即停盘排查
- [ ] **代码 freeze** — 灰度期间任何 strategy/factor/broker 改动需重跑 Phase B 验证 checklist

### 灰度通过的标志

- 月度对账差异 0 笔订单
- 月度 NAV 偏离 < 1%
- 任何熔断事件 < 1 分钟 alert 到达
- 零次数据中断引发的异常订单

---

## Phase D — 逐月扩资

通过 Phase C → 资金扩到 30%。再过 1 个月 → 50%。再过 1 个月 → 100%。
**不要一次性 all-in**，即使回测再好。

---

## 常见错误（按真实出错率排序）

1. **复权数据不一致**：QMT 行情、AkShare 回测、Tushare 验证 — 三家复权因子可能差几个 bp。**实盘下单价务必用 QMT 不复权价**
2. **涨跌停判定错误**：用复权价判断涨跌停一定错，必须用**前收盘 × (1 + 限制%)**
3. **T+1 边界**：周五买入 → 周一可卖。中秋/国庆节前买入 → 节后可卖。日历必须用真实交易日
4. **撤单超时**：QMT 撤单有时不立即返回，需要轮询 `query_order` 确认状态
5. **断网恢复**：QMT 客户端会自动重连，但 xtquant Python session 不会 — 你要监听 `on_disconnected` 并手动 reconnect
6. **数据延迟**：盘后 17:00 不是所有票都有当日数据，部分要等 19:00。**调仓信号必须用昨日收盘数据**避免不齐
7. **错单**：股票代码错（00xxxx vs 60xxxx）、数量没乘 100、买卖方向反 — 都需要 OMS pre-trade 强 check

---

## 监控告警 — 必须配齐

| 告警类型 | 触发条件 | 渠道 |
|---|---|---|
| 数据中断 | 任一数据源 30 min 无更新 | 飞书 |
| OMS 异常订单 | reject 率 > 10% | 飞书 |
| 对账不平 | 本地 vs broker 任何 diff | 飞书 + 电话 |
| 单日亏损 | > 1% | 飞书 |
| 单日亏损（重度） | > 1.5% — 触发熔断 | 飞书 + 电话 + 邮件 |
| QMT 断连 | `on_disconnected` | 飞书 + 电话 |

`open_quant.monitor.AlertManager` 框架已有，**webhook URL 必须在 `configs/trading.yaml` 配齐**。

---

## 法规 & 税务

- **个人自营资金**：自己买自己的不涉及合规，**绝不要代客理财** — 那是非法。
- **资管业务**：如果要管别人的钱，走券商定向 / 期货资管 / 私募备案 — 这是另一个项目
- **税务**：A 股自然人买卖股票免征个人所得税；股息红利持有 ≤ 1 月按 20% 扣，> 1 年免

---

## License

[Apache License 2.0](../LICENSE)
