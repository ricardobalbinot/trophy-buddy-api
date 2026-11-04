"""Library use cases independent of FastAPI request and response objects."""

from pydantic import ValidationError
from trophy_buddy_api.gateway import BackendUnavailable, LibraryGateway
from trophy_buddy_api.models import LibrarySnapshot, LibrarySort, LibraryStatistics, SortDirection


def read_library(
    gateway: LibraryGateway,
    uid: str,
    sort: LibrarySort = "last_played_at",
    direction: SortDirection = "desc",
) -> LibrarySnapshot | None:
    """Validate persisted data and preserve unknown progress instead of inventing zeroes."""

    payload = gateway.read_library(uid, sort, direction)
    if payload is None:
        return None

    try:
        return LibrarySnapshot.model_validate(payload)
    except ValidationError:
        raise BackendUnavailable from None


def calculate_statistics(snapshot: LibrarySnapshot | None) -> LibraryStatistics:
    """Count only confirmed earnings; an earned platinum does not require DLC completion."""

    games = snapshot.games if snapshot else []

    return LibraryStatistics(
        total_games=len(games),
        games_with_earned_trophies=sum(
            game.earned is not None and game.earned > 0 for game in games
        ),
        games_without_earned_trophies=sum(game.earned == 0 for game in games),
        games_with_unknown_progress=sum(game.earned is None for game in games),
        platinum_games=sum(game.platinum for game in games),
        known_earned_trophies=sum(game.earned for game in games if game.earned is not None),
    )
