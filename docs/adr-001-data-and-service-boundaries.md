# ADR 001: Product storage and service responsibilities

Date: 2026-11-03

Status: Accepted. The user authorized removing Firestore and migrating all existing Node backend functionality to Python on 2026-11-03. PostgreSQL is now the only runtime product store.

## Original context

The MVP stores imported libraries and detailed trophies as Firestore snapshots. Python currently verifies Firebase sessions and reads these snapshots, while Next.js still owns synchronization and external integrations. This is a temporary migration boundary.

The product roadmap connects users, public PSN profiles, platform-specific releases, trophy sets, guide versions, source evidence and personal checklist progress. It needs consistent updates, explicit uniqueness, version history and queries across those relationships. There is no current requirement for direct browser database access, offline synchronization or Firestore subscriptions.

## Decision

Use PostgreSQL as the authoritative product database, owned exclusively by the Python backend. Retain Firebase Authentication as the identity provider. Retire Firestore from the active application after a verified migration and rollback window. Do not add permanent dual writes or divide related product entities between Firestore and PostgreSQL without a separate, demonstrated requirement.

Keep a modular Python backend in its independent repository, with API and worker processes sharing domain/application modules. These deployment processes do not require separate business microservices.

## Storage ownership

| Information | Target owner and storage | Important boundary |
| --- | --- | --- |
| Google login, authentication identity and login lifecycle | Firebase Authentication | Python verifies Firebase ID tokens; authentication does not require Firestore. |
| Application users, preferences and permissions | Python / PostgreSQL | An internal user ID maps uniquely to the verified Firebase UID. |
| Public PSN profiles and a user's saved profile associations | Python / PostgreSQL | A saved association is not proof that the user owns the PSN profile. |
| Games, platform releases, external IDs and catalogue matches | Python / PostgreSQL | Preserve distinct PSN releases/trophy sets; uncertain IGDB matches remain unlinked. |
| Trophy definitions, groups and imported public progress | Python / PostgreSQL | Definitions and observed profile progress are distinct from personal app actions. |
| Library generations, import operations and publication state | Python / PostgreSQL | Only a successfully completed generation replaces the visible library. |
| Guide sources, versions, extracted claims, evidence and review state | Python / PostgreSQL | Published guides are shared; publication is explicit and versioned. |
| Personal checklists, checkpoints and spoiler preferences | Python / PostgreSQL | Scope private application state to the authenticated user. |
| Renewable PSN service session | Python / encrypted PostgreSQL record | Separate technical identity; restricted access, expiry and atomic refresh coordination. |
| Encryption keys, bootstrap credentials and provider secrets | Deployment secret manager | Keep encryption keys outside the database containing encrypted sessions. |
| Large permitted source documents or processing artifacts, if needed | Object storage, with PostgreSQL metadata | Add object storage when actual artifact size/lifecycle requires it. |
| Durable workflow execution state, when Temporal is introduced | Temporal-managed persistence | Application operations and their public status remain represented in PostgreSQL. |
| Historical MVP library/detail snapshots | Existing Firestore documents, inactive | Not read or written by the application; no migration required. |

Use relational columns, foreign keys, unique constraints and transactions for identity and business invariants. Scope personal records through the verified user; never accept a storage UID from a request body. Public provider facts can be reusable across saved profiles, but access to private saved state and actions remains user-scoped. Future sharing requires an explicit access policy.

Use JSONB selectively for versioned provider payloads and validated extraction documents whose shape varies. Keep fields used for joins, uniqueness, filtering and authorization relational. JSONB is not a substitute for designing the domain, and flexible JSON does not by itself require another database. Preserve unknown progress separately from confirmed zero and preserve provider timestamps/percentages through migration.

PostgreSQL adds connection management, schema migrations, backups and operational cost. Use SQLAlchemy, psycopg and Alembic, bounded connection pools sized across all API/worker replicas, and PostgreSQL integration tests. Do not hold database transactions open while making PSN, IGDB or AI requests.

## Next.js and Python boundaries

| Responsibility | Next.js | Python API / workers |
| --- | --- | --- |
| Routes, React components, accessibility, localization and design system | Owns | Returns stable contracts/error codes |
| Rendering, metadata and web-specific presentation | Owns | Supplies application data |
| Google login UI and Firebase client session | Owns | Verifies tokens and authorizes every protected operation |
| Product rules, resource access and persistence | Calls REST API | Owns |
| PSN/IGDB integrations, service sessions and provider limits | Calls REST API | Owns |
| Catalogue matching, trophy state and spoiler-safe data policy | Displays allowed states | Owns policy; UI concealment is not an authorization boundary |
| Guide ingestion, AI extraction, validation and publication | Displays status/results | Owns |
| Import concurrency, retries, idempotency and long-running work | Starts operations and shows status | Owns; workers perform durable work |
| Web session cookie endpoints or response composition, if required | Thin optional BFF | Remains authoritative for business authorization |

Start with the already implemented browser-to-Python REST pattern, sending the Firebase ID token over HTTPS and restricting CORS to allowed origins. CORS is not authorization. Do not introduce a Next proxy for every endpoint without a concrete session, rendering or aggregation need.

Server Components may call Python for rendering when appropriate. Any future BFF must forward a verifiable identity and preserve Python authorization; client-supplied UID headers are not identity. Cookie-based authentication would need a deliberate session and CSRF design. Never cache private responses in a shared rendering/CDN cache.

Next Route Handlers remain appropriate for web-specific concerns. They must not gain direct PostgreSQL access or duplicate PSN OAuth, import logic, guide rules or persistence. After migration, remove obsolete Node domain endpoints and their provider secrets. Keep imports and AI processing outside the web request lifecycle: accept an operation, return its ID/status URL and let workers execute it.

## Transaction and workflow boundaries

Fetch and validate external pages into a new import generation. Publish it and its successful operation state in one short database transaction; incomplete imports must not erase the last successful library. Use constraints and explicit concurrency control to prevent conflicting imports for the same saved library. Coordinate shared PSN session refresh and provider limits separately from per-user import concurrency.

When durable processing is introduced, design the database-to-workflow handoff explicitly: use a transactional outbox or a documented recoverable dispatch mechanism with stable operation/workflow IDs. Do not assume a PostgreSQL commit and a Temporal start are atomic. Activities must tolerate retries; Temporal execution history does not make external effects exactly once. PostgreSQL product transactions must not access Temporal's internal database tables.

## Alternatives and tradeoffs

- **Keep Firestore as the only product database:** valid for the existing snapshot MVP and avoids migration. Reconsider if the roadmap remains simple. PostgreSQL is recommended for this roadmap because relationship integrity, versioned guide workflows and cross-entity queries are central requirements, not because Firestore is incapable of transactions or production use.
- **Keep Firestore for checklists/realtime and PostgreSQL for the rest:** rejected for now. No demonstrated realtime/offline requirement offsets additional access policies, monitoring, consistency boundaries and operational cost. Reevaluate against a measured use case.
- **Keep all backend logic in Next.js:** valid for a smaller application, but does not provide the chosen independent Python domain service and durable worker boundary. Existing Node code is migrated incrementally rather than rewritten in one unverified release.
- **Make Next.js proxy every API request:** deferred. A BFF can support web-specific sessions or aggregation, but an unconditional extra hop adds latency and another failure surface without a current benefit.

## Implemented cutover and acceptance

The user explicitly waived existing Firestore library migration because the product is embryonic. Python now reads and writes PostgreSQL; Next domain routes, Admin SDK and psn-api dependencies are removed. No dual write or legacy fallback remains. Existing cloud documents have not been deleted. Users rebuild their saved libraries by synchronizing again. The existing encrypted technical PSN credential was relocated administratively once; no runtime Firestore dependency was retained.

Current models implement relational library/profile identities and per-game progress, with validated JSONB detail snapshots and public catalogue cache. Shared trophy definitions, guides, preferences and durable workflow records in the target table above describe future feature models, not implemented tables.

Real PostgreSQL tests apply migrations and verify user scoping, constraints, atomic failure preservation, lease competition and stale-owner rejection. Live checks must verify PSN imports, details and React requests separately. Imports remain synchronous until durable orchestration is implemented. Cloud deployment remains separate from local validation.

To revert after PostgreSQL receives writes, pause imports and reconcile data explicitly. Changing the frontend back to an old Firestore deployment is not a safe rollback.

## References

- [PostgreSQL JSON and JSONB](https://www.postgresql.org/docs/current/datatype-json.html)
- [Firebase ID token verification](https://firebase.google.com/docs/auth/admin/verify-id-tokens)
- [Next.js backend for frontend](https://nextjs.org/docs/app/guides/backend-for-frontend)
