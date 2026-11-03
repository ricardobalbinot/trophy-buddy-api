"""Bounded HTTPX adapter for the Sony endpoints already used by Trophy Buddy."""

import httpx
import re
import time
from trophy_buddy_api.errors import ApplicationError, ReconnectRequired
from trophy_buddy_api.models import LibraryGame, LibrarySnapshot, Profile, Trophy, TrophyGroup
from trophy_buddy_api.psn_mapping import normalize_library, normalize_trophies, timestamp
from trophy_buddy_api.psn_session import PsnSessionManager
from typing import Any

BASE = "https://m.np.playstation.com/api"


class PsnClient:
    """Keep community endpoint details outside application services; never log HTTPX requests."""

    def __init__(self, sessions: PsnSessionManager) -> None:
        self.sessions = sessions

    def request(
        self,
        client: httpx.Client,
        session: dict[str, Any],
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for attempt in range(2):
            try:
                response = client.request(
                    "POST" if body else "GET",
                    f"{BASE}/{path}",
                    params=params,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {session['accessToken']}",
                        "Accept-Language": "pt-BR",
                    },
                )
                if response.status_code == 401:
                    raise ReconnectRequired
                if response.status_code == 429:
                    raise ApplicationError("psn_rate_limited", 429)
                if response.status_code >= 500 and attempt == 0:
                    time.sleep(0.3)
                    continue
                response.raise_for_status()
                result: dict[str, Any] = response.json()
                if not isinstance(result, dict) or result.get("error"):
                    raise ApplicationError("psn_data_unavailable")

                return result
            except ApplicationError:
                raise
            except (httpx.HTTPError, ValueError):
                raise ApplicationError("psn_data_unavailable") from None

        raise ApplicationError("psn_data_unavailable")

    def pages(
        self,
        client: httpx.Client,
        session: dict[str, Any],
        path: str,
        collection: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Reject incomplete, stalled or unbounded pagination before any library publication."""
        result: list[dict[str, Any]] = []
        offset = 0
        deadline = time.monotonic() + 180
        for _ in range(100):
            if time.monotonic() > deadline:
                raise ApplicationError("psn_import_timeout")
            page = self.request(
                client, session, path, params={**(params or {}), "offset": offset, "limit": 100}
            )
            items = page.get(collection)
            count = page.get("totalItemCount")
            if (
                not isinstance(items, list)
                or type(count) is not int
                or count < 0
                or not all(isinstance(item, dict) for item in items)
            ):
                raise ApplicationError("invalid_psn_page")
            result.extend(items)
            following = page.get("nextOffset")
            if following is None:
                if len(result) != count:
                    raise ApplicationError("incomplete_psn_page")

                return result
            if type(following) is not int or following <= offset or not items:
                raise ApplicationError("invalid_psn_pagination")
            offset = following

        raise ApplicationError("psn_pagination_limit")

    def import_library(self, online_id: str) -> LibrarySnapshot:
        session = self.sessions.get()
        with httpx.Client(timeout=15) as client:
            account_id = session["accountId"]
            if online_id.casefold() != session["onlineId"].casefold():
                search = self.request(
                    client,
                    session,
                    "search/v1/universalSearch",
                    body={
                        "searchTerm": online_id,
                        "domainRequests": [{"domain": "SocialAllAccounts"}],
                    },
                )
                matches: set[str] = set()
                domains = search.get("domainResponses")
                if not isinstance(domains, list):
                    raise ApplicationError("psn_search_unavailable")
                for domain in domains:
                    for entry in domain.get("results", []):
                        profile = entry.get("socialMetadata", {})
                        if str(
                            profile.get("onlineId", "")
                        ).casefold() == online_id.casefold() and re.fullmatch(
                            r"\d+", str(profile.get("accountId", ""))
                        ):
                            matches.add(str(profile["accountId"]))
                if len(matches) != 1:
                    raise ApplicationError("psn_profile_not_found", 404)
                account_id = matches.pop()
            profile = self.request(
                client, session, f"userProfile/v1/internal/users/{account_id}/profiles"
            )
            if str(profile.get("onlineId", "")).casefold() != online_id.casefold():
                raise ApplicationError("psn_profile_not_found", 404)
            titles = self.pages(
                client, session, f"trophy/v1/users/{account_id}/trophyTitles", "trophyTitles"
            )
            played = self.pages(client, session, f"gamelist/v2/users/{account_id}/titles", "titles")
            ids = list(
                dict.fromkeys(
                    row["titleId"]
                    for row in played
                    if re.fullmatch(r"(?:CUSA|PPSA)\d+_\d{2}", row["titleId"])
                )
            )
            links: dict[str, list[str]] = {}
            for offset in range(0, len(ids), 5):
                response = self.request(
                    client,
                    session,
                    f"trophy/v1/users/{account_id}/titles/trophyTitles",
                    params={"npTitleIds": ",".join(ids[offset : offset + 5])},
                )
                if not isinstance(response.get("titles"), list):
                    raise ApplicationError("psn_mapping_unavailable")
                for title in response["titles"]:
                    links[title["npTitleId"]] = [
                        row["npCommunicationId"] for row in title.get("trophyTitles", [])
                    ]

        return LibrarySnapshot(
            version=1,
            profile=Profile(account_id=account_id, online_id=profile["onlineId"]),
            synced_at=timestamp(),
            games=normalize_library(titles, played, links),
        )

    def trophies(
        self, game: LibraryGame, account_id: str
    ) -> tuple[list[Trophy], list[TrophyGroup]]:
        if not game.trophy_set_id:
            return [], []
        session = self.sessions.get()
        service = game.trophy_service or (
            "trophy2" if "PS5" in game.platform.split(",") else "trophy"
        )
        params = {"npServiceName": service}
        path = f"trophy/v1/npCommunicationIds/{game.trophy_set_id}/trophyGroups"
        with httpx.Client(timeout=15) as client:
            definitions = self.pages(client, session, f"{path}/all/trophies", "trophies", params)
            try:
                earnings = self.pages(
                    client,
                    session,
                    f"trophy/v1/users/{account_id}/npCommunicationIds/{game.trophy_set_id}/trophyGroups/all/trophies",
                    "trophies",
                    params,
                )
            except ApplicationError:
                earnings = []
            try:
                response = self.request(client, session, path, params=params)
                groups = [
                    TrophyGroup(id=row["trophyGroupId"], name=row["trophyGroupName"])
                    for row in response.get("trophyGroups", [])
                ]
            except ApplicationError:
                groups = []

        return normalize_trophies(definitions, earnings), groups
