"""Real PostgreSQL transactions, migrations and application failure boundaries."""

import os
import pytest
from alembic import command
from alembic.config import Config
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import make_url
from test_api import snapshot
from trophy_buddy_api.config import BACKEND_ROOT, Settings
from trophy_buddy_api.database import Base, Database, DetailSnapshot, Lease, LibraryEntry, User
from trophy_buddy_api.errors import ApplicationError, Busy, NotFound
from trophy_buddy_api.gateway import BackendUnavailable
from trophy_buddy_api.guide_index import bind_guide, load_guide, parse_index, save_index
from trophy_buddy_api.models import GameDetails, LibrarySnapshot, Trophy, TrophyGroup
from trophy_buddy_api.service import ProductService
from trophy_buddy_api.trophy_resources import ResourceDraft, save_resource
from trophy_buddy_api.trophy_storage import ProfileTrophy, TrophyDefinition


@pytest.fixture
def database() -> Iterator[Database]:
    url = os.environ.get("TEST_DATABASE_URL") or Settings().test_database_url.get_secret_value()
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated PostgreSQL database ending in _test")
    if not (make_url(url).database or "").endswith("_test"):
        pytest.fail("Refusing to clear a database whose name does not end in _test")
    database = Database(Settings(_env_file=None, database_url=url))
    configuration = Config(str(BACKEND_ROOT / "alembic.ini"))
    with database.engine.begin() as connection:
        configuration.attributes["connection"] = connection
        command.upgrade(configuration, "head")
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(delete(table))
    yield database
    database.engine.dispose()


def seed(database: Database, uid: str = "user-a") -> LibrarySnapshot:
    library = LibrarySnapshot.model_validate(snapshot())
    lease = database.acquire(f"library:{uid}", 60)
    database.save_library(uid, library, lease)

    return library


def test_postgres_preserves_identity_unknowns_and_exact_timestamps(database: Database) -> None:
    original = seed(database)
    loaded = LibrarySnapshot.model_validate(database.read_library("user-a"))
    assert loaded == original
    assert database.read_library("user-b") is None


def test_activity_sorting_uses_dates_nulls_last_and_stable_ties(database: Database) -> None:
    library = seed(database)
    default = database.read_library("user-a")
    assert [game["id"] for game in default["games"]] == [game.id for game in library.games]
    ascending = database.read_library("user-a", direction="asc")
    assert [game["id"] for game in ascending["games"]] == ["zero", "trophy-NPWR00001_00", "unknown"]
    with database.transaction() as session:
        session.execute(update(LibraryEntry).values(last_played_at=None))
    tied = database.read_library("user-a")
    assert [game["id"] for game in tied["games"]] == sorted(game.id for game in library.games)


def test_product_identity_is_independent_and_survives_library_refresh(database: Database) -> None:
    library = seed(database)
    with database.transaction() as session:
        user = session.get(User, "user-a")
        assert user is not None
        assert user.display_name is None
        user.display_name = "Public name"
        session.execute(update(Lease).values(retry_after=0))
    database.save_library("user-a", library, database.acquire("library:user-a", 60))
    with database.transaction() as session:
        assert session.get(User, "user-a").display_name == "Public name"


def test_only_one_process_can_acquire_same_resource(database: Database) -> None:
    def acquire(_: int) -> bool:
        try:
            database.acquire("library:concurrent", 60)
            return True
        except Busy:
            return False

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(acquire, range(5)))
    assert sum(results) == 1


def test_expired_lease_owner_cannot_publish_or_release_new_owner(database: Database) -> None:
    old = database.acquire("library:user-a", 60)
    with database.transaction() as session:
        session.execute(
            update(Lease).where(Lease.resource == "library:user-a").values(lease_until=0)
        )
    new = database.acquire("library:user-a", 60)
    database.release("library:user-a", old, 0)
    with pytest.raises(Busy):
        database.save_library("user-a", LibrarySnapshot.model_validate(snapshot()), old)
    database.save_library("user-a", LibrarySnapshot.model_validate(snapshot()), new)
    assert database.read_library("user-a") is not None


def test_sql_constraints_rollback_invalid_progress(database: Database) -> None:
    seed(database)
    with pytest.raises(BackendUnavailable), database.transaction() as session:
        session.execute(
            update(LibraryEntry).where(LibraryEntry.uid == "user-a").values(progress=101)
        )
    assert database.read_library("user-a")["games"][0]["progress"] == 60


def test_failed_import_preserves_committed_library(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = seed(database)
    with database.transaction() as session:
        session.execute(update(Lease).values(retry_after=0))
    service = ProductService(Settings(_env_file=None), database)

    def fail(_: str) -> LibrarySnapshot:
        raise ApplicationError("psn_unavailable")

    monkeypatch.setattr(service.psn, "import_library", fail)
    with pytest.raises(ApplicationError):
        service.synchronize("user-a", "Player")
    assert LibrarySnapshot.model_validate(database.read_library("user-a")) == original


def test_details_cache_scoping_and_profile_change_fence(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = seed(database)
    service = ProductService(Settings(_env_file=None), database)
    calls = []

    def trophies(*args: object) -> tuple[list[Trophy], list]:
        calls.append(args)
        return [], []

    monkeypatch.setattr(service.psn, "trophies", trophies)
    details = service.details("user-a", library.games[0].id, False)
    assert service.details("user-a", library.games[0].id, False) == details
    assert len(calls) == 1
    with pytest.raises(NotFound):
        service.details("user-b", library.games[0].id, False)
    resource = f"details:user-a:{details.game.id}"
    with database.transaction() as session:
        session.execute(
            update(Lease)
            .where(Lease.resource.in_([resource, "library:user-a"]))
            .values(retry_after=0)
        )
    operation = database.acquire(resource, 60)
    changed = library.model_copy(update={"synced_at": "2026-11-03T18:00:00.000Z"})
    database.save_library("user-a", changed, database.acquire("library:user-a", 60))
    with pytest.raises(Busy):
        database.save_details("user-a", details, operation)
    assert database.read_details("user-a", details.game.id) == details


def detailed_snapshot(library: LibrarySnapshot, earned: bool | None = False) -> GameDetails:
    return GameDetails(
        profile=library.profile,
        library_synced_at=library.synced_at,
        fetched_at="2026-11-04T12:00:00.000Z",
        game=library.games[0],
        catalog=None,
        trophy_status="available",
        groups=[TrophyGroup(id="default", name="Base game")],
        trophies=[
            Trophy(
                id=0,
                group_id="default",
                name="Secret",
                description="Hidden action",
                image_url=None,
                type="bronze",
                hidden=True,
                earned=earned,
                earned_at="2026-11-04T10:00:00.000Z" if earned else None,
                earned_rate=12.5,
            )
        ],
    )


def test_relational_trophies_share_definitions_but_isolate_imports(database: Database) -> None:
    library = seed(database)
    second = seed(database, "user-b")
    first_details = detailed_snapshot(library, True)
    second_details = detailed_snapshot(second, False)
    for uid, details in [("user-a", first_details), ("user-b", second_details)]:
        database.save_details(
            uid, details, database.acquire(f"details:{uid}:{details.game.id}", 60)
        )
    assert database.read_details("user-a", library.games[0].id) == first_details
    assert database.read_details("user-b", library.games[0].id) == second_details
    assert database.read_details("user-c", library.games[0].id) is None
    with database.transaction() as session:
        assert session.scalar(select(func.count()).select_from(TrophyDefinition)) == 1
        assert session.scalar(select(func.count()).select_from(ProfileTrophy)) == 2
        payload = session.get(DetailSnapshot, ("user-a", library.games[0].id)).payload
        assert "trophies" not in payload and "groups" not in payload


def test_trophy_refresh_replaces_progress_and_cascades_only_private_observations(
    database: Database,
) -> None:
    library = seed(database)
    details = detailed_snapshot(library)
    resource = f"details:user-a:{details.game.id}"
    database.save_details("user-a", details, database.acquire(resource, 60))
    with database.transaction() as session:
        session.execute(update(Lease).values(retry_after=0))
    changed = detailed_snapshot(library, True)
    database.save_details("user-a", changed, database.acquire(resource, 60))
    assert database.read_details("user-a", details.game.id).trophies[0].earned is True
    with database.transaction() as session:
        session.execute(delete(LibraryEntry).where(LibraryEntry.uid == "user-a"))
        assert session.scalar(select(func.count()).select_from(ProfileTrophy)) == 0
        assert session.scalar(select(func.count()).select_from(TrophyDefinition)) == 1


def test_legacy_details_migration_round_trip_preserves_trophies(database: Database) -> None:
    library = seed(database)
    details = detailed_snapshot(library, True)
    configuration = Config(str(BACKEND_ROOT / "alembic.ini"))
    with database.engine.begin() as connection:
        configuration.attributes["connection"] = connection
        command.downgrade(configuration, "b84e03fa721c")
        connection.execute(
            DetailSnapshot.__table__.insert().values(
                uid="user-a",
                game_id=details.game.id,
                payload=details.model_dump(by_alias=True, mode="json"),
            )
        )
        command.upgrade(configuration, "head")
        command.check(configuration)
    assert database.read_details("user-a", details.game.id) == details
    with database.engine.begin() as connection:
        configuration.attributes["connection"] = connection
        command.downgrade(configuration, "b84e03fa721c")
        payload = connection.scalar(select(DetailSnapshot.payload))
        assert payload["trophies"][0]["earned"] is True
        command.upgrade(configuration, "head")
    assert database.read_details("user-a", details.game.id) == details


def test_guide_binding_requires_review_and_invalidates_only_changed_records(
    database: Database,
) -> None:
    library = seed(database)
    game = library.games[0]
    details = detailed_snapshot(library)
    database.save_details("user-a", details, database.acquire(f"details:user-a:{game.id}", 60))
    payload = {
        "123": {
            "a": 1 | 32 | 512 | 16384,
            "n": "Example Game Trophy Guide",
            "g": [12],
            "r": [2, 1, 120],
            "u": ["Author"],
            "t": [10],
        }
    }
    with database.transaction() as session:
        save_index(session, "a" * 64, parse_index(payload, "a" * 64))
        assert load_guide(session, game) is None

        with pytest.raises(ValueError):
            bind_guide(
                session,
                game.model_copy(update={"platform": "PS4"}),
                "psnprofiles:123",
                "Reviewed title, platform and trophy count.",
            )
        bind_guide(session, game, "psnprofiles:123", "Reviewed title, platform and trophy count.")
    overview = database.read_details("user-a", game.id).guide
    assert overview is not None and overview.missables == "confirmed_some"
    assert overview.online == "unknown" and overview.platinum_hours == 120
    with database.transaction() as session:
        save_index(session, "b" * 64, parse_index(payload, "b" * 64))
        assert load_guide(session, game) is not None
    changed = {"123": {**payload["123"], "r": [3, 2, 180]}}
    with database.transaction() as session:
        save_index(session, "c" * 64, parse_index(changed, "c" * 64))
        assert load_guide(session, game) is None


def test_curated_resources_survive_imports_and_drafts_are_private(database: Database) -> None:
    library = seed(database)
    details = detailed_snapshot(library)
    resource = f"details:user-a:{details.game.id}"
    database.save_details("user-a", details, database.acquire(resource, 60))
    draft = ResourceDraft(
        service="trophy2",
        set_id=details.game.trophy_set_id,
        trophy_id=0,
        title="External help",
        source_name="Guide author",
        source_url="https://example.com/help",
        kind="article",
        language="en",
    )
    with database.transaction() as session:
        save_resource(session, draft)
    assert database.read_details("user-a", details.game.id).trophies[0].resources == []
    with database.transaction() as session:
        save_resource(session, draft, publish=True)
        session.execute(update(Lease).values(retry_after=0))
    database.save_details("user-a", details, database.acquire(resource, 60))
    imported = database.read_details("user-a", details.game.id)
    assert len(imported.trophies[0].resources) == 1
    assert imported.trophies[0].resources[0].contains_spoilers is True
