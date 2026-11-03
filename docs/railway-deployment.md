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

## 4. Deploy with repository configuration

`railway.toml` defines:

- Dockerfile builder.
- Pre-deploy command: `/app/.venv/bin/alembic upgrade head`.
- Uvicorn bound to `0.0.0.0` and Railway's `PORT` environment variable.
- Readiness path `/readyz` with a 120-second startup health-check timeout.
- Restart on failure, with up to three retries.

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

## Troubleshooting

- **Pre-deploy fails:** check database reference, service name, database availability and Alembic logs. Do not bypass a failed migration to publish the app.
- **Invalid CORS configuration at startup:** `CORS_ORIGINS` must be a JSON array, not a comma-separated string or empty value.
- **Authentication returns 503:** check the Firebase project and PEM formatting; never expose the private key in diagnostics.
- **`psn_reconnect_required` on first synchronization:** the cloud database has no local renewable session. Generate a fresh dedicated-account NPSSO, update only the API service variable and redeploy. No end-user PSN credentials are involved.
- **PSN succeeds locally but fails in the cloud:** inspect sanitized error/status and request ID. Validate provider connectivity and the new technical session separately; local success does not establish cloud access.
- **Browser CORS error:** match the exact frontend scheme/host/port and redeploy API settings; verify the API itself responds before blaming CORS.

## References

- [FastAPI deployment](https://railway.com/guides/fastapi)
- [Configuration as code](https://docs.railway.com/config-as-code/reference)
- [Pre-deploy commands](https://docs.railway.com/deployments/pre-deploy-command)
- [GitHub autodeploys and Wait for CI](https://docs.railway.com/deployments/github-autodeploys)
- [PostgreSQL](https://docs.railway.com/databases/postgresql)
- [Public networking](https://docs.railway.com/networking/public-networking)
