"""Curated external help is independent of Sony imports and never fetched during navigation."""

import hashlib
from datetime import UTC, datetime
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    select,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column
from trophy_buddy_api.db_base import Base
from trophy_buddy_api.models import TrophyResource
from typing import Literal
from urllib.parse import urlsplit


class ResourceDraft(BaseModel):
    service: Literal["trophy", "trophy2"]
    set_id: str = Field(pattern=r"^NPWR\d+_\d{2}$")
    trophy_id: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=180)
    source_name: str = Field(min_length=1, max_length=100)
    source_url: str = Field(max_length=2000)
    kind: Literal["video", "article"]
    language: str = Field(pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$", max_length=16)
    contains_spoilers: bool = True

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        """Accept explicit HTTPS links without credentials; no URL is fetched or embedded."""
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("A resource requires a public HTTPS URL without credentials")

        return value


class CuratedResource(Base):
    __tablename__ = "trophy_resources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["service", "set_id", "trophy_id"],
            ["trophies.service", "trophies.set_id", "trophies.trophy_id"],
        ),
        CheckConstraint("kind IN ('video', 'article')", name="ck_resources_kind"),
        CheckConstraint("source_url LIKE 'https://%'", name="ck_resources_https"),
        Index("ix_resources_trophy", "service", "set_id", "trophy_id"),
    )
    resource_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    service: Mapped[str] = mapped_column(String(16))
    set_id: Mapped[str] = mapped_column(String(64))
    trophy_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(180))
    source_name: Mapped[str] = mapped_column(String(100))
    source_url: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))
    language: Mapped[str] = mapped_column(String(16))
    contains_spoilers: Mapped[bool] = mapped_column(Boolean)
    published: Mapped[bool] = mapped_column(Boolean)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def save_resource(session: Session, draft: ResourceDraft, publish: bool = False) -> str:
    """Upsert an explicitly reviewed resource; the trophy foreign key rejects guessed identities."""
    identity = (
        f"{draft.service}:{draft.set_id}:{draft.trophy_id}:{draft.language}:{draft.source_url}"
    )
    resource_id = hashlib.sha256(identity.encode()).hexdigest()
    values = {**draft.model_dump(), "published": publish, "reviewed_at": datetime.now(UTC)}
    session.execute(
        insert(CuratedResource)
        .values(resource_id=resource_id, **values)
        .on_conflict_do_update(
            index_elements=[CuratedResource.resource_id],
            set_=values,
        )
    )

    return resource_id


def load_resources(session: Session, service: str, set_id: str) -> dict[int, list[TrophyResource]]:
    """Read published help once per trophy set; drafts never enter the public detail contract."""
    rows = session.scalars(
        select(CuratedResource)
        .where(
            CuratedResource.service == service,
            CuratedResource.set_id == set_id,
            CuratedResource.published.is_(True),
        )
        .order_by(CuratedResource.resource_id)
    ).all()
    resources: dict[int, list[TrophyResource]] = {}
    for row in rows:
        resources.setdefault(row.trophy_id, []).append(
            TrophyResource.model_validate(
                {
                    "id": row.resource_id,
                    "title": row.title,
                    "source_url": row.source_url,
                    "source_name": row.source_name,
                    "kind": row.kind,
                    "language": row.language,
                    "contains_spoilers": row.contains_spoilers,
                }
            )
        )

    return resources
