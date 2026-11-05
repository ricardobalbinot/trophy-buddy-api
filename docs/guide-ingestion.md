# Guide ingestion and editorial boundaries

## Working delivery

The API imports factual metadata from [trophy-guide-list-json](https://github.com/langdonx/trophy-guide-list-json). This is an external discovery index, not a complete or authoritative catalogue of all games. Its [schema](https://github.com/langdonx/trophy-guide-list-json/tree/main/types) describes source/platform flags, estimated difficulty, playthroughs, hours, authors and PSNProfiles game IDs. These IDs are not Sony trophy-set IDs.

An administrative command downloads one bounded public JSON response, validates supported main-game trophy guides, records a SHA-256 source version and publishes the complete import transactionally. It does not crawl guide pages, call an LLM or run during browser navigation. Unsupported sources, DLC-only guides and unsafe paths are excluded. Missing/negative estimates remain null. An absent positive requirement flag remains `unknown`, not confirmed absence.

`guide_index_versions` records source provenance. `indexed_guides` retains parsed facts and a hash of each upstream record. `guide_bindings` associates one explicitly reviewed record with an exact `(service, trophy_set_id)`, with title/platform/count validation, review time and rationale. No request-time fuzzy title association is made. Unchanged record hashes keep their review when unrelated games change. Changed or removed records stop being served until reviewed again. Estimates from different sources are never averaged or silently reconciled.

`trophy_resources` stores independent curated video/article links, language, source, spoiler flag and publication state, with a foreign key to the exact trophy definition. Drafts are private to administrative storage. Library/detail refreshes cannot overwrite or delete these resources. No external video, thumbnail or transcript is automatically loaded. The frontend hides resource titles behind a disclosure and excludes the entire resource section for concealed secret trophies.

## Operator workflow

Run from this independent API project, with its own private configuration. Operators have database-level authority; public Firebase users have no editorial mutation endpoint.

```sh
uv run alembic upgrade head
uv run python -m trophy_buddy_api.guide_admin import-index
uv run python -m trophy_buddy_api.guide_admin list --game "Persona 5 Royal"
uv run python -m trophy_buddy_api.guide_admin bind \
  --service trophy2 --set-id NPWR25017_00 --entry-id psnprofiles:9920 \
  --rationale "Reviewed Royal edition, PS5 applicability and all 53 trophies against the stored Sony list."
uv run python -m trophy_buddy_api.guide_admin add-resource \
  --service trophy2 --set-id NPWR25017_00 --trophy-id 26 \
  --title "Persona 5 Royal | The Purpose of a Thief Trophy Guide" \
  --source-name "The Commissioner of Games" \
  --url "https://www.youtube.com/watch?v=xQspFIr6ERc" \
  --kind video --language en --publish
```

Resource drafts are the default; `--publish` is explicit. Resources default to possible spoilers; `--spoiler-safe` is a deliberate editorial claim. The trophy must already have been imported. These example associations were seeded only in the local application database, not in migrations or production.

The selected Persona 5 Royal PS5 set is `NPWR25017_00`, with 53 trophies. The index lists PSNProfiles guide 9920 as applicable to PSNProfiles game 18834 among other stacks, with difficulty 2/10, one run and 120 hours. Its PlayStationTrophies alternative reports 60 hours. The selected estimate is attributed to PSNProfiles, not presented as a Sony fact or a completed independent walkthrough review. The linked video is an external help resource, not evidence that individual missables/checkpoints have been analyzed.

Reimporting the same source is idempotent. Import failures happen before publication or roll back the transaction. Guide index imports are currently a single-operator CLI workflow; scheduled/distributed orchestration remains pending. Applying migrations alone creates schema, not third-party data or reviewed bindings. Public deployments require an explicit operator import/review step after release.

## Next product deliveries

1. **Per-trophy evidence:** collect permitted guide content on demand; persist source revisions, claims, evidence locations, extraction versions and review/publication state. A game-level missable flag cannot identify individual missable trophies.
2. **Deterministic extraction first:** parse stable overview/tag structures where available. Store source hashes and reuse unchanged extractions. Treat absent/ambiguous fields as unknown. Use an LLM only for prose interpretation or unresolved structure, with bounded context and evidence validation.
3. **Durable ingestion:** move multi-step imports into a worker with operation status, bounded retries and a recoverable database-to-workflow handoff. Temporal remains planned, not provided by the current database leases.
4. **AI-assisted candidates:** introduce the Bedrock adapter once credentials, allowed sources and evaluation examples are configured. Source text is untrusted data; output is a candidate, not automatic publication.
5. **Real contextual Companion:** combine reviewed requirements with PSN observations and explicit player context. Persist personal checklist/checkpoint actions separately from Sony achievements. Trophies alone do not establish story position.

No scraping bypass, automatic guide-body republication, Bedrock call or contextual checklist is implemented in this delivery. The existing fictional Companion remains separate.


## On-demand discovery

Detail requests now resolve guide metadata from the local index on both cache hits and fresh PSN imports. Matching requires the exact normalized edition name, a compatible platform flag and a known equal trophy count. Source priority is PSNProfiles, PowerPyx, PlayStationTrophies, PlatGet, then Knoef. Multiple candidates within the preferred available source remain ambiguous. An automatic association is explicitly labeled automatic, not a manual editorial review.

`guide_discoveries` serializes concurrent first access by Sony service/set. Negative and ambiguous outcomes have a six-hour retry window. Existing stale bindings require review instead of silent reassociation. The current index must be administratively imported before discovery can find candidates. No guide page, search engine or LLM is called by discovery.

`guide_analysis_requests` persists one shared intent per guide record hash and extraction schema version. Requests currently remain `blocked` with reason `analysis_worker_not_configured`. There is no running extractor, background worker, retry executor or completion publisher yet. These records are a durable handoff boundary for the next worker delivery, not a claim that background analysis is already functioning. The details contract exposes `guideDiscovery` and `guideAnalysis`; the frontend distinguishes automatic matching, missing candidates, ambiguous candidates and stale associations.
