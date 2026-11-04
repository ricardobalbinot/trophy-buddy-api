"""Operator-only guide ingestion and review using the API's private database configuration."""

import argparse
import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from trophy_buddy_api.config import Settings
from trophy_buddy_api.database import Database, LibraryEntry
from trophy_buddy_api.gateway import BackendUnavailable
from trophy_buddy_api.guide_index import (
    IndexedGuide,
    bind_guide,
    download_index,
    game_name,
    save_index,
)
from trophy_buddy_api.models import LibraryGame
from trophy_buddy_api.trophy_resources import ResourceDraft, save_resource
from trophy_buddy_api.trophy_storage import TrophySet, trophy_service


def command_parser() -> argparse.ArgumentParser:
    """Expose explicit operator actions without granting publication to public API callers."""
    parser = argparse.ArgumentParser(description="Import and review public guide metadata")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("import-index")
    listing = commands.add_parser("list")
    listing.add_argument("--game", required=True)
    binding = commands.add_parser("bind")
    binding.add_argument("--service", choices=["trophy", "trophy2"], required=True)
    binding.add_argument("--set-id", required=True)
    binding.add_argument("--entry-id", required=True)
    binding.add_argument("--rationale", required=True)
    resource = commands.add_parser("add-resource")
    resource.add_argument("--service", choices=["trophy", "trophy2"], required=True)
    resource.add_argument("--set-id", required=True)
    resource.add_argument("--trophy-id", type=int, required=True)
    resource.add_argument("--title", required=True)
    resource.add_argument("--source-name", required=True)
    resource.add_argument("--url", required=True)
    resource.add_argument("--kind", choices=["video", "article"], required=True)
    resource.add_argument("--language", required=True)
    resource.add_argument("--spoiler-safe", action="store_true")
    resource.add_argument("--publish", action="store_true")

    return parser


def import_index(database: Database) -> None:
    """Validate the complete download before opening a bounded publication transaction."""
    digest, rows = download_index()
    with database.transaction() as session:
        save_index(session, digest, rows)
    print(f"Imported {len(rows)} trophy guides; source SHA-256 {digest}.")


def list_guides(database: Database, title: str) -> None:
    """List candidates for review without creating a product association by name alone."""
    with database.transaction() as session:
        guides = session.scalars(
            select(IndexedGuide)
            .where(IndexedGuide.game_name == game_name(title), IndexedGuide.active.is_(True))
            .order_by(IndexedGuide.entry_id)
        ).all()
        for guide in guides:
            print(
                f"{guide.entry_id}: {guide.title} | {guide.source_name} | "
                f"difficulty={guide.difficulty}, runs={guide.playthroughs}, "
                f"hours={guide.platinum_hours}, trophies={guide.trophy_count} | {guide.url}"
            )


def review_binding(database: Database, arguments: argparse.Namespace) -> None:
    """Resolve an existing Sony identity before accepting a reviewed external guide."""
    with database.transaction() as session:
        entries = session.scalars(
            select(LibraryEntry).where(LibraryEntry.trophy_set_id == arguments.set_id)
        ).all()
        games = [
            LibraryGame(
                id=entry.game_id,
                **{
                    column.name: getattr(entry, column.name)
                    for column in LibraryEntry.__table__.columns
                    if column.name not in {"uid", "game_id"}
                },
            )
            for entry in entries
        ]
        games = [game for game in games if trophy_service(game) == arguments.service]
        identities = {(game.title, game.platform, game.total) for game in games}
        if len(identities) != 1:
            raise ValueError("A unique saved game identity is required")
        game = games[0]
        session.execute(
            insert(TrophySet)
            .values(service=arguments.service, set_id=arguments.set_id)
            .on_conflict_do_nothing()
        )
        bind_guide(session, game, arguments.entry_id, arguments.rationale)
    print("Reviewed guide bound to the exact stored trophy set.")


def review_resource(database: Database, arguments: argparse.Namespace) -> None:
    """Create drafts by default; publication is an explicit operator action with a trophy FK."""
    draft = ResourceDraft(
        service=arguments.service,
        set_id=arguments.set_id,
        trophy_id=arguments.trophy_id,
        title=arguments.title,
        source_name=arguments.source_name,
        source_url=arguments.url,
        kind=arguments.kind,
        language=arguments.language,
        contains_spoilers=not arguments.spoiler_safe,
    )
    with database.transaction() as session:
        resource_id = save_resource(session, draft, publish=arguments.publish)
    state = "published" if arguments.publish else "draft"
    print(f"Curated resource {resource_id}: {state}.")


def main() -> None:
    """Keep publishing privileges outside public REST routes until editorial roles exist."""
    parser = command_parser()
    arguments = parser.parse_args()
    database = Database(Settings())
    try:
        if arguments.command == "import-index":
            import_index(database)
        elif arguments.command == "list":
            list_guides(database, arguments.game)
        elif arguments.command == "bind":
            review_binding(database, arguments)
        else:
            review_resource(database, arguments)
    except (BackendUnavailable, ValueError, httpx.HTTPError):
        parser.exit(
            1, "Guide operation failed: check index schema, database and reviewed identity.\n"
        )
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    main()
