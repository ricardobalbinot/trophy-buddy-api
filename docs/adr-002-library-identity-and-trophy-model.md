# ADR 002: Library activity, product identity and trophy evolution

Date: 2026-11-03

Status: Activity ordering, product identities and normalized trophy storage implemented and locally verified. Guide resources remain the next delivery.

## Activity and ordering

Persist nullable `last_played_at` and `trophy_progress_updated_at` as PostgreSQL timezone-aware timestamps. Read Sony's play history and trophy progress timestamps separately; never interpret progress synchronization as proof of a trophy being earned. Use only confirmed Sony title-to-trophy-set mappings and retain the latest played timestamp when multiple title IDs map to the same set. Missing or invalid dates remain unknown.

Remove imported positions. Library reads accept allowlisted optional sorting with last played descending by default, unknown values last and game ID as a deterministic tie-breaker. Sync responses use the same default order. Existing libraries need a new sync to populate dates.

## Product and provider identities

Keep `users` distinct from `psn_profiles`. The verified Firebase UID currently identifies a product user directly. Successful imports create missing users without overwriting their product display names. Migrations backfill existing library owners. Firebase continues to own authentication; PostgreSQL owns product data.

A library is a saved association to a public PSN profile, not an ownership claim. Multiple users may consult the same profile. Do not copy PSN identifiers into the product user's primary key or treat a queried profile as the user's verified identity. Additional product profile fields and relational badges can be introduced with their features; no profile editing endpoint exists yet.

## Relational trophy model and guide evolution

`game_details.payload` retains import metadata and optional catalogue data. Trophy definitions and earned states have been removed from JSONB and migrated into relational tables. The REST response remains compatible.

Shared `trophy_sets`, `trophy_groups` and `trophies` are uniquely scoped by service, set and provider trophy ID. Older observations cannot overwrite newer shared definitions. A provider title ID, trophy-set ID and IGDB game ID represent different identities. Never join trophy records by display name or trophy ID alone.

`profile_trophies` records PSN account identity and relational trophy identity, scoped to the owning UID/game import. This intentionally preserves each saved library's observation rather than letting another user's sync silently change its cached progress. `detail_trophy_groups` records the groups observed in that import. Both cascade with private details; shared definitions survive. Unknown earned states remain nullable and observation freshness comes from the parent import. These facts come from Sony; user-created checklists and annotations are separate product-user state. Guide publication must not change when a PSN snapshot is refreshed.

Attach curated help through `trophy_resources` and guide versions, with a trophy foreign key, resource type, source URL, language, timestamps and publication/review status. Video links and text should not be embedded in replaceable provider payloads. Regional/platform trophy sets require explicit applicability rather than title-name guesses.

The reversible Alembic migration backfills legacy snapshots transactionally before removing their trophy/group arrays. Real PostgreSQL tests exercise upgrade, downgrade, refresh, isolation and cascades. Preserve UID authorization, snapshot generations, known earnings, concealment rules and stale-writer fencing. A JSON response can remain the REST contract even after its source becomes relational. Keeping a redundant detail cache should depend on measured need and an explicit invalidation policy.

## Catalogue and coordination

`catalog_cache` currently stores the result of a title/platform lookup, including unsuccessful lookups, for 24 hours. Its key is a lookup hash, not an IGDB entity ID, so it is accurately a cache. For a persistent product catalogue, introduce canonical game/release identities and provider mappings with refresh timestamps rather than merely renaming this table. Corrections, cover art, provider matches and time estimates can change independently of the game's original release.

`operation_leases` coordinates imports, detail refreshes and technical-session renewals across API processes. Expiring resource claims and owner tokens prevent concurrent publication and reject stale writers after another process acquires the resource. Cooldowns limit repeat requests. Leases are operational coordination, not durable job history or a workflow queue.
