# OpenQuant Web

Industrial-grade dashboard for the OpenQuant quant trading system.

- Backend: **FastAPI** (async) + **PostgreSQL** + **SQLAlchemy 2**
- Frontend: **React 18 + TypeScript** + **Vite** + **Tailwind + shadcn/ui**
- Auth: **JWT** + RBAC (admin / trader / viewer)
- Deploy: Docker Compose + Caddy + HTTPS (Let's Encrypt)

Full design: [`docs/WEB_ARCHITECTURE.md`](../docs/WEB_ARCHITECTURE.md).

---

## Phase 0 (skeleton) — quick start

### 1. Install web deps in the main venv

```bash
# from project root
source .venv/bin/activate
uv pip install -e ".[web]"             # adds FastAPI, SQLAlchemy[asyncio], Alembic, structlog ...
```

### 2. Start PostgreSQL

```bash
cd web
docker compose -f docker-compose.dev.yml up -d
docker compose -f docker-compose.dev.yml logs -f postgres   # confirm "ready to accept connections"
```

This exposes Postgres on **localhost:5433** (port shifted to avoid host PG clashes).

### 3. Initialise the database

```bash
# Copy env defaults to project root .env if you don't have one yet
cp web/.env.example .env

# Apply migrations (Phase 0 has none yet — runs cleanly)
cd web/backend
alembic upgrade head
cd ../..
```

### 4. Run the backend

```bash
# from project root
uvicorn web.backend.app.main:app --reload --port 8000
```

Verify:
- http://localhost:8000/healthz → `{"status":"ok",...}`
- http://localhost:8000/api/docs → Swagger UI

### 5. Run the frontend

```bash
cd web/frontend
pnpm install                           # or npm install
pnpm dev
```

Open **http://localhost:5173** — you should see two green status dots indicating backend + API are reachable.

### 6. (Optional) Use the friendly local domain

```bash
echo "127.0.0.1 openquant.local" | sudo tee -a /etc/hosts
caddy run --config web/Caddyfile.dev
```

Then visit **http://openquant.local** instead of `localhost:5173`.

---

## Project layout

```
web/
├── README.md                  ← you are here
├── docker-compose.dev.yml     postgres only
├── Caddyfile.dev              optional local reverse proxy
├── .env.example
├── backend/
│   ├── alembic.ini
│   ├── alembic/               db migration scripts
│   └── app/
│       ├── main.py            FastAPI entrypoint
│       ├── core/              config, logging
│       ├── db/                async SQLAlchemy session
│       ├── models/            declarative base + tables
│       └── api/               health + v1 routers
└── frontend/
    ├── package.json
    ├── vite.config.ts         ← proxies /api and /healthz to localhost:8000
    ├── tailwind.config.ts     ← A-share 红涨/绿跌 token mapping
    └── src/
        ├── main.tsx           React Query provider
        ├── App.tsx            Phase 0 hello + health probe
        ├── index.css          CSS tokens (dark + light)
        └── lib/api.ts         axios wrapper
```

---

## Phase roadmap

| Phase | Content | Status |
|-------|---------|--------|
| **0** | Skeleton: FastAPI ↔ Vite hello, Postgres up, Tailwind tokens | **← current** |
| 0.5 | User / refresh_token / audit_log models, JWT auth, login page | TODO |
| 1   | Dashboard, Strategies list, Holdings view (NAV chart) | TODO |
| 2   | Backtest runner (SSE log stream), Factors lab, Data Health | TODO |
| 3   | Admin: users, audit, alerts, ack workflows | TODO |
| 3.5 | Caddy + Cloudflare Tunnel + production .env | TODO |
| 4   | Live trading: OMS, fills, risk dashboard (after QMT) | TODO |

---

## Conventions

- **Dark theme by default** — A-share quant ops happen at night
- **Red 上涨 / Green 下跌** — opposite of US/EU; CSS vars in `index.css`
- **All numbers** use `tabular-nums` class for vertical alignment
- **Locales**: `zh-CN` (default) + `en`, stored per-user in `users.locale`
- **All write actions** must record a row in `audit_log`

---

## License

Apache 2.0 — same as the main project.
