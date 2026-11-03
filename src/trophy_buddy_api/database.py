"""PostgreSQL persistence with short transactions and cross-process fenced leases."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from functools import cached_property
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from trophy_buddy_api.config import Settings
from trophy_buddy_api.errors import ApplicationError, Busy
from trophy_buddy_api.gateway import BackendUnavailable
from trophy_buddy_api.models import GameDetails, LibraryGame, LibrarySnapshot, Profile
from typing import Any, Literal, cast
from uuid import uuid4


class Base(DeclarativeBase):
    pass


class PublicProfile(Base):
    __tablename__ = "psn_profiles"
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    online_id: Mapped[str] = mapped_column(String(32))


class Library(Base):
    __tablename__ = "libraries"
    uid: Mapped[str] = mapped_column(String(128), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("psn_profiles.account_id"))
    synced_at: Mapped[str] = mapped_column(String(40))


class LibraryEntry(Base):
    __tablename__ = "library_games"
    __table_args__ = (
        CheckConstraint("earned IS NULL OR earned >= 0", name="ck_games_earned"),
        CheckConstraint("total IS NULL OR total >= 0", name="ck_games_total"),
        CheckConstraint("progress IS NULL OR progress BETWEEN 0 AND 100", name="ck_games_progress"),
        CheckConstraint(
            "trophy_service IS NULL OR trophy_service IN ('trophy', 'trophy2')",
            name="ck_games_service",
        ),
    )
    uid: Mapped[str] = mapped_column(
        ForeignKey("libraries.uid", ondelete="CASCADE"), primary_key=True
    )
    game_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(String(64))
    image_url: Mapped[str | None] = mapped_column(Text)
    title_ids: Mapped[list[str]] = mapped_column(JSONB)
    trophy_set_id: Mapped[str | None] = mapped_column(String(64))
    trophy_service: Mapped[str | None] = mapped_column(String(16))
    earned: Mapped[int | None] = mapped_column(Integer)
    total: Mapped[int | None] = mapped_column(Integer)
    progress: Mapped[int | None] = mapped_column(Integer)
    platinum: Mapped[bool] = mapped_column(Boolean)


class DetailSnapshot(Base):
    __tablename__ = "game_details"
    __table_args__ = (
        ForeignKeyConstraint(
            ["uid", "game_id"],
            ["library_games.uid", "library_games.game_id"],
            ondelete="CASCADE",
            name="fk_details_library_game",
        ),
    )
    uid: Mapped[str] = mapped_column(String(128), primary_key=True)
    game_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class Lease(Base):
    __tablename__ = "operation_leases"
    resource: Mapped[str] = mapped_column(String(256), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(32), default="")
    lease_until: Mapped[float] = mapped_column(default=0.0)
    retry_after: Mapped[float] = mapped_column(default=0.0)


class ServiceSession(Base):
    __tablename__ = "service_sessions"
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    encrypted: Mapped[str] = mapped_column(Text)
    bootstrap_digest: Mapped[str | None] = mapped_column(String(64))
    access_expires_at: Mapped[int] = mapped_column(BigInteger)


class CatalogCache(Base):
    __tablename__ = "catalog_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @cached_property
    def engine(self) -> Engine:
        """Allocate a bounded pool lazily so builds and authentication tests need no database."""
        raw_url = self.settings.database_url.get_secret_value()
        if not raw_url.startswith(("postgresql://", "postgres://", "postgresql+psycopg://")):
            raise BackendUnavailable
        url = make_url(raw_url).set(drivername="postgresql+psycopg")

        return create_engine(
            url,
            pool_size=5,
            max_overflow=5,
            pool_pre_ping=True,
            hide_parameters=True,
            connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000"},
        )

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """Rollback provider/SQL failures and strip details before returning to the HTTP layer."""
        try:
            with Session(self.engine) as session, session.begin():
                yield session
        except (ApplicationError, BackendUnavailable):
            raise
        except Exception:
            raise BackendUnavailable from None

    def read_library(self, uid: str) -> dict[str, Any] | None:
        with self.transaction() as session:
            # A single joined statement observes one committed generation at READ COMMITTED.
            rows = session.execute(
                select(Library, PublicProfile, LibraryEntry)
                .join(PublicProfile, Library.account_id == PublicProfile.account_id)
                .outerjoin(LibraryEntry, Library.uid == LibraryEntry.uid)
                .where(Library.uid == uid)
                .order_by(LibraryEntry.position)
            ).all()
            if not rows:
                return None
            library, profile, _ = rows[0]
            games = [
                LibraryGame(
                    id=entry.game_id,
                    title=entry.title,
                    platform=entry.platform,
                    image_url=entry.image_url,
                    title_ids=entry.title_ids,
                    trophy_set_id=entry.trophy_set_id,
                    trophy_service=cast(Literal["trophy", "trophy2"] | None, entry.trophy_service),
                    earned=entry.earned,
                    total=entry.total,
                    progress=entry.progress,
                    platinum=entry.platinum,
                )
                for _, _, entry in rows
                if entry
            ]

            return LibrarySnapshot(
                version=1,
                profile=Profile(account_id=profile.account_id, online_id=profile.online_id),
                synced_at=library.synced_at,
                games=games,
            ).model_dump(by_alias=True, mode="json", exclude_none=False)

    def acquire(self, resource: str, duration: int) -> str:
        """Atomically claim an expired lease; expired owners cannot publish over newer owners."""
        operation = uuid4().hex
        with self.transaction() as session:
            now = float(
                session.scalar(select(func.extract("epoch", func.clock_timestamp()))) or time.time()
            )
            session.execute(
                insert(Lease)
                .values(resource=resource, operation_id="", lease_until=0, retry_after=0)
                .on_conflict_do_nothing()
            )
            claimed = session.execute(
                update(Lease)
                .where(
                    Lease.resource == resource, Lease.lease_until <= now, Lease.retry_after <= now
                )
                .values(operation_id=operation, lease_until=now + duration)
                .returning(Lease.resource)
            ).scalar_one_or_none()
            if claimed is None:
                raise Busy

        return operation

    def fence(self, session: Session, resource: str, operation: str) -> Lease:
        lease = session.scalar(select(Lease).where(Lease.resource == resource).with_for_update())
        now = float(
            session.scalar(select(func.extract("epoch", func.clock_timestamp()))) or time.time()
        )
        if lease is None or lease.operation_id != operation or lease.lease_until <= now:
            raise Busy

        return lease

    def release(self, resource: str, operation: str, cooldown: int = 15) -> None:
        with self.transaction() as session:
            session.execute(
                update(Lease)
                .where(Lease.resource == resource, Lease.operation_id == operation)
                .values(
                    lease_until=0,
                    retry_after=func.extract("epoch", func.clock_timestamp()) + cooldown,
                )
            )

    def save_library(self, uid: str, snapshot: LibrarySnapshot, operation: str) -> None:
        """Publish one generation atomically; retain prior details for earnings-loss protection."""
        with self.transaction() as session:
            lease = self.fence(session, f"library:{uid}", operation)
            session.execute(
                insert(PublicProfile)
                .values(
                    account_id=snapshot.profile.account_id, online_id=snapshot.profile.online_id
                )
                .on_conflict_do_update(
                    index_elements=[PublicProfile.account_id],
                    set_={"online_id": snapshot.profile.online_id},
                )
            )
            session.execute(
                insert(Library)
                .values(
                    uid=uid, account_id=snapshot.profile.account_id, synced_at=snapshot.synced_at
                )
                .on_conflict_do_update(
                    index_elements=[Library.uid],
                    set_={
                        "account_id": snapshot.profile.account_id,
                        "synced_at": snapshot.synced_at,
                    },
                )
            )
            for position, game in enumerate(snapshot.games):
                values = game.model_dump(exclude={"id"})
                statement = insert(LibraryEntry).values(
                    uid=uid, game_id=game.id, position=position, **values
                )
                session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[LibraryEntry.uid, LibraryEntry.game_id],
                        set_={**values, "position": position},
                    )
                )
            session.execute(
                delete(LibraryEntry).where(
                    LibraryEntry.uid == uid,
                    LibraryEntry.game_id.not_in([game.id for game in snapshot.games]),
                )
            )
            lease.lease_until = 0
            lease.retry_after = time.time() + 60

    def read_details(self, uid: str, game_id: str) -> GameDetails | None:
        with self.transaction() as session:
            entry = session.get(DetailSnapshot, (uid, game_id))

            return GameDetails.model_validate(entry.payload) if entry else None

    def save_details(self, uid: str, details: GameDetails, operation: str) -> None:
        """Fence publication against lease replacement and library changes."""
        with self.transaction() as session:
            lease = self.fence(session, f"details:{uid}:{details.game.id}", operation)
            library = session.scalar(select(Library).where(Library.uid == uid).with_for_update())
            if (
                library is None
                or library.synced_at != details.library_synced_at
                or library.account_id != details.profile.account_id
            ):
                raise Busy
            session.execute(
                insert(DetailSnapshot)
                .values(
                    uid=uid,
                    game_id=details.game.id,
                    payload=details.model_dump(by_alias=True, mode="json"),
                )
                .on_conflict_do_update(
                    index_elements=[DetailSnapshot.uid, DetailSnapshot.game_id],
                    set_={"payload": details.model_dump(by_alias=True, mode="json")},
                )
            )
            lease.lease_until = 0
            lease.retry_after = time.time() + 60

    def session_state(self) -> dict[str, Any] | None:
        with self.transaction() as session:
            state = session.get(ServiceSession, "psn")

            return (
                {
                    "encrypted": state.encrypted,
                    "bootstrapDigest": state.bootstrap_digest,
                    "accessExpiresAt": state.access_expires_at,
                }
                if state
                else None
            )

    def save_session(self, state: dict[str, Any], operation: str) -> None:
        with self.transaction() as session:
            lease = self.fence(session, "psn-session", operation)
            values = dict(
                encrypted=state["encrypted"],
                bootstrap_digest=state["bootstrapDigest"],
                access_expires_at=state["accessExpiresAt"],
            )
            session.execute(
                insert(ServiceSession)
                .values(provider="psn", **values)
                .on_conflict_do_update(index_elements=[ServiceSession.provider], set_=values)
            )
            lease.lease_until = 0
            lease.retry_after = 0

    def cached_catalog(self, key: str) -> tuple[bool, dict[str, Any] | None]:
        with self.transaction() as session:
            row = session.scalar(
                select(CatalogCache).where(
                    CatalogCache.key == key, CatalogCache.expires_at > func.now()
                )
            )

            return (True, row.payload) if row else (False, None)

    def save_catalog(self, key: str, payload: dict[str, Any] | None, expires: datetime) -> None:
        with self.transaction() as session:
            session.execute(
                insert(CatalogCache)
                .values(key=key, payload=payload, expires_at=expires)
                .on_conflict_do_update(
                    index_elements=[CatalogCache.key],
                    set_={"payload": payload, "expires_at": expires},
                )
            )
