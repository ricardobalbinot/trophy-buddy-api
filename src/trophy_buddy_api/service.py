"""Application orchestration: validated imports, cached details and atomic publication."""

import re
from trophy_buddy_api.catalog import CatalogClient
from trophy_buddy_api.config import Settings
from trophy_buddy_api.database import Database
from trophy_buddy_api.errors import ApplicationError, NotFound
from trophy_buddy_api.gateway import FirebaseGateway
from trophy_buddy_api.models import GameDetails, LibrarySnapshot
from trophy_buddy_api.psn import PsnClient
from trophy_buddy_api.psn_mapping import timestamp
from trophy_buddy_api.psn_session import PsnSessionManager
from typing import Any


class ProductService:
    def __init__(self, settings: Settings, database: Database | None = None) -> None:
        self.database = database or Database(settings)
        self.identity = FirebaseGateway(settings)
        self.psn = PsnClient(PsnSessionManager(settings, self.database))
        self.catalog = CatalogClient(settings, self.database)

    def verify_token(self, token: str) -> str:
        return self.identity.verify_token(token)

    def read_library(self, uid: str) -> dict[str, Any] | None:
        return self.database.read_library(uid)

    def synchronize(self, uid: str, online_id: str) -> LibrarySnapshot:
        """Keep the previous library intact unless every required Sony collection validates."""
        operation = self.database.acquire(f"library:{uid}", 600)
        try:
            snapshot = self.psn.import_library(online_id)
            if len({game.id for game in snapshot.games}) != len(snapshot.games):
                raise ApplicationError("invalid_psn_identity")
            self.database.save_library(uid, snapshot, operation)

            return snapshot
        except Exception as error:
            self.database.release(f"library:{uid}", operation)
            if isinstance(error, ApplicationError):
                raise
            raise ApplicationError("library_sync_failed") from None

    def details(self, uid: str, game_id: str, refresh: bool) -> GameDetails:
        """Resolve the target exclusively from saved user state and reuse matching snapshots."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", game_id):
            raise NotFound
        payload = self.database.read_library(uid)
        if payload is None:
            raise NotFound
        library = LibrarySnapshot.model_validate(payload)
        game = next((entry for entry in library.games if entry.id == game_id), None)
        if game is None:
            raise NotFound
        previous = self.database.read_details(uid, game_id)
        same_context = (
            previous is not None
            and previous.profile.account_id == library.profile.account_id
            and previous.library_synced_at == library.synced_at
        )
        if not refresh and same_context and previous is not None:
            return previous
        resource = f"details:{uid}:{game_id}"
        operation = self.database.acquire(resource, 300)
        try:
            trophies, groups = self.psn.trophies(game, library.profile.account_id)
            if (
                previous is not None
                and previous.profile.account_id == library.profile.account_id
                and any(row.earned is not None for row in previous.trophies)
                and any(row.earned is None for row in trophies)
            ):
                raise ApplicationError("incomplete_trophy_earnings")
            result = GameDetails(
                profile=library.profile,
                library_synced_at=library.synced_at,
                fetched_at=timestamp(),
                game=game,
                catalog=self.catalog.get(game.title, game.platform),
                trophies=trophies,
                groups=groups,
                trophy_status="available" if game.trophy_set_id else "unavailable",
            )
            self.database.save_details(uid, result, operation)

            return result
        except Exception as error:
            self.database.release(resource, operation)
            if isinstance(error, ApplicationError):
                raise
            raise ApplicationError("game_details_failed") from None
