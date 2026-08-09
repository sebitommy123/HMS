# DataPro AI

The AI plane of DataPro (see `../datapro_revised_v2.md` § DataPro AI). A Flask
HTTP service that holds chat conversations between an operator and Claude,
where Claude has tool-use access to DataPro Core.

## Architecture rules this service follows

- **All LLM calls live here.** Core never calls Anthropic; the UI never holds
  the API key. AI is the only thing with one.
- **Single-instance, like Core.** Same reasoning (`../core/README.md`
  § Deployment model). One process per deployment.
- **No mocks in tests.** Unit tests hit the live Core via testcontainers; live
  tests hit the real Anthropic API. Marked `live` so they can be skipped or
  opted into.

## Stack

- **Python 3.12 + uv** (matches `core/`)
- **Anthropic SDK direct** — no LangChain, no LangGraph (per the discussion
  in the planning thread; LangGraph stays in reserve for the multi-step
  catalog-building agent later, where its sweet spot actually matters)
- **Flask + SQLAlchemy + Alembic + Pydantic** (matches `core/`)
- **Postgres** — separate from Core's Postgres, on `:5434`

## What's in here

```
ai/
├── src/datapro_ai/
│   ├── app.py                   # Flask factory
│   ├── config.py                # env-driven config; defaults to claude-opus-4-8
│   ├── db.py                    # SQLAlchemy engine + session
│   ├── models.py                # Conversation + Message
│   ├── api/
│   │   ├── health.py            # GET /health
│   │   ├── conversations.py     # CRUD on /conversations
│   │   └── messages.py          # POST /conversations/{id}/messages
│   └── llm/
│       ├── agent.py             # The manual agent loop
│       └── tools/
│           ├── base.py          # Tool protocol + registry
│           ├── list_catalogs.py
│           └── run_raw_trino_query.py
├── alembic/                     # Postgres migrations
├── tests/
│   ├── unit/                    # ~14 tests, Postgres via testcontainers, hits live Core
│   └── integration/             # `live` marker — needs ANTHROPIC_API_KEY + Core
├── docker-compose.yml           # AI's Postgres (and AI itself)
├── Dockerfile
├── Makefile
├── alembic.ini
└── pyproject.toml
```

## Data model

Two tables: `conversations` and `messages`. The interesting design choice is
that `messages.content` stores Anthropic's **content blocks verbatim** as JSON
— `text`, `tool_use`, `tool_result`, `thinking`, etc. — so we can replay
history straight back into the API without a translation layer, and the UI
walks the same JSON to render tool calls inline.

Tool invocations are **not** a separate table. They live inside `messages.content`
as `tool_use` blocks on assistant turns and `tool_result` blocks on user turns.
One source of truth per turn.

## Tools available to Claude

Phase 0 ships two:

- **`list_catalogs`** — calls `GET /catalogs` on Core
- **`run_raw_trino_query`** — calls `POST /raw-trino-query` on Core, with the same Core-enforced
  timeout + max-rows caps

Both are concrete classes implementing the `Tool` protocol in
`llm/tools/base.py`. Adding more is a one-class job.

## Agent loop

`llm/agent.py` runs a manual loop instead of using the SDK's tool runner so
that we can **persist every step** to Postgres as it happens — user input,
assistant turn (with tool_use blocks), tool results, and the next assistant
turn. The loop terminates on `end_turn`, `refusal`, `max_tokens`, or after a
configurable iteration cap (default 10).

Per the `claude-api` skill: model defaults to `claude-opus-4-8`, adaptive
thinking is on, and `effort` defaults to `high`.

## API surface

| Method | Path | What |
|---|---|---|
| `GET` | `/health` | Reports Postgres, Core reachability, and whether `ANTHROPIC_API_KEY` is configured |
| `GET` | `/conversations` | List with `message_count` + preview |
| `POST` | `/conversations` | Create — optional `title`, `model`, `system_prompt` |
| `GET` | `/conversations/{id}` | Detail with full message transcript |
| `PATCH` | `/conversations/{id}` | Update title or system prompt |
| `DELETE` | `/conversations/{id}` | Delete (cascades to messages) |
| `POST` | `/conversations/{id}/messages` | Send a user message; runs the agent loop synchronously and returns every new message produced this turn |

## Quickstart

```bash
# 1. From this directory: install deps
make install

# 2. Start AI's Postgres
make up

# 3. Apply migrations
make migrate

# 4. Provide an Anthropic API key
export ANTHROPIC_API_KEY=sk-ant-...

# 5. Make sure Core is running (separate shell, separate compose)
cd ../core && make up && make migrate
uv run flask --app datapro_core.app run --host 0.0.0.0 --port 5001

# 6. Run AI
uv run flask --app datapro_ai.app run --host 0.0.0.0 --port 5002

# 7. Smoke test
curl http://127.0.0.1:5002/health
CID=$(curl -s -X POST http://127.0.0.1:5002/conversations \
  -H 'content-type: application/json' \
  -d '{"title":"Demo"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
curl -s -X POST http://127.0.0.1:5002/conversations/$CID/messages \
  -H 'content-type: application/json' \
  -d '{"text":"What catalogs are registered? Then run SHOW CATALOGS via SQL to confirm."}' \
  | python3 -m json.tool
```

The response includes every message produced this turn — the user input, every
assistant `tool_use`, every `tool_result`, and the final assistant text.

## Test split

| Layer | Tool | Where | Needs |
|---|---|---|---|
| Unit (Postgres + Core, no LLM) | pytest + testcontainers | `tests/unit/` | Docker + Core running |
| Live (Postgres + Core + Anthropic API) | pytest with `live` marker | `tests/integration/` | + `ANTHROPIC_API_KEY` |

```bash
make test         # unit tests only — runs without a key
make test-live    # full live loop — needs ANTHROPIC_API_KEY and Core running
```

Live tests cost real Anthropic API tokens (Opus-tier). They're opt-in.

## Where to next

- **Streaming** — currently the POST blocks until the loop terminates. SSE
  would let the UI render tool calls as they happen.
- **Chat UI in `../ui/`** — the API surface is ready; needs a conversation
  list + chat view.
- **More tools** — `get_catalog`, `inspect_table`, `recent_query_history`,
  the future PATCH endpoint, etc. Just drop another class into `llm/tools/`
  and register it in `default_tools()`.
- **System-prompt presets** — operator-curated templates for common workflows.
- **Multi-step catalog-building agent** — the natural fit for LangGraph,
  separate from the chat surface.
