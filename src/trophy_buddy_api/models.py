"""Typed API contracts compatible with the React application's existing snapshots."""

from pydantic import BaseModel, ConfigDict, Field, StrictInt
from pydantic.alias_generators import to_camel
from typing import Annotated, Literal

Count = Annotated[StrictInt, Field(ge=0)]


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
    trophy_service: Literal["trophy", "trophy2"] | None = None
    earned: Count | None
    total: Count | None
    progress: Annotated[StrictInt, Field(ge=0, le=100)] | None
    platinum: bool


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
