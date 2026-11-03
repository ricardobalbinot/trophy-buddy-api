# Railway deployment

Deploy the independent FastAPI service and PostgreSQL in one Railway project. The Next.js frontend remains on Vercel. No local Docker installation is required: Railway builds the repository's Dockerfile.

## 1. Publish the API branch

The repository must be available to the Railway GitHub integration with the migration commits pushed to `master`. Local commits are not visible to Railway. The frontend migration is a separate repository and must also be published before switching the production UI to this API.

## 2. Create the project and database

1. Sign in to Railway and connect GitHub. Authorize access to `ricardobalbinot/trophy-buddy-api`.
2. Create an empty project named `trophy-buddy`.
3. Add a PostgreSQL database service. Keep its name `Postgres` for the reference below, or adapt the reference to its actual service name.
4. Add a service from the GitHub repository `ricardobalbinot/trophy-buddy-api`.
5. In the API service's source settings, select `master`. The project root is the repository root; do not enter the frontend folder or `src` as root.
6. Put API and database in the same region. Use the database's internal connection for the API; no public database endpoint is needed for application traffic.

An initial automatic attempt can fail before variables are configured. Configure them, then trigger a new deployment. Do not copy the local database URL: it points at this workstation.

## 3. Configure API variables

In the **API service**, add:

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` using Railway's variable reference picker |
| `FIREBASE_PROJECT_ID` | Existing Firebase project ID |
| `FIREBASE_CLIENT_EMAIL` | Existing Firebase Admin service-account email |
| `FIREBASE_PRIVATE_KEY` | Existing private PEM key, preserving real newlines or literal `\n` escapes |
| `CORS_ORIGINS` | JSON list containing the exact deployed frontend origin, e.g. `["https://your-app.vercel.app"]` |
| `PSN_SERVICE_NPSSO` | Valid NPSSO for the dedicated technical PSN identity |
| `PSN_TOKEN_ENCRYPTION_KEY` | Existing private base64-encoded 32-byte encryption key; keep it stable |
| `IGDB_CLIENT_ID` | Twitch application Client ID; optional metadata integration |
| `IGDB_CLIENT_SECRET` | Twitch application Client Secret; optional metadata integration |

Use the private API `.env` as the source for existing credentials, without pasting them into chat or Git. In the dashboard, paste actual variable values without the surrounding dotenv serialization quotes. For `CORS_ORIGINS`, preserve the JSON array itself. The API accepts standard Railway `postgresql://` URLs and explicitly selects psycopg.

Do not set the workstation's `GOOGLE_APPLICATION_CREDENTIALS` file path in Railway. Use the service-account fields above. `TEST_DATABASE_URL` and `POSTGRES_PASSWORD` from the local Compose setup are not API production settings. Railway manages its database service variables separately.

If local browser access to the cloud API is needed, explicitly include `http://localhost:3000` in `CORS_ORIGINS`. Avoid a wildcard. Production CORS does not need the Railway API's own origin; it needs the frontend's browser origin.

## 4. Configure deployment settings

The current Railway dashboard reports that Config as Code (`railway.toml` / `railway.json`) is deprecated and unavailable to new services. Existing legacy configurations stop being read on 2026-12-01. A syntactically valid legacy file does not configure a new service. The obsolete repository file has been removed to avoid implying that migrations and health checks are automatic without setup.

In the API service's **Settings**, configure and save:

| Setting | Value |
| --- | --- |
| Builder | Dockerfile, detected from repository root |
| Pre-deploy command | `/app/.venv/bin/alembic upgrade head` |
| Start command | `sh -c 'exec /app/.venv/bin/uvicorn trophy_buddy_api.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --no-access-log'` |
| Healthcheck Path | `/readyz` |
| Healthcheck Timeout | `120` seconds |
| Restart Policy | On Failure |
| Max restart retries | `3` |
| API variable `PORT` | `8000`, also used as the generated domain's target port |

Numeric fields can require clicking their checkmark/Save control; typing a value alone is not sufficient. Review staged changes and apply them with Deploy.

Railway's replacement is project-level Infrastructure as Code, evaluated through `railway config plan/apply`, rather than a per-service TOML file read on every Git push. This initial deployment uses persisted dashboard settings. A future IaC import should preserve secret values rather than embedding them in source.

No custom build command is needed. Do not run migrations during image build: the database belongs to the deployment's private network and credentials are runtime configuration. The pre-deploy command must succeed before Railway starts the new release.

In service settings, enable **Wait for CI**. The GitHub workflow already runs on pushes to `master` and checks Python 3.12/3.13 with PostgreSQL tests, Ruff, mypy and a Docker build. Railway's CI gate and Alembic pre-deploy step serve different purposes; retain both.

## 5. Generate the public URL and verify

1. In the API service, open **Settings → Networking → Public Networking → Generate Domain**.
2. Use the application's Railway `PORT` if asked for a target port. The start command uses that same value.
3. Visit `https://YOUR-API-DOMAIN/healthz`: process status should be `ok`.
4. Visit `/readyz`: status should be `ready`, proving access to a migrated PostgreSQL table.
5. Visit `/docs`: Swagger should show the library, synchronization, trophy details and catalogue endpoints.

A public `/api/v1/library` request without a Firebase token should return 401. Swagger being available does not make private endpoints anonymous. Readiness does not validate Firebase, PSN or IGDB; the authenticated end-to-end check below does.

## 6. Connect the frontend

1. Publish the frontend's Python API integration changes in its own repository.
2. In Vercel, set `NEXT_PUBLIC_LIBRARY_API_URL=https://YOUR-API-DOMAIN` without `/api/v1`.
3. Keep the existing `NEXT_PUBLIC_FIREBASE_*` identifiers.
4. Redeploy the frontend: public environment variables are embedded at build time.
5. Verify the frontend origin is allowed by both API CORS and Firebase Authentication authorized domains.
6. Sign in and synchronize a public profile; the new cloud database starts without libraries. Open a game, navigate back and reopen it to verify detailed snapshot reuse.

After retiring the old frontend deployment, remove its obsolete PSN/IGDB/Firebase Admin secrets from Vercel. They belong in the API service. Firebase authorized domains concern the site performing login; adding the API domain is unnecessary for bearer-token verification alone.

## 7. Confirm ongoing delivery and persistence

After setup, push to `master` → GitHub Actions → Railway waits for CI → Alembic migrations → readiness check → new API release. The frontend has its independent Vercel pipeline.

Enable database volume backups, review retention and monitor Railway resource usage. Keep application data on the PostgreSQL service's persistent volume, not the API container filesystem. Redeploy the API and confirm the saved library/session survive. Do not change the encryption key casually: existing encrypted sessions need the matching key.

On the current workspace, the Backups page requires upgrading to Pro to create backups, schedule them or enable PITR. Automatic backups remain unconfigured; no plan upgrade was performed. Persistent storage is not a backup. Choose a paid backup option or a separately designed encrypted off-platform backup before relying on this deployment for irreplaceable data.

## Verified deployment (2026-11-03)

- Project: `trophy-buddy`, production environment.
- API: [Swagger](https://trophy-buddy-api-production.up.railway.app/docs), [readiness](https://trophy-buddy-api-production.up.railway.app/readyz).
- GitHub source: this repository's `master`; Wait for CI enabled.
- Database: Railway PostgreSQL 18 template with persistent volume, private networking and no public database endpoint. Local and CI integration tests currently use PostgreSQL 17; live migrations and product checks also succeeded on the deployed PostgreSQL 18 instance.
- Allowed browser origins: `https://trophy-buddy.vercel.app` and `http://localhost:3000`.
- Firebase-authenticated cloud synchronization imported 290 games, including 19 unknown-progress entries. Persona 5 Royal returned 53 trophies and an IGDB match; repeat detail loading reused the stored snapshot. Anonymous library access returned 401 and Vercel-origin preflight returned 200.
- The frontend migration is still local in its separate repository. Updating Vercel configuration alone cannot publish those frontend changes.

## Troubleshooting

- **Pre-deploy fails:** check database reference, service name, database availability and Alembic logs. Do not bypass a failed migration to publish the app.
- **Invalid CORS configuration at startup:** `CORS_ORIGINS` must be a JSON array, not a comma-separated string or empty value.
- **Authentication returns 503:** check the Firebase project and PEM formatting; never expose the private key in diagnostics.
- **`psn_reconnect_required` on first synchronization:** the cloud database has no local renewable session. Generate a fresh dedicated-account NPSSO, update only the API service variable and redeploy. No end-user PSN credentials are involved.
- **PSN succeeds locally but fails in the cloud:** inspect sanitized error/status and request ID. Validate provider connectivity and the new technical session separately; local success does not establish cloud access.
- **Browser CORS error:** match the exact frontend scheme/host/port and redeploy API settings; verify the API itself responds before blaming CORS.

## References

- [FastAPI deployment](https://railway.com/guides/fastapi)
- [Infrastructure as Code and legacy configuration deprecation](https://docs.railway.com/infrastructure-as-code)
- [Pre-deploy commands](https://docs.railway.com/deployments/pre-deploy-command)
- [GitHub autodeploys and Wait for CI](https://docs.railway.com/deployments/github-autodeploys)
- [PostgreSQL](https://docs.railway.com/databases/postgresql)
- [Public networking](https://docs.railway.com/networking/public-networking)
