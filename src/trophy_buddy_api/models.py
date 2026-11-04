"""Typed API contracts compatible with the React application's existing snapshots."""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt
from pydantic.alias_generators import to_camel
from typing import Annotated, Literal

Count = Annotated[StrictInt, Field(ge=0)]
LibrarySort = Literal["last_played_at", "trophy_progress_updated_at", "title"]
SortDirection = Literal["asc", "desc"]


class ApiModel(BaseModel):
    """Use Python identifiers internally and preserve camelCase at the REST boundary."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class Profile(ApiModel):
    online_id: str = Field(min_length=1)
    account_id: str = Field(pattern=r"^\d+$")


class LibraryGame(ApiModel):
    id: str = Field(min_length=1)
    title: str
    platform: str
    image_url: str | None
    title_ids: list[str]
    trophy_set_id: str | None
    trophy_service: Literal["trophy", "trophy2"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    earned: Count | None
    total: Count | None
    progress: Annotated[StrictInt, Field(ge=0, le=100)] | None
    platinum: bool
    last_played_at: AwareDatetime | None = None
    trophy_progress_updated_at: AwareDatetime | None = None


class LibrarySnapshot(ApiModel):
    version: Literal[1]
    profile: Profile
    # Preserve the exact stored timestamp: the frontend uses it to invalidate detailed snapshots.
    synced_at: str = Field(min_length=1)
    games: list[LibraryGame]


class LibraryResponse(ApiModel):
    snapshot: LibrarySnapshot | None


class LibraryStatistics(ApiModel):
    total_games: Count
    games_with_earned_trophies: Count
    games_without_earned_trophies: Count
    games_with_unknown_progress: Count
    platinum_games: Count
    known_earned_trophies: Count


class SyncRequest(ApiModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    online_id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{2,15}$")


class DetailsRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    refresh: bool = False


class Trophy(ApiModel):
    id: Count
    group_id: str
    name: str | None
    description: str | None
    image_url: str | None
    type: Literal["bronze", "silver", "gold", "platinum"]
    hidden: bool
    earned: bool | None
    earned_at: str | None
    earned_rate: Annotated[float, Field(ge=0, le=100)] | None


class TrophyGroup(ApiModel):
    id: str
    name: str


class GameCatalog(ApiModel):
    id: int
    name: str
    source_url: str
    cover_url: str | None
    release_date: str | None
    developers: list[str]
    publishers: list[str]
    genres: list[str]
    platforms: list[str]
    main_story_hours: float | None


class GameDetails(ApiModel):
    version: Literal[1] = 1
    profile: Profile
    library_synced_at: str
    fetched_at: str
    game: LibraryGame
    catalog: GameCatalog | None
    trophies: list[Trophy]
    groups: list[TrophyGroup]
    trophy_status: Literal["available", "unavailable"]


class DetailsResponse(ApiModel):
    details: GameDetails
