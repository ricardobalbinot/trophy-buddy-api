"""Real PostgreSQL transactions, migrations and application failure boundaries."""

import os
import pytest
from alembic import command
from alembic.config import Config
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import delete, update
from sqlalchemy.engine import make_url
from test_api import snapshot
from trophy_buddy_api.config import BACKEND_ROOT, Settings
from trophy_buddy_api.database import Base, Database, Lease, LibraryEntry
from trophy_buddy_api.errors import ApplicationError, Busy, NotFound
from trophy_buddy_api.gateway import BackendUnavailable
from trophy_buddy_api.models import LibrarySnapshot, Trophy
from trophy_buddy_api.service import ProductService


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
