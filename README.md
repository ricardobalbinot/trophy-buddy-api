# Trophy Buddy API

The Python REST service behind Trophy Buddy. It imports game libraries and trophies from public PlayStation profiles, enriches game details with catalogue and guide data, and persists results in PostgreSQL.

[Frontend repository](https://github.com/ricardobalbinot/trophy-buddy) · [Live app](https://trophy-buddy.vercel.app/)

## What the service does

- Resolves public profiles by PSN Online ID and imports the avatar, account-level trophy totals, and game progress.
- Stores each signed-in user's library under their verified Firebase UID. Libraries can track any public PSN profile accessible to the service account.
- Publishes a library import in one database transaction. Database leases coordinate concurrent imports and keep incomplete imports from replacing saved data.
- Stores shared trophy definitions separately from player progress and reuses game details for the current library snapshot.
- Adds optional IGDB metadata, edition-aware guide links, and curated resources where available.

## Technology

| Area | Stack |
| --- | --- |
| HTTP API | Python, FastAPI, Pydantic, HTTPX |
| Persistence | PostgreSQL, SQLAlchemy, psycopg, Alembic |
| Identity | Firebase Admin token verification and an encrypted PSN service session |
| Tooling | uv, pytest, Ruff, mypy, GitHub Actions |
| Deployment | Docker and Railway |

## Run locally

Use Python 3.12 or 3.13, uv, and PostgreSQL. Copy `.env.example` to `.env` and set `DATABASE_URL`. To run PostgreSQL with Docker, set `POSTGRES_PASSWORD` and use `docker compose up -d postgres`. The bundled service listens on host port 5433.

For authenticated library routes, configure the Firebase project and service account and allow the frontend origin with `CORS_ORIGINS`. PSN imports require `PSN_SERVICE_NPSSO` to bootstrap a dedicated service session and a stable `PSN_TOKEN_ENCRYPTION_KEY` containing 32 random bytes encoded in Base64. Configure IGDB credentials for optional catalogue enrichment. Store these credentials in the API environment.

```sh
uv sync --locked
uv run alembic upgrade head
uv run uvicorn trophy_buddy_api.app:create_app --factory --host 127.0.0.1 --port 8000
```

Open [Swagger UI](http://127.0.0.1:8000/docs) to explore the OpenAPI contract.

## API overview

| Method and path | Purpose |
| --- | --- |
| `GET /healthz` and `GET /readyz` | Liveness and database readiness |
| `GET /api/v1/library` | Read the saved library |
| `GET /api/v1/library/statistics` | Read library statistics |
| `POST /api/v1/library/sync` | Import the profile specified by `onlineId` |
| `POST /api/v1/library/games/{game_id}` | Load trophy progress, catalogue data, and available guides |
| `GET /api/v1/catalog` | Look up public game metadata |

Library endpoints require a Firebase bearer token. The API derives the storage UID from that token and resolves game details from the saved library.

## Checks

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

Set `TEST_DATABASE_URL` to a dedicated database with a name ending in `_test` to run the PostgreSQL integration tests.

See the [architecture](docs/architecture.md), [data model](docs/data-model.md), and [Railway deployment guide](docs/railway-deployment.md) for implementation and deployment details.
