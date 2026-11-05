"""Resolve local guide candidates on demand and persist shared analysis requests."""

from datetime import UTC, datetime, timedelta
from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column
from trophy_buddy_api.db_base import Base
from trophy_buddy_api.guide_index import (
    PLATFORM_FLAGS,
    GuideBinding,
    IndexedGuide,
    bind_guide,
    game_name,
    load_guide,
)
from trophy_buddy_api.models import GameDetails
from trophy_buddy_api.trophy_storage import TrophySet, trophy_service


class GuideDiscovery(Base):
    __tablename__ = "guide_discoveries"
    __table_args__ = (
        ForeignKeyConstraint(["service", "set_id"], ["trophy_sets.service", "trophy_sets.set_id"]),
    )
    service: Mapped[str] = mapped_column(String(16), primary_key=True)
    set_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(32))
    retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GuideAnalysisRequest(Base):
    """Durable deduplicated intent; blocked requests are not advertised as running work."""

    __tablename__ = "guide_analysis_requests"
    entry_id: Mapped[str] = mapped_column(ForeignKey("indexed_guides.entry_id"), primary_key=True)
    content_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(16), primary_key=True)
    state: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(100))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def discover_guide(session: Session, details: GameDetails) -> GameDetails:
    """Serialize exact local discovery and retain stale reviews for operator action."""
    game = details.game
    if not game.trophy_set_id:
        return details.model_copy(update={"guide_discovery": "unsupported"})
    now = datetime.now(UTC)
    identity = {"service": trophy_service(game), "set_id": game.trophy_set_id}
    session.execute(insert(TrophySet).values(**identity).on_conflict_do_nothing())
    session.execute(
        insert(GuideDiscovery)
        .values(**identity, state="pending", retry_at=now)
        .on_conflict_do_nothing()
    )
    discovery = session.scalar(
        select(GuideDiscovery)
        .where(
            GuideDiscovery.service == identity["service"],
            GuideDiscovery.set_id == identity["set_id"],
        )
        .with_for_update()
    )
    assert discovery is not None
    guide = load_guide(session, game)
    if guide is None:
        binding = session.get(GuideBinding, (identity["service"], identity["set_id"]))
        if binding is not None:
            return details.model_copy(update={"guide": None, "guide_discovery": "review_required"})
        if discovery.retry_at > now:
            return details.model_copy(update={"guide": None, "guide_discovery": discovery.state})
        flag = PLATFORM_FLAGS.get(game.platform, 0)
        candidates = session.scalars(
            select(IndexedGuide)
            .where(
                IndexedGuide.active.is_(True),
                IndexedGuide.game_name == game_name(game.title),
                IndexedGuide.trophy_count == game.total,
            )
            .order_by(IndexedGuide.entry_id)
        ).all()
        candidates = [candidate for candidate in candidates if flag and candidate.attributes & flag]
        # Prefer an unambiguous PSNProfiles guide, then each documented fallback source.
        selected = []
        for source in ["psnprofiles", "powerpyx", "playstationtrophies", "platget", "knoef"]:
            selected = [candidate for candidate in candidates if candidate.source == source]
            if selected:
                break
        if len(selected) != 1 or game.total is None:
            discovery.state = "ambiguous" if selected else "not_found"
            discovery.retry_at = now + timedelta(hours=6)

            return details.model_copy(update={"guide": None, "guide_discovery": discovery.state})
        bind_guide(
            session,
            game,
            selected[0].entry_id,
            "Automatic exact edition, platform and trophy-count match; "
            "unique source candidate, not a manual content review.",
            match_method="automatic",
        )
        guide = load_guide(session, game)
    discovery.state = "matched"
    discovery.retry_at = now

    binding = session.get(GuideBinding, (identity["service"], identity["set_id"]))
    assert binding is not None
    session.execute(
        insert(GuideAnalysisRequest)
        .values(
            entry_id=binding.entry_id,
            content_digest=binding.content_digest,
            schema_version="1",
            state="blocked",
            reason="analysis_worker_not_configured",
            requested_at=now,
        )
        .on_conflict_do_nothing()
    )

    request = session.get(GuideAnalysisRequest, (binding.entry_id, binding.content_digest, "1"))
    assert request is not None

    return details.model_copy(
        update={"guide": guide, "guide_discovery": "matched", "guide_analysis": request.state}
    )
