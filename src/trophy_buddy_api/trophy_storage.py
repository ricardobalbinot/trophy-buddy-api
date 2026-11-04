"""Shared trophy definitions and isolated observations belonging to a saved detail import."""

from datetime import datetime
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    delete,
    select,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column
from trophy_buddy_api.db_base import Base
from trophy_buddy_api.models import GameDetails, LibraryGame, Trophy, TrophyGroup
from trophy_buddy_api.psn_mapping import provider_datetime
from trophy_buddy_api.trophy_resources import load_resources
from typing import Any, Literal, cast


def trophy_service(game: LibraryGame) -> str:
    """Use Sony's service identity, inferring legacy records only from their exact platform."""
    return game.trophy_service or ("trophy2" if "PS5" in game.platform.split(",") else "trophy")


class TrophySet(Base):
    __tablename__ = "trophy_sets"
    __table_args__ = (CheckConstraint("service IN ('trophy', 'trophy2')"),)
    service: Mapped[str] = mapped_column(String(16), primary_key=True)
    set_id: Mapped[str] = mapped_column(String(64), primary_key=True)


class TrophyGroupDefinition(Base):
    __tablename__ = "trophy_groups"
    __table_args__ = (
        ForeignKeyConstraint(["service", "set_id"], ["trophy_sets.service", "trophy_sets.set_id"]),
    )
    service: Mapped[str] = mapped_column(String(16), primary_key=True)
    set_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TrophyDefinition(Base):
    __tablename__ = "trophies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["service", "set_id", "group_id"],
            ["trophy_groups.service", "trophy_groups.set_id", "trophy_groups.group_id"],
        ),
        CheckConstraint("trophy_id >= 0"),
        CheckConstraint("type IN ('bronze', 'silver', 'gold', 'platinum')"),
    )
    service: Mapped[str] = mapped_column(String(16), primary_key=True)
    set_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trophy_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[str] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(16))
    hidden: Mapped[bool] = mapped_column(Boolean)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProfileTrophy(Base):
    """A profile observation scoped to an owner's import, never a user-editable achievement."""

    __tablename__ = "profile_trophies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["uid", "game_id"], ["game_details.uid", "game_details.game_id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["service", "set_id", "trophy_id"],
            ["trophies.service", "trophies.set_id", "trophies.trophy_id"],
        ),
        CheckConstraint("earned_rate IS NULL OR earned_rate BETWEEN 0 AND 100"),
        CheckConstraint("earned_at IS NULL OR earned IS TRUE"),
    )
    uid: Mapped[str] = mapped_column(String(128), primary_key=True)
    game_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    trophy_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("psn_profiles.account_id"))
    service: Mapped[str] = mapped_column(String(16))
    set_id: Mapped[str] = mapped_column(String(64))
    earned: Mapped[bool | None] = mapped_column(Boolean)
    earned_at: Mapped[str | None] = mapped_column(String(40))
    earned_rate: Mapped[float | None]


class DetailTrophyGroup(Base):
    __tablename__ = "detail_trophy_groups"
    __table_args__ = (
        ForeignKeyConstraint(
            ["uid", "game_id"], ["game_details.uid", "game_details.game_id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["service", "set_id", "group_id"],
            ["trophy_groups.service", "trophy_groups.set_id", "trophy_groups.group_id"],
        ),
    )
    uid: Mapped[str] = mapped_column(String(128), primary_key=True)
    game_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    service: Mapped[str] = mapped_column(String(16))
    set_id: Mapped[str] = mapped_column(String(64))


def save_trophies(session: Session, uid: str, details: GameDetails) -> None:
    """Replace one import atomically; older imports cannot regress shared trophy definitions."""
    game = details.game
    session.execute(
        delete(ProfileTrophy).where(ProfileTrophy.uid == uid, ProfileTrophy.game_id == game.id)
    )
    session.execute(
        delete(DetailTrophyGroup).where(
            DetailTrophyGroup.uid == uid, DetailTrophyGroup.game_id == game.id
        )
    )
    if not game.trophy_set_id:
        return
    identity = {"service": trophy_service(game), "set_id": game.trophy_set_id}
    observed = provider_datetime(details.fetched_at)
    if observed is None:
        raise ValueError("A trophy observation requires an aware timestamp")
    session.execute(insert(TrophySet).values(**identity).on_conflict_do_nothing())
    groups: dict[str, str | None] = {row.group_id: None for row in details.trophies}
    groups.update({row.id: row.name for row in details.groups})
    for group_id, name in sorted(groups.items()):
        values = {**identity, "group_id": group_id, "name": name, "observed_at": observed}
        statement = insert(TrophyGroupDefinition).values(**values)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    TrophyGroupDefinition.service,
                    TrophyGroupDefinition.set_id,
                    TrophyGroupDefinition.group_id,
                ],
                set_={"name": statement.excluded.name, "observed_at": observed},
                where=(TrophyGroupDefinition.observed_at <= observed)
                & statement.excluded.name.is_not(None),
            )
        )
    for group in details.groups:
        session.add(DetailTrophyGroup(uid=uid, game_id=game.id, group_id=group.id, **identity))
    for trophy in sorted(details.trophies, key=lambda row: row.id):
        definition_values: dict[str, Any] = trophy.model_dump(
            exclude={"id", "earned", "earned_at", "earned_rate", "resources"}
        )
        definition_values.update(identity, trophy_id=trophy.id, observed_at=observed)
        statement = insert(TrophyDefinition).values(**definition_values)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    TrophyDefinition.service,
                    TrophyDefinition.set_id,
                    TrophyDefinition.trophy_id,
                ],
                set_={
                    key: value
                    for key, value in definition_values.items()
                    if key not in {"service", "set_id", "trophy_id"}
                },
                where=TrophyDefinition.observed_at <= observed,
            )
        )
        session.add(
            ProfileTrophy(
                uid=uid,
                game_id=game.id,
                account_id=details.profile.account_id,
                trophy_id=trophy.id,
                earned=trophy.earned,
                earned_at=trophy.earned_at,
                earned_rate=trophy.earned_rate,
                **identity,
            )
        )


def load_trophies(
    session: Session, uid: str, game_id: str
) -> tuple[list[Trophy], list[TrophyGroup]]:
    """Read only the owner's imported observations while sharing provider definitions."""
    rows = session.execute(
        select(TrophyDefinition, ProfileTrophy)
        .join(
            ProfileTrophy,
            (ProfileTrophy.service == TrophyDefinition.service)
            & (ProfileTrophy.set_id == TrophyDefinition.set_id)
            & (ProfileTrophy.trophy_id == TrophyDefinition.trophy_id),
        )
        .where(ProfileTrophy.uid == uid, ProfileTrophy.game_id == game_id)
        .order_by(TrophyDefinition.trophy_id)
    ).all()
    resources = load_resources(session, rows[0][0].service, rows[0][0].set_id) if rows else {}
    trophies = [
        Trophy(
            id=definition.trophy_id,
            group_id=definition.group_id,
            name=definition.name,
            description=definition.description,
            image_url=definition.image_url,
            type=cast(Literal["bronze", "silver", "gold", "platinum"], definition.type),
            hidden=definition.hidden,
            earned=progress.earned,
            earned_at=progress.earned_at,
            earned_rate=progress.earned_rate,
            resources=resources.get(definition.trophy_id, []),
        )
        for definition, progress in rows
    ]
    groups = session.scalars(
        select(TrophyGroupDefinition)
        .join(
            DetailTrophyGroup,
            (DetailTrophyGroup.service == TrophyGroupDefinition.service)
            & (DetailTrophyGroup.set_id == TrophyGroupDefinition.set_id)
            & (DetailTrophyGroup.group_id == TrophyGroupDefinition.group_id),
        )
        .where(DetailTrophyGroup.uid == uid, DetailTrophyGroup.game_id == game_id)
        .order_by(
            (TrophyGroupDefinition.group_id == "default").desc(), TrophyGroupDefinition.group_id
        )
    ).all()

    return trophies, [
        TrophyGroup(id=group.group_id, name=group.name)
        for group in groups
        if group.name is not None
    ]
