# Project conventions

- Keep code, filenames, identifiers, comments and API error codes in English. User-facing product copy is localized by the React frontend with pt-BR as its source locale.
- This is an independent Python REST service. Do not read configuration, source code or credentials from the sibling frontend project. Load private settings only from this project's .env or process environment.
- Use uv with the committed lockfile, FastAPI, Pydantic, Ruff, mypy and pytest. Use HTTPX for direct application HTTP requests in Python; do not block async endpoints with synchronous SDK calls.
- Keep imports together without blank lines between groups. Use English docstrings for significant boundaries and non-obvious behavior. Keep route handlers small and domain rules independent from framework objects.
- Any authenticated Firebase user may access their own library. Verify signature, expiration and revocation. Derive storage UIDs exclusively from verified tokens, never client input. Keep provider errors, private configuration and bearer tokens out of responses and logs.
- The current slice reads existing Firestore snapshots. Do not introduce independent PostgreSQL writes until the data migration and source-of-truth switch are implemented. Preserve unknown trophy progress, official PSN percentages and exact snapshot timestamps.
- PSN service credentials belong to a dedicated technical identity, separate from the public profile being consulted. This first Python slice does not consume PSN credentials.
- Keep .env and credential JSON private and Git-ignored. Keep .env.example with empty onboarding values. Containers must never include private environment or credential files.
- Run uv run ruff check ., uv run ruff format --check ., uv run mypy and uv run pytest before delivery. Validate Docker and hosted CI separately before claiming they passed.
- Keep planned technologies distinct from working integrations in documentation. Do not create commits, branches or pushes unless explicitly requested.
