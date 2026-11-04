"""Low-cost guide discovery from an external index, with explicit version-specific review."""

import hashlib
import httpx
import json
import math
import re
from datetime import UTC, datetime
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column
from trophy_buddy_api.db_base import Base
from trophy_buddy_api.models import GuideOverview, LibraryGame
from trophy_buddy_api.trophy_storage import trophy_service
from typing import Any
from urllib.parse import quote

INDEX_URL = (
    "https://raw.githubusercontent.com/langdonx/trophy-guide-list-json/main/guides-v2.min.json"
)
INDEX_REPOSITORY = "https://github.com/langdonx/trophy-guide-list-json"
PLATFORM_FLAGS = {"PS3": 128, "PS4": 256, "PS5": 512, "PSVITA": 1024, "PC": 2048}
SOURCES = {
    1: ("psnprofiles", "PSNProfiles", "https://psnprofiles.com/guide/"),
    2: ("knoef", "Knoef", "https://knoef.info/"),
    4: ("platget", "PlatGet", "https://www.platget.com/"),
    8: ("playstationtrophies", "PlayStationTrophies", "https://www.playstationtrophies.org/game/"),
    16: ("powerpyx", "PowerPyx", "https://powerpyx.com/"),
}


class GuideIndexVersion(Base):
    __tablename__ = "guide_index_versions"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_url: Mapped[str] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IndexedGuide(Base):
    __tablename__ = "indexed_guides"
    __table_args__ = (
        CheckConstraint(
            "difficulty IS NULL OR difficulty BETWEEN 0 AND 10", name="ck_guides_difficulty"
        ),
        CheckConstraint("playthroughs IS NULL OR playthroughs > 0", name="ck_guides_runs"),
        CheckConstraint("platinum_hours IS NULL OR platinum_hours > 0", name="ck_guides_hours"),
        CheckConstraint("trophy_count IS NULL OR trophy_count > 0", name="ck_guides_trophy_count"),
    )
    entry_id: Mapped[str] = mapped_column(String(300), primary_key=True)
    version_digest: Mapped[str] = mapped_column(ForeignKey("guide_index_versions.digest"))
    content_digest: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(32))
    source_name: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(Text)
    game_name: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    attributes: Mapped[int] = mapped_column(Integer)
    external_game_ids: Mapped[list[int]] = mapped_column(JSONB)
    authors: Mapped[list[str]] = mapped_column(JSONB)
    difficulty: Mapped[float | None]
    playthroughs: Mapped[float | None]
    platinum_hours: Mapped[float | None]
    trophy_count: Mapped[int | None] = mapped_column(Integer)


class GuideBinding(Base):
    """A reviewed association, never inferred from a similarly named game at request time."""

    __tablename__ = "guide_bindings"
    __table_args__ = (
        ForeignKeyConstraint(["service", "set_id"], ["trophy_sets.service", "trophy_sets.set_id"]),
    )
    service: Mapped[str] = mapped_column(String(16), primary_key=True)
    set_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entry_id: Mapped[str] = mapped_column(ForeignKey("indexed_guides.entry_id"))
    version_digest: Mapped[str] = mapped_column(ForeignKey("guide_index_versions.digest"))
    content_digest: Mapped[str] = mapped_column(String(64))
    game_title: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(String(16))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rationale: Mapped[str] = mapped_column(Text)


class IndexRecord(BaseModel):
    a: int = Field(strict=True, ge=0, le=2147483647)
    n: str = Field(min_length=1, max_length=500)
    g: list[int | None]
    r: list[float | None]
    u: list[str]
    p: str | None = None
    t: list[int] | None = None


def game_name(title: str) -> str:
    """Normalize only the guide suffix and trademark marks, preserving edition identity."""
    return (
        re.sub(r"\s+", " ", re.sub(r"\s+trophy guide$", "", title, flags=re.I))
        .strip()
        .casefold()
        .replace("™", "")
        .replace("®", "")
    )


def positive_number(value: float | None, maximum: float | None = None) -> float | None:
    """Upstream negative sentinels, nulls and nonfinite values mean unknown, never zero."""
    if value is None or not math.isfinite(value) or value <= 0 or (maximum and value > maximum):
        return None

    return value


def parse_index(payload: dict[str, Any], digest: str) -> list[dict[str, Any]]:
    """Parse index facts without fetching guide pages; absent flags never imply false."""
    rows = []
    for key, raw in payload.items():
        if not isinstance(raw, dict) or type(raw.get("a")) is not int:
            raise ValueError("Invalid guide index attributes")
        attributes = raw["a"]
        sources = [entry for flag, entry in SOURCES.items() if attributes & flag]
        if not attributes & 32 or attributes & 64 or len(sources) != 1:
            continue
        record = IndexRecord.model_validate(raw)
        source, source_name, root = sources[0]
        path = key if source == "psnprofiles" else record.p or key
        if (
            not path
            or len(path) > 250
            or any(value in path for value in [":", "..", "\\", "?", "#"])
            or path.startswith("/")
        ):
            continue
        if source == "psnprofiles" and not path.isdigit():
            continue
        ratings = (record.r + [None, None, None])[:3]
        count = sum(record.t) if record.t and all(value >= 0 for value in record.t) else None
        count = count if count and count > 0 else None
        rows.append(
            {
                "entry_id": f"{source}:{key}",
                "version_digest": digest,
                "source": source,
                "source_name": source_name,
                "title": record.n,
                "game_name": game_name(record.n),
                "url": root + quote(path, safe="/-_"),
                "attributes": record.a,
                "external_game_ids": [
                    identity for identity in record.g if identity is not None and identity > 0
                ],
                "authors": record.u,
                "difficulty": positive_number(ratings[0], 10),
                "playthroughs": positive_number(ratings[1]),
                "platinum_hours": positive_number(ratings[2]),
                "trophy_count": count,
                "content_digest": hashlib.sha256(
                    json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "active": True,
            }
        )

    return rows


def download_index() -> tuple[str, list[dict[str, Any]]]:
    """Fetch the fixed public source once per administrative import, outside SQL transactions."""
    with (
        httpx.Client(timeout=30, follow_redirects=False) as client,
        client.stream("GET", INDEX_URL) as response,
    ):
        response.raise_for_status()
        chunks = bytearray()
        for chunk in response.iter_bytes():
            chunks.extend(chunk)
            if len(chunks) > 32 * 1024 * 1024:
                raise ValueError("Guide index exceeds the supported size")
    digest = hashlib.sha256(chunks).hexdigest()
    payload = json.loads(chunks)
    if not isinstance(payload, dict) or not payload:
        raise ValueError("Invalid guide index")

    return digest, parse_index(payload, digest)


def save_index(session: Session, digest: str, rows: list[dict[str, Any]]) -> None:
    """Publish one validated index atomically; changed records require binding review again."""
    session.execute(
        insert(GuideIndexVersion)
        .values(digest=digest, source_url=INDEX_URL, retrieved_at=datetime.now(UTC))
        .on_conflict_do_nothing()
    )
    session.execute(update(IndexedGuide).values(active=False))
    for offset in range(0, len(rows), 250):
        statement = insert(IndexedGuide).values(rows[offset : offset + 250])
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[IndexedGuide.entry_id],
                set_={
                    column.name: getattr(statement.excluded, column.name)
                    for column in IndexedGuide.__table__.columns
                    if column.name != "entry_id"
                },
            )
        )


def bind_guide(session: Session, game: LibraryGame, entry_id: str, rationale: str) -> None:
    """Require explicit review plus matching edition, platform and known trophy count."""
    guide = session.get(IndexedGuide, entry_id)
    flag = PLATFORM_FLAGS.get(game.platform)
    if (
        not game.trophy_set_id
        or guide is None
        or not guide.active
        or not flag
        or not guide.attributes & flag
        or guide.game_name != game_name(game.title)
    ):
        raise ValueError("Guide edition or platform does not match the stored game")
    if game.total is None or guide.trophy_count != game.total or len(rationale.strip()) < 30:
        raise ValueError("Guide review requires matching trophy counts and a written rationale")
    values = dict(
        service=trophy_service(game),
        set_id=game.trophy_set_id,
        entry_id=entry_id,
        version_digest=guide.version_digest,
        content_digest=guide.content_digest,
        game_title=game.title,
        platform=game.platform,
        reviewed_at=datetime.now(UTC),
        rationale=rationale,
    )
    session.execute(
        insert(GuideBinding)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[GuideBinding.service, GuideBinding.set_id],
            set_=values,
        )
    )


def load_guide(session: Session, game: LibraryGame) -> GuideOverview | None:
    """Serve reviewed, version-pinned metadata locally; uncertain requirements remain unknown."""
    if not game.trophy_set_id:
        return None
    row = session.execute(
        select(GuideBinding, IndexedGuide, GuideIndexVersion)
        .join(IndexedGuide, GuideBinding.entry_id == IndexedGuide.entry_id)
        .join(GuideIndexVersion, IndexedGuide.version_digest == GuideIndexVersion.digest)
        .where(
            GuideBinding.service == trophy_service(game),
            GuideBinding.set_id == game.trophy_set_id,
            GuideBinding.content_digest == IndexedGuide.content_digest,
            IndexedGuide.active.is_(True),
            GuideBinding.platform == game.platform,
            IndexedGuide.trophy_count == game.total,
        )
    ).one_or_none()
    if row is None:
        return None
    binding, guide, version = row

    return GuideOverview(
        title=guide.title,
        source_name=guide.source_name,
        source_url=guide.url,
        index_url=INDEX_REPOSITORY,
        retrieved_at=version.retrieved_at,
        reviewed_at=binding.reviewed_at,
        difficulty=guide.difficulty,
        playthroughs=guide.playthroughs,
        platinum_hours=guide.platinum_hours,
        missables="confirmed_some" if guide.attributes & 16384 else "unknown",
        online="confirmed_some" if guide.attributes & 32768 else "unknown",
        buggy="confirmed_some" if guide.attributes & 8192 else "unknown",
    )
