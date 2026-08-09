# DataPro UI

The presentation plane of DataPro (see `../datapro_revised_v2.md` § DataPro UI).

A React SPA served as static assets. Talks to **Core** (and eventually AI) over HTTP.
No backend of its own — `pnpm build` produces a static `dist/` you can serve from
any web server, CDN, or object store.

## Vocabulary

- **Connector** — a Trino plugin (`postgresql`, `tpch`, …). UI never picks one
  arbitrarily; the catalog form constrains the choice.
- **Catalog** — a registered, configured instance Core manages. What the UI
  shows, creates, and deletes.

## Stack

- **React 18 + TypeScript** (Vite)
- **Tailwind v4** + shadcn-style design tokens (light mode only)
- **TanStack Query** for server state
- **React Router 6** for routing
- **Zod** for runtime schema validation of API responses
- **react-hook-form + @hookform/resolvers** for forms
- **Vitest + Testing Library** for unit + component tests
- **Playwright** for end-to-end browser tests against live Core

## Where it talks to

| Env var | Default | Used at |
|---|---|---|
| `VITE_CORE_URL` | `http://127.0.0.1:5001` | build time (baked into the bundle) |

To deploy against a different Core, rebuild with the env var set:

```bash
VITE_CORE_URL=https://core.mycompany.internal pnpm build
```

## Quickstart

```bash
# 1. Install deps
pnpm install

# 2. Make sure Core is running (in a separate shell)
cd ../core && make up && make migrate
uv run flask --app datapro_core.app run --host 0.0.0.0 --port 5001

# 3. Start the UI dev server
cd ../ui && pnpm dev
# → http://localhost:5173
```

## Test split

| Layer | Tool | Where | Purpose |
|---|---|---|---|
| Unit | Vitest | `tests/unit/`, `src/**/*.test.ts` | pure logic, schemas, formatters |
| Component | Vitest + RTL (jsdom) | `tests/component/` | components rendered with mocked fetch |
| End-to-end | Playwright | `tests/e2e/` | real browser + live Core + live Trino |

```bash
pnpm test          # unit + component (vitest)
pnpm test:e2e      # playwright (needs Core running)
```

## Deployment model

The UI bundle is intentionally **disconnected from Core's process**. You can:

- Serve `dist/` from a CDN / nginx / S3+CloudFront
- Run multiple UI instances pointing at one Core (different regions, different
  internal teams, etc.)
- Roll the UI forward independently of Core

Core's only awareness of the UI is the CORS allowlist (`CORS_ORIGINS` env var,
defaults to `http://localhost:5173,http://127.0.0.1:5173`).

## Status

**U1 — scaffold + health indicator.** Layout, routing, API client, status bar.
Catalog list / detail / register pages land in U2–U4.
