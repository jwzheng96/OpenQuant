# OpenQuant Web 平台 — 架构规划

> 量化操作驾驶舱 + 多用户研究协作平台。
> 工业级标准：类型安全 / 公网 HTTPS / RBAC / 审计 / 可观测。

## 目录

1. [产品定位 & 用户角色](#1-产品定位--用户角色)
2. [系统拓扑](#2-系统拓扑)
3. [技术栈](#3-技术栈)
4. [PostgreSQL 数据模型](#4-postgresql-数据模型)
5. [API 设计](#5-api-设计)
6. [安全模型](#6-安全模型)
7. [部署 — 公网 HTTPS](#7-部署--公网-https)
8. [前端 i18n + 主题](#8-前端-i18n--主题)
9. [可观测性](#9-可观测性)
10. [测试策略](#10-测试策略)
11. [实施阶段](#11-实施阶段)

---

## 1. 产品定位 & 用户角色

**定位**：研究 + Paper Trading + Live 监控的 Web 入口。后端 ML/cron 不变，Web 只做"可视化 + 受控操作 + 协作"。

### 用户角色 (RBAC)

| 角色 | 权限 |
|---|---|
| `admin` | 用户管理 / 系统配置 / 全部操作（含活跃策略切换、停盘）|
| `trader` | 跑 backtest / 查看所有数据 / 不能切活跃策略 |
| `viewer` | 只读：NAV / 持仓 / 策略列表 / 因子统计 |

策略 / paper_state 是**全局共享**的（不分用户），但所有"写操作"必须写 audit log（操作人 + 时间 + 内容）。

---

## 2. 系统拓扑

```mermaid
flowchart TB
    subgraph CLIENT [客户端]
        BROWSER[浏览器<br/>桌面优先 + 移动只读]
    end

    subgraph EDGE [公网入口]
        CADDY[Caddy<br/>自动 HTTPS / Let's Encrypt]
        TS[Tailscale 内网<br/>可选, 备用]
    end

    subgraph BACKEND [后端 Docker]
        UVICORN[FastAPI / uvicorn<br/>:8000]
        SSE[SSE Endpoint<br/>backtest 日志流]
        WORKER[BackgroundTasks<br/>跑 paper_daily.py]
    end

    subgraph DATA [数据层]
        PG[(PostgreSQL<br/>auth/audit/tasks)]
        DUCK[(DuckDB<br/>panel / factors)]
        PARQUET[(Parquet<br/>paper_state)]
    end

    subgraph CORE [现有 open_quant]
        OQ[src/open_quant/*]
        SCR[scripts/*.py]
    end

    BROWSER -- HTTPS --> CADDY
    CADDY -- :443 → :8000 --> UVICORN
    UVICORN --> SSE
    UVICORN --> WORKER
    WORKER --> SCR
    UVICORN --> PG
    UVICORN --> OQ
    OQ --> DUCK
    OQ --> PARQUET
```

后端 = 一个 FastAPI 进程。复杂任务（backtest / 训练）通过 `subprocess` 起 `paper_daily.py` / `train_strict_holdout*.py`，**不在 web 进程里跑** — 避免阻塞 + 复用现有脚本。

---

## 3. 技术栈

### 后端

| 项 | 选型 | 版本 |
|---|---|---|
| 框架 | FastAPI | ≥ 0.115 |
| ASGI | uvicorn[standard] | ≥ 0.30 |
| 数据校验 | Pydantic v2 | ≥ 2.7 |
| ORM | SQLAlchemy 2.x (async) + Alembic | ≥ 2.0 / ≥ 1.13 |
| Postgres 驱动 | psycopg[binary] | ≥ 3.1（已有）|
| 认证 | python-jose + passlib[bcrypt] | — |
| 限流 | slowapi | — |
| 后台任务 | FastAPI BackgroundTasks + subprocess | — |
| 日志 | structlog → JSON stdout | — |

### 前端

| 项 | 选型 |
|---|---|
| 框架 | React 18 + TypeScript 5 + Vite 5 |
| UI | shadcn/ui (Tailwind 3 + Radix UI) |
| 图表 | Apache ECharts via `echarts-for-react` |
| 表格 | TanStack Table v8 |
| 请求 / 缓存 | TanStack Query v5 |
| 路由 | TanStack Router |
| 表单 | React Hook Form + Zod |
| i18n | react-i18next + i18next |
| 主题 | next-themes (暗色优先, 支持切换) |
| 时间 | dayjs (轻量, 支持时区) |
| Lint / 格式 | ESLint + Prettier + TypeScript strict |
| 测试 | Vitest + React Testing Library + Playwright |

### 客户端 API 生成

后端 OpenAPI spec → 前端 `openapi-typescript` 生成 `lib/api.ts` 完整类型。
开发流程：`make gen-api`，类型自动同步。

---

## 4. PostgreSQL 数据模型

```sql
-- 用户与认证 ----------------------------------------------------------------
CREATE TABLE users (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email        TEXT UNIQUE NOT NULL,
    username     TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('admin','trader','viewer')),
    locale       TEXT NOT NULL DEFAULT 'zh-CN',
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login   TIMESTAMPTZ
);

CREATE TABLE refresh_tokens (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash   TEXT NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL,
    revoked      BOOLEAN NOT NULL DEFAULT FALSE,
    user_agent   TEXT,
    ip_addr      INET
);
CREATE INDEX ON refresh_tokens (user_id);

-- 审计日志 -----------------------------------------------------------------
CREATE TABLE audit_log (
    id           BIGSERIAL PRIMARY KEY,
    user_id      UUID REFERENCES users(id),
    action       TEXT NOT NULL,             -- e.g. 'strategy.activate', 'backtest.run'
    target_type  TEXT,                      -- 'strategy', 'config', etc.
    target_id    TEXT,
    payload      JSONB,                     -- 详情
    ip_addr      INET,
    user_agent   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON audit_log (created_at DESC);
CREATE INDEX ON audit_log (user_id, created_at DESC);

-- 后台任务（backtest / 训练）------------------------------------------------
CREATE TABLE tasks (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind         TEXT NOT NULL CHECK (kind IN ('backtest','train','sync','custom')),
    status       TEXT NOT NULL CHECK (status IN ('queued','running','success','failed','cancelled')),
    created_by   UUID REFERENCES users(id),
    params       JSONB NOT NULL,            -- yaml name, date range...
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    exit_code    INT,
    log_path     TEXT,                      -- log 文件路径
    result       JSONB,                     -- 结果摘要（NAV, Sharpe...）
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON tasks (status, created_at DESC);
CREATE INDEX ON tasks (created_by, created_at DESC);

-- 策略版本历史（可选, 给"切活跃策略"留快照）--------------------------------
CREATE TABLE strategy_activations (
    id             BIGSERIAL PRIMARY KEY,
    strategy_name  TEXT NOT NULL,
    activated_by   UUID REFERENCES users(id),
    activated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    yaml_snapshot  TEXT NOT NULL              -- 完整 yaml 内容快照
);

-- 告警 / 通知 --------------------------------------------------------------
CREATE TABLE alerts (
    id          BIGSERIAL PRIMARY KEY,
    severity    TEXT NOT NULL CHECK (severity IN ('info','warning','critical')),
    source      TEXT NOT NULL,                -- 'cron','live-oms','data-health'
    message     TEXT NOT NULL,
    payload     JSONB,
    acked_by    UUID REFERENCES users(id),
    acked_at    TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

迁移用 Alembic — `alembic revision --autogenerate` + `alembic upgrade head`。

---

## 5. API 设计

所有 API 前缀 `/api/v1`。

### 公开（无需 auth）

```
GET  /healthz                       探活
GET  /metrics                       Prometheus
POST /api/v1/auth/login             { username, password } → access + refresh token
POST /api/v1/auth/refresh           refresh token → 新 access token
```

### 需要登录（任意角色）

```
GET  /api/v1/auth/me                当前用户信息

GET  /api/v1/strategies             全策略列表（含 Sharpe/MDD/最近 backtest）
GET  /api/v1/strategies/{name}      单策略详情 + yaml + 历史 backtest

GET  /api/v1/paper/{name}/nav       NAV 时序 [?from=&to=]
GET  /api/v1/paper/{name}/positions 当前持仓
GET  /api/v1/paper/{name}/fills     成交流水 [?limit=&offset=]
GET  /api/v1/paper/{name}/report    HTML report (proxy)

GET  /api/v1/factors                因子库列表 + IC stats
GET  /api/v1/factors/{name}         单因子详情

GET  /api/v1/data/health            数据健康度
GET  /api/v1/data/coverage          覆盖矩阵
GET  /api/v1/data/last-cron-run     上次 cron 状态

GET  /api/v1/tasks                  自己提交的任务（admin 看全部）
GET  /api/v1/tasks/{id}             单任务详情
GET  /api/v1/events/tasks/{id}      [SSE] 任务日志流
```

### 需要 trader+

```
POST /api/v1/backtest/run           { strategy, from, to, initial_cash, reset }
                                    → 创建 task, 返回 task_id
POST /api/v1/tasks/{id}/cancel      取消运行中任务
```

### 需要 admin

```
POST /api/v1/strategies/{name}/activate    切 cron yaml 路径
                                           写 strategy_activations + audit_log

GET  /api/v1/admin/users                   用户列表
POST /api/v1/admin/users                   创建用户
PATCH /api/v1/admin/users/{id}             改角色 / 禁用
DELETE /api/v1/admin/users/{id}            删除

GET  /api/v1/admin/audit                   审计日志查询（带过滤器）

POST /api/v1/admin/alerts/{id}/ack         确认告警
```

### Pydantic schema 风格

```python
class NavPoint(BaseModel):
    trade_date: date
    nav: float
    cash: float
    market_value: float
    daily_ret: float

class StrategySummary(BaseModel):
    name: str
    enabled: bool
    type: Literal["multi_factor","cta","event_driven"]
    factors: list[FactorWeight]
    is_active: bool
    last_backtest: BacktestSummary | None
    sharpe: float | None
    cum_return: float | None
    mdd: float | None
```

---

## 6. 安全模型

### 认证

- **JWT**: HS256, access token TTL = 1h, refresh token TTL = 30d
- access token 存 **HTTP-only Secure SameSite=Strict cookie**（防 XSS）
- refresh token 存数据库 + cookie，server-side 可吊销
- 登录前端只发 `{username, password}`，前端绝不存 token 在 `localStorage`
- 密码 bcrypt cost=12

### 防御

| 风险 | 缓解 |
|---|---|
| XSS | React 默认转义；CSP header `default-src 'self'`; cookie HttpOnly |
| CSRF | SameSite=Strict cookie + double-submit token for sensitive ops |
| Brute force | slowapi 限流 5 次/分钟/IP for /auth/login |
| 越权 | 每个端点 RBAC 装饰器：`@require_role("trader")` |
| SQL injection | SQLAlchemy 参数化全程；禁直接拼 SQL |
| 路径遍历 | 所有"读 yaml/log 路径"都白名单校验 |
| 资源耗尽 | backtest 队列限并发 ≤ 2；超时杀进程 |
| 暴露内部 | 生产 `DEBUG=false`，错误返结构化 `{code, msg}` 不暴 stack |

### 网络

- 公网入口走 **Caddy**（自动 Let's Encrypt + HTTPS redirect + HSTS）
- Caddy 反代到 `127.0.0.1:8000` 的 uvicorn
- 后端不直接绑公网端口
- 强烈推荐叠加 **Cloudflare Tunnel**（隐藏源 IP + 防 DDoS）

---

## 7. 部署 — 公网 HTTPS

```mermaid
flowchart LR
    USER[用户浏览器] -->|HTTPS| CF[Cloudflare Tunnel<br/>可选]
    CF -->|encrypted| CADDY[Caddy<br/>自签 / Let's Encrypt]
    CADDY -->|http :8000| FA[FastAPI<br/>uvicorn]
    CADDY -->|static| NG[/dist 静态文件/]
    FA --> PG[(PostgreSQL)]
    FA -->|read/write| FS[(本地 Parquet/DuckDB)]
```

### docker-compose.web.yml

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: openquant
      POSTGRES_USER: openquant
      POSTGRES_PASSWORD: ${PG_PASSWORD}
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck: ...

  backend:
    build: ./web/backend
    environment:
      DATABASE_URL: postgresql://openquant:${PG_PASSWORD}@postgres/openquant
      JWT_SECRET: ${JWT_SECRET}
      OPEN_QUANT_ROOT: /workspace
    volumes:
      - .:/workspace                      # 挂载整个项目，read 现有 yaml/parquet
    depends_on:
      postgres:
        condition: service_healthy
    expose: ["8000"]

  caddy:
    image: caddy:2-alpine
    ports: ["80:80", "443:443"]
    volumes:
      - ./web/Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data
      - ./web/frontend/dist:/var/www/openquant
    depends_on: [backend]

volumes: { pgdata: {}, caddy_data: {} }
```

### Caddyfile

```
openquant.your-domain.com {
    encode gzip zstd
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy strict-origin-when-cross-origin
        Content-Security-Policy "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'"
    }

    handle /api/* {
        reverse_proxy backend:8000
    }
    handle /healthz /metrics {
        reverse_proxy backend:8000
    }
    handle {
        root * /var/www/openquant
        try_files {path} /index.html       # SPA fallback
        file_server
    }
}
```

域名 + 部署最简路径：
1. 买个域名（Cloudflare 注册或国内 .com）
2. 域名 A 记录 → 你公网 IP（家宽走 Cloudflare Tunnel 隐藏 IP）
3. `docker compose -f docker-compose.web.yml up -d`
4. Caddy 自动签 Let's Encrypt 证书

---

## 8. 前端 i18n + 主题

### i18n（中 + 英双语）

```typescript
// src/lib/i18n.ts
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

i18n.use(initReactI18next).init({
  fallbackLng: 'zh-CN',
  resources: {
    'zh-CN': { translation: require('./locales/zh-CN.json') },
    'en':    { translation: require('./locales/en.json') },
  },
});
```

每个用户的 locale 存在 PG `users.locale`，登录后自动加载。
切换按钮在右上角用户菜单里。

### 主题

- Tailwind `dark:` 前缀 + `next-themes`
- 默认 `dark`，用户可切 `light` / `system`
- A 股配色：**红涨绿跌**（与西方相反），通过 CSS 变量集中定义

```css
:root[data-theme="dark"] {
  --color-up: #ef4444;       /* 红涨 */
  --color-down: #10b981;     /* 绿跌 */
  --color-bg: #0a0a0a;
  --color-card: #18181b;
  --color-border: #27272a;
}
```

---

## 9. 可观测性

### 后端

- structlog → JSON stdout (容器收集)
- 每 request 注入 `trace_id`（uuid7）+ user_id
- Prometheus 指标 `/metrics`:
  - `http_requests_total{method,endpoint,status}` (counter)
  - `http_request_duration_seconds{...}` (histogram)
  - `backtest_queue_depth` (gauge)
  - `db_connection_pool_size` (gauge)
- 关键操作发飞书 webhook（已有 monitor.AlertManager 框架）

### 前端

- Sentry / Glitchtip 上传 JS error（可选）
- 关键 user action 发 `/api/v1/events`（埋点）

---

## 10. 测试策略

| 层 | 框架 | 覆盖率 |
|---|---|---|
| 后端单元 | pytest + httpx AsyncClient | ≥ 75% |
| 后端集成 | pytest + 真 PostgreSQL (容器) | 关键路径 |
| 前端单元 | Vitest + RTL | ≥ 60% |
| 前端集成 | Playwright | 主要 user flow |
| E2E | Playwright + docker-compose | 登录 / 跑 backtest / 看曲线 |

CI: 现有 GH Actions 加 `web/backend` 和 `web/frontend` 两个 job。

---

## 11. 实施阶段

| Phase | 内容 | 时间 |
|---|---|---|
| **0. 骨架** | Docker compose + Postgres + Alembic + FastAPI hello + Vite hello + Caddy 本地 | 2 天 |
| **0.5. 认证** | 用户表 + JWT + 登录页 + RBAC 中间件 + audit_log | 2 天 |
| **1. Dashboard** | 整条链路：FastAPI 端点 + React Query + ECharts NAV 曲线 | 3 天 |
| **1.5. 策略列表 + 持仓** | Strategies 页 + Holdings 页 + 简版 A/B 对比 | 3 天 |
| **2. Backtest Runner** | 任务表 + subprocess + SSE 日志流 + 结果页 | 4 天 |
| **2.5. Factors + Data Health** | 因子统计页 + 数据覆盖矩阵 | 2 天 |
| **3. 管理后台** | 用户管理 / 审计日志查询 / 告警 | 2 天 |
| **3.5. 部署** | Caddy + Cloudflare Tunnel + 生产配置 | 1-2 天 |
| **4. Live Trading 等 QMT** | 实时持仓 / OMS / 风险大盘 | 1 周 |

**总计 Phase 0-3.5：约 3 周（个人节奏 / 业余时间）**

---

## 12. 开始之前 — 你需要准备

| 项 | 操作 |
|---|---|
| 域名 | 注册一个（Cloudflare / 阿里云 .com 等），10-50 元/年 |
| 服务器 | 你的 Mac 持续开机 + Tunnel 即可起步；后期换 VPS（阿里云 2C4G 200元/月） |
| Cloudflare 账号 | 接入 Tunnel（免费）+ DNS |
| 第一个 admin 账号 | 通过 `alembic upgrade head` 后跑 seed script 创建 |

---

## License

[Apache License 2.0](../LICENSE)
