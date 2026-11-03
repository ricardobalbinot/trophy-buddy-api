# Trophy Buddy API

Independent Python REST service for Trophy Buddy. This project has its own dependencies, environment, tests, CI workflow and container image. It does not require Node.js or read files from the frontend project.

## Current implementation

- FastAPI and Pydantic with generated OpenAPI and Swagger UI.
- Firebase token verification with signature, expiry and revocation checks.
- Private library reads scoped exclusively to the verified Firebase UID.
- Trophy statistics separating unknown progress from confirmed zero and platinum from DLC completion.
- Explicit CORS origins, private cache headers, bounded Firebase calls and request correlation.
- pytest API/security tests, Ruff, mypy, uv and a committed lockfile.

This first migration slice reads the existing Firestore snapshots directly. The frontend's temporary Next.js synchronization code still writes those snapshots. PostgreSQL and Python PSN workflows are subsequent deliveries; the API is not a proxy to Next.js.

## Local setup

Install Python 3.12 or 3.13 and [uv](https://docs.astral.sh/uv/getting-started/installation/), then run from this project root:

```sh
uv sync --locked
uv run uvicorn trophy_buddy_api.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

On Windows, after the environment exists, the equivalent startup command does not require uv on PATH:

```powershell
.\.venv\Scripts\python.exe -m uvicorn trophy_buddy_api.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Restart the service after source or environment changes. Run the React frontend separately with its own package manager. The services communicate through HTTP, not filesystem imports or a shared development script.

Swagger: http://localhost:8000/docs. Health: http://localhost:8000/healthz. Health checks process liveness only, not cloud availability.

## Configuration

Copy `.env.example` to this project's private `.env` and fill `FIREBASE_PROJECT_ID` plus either service-account email/private key or `GOOGLE_APPLICATION_CREDENTIALS`. No frontend environment file is read. Never send private key values through chat or commit them.

`CORS_ORIGINS` accepts a JSON list, defaulting to `["http://localhost:3000"]`. Configure the deployed frontend origin explicitly in hosted environments. The frontend may set its optional `NEXT_PUBLIC_LIBRARY_API_URL` to this service origin. Do not point the deployed frontend at localhost.

## REST contract

| Method | Path                         | Authentication  | Purpose                                 |
| ------ | ---------------------------- | --------------- | --------------------------------------- |
| GET    | `/healthz`                   | Public          | Process liveness                        |
| GET    | `/api/v1/library`            | Firebase bearer | Existing `{ "snapshot": ... }` contract |
| GET    | `/api/v1/library/statistics` | Firebase bearer | Confirmed library statistics            |
| GET    | `/docs`                      | Public          | Swagger and OpenAPI contracts           |

Missing, invalid, expired, revoked or disabled sessions return 401. Provider configuration/network and stored-data failures return a controlled 503. Responses include `X-Request-ID` and `Cache-Control: private, no-store`. A missing library returns a null snapshot, distinct from a failed request.

Synchronous Firebase SDK calls use regular `def` endpoints/dependencies, which FastAPI runs in its thread pool. Merely changing blocking SDK calls to `async def` would block the event loop. Native async HTTP adapters and durable workers follow later.

## Checks

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

API tests use HTTPX's ASGI transport with the real application factory, routing, authentication dependencies, serialization and domain rules. Fake provider boundaries require no live credentials. Dedicated SDK-boundary tests check revocation options and provider failure behavior.

## Container

```sh
docker build -t trophy-buddy-api .
docker run --rm -p 8000:8000 --env-file .env trophy-buddy-api
```

Use service-account fields or mount credential JSON read-only and set its **container** path. A host Windows path does not exist inside a Linux container. Private env files and credential JSON are excluded from the image. The process runs as a non-root user.

The Dockerfile and GitHub Actions workflow are prepared; container execution and hosted CI need their own validation before being claimed as passing. This service deploys independently from the Vercel frontend. Cloud provisioning and paid infrastructure require a separate deployment decision.

See [architecture and migration roadmap](docs/architecture.md) for PostgreSQL, Temporal, Bedrock, observability and the interview demonstration.
