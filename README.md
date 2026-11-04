# Trophy Buddy API

Independent Python REST backend for Trophy Buddy. Next.js consumes its REST contracts; it does not access the database or external game providers.

## Implemented

- FastAPI/Pydantic REST contracts and OpenAPI (`/docs`).
- Firebase Authentication token verification, including expiration and revocation; private storage always uses the verified UID.
- PostgreSQL, SQLAlchemy, psycopg and versioned Alembic migrations. No runtime Firestore access.
- Public PSN profile lookup, complete played-game/trophy-title pagination and exact Sony ID association.
- Dedicated PSN technical session, encrypted with AES-256-GCM and renewed through OAuth under a database lease.
- Atomic library publication, cross-process import leases, cooldowns and stale-owner fencing.
- Detailed trophy definitions, earnings, secret flags, artwork and base/DLC groups. Matching snapshots are reused until explicit refresh or library generation change.
- Optional IGDB metadata with unique compatible name/platform matching, OAuth token reuse and a shared PostgreSQL cache. Campaign estimates never represent platinum time.
- Statistics preserving unknown progress and official PSN percentages.
- Controlled errors, request correlation, explicit CORS and private response caching.
- pytest with real PostgreSQL integration tests, Ruff, mypy, Docker packaging and GitHub Actions.

Imports remain synchronous HTTP operations, matching the working MVP. Worker crashes or request timeouts do not publish partial libraries, but an unfinished import does not resume automatically. Temporal, guide ingestion, AI processing, full OpenTelemetry and cloud deployment remain future deliveries.

## Local setup

Use Python 3.12/3.13 and uv. Copy `.env.example` to `.env`, then configure:

- `DATABASE_URL`: `postgresql+psycopg://USER:PASSWORD@HOST:PORT/trophy_buddy`. Standard provider `postgresql://` and `postgres://` URLs also work; the adapter selects psycopg explicitly. URL-encode special password characters.
- Firebase project and service-account fields, or a private JSON path, for Authentication only.
- `CORS_ORIGINS`: a JSON list such as `["http://localhost:3000"]`.
- `PSN_TOKEN_ENCRYPTION_KEY`: a base64-encoded, random 32-byte key. Preserve it across replicas/restarts.
- `PSN_SERVICE_NPSSO`: initial technical-account bootstrap or deliberate administrative reconnection. No PSN email/password is used. Replacing the configured NPSSO intentionally reconnects the technical identity; unchanged bootstrap values are not endless refresh fallbacks.
- Optional IGDB Twitch application credentials.
- `TEST_DATABASE_URL`: a separate PostgreSQL database whose name ends in `_test`. Integration tests clear that database only. Never point it at application data.

```sh
uv sync --locked
uv run alembic upgrade head
uv run uvicorn trophy_buddy_api.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

After uv creates the environment, Windows can use `.\.venv\Scripts\python.exe -m alembic upgrade head` and `.\.venv\Scripts\python.exe -m uvicorn ...` without putting Python or uv on PATH.

For a Docker database, configure `POSTGRES_PASSWORD`, run `docker compose up -d postgres`, and point `DATABASE_URL` at port 5433. The volume persists data; the database is bound only to loopback. When using an API container, configure a database hostname reachable from that container rather than its own localhost.

This workstation also has a user-local PostgreSQL 17 cluster on `127.0.0.1:5433`, installed outside both repositories. `./scripts/local-postgres.ps1 start|stop|status` manages that existing installation; other machines should use Docker Compose or their own PostgreSQL installation. It does not run as an automatically started Windows service.

Run the frontend separately with `NEXT_PUBLIC_LIBRARY_API_URL=http://localhost:8000`. Each project reads only its own private environment. Restart API processes after configuration/source changes.

## REST contracts

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| GET | `/healthz` | Public | Process liveness |
| GET | `/readyz` | Public | Database connectivity and migrated library table |
| GET | `/api/v1/library` | Firebase bearer | Last committed `{snapshot}` or null |
| GET | `/api/v1/library/statistics` | Firebase bearer | Confirmed statistics |
| POST | `/api/v1/library/sync` | Firebase bearer | `{onlineId}` to imported `{snapshot}` |
| POST | `/api/v1/library/games/{gameId}` | Firebase bearer | `{refresh: false}` to cached/imported `{details}` |
| GET | `/api/v1/catalog?title=...&platform=PS5` | Public | Optional bounded public metadata for previews |

Supplying an Online ID never proves PSN profile ownership. Target account IDs and game identities are resolved from the authenticated user's persisted library, never accepted as storage selectors from the client. Missing/revoked sessions return 401; invalid request bodies 422; missing profiles/games 404; active leases/cooldowns/provider rate limits 429; unavailable dependencies 503. Failures do not return provider requests or credentials. Correlation IDs are returned in `X-Request-ID`.

Firebase and SQLAlchemy/HTTPX calls are synchronous and execute in FastAPI's thread pool using regular `def` handlers. Switching these handlers to `async def` without async adapters would block the event loop. Database transactions never span provider requests.

## Data model

`users` holds product identity through the verified Firebase UID and an optional public display name. Rows are created on successful library imports; a profile editor is not implemented yet. `psn_profiles` holds independent public PSN identities. `libraries` binds one user to one saved public profile; this association is not proof of ownership. `library_games` stores relational identity/progress columns, version-specific Sony title IDs and nullable timezone-aware activity dates. Compound keys, foreign keys and checks protect isolation and valid progress ranges.

`GET /api/v1/library` defaults to `sort=last_played_at&direction=desc`. Optional sort values are `last_played_at`, `trophy_progress_updated_at` and `title`; direction accepts `asc` or `desc`. PostgreSQL orders unknown values last and breaks ties by game ID. `lastPlayedAt` and `trophyProgressUpdatedAt` are returned as nullable ISO timestamps. A trophy progress update may represent a first sync with zero earnings, not an earned trophy. Existing rows retain unknown dates until the next PSN sync; former list positions cannot reconstruct them.

`game_details` retains validated import metadata. Shared `trophy_sets`, `trophy_groups` and `trophies` hold definitions; `profile_trophies` holds nullable earnings scoped to each owner/game import and its PSN target. `detail_trophy_groups` preserves observed group membership. A reversible migration removes trophy arrays from JSONB after relational backfill. Old observations still protect known earnings during failed refreshes, and only matching account/generation imports are reused. Removed library entries cascade to private observations while shared definitions survive.

`catalog_cache` shares public IGDB metadata only. `operation_leases` coordinates application imports and refreshes. `service_sessions` contains encrypted technical tokens; its encryption key lives outside PostgreSQL. New product domains such as guide versions and personal progress will have their own relational models when implemented.

`guide_index_versions`, `indexed_guides` and `guide_bindings` provide versioned external guide metadata with explicit review against Sony identities. `trophy_resources` stores published curated videos/articles independently of PSN progress. Details include optional `guide` metadata and per-trophy `resources`; older frontend contracts remain compatible. See [guide ingestion](docs/guide-ingestion.md) for operator commands, uncertainty rules and the remaining roadmap, and [data relationships](docs/data-model.md) for the current diagram. No provider or LLM request is needed to read these persisted guide fields.

There is no Firestore migration requirement: users rebuild libraries with a new PSN synchronization. Existing cloud Firestore documents are untouched and no longer read or written by application code. The workstation's existing encrypted technical session was relocated once to avoid an unnecessary service reconnection; no Firestore adapter remains in the application.

## Verification and deployment

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run alembic check
```

PostgreSQL tests apply real Alembic migrations and verify concurrent leases, fencing, foreign keys, rollback and user isolation. They skip only when no test database is configured; CI supplies PostgreSQL explicitly. Run PostgreSQL tests before release.

```sh
docker build -t trophy-buddy-api .
# Run migrations once as a release step, with deployment-provided environment secrets:
docker run --rm --env-file .env trophy-buddy-api /app/.venv/bin/alembic upgrade head
docker run --rm -p 8000:8000 --env-file .env trophy-buddy-api
```

The image excludes private environments/credential JSON and runs as a non-root user. Mount credential JSON read-only if using it; Windows host paths are not Linux container paths. Configure HTTPS, public API origin, CORS, secrets and PostgreSQL networking for deployment. Vercel alone does not deploy this API or PostgreSQL. No paid infrastructure or new hosted deployment was provisioned by this migration.

See [architecture](docs/architecture.md) and [ADR 001](docs/adr-001-data-and-service-boundaries.md).

For public deployment, follow the [Railway setup guide](docs/railway-deployment.md). New Railway services do not consume legacy `railway.toml` files. Configure the Docker build, Alembic pre-deploy command, dynamic HTTP port and readiness check in service settings. The guide records those values and the verified deployment; Railway's newer project-level IaC is a separate future infrastructure improvement.

## Local migration evidence (2026-11-03)

Ruff, format checking, strict mypy, all 42 pytest tests (including real PostgreSQL), Alembic schema comparison and Python source/wheel builds passed locally. Live Firebase-authenticated HTTP requests imported 290 games, preserving 19 unknown-progress entries; Persona 5 Royal returned 53 trophies and subsequent navigation reused its snapshot. ASTRO BOT returned 63 trophies. A real PSN refresh persisted a renewed encrypted session. Anonymous private-library access returned 401. React library/detail rendering was checked in Chrome; frontend lint, type checking, i18n, all nine tests and production build passed.

Docker execution and the updated hosted CI/deployment have not been validated by this local change. This evidence establishes local functionality, not a production availability claim.
