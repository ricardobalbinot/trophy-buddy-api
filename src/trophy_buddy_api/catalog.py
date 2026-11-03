"""Optional IGDB metadata with conservative identity matching and a shared PostgreSQL cache."""

import hashlib
import httpx
import json
import re
import threading
import time
import unicodedata
from datetime import UTC, datetime, timedelta
from trophy_buddy_api.config import Settings
from trophy_buddy_api.database import Database
from trophy_buddy_api.models import GameCatalog
from typing import Any

PLATFORMS = {"PS3": 9, "PS4": 48, "PS5": 167, "Vita": 46, "PSVITA": 46}


def normalized_title(title: str) -> str:
    """Normalize typography while preserving edition, remaster and sequel distinctions."""
    value = unicodedata.normalize("NFKD", re.sub(r"[™®©]", "", title)).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))

    return " ".join("".join(char if char.isalnum() else " " for char in value).split())


def names(entries: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(row["name"] for row in entries if isinstance(row.get("name"), str)))


def match_catalog(
    rows: list[dict[str, Any]], title: str, platforms: list[int]
) -> GameCatalog | None:
    candidates = [
        row
        for row in rows
        if isinstance(row.get("id"), int)
        and row["id"] > 0
        and isinstance(row.get("slug"), str)
        and re.fullmatch(r"[a-z0-9-]+", row["slug"])
        and any(item.get("id") in platforms for item in row.get("platforms", []))
        and any(
            normalized_title(name) == normalized_title(title)
            for name in [row.get("name", ""), *names(row.get("alternative_names", []))]
        )
    ]
    if len(candidates) != 1:
        return None
    row = candidates[0]
    cover = row.get("cover", {}).get("image_id", "")
    release = row.get("first_release_date")
    companies = row.get("involved_companies", [])

    return GameCatalog(
        id=row["id"],
        name=row["name"],
        source_url=f"https://www.igdb.com/games/{row['slug']}",
        cover_url=f"https://images.igdb.com/igdb/image/upload/t_cover_big_2x/{cover}.jpg"
        if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", cover)
        else None,
        release_date=datetime.fromtimestamp(release, UTC).isoformat().replace("+00:00", "Z")
        if isinstance(release, int)
        else None,
        developers=names([item["company"] for item in companies if item.get("developer") is True]),
        publishers=names([item["company"] for item in companies if item.get("publisher") is True]),
        genres=names(row.get("genres", [])),
        platforms=names(row.get("platforms", [])),
        main_story_hours=None,
    )


class CatalogClient:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self._token = ""
        self._expires = 0.0
        self._lock = threading.Lock()

    def request(self, client: httpx.Client, endpoint: str, query: str) -> list[dict[str, Any]]:
        """Reuse app OAuth tokens and renew once after an IGDB authentication failure."""
        for attempt in range(2):
            with self._lock:
                if self._expires <= time.time():
                    response = client.post(
                        "https://id.twitch.tv/oauth2/token",
                        data={
                            "client_id": self.settings.igdb_client_id,
                            "client_secret": self.settings.igdb_client_secret.get_secret_value(),
                            "grant_type": "client_credentials",
                        },
                    )
                    response.raise_for_status()
                    body = response.json()
                    self._token = body["access_token"]
                    self._expires = time.time() + max(0, body["expires_in"] - 60)
                token = self._token
            response = client.post(
                f"https://api.igdb.com/v4/{endpoint}",
                content=query,
                headers={
                    "Client-ID": self.settings.igdb_client_id,
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "text/plain",
                },
            )
            if response.status_code == 401 and attempt == 0:
                with self._lock:
                    if self._token == token:
                        self._expires = 0
                continue
            response.raise_for_status()
            result: list[dict[str, Any]] = response.json()
            if not isinstance(result, list):
                raise ValueError("Invalid catalogue response")

            return result
        raise ValueError("Catalogue authorization unavailable")

    def get(self, title: str, platform: str) -> GameCatalog | None:
        """Metadata is optional: provider/cache failures must not hide a user's trophy list."""
        platforms = sorted(
            {PLATFORMS[name.strip()] for name in platform.split(",") if name.strip() in PLATFORMS}
        )
        if (
            not platforms
            or not self.settings.igdb_client_id
            or not self.settings.igdb_client_secret.get_secret_value()
        ):
            return None
        key = hashlib.sha256(json.dumps([normalized_title(title), platforms]).encode()).hexdigest()
        try:
            hit, payload = self.database.cached_catalog(key)
            if hit:
                return GameCatalog.model_validate(payload) if payload else None
            with httpx.Client(timeout=5) as client:
                query = (
                    f"search {json.dumps(re.sub(r'[™®©]', '', title)[:200])}; "
                    "fields name,slug,alternative_names.name,cover.image_id,first_release_date,"
                    "involved_companies.developer,involved_companies.publisher,"
                    "involved_companies.company.name,genres.name,platforms.id,platforms.name; "
                    f"where platforms = ({','.join(map(str, platforms))}); limit 50;"
                )
                catalog = match_catalog(self.request(client, "games", query), title, platforms)
                if catalog:
                    try:
                        timing = self.request(
                            client,
                            "game_time_to_beats",
                            f"fields game_id,hastily,count; where game_id = {catalog.id}; limit 2;",
                        )
                        records = [row for row in timing if row.get("game_id") == catalog.id]
                        if (
                            len(records) == 1
                            and records[0].get("count", 0) > 0
                            and records[0].get("hastily", 0) > 0
                        ):
                            catalog.main_story_hours = records[0]["hastily"] / 3600
                    except Exception:
                        pass
            self.database.save_catalog(
                key,
                catalog.model_dump(by_alias=True, mode="json") if catalog else None,
                datetime.now(UTC) + timedelta(days=1),
            )

            return catalog
        except Exception:
            return None
