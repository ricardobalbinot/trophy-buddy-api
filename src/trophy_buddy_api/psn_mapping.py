"""Normalize Sony identities and earnings without guessing joins or unknown progress."""

import contextlib
import math
import re
from datetime import UTC, datetime
from trophy_buddy_api.errors import ApplicationError
from trophy_buddy_api.models import LibraryGame, Trophy
from typing import Any, cast
from urllib.parse import urlsplit


def timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def artwork(value: Any) -> str | None:
    """Allow HTTPS artwork only; missing or malformed artwork uses the frontend fallback."""
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)

        return (
            value
            if url.scheme == "https" and url.hostname and not url.username and not url.password
            else None
        )
    except ValueError:
        return None


def normalize_library(
    titles: list[dict[str, Any]], played: list[dict[str, Any]], links: dict[str, list[str]]
) -> list[LibraryGame]:
    """Join only Sony title-to-trophy-set mappings; identical display names prove no identity."""
    games: dict[str, LibraryGame] = {}
    platforms = {"ps4_game": "PS4", "ps5_native_game": "PS5", "pspc_game": "PC"}
    for title in titles:
        set_id = title["npCommunicationId"]
        key = f"trophy-{set_id}"
        if key in games or not re.fullmatch(r"NPWR\d+_\d{2}", set_id):
            raise ApplicationError("invalid_psn_identity")
        progress = title.get("progress")
        games[key] = LibraryGame.model_validate(
            {
                "id": key,
                "title": title["trophyTitleName"],
                "platform": title["trophyTitlePlatform"],
                "imageUrl": artwork(title.get("trophyTitleIconUrl")),
                "titleIds": [],
                "trophySetId": set_id,
                "trophyService": title.get("npServiceName"),
                "earned": sum(title["earnedTrophies"].values()),
                "total": sum(title["definedTrophies"].values()),
                "progress": max(0, min(100, progress)) if isinstance(progress, int) else None,
                "platinum": title["earnedTrophies"].get("platinum", 0) > 0,
            }
        )
    for title in played:
        title_id = title["titleId"]
        matched = [
            games[f"trophy-{set_id}"]
            for set_id in links.get(title_id, [])
            if f"trophy-{set_id}" in games
        ]
        if matched:
            for game in matched:
                if title_id not in game.title_ids:
                    game.title_ids.append(title_id)
        else:
            key = f"played-{title_id}"
            games[key] = LibraryGame(
                id=key,
                title=title["name"],
                platform=platforms.get(title.get("category", ""), "unknown"),
                image_url=artwork(title.get("imageUrl")),
                title_ids=[title_id],
                trophy_set_id=None,
                earned=None,
                total=None,
                progress=None,
                platinum=False,
            )

    return list(games.values())


def normalize_trophies(
    definitions: list[dict[str, Any]], earnings: list[dict[str, Any]]
) -> list[Trophy]:
    """Join by trophy ID and preserve unknown earnings, hidden metadata and official rarity."""
    progress = {row["trophyId"]: row for row in earnings}
    seen: set[int] = set()
    trophies: list[Trophy] = []
    for row in definitions:
        trophy_id = row["trophyId"]
        if type(trophy_id) is not int or trophy_id < 0 or trophy_id in seen:
            raise ApplicationError("invalid_psn_trophy")
        seen.add(trophy_id)
        entry = progress.get(trophy_id, {})
        earned = entry.get("earned") if type(entry.get("earned")) is bool else None
        rate: float | None
        try:
            rate = float(entry.get("trophyEarnedRate", ""))
            rate = rate if math.isfinite(rate) and 0 <= rate <= 100 else None
        except (ValueError, TypeError):
            rate = None
        earned_at = None
        if earned and entry.get("earnedDateTime"):
            with contextlib.suppress(ValueError):
                earned_at = (
                    datetime.fromisoformat(entry["earnedDateTime"].replace("Z", "+00:00"))
                    .astimezone(UTC)
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z")
                )
        trophies.append(
            Trophy.model_validate(
                {
                    "id": trophy_id,
                    "groupId": row.get("trophyGroupId") or "default",
                    "name": row.get("trophyName") or None,
                    "description": row.get("trophyDetail") or None,
                    "imageUrl": artwork(row.get("trophyIconUrl")),
                    "type": row["trophyType"],
                    "hidden": row.get("trophyHidden") is True,
                    "earned": cast(bool | None, earned),
                    "earnedAt": earned_at,
                    "earnedRate": rate,
                }
            )
        )

    return trophies
