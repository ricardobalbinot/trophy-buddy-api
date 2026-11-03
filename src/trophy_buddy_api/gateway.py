"""Firebase Authentication boundary and injectable private-library contract."""

import firebase_admin
from firebase_admin import auth, credentials
from functools import cached_property
from threading import Lock
from trophy_buddy_api.config import Settings
from typing import Any, Protocol


class InvalidSession(Exception):
    """The supplied Firebase identity cannot be trusted."""


class BackendUnavailable(Exception):
    """A required backend integration is unavailable without exposing provider details."""


class LibraryGateway(Protocol):
    """Keep domain behavior testable without real credentials or cloud access."""

    def verify_token(self, token: str) -> str: ...

    def read_library(self, uid: str) -> dict[str, Any] | None: ...


class FirebaseGateway:
    """Verify identity through Firebase Authentication without using Firestore."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._app_name = f"trophy-buddy-python-{id(self)}"
        self._app_lock = Lock()

    @cached_property
    def app(self) -> Any:
        """Initialize a private Admin app lazily; never expose credential exceptions."""

        if not self.settings.firebase_project_id:
            raise BackendUnavailable

        try:
            private_key = self.settings.firebase_private_key.get_secret_value()
            if self.settings.firebase_client_email and private_key:
                credential = credentials.Certificate(
                    {
                        "type": "service_account",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "project_id": self.settings.firebase_project_id,
                        "client_email": self.settings.firebase_client_email,
                        "private_key": private_key.replace("\\n", "\n"),
                    }
                )
            elif self.settings.google_application_credentials:
                credential = credentials.Certificate(self.settings.google_application_credentials)
            else:
                raise BackendUnavailable

            # Python 3.12 cached properties do not serialize concurrent first reads.
            with self._app_lock:
                try:
                    return firebase_admin.get_app(self._app_name)
                except ValueError:
                    return firebase_admin.initialize_app(
                        credential,
                        options={"projectId": self.settings.firebase_project_id, "httpTimeout": 10},
                        name=self._app_name,
                    )
        except Exception:
            raise BackendUnavailable from None

    def verify_token(self, token: str) -> str:
        """Check signature, expiry and revocation; derive the storage UID only from Firebase."""

        app = self.app
        try:
            identity = auth.verify_id_token(token, app=app, check_revoked=True)
            uid = identity.get("uid")
            if not isinstance(uid, str) or not uid or "/" in uid:
                raise InvalidSession

            return uid
        except (
            InvalidSession,
            ValueError,
            auth.InvalidIdTokenError,
            auth.UserDisabledError,
            auth.UserNotFoundError,
        ):
            raise InvalidSession from None
        except Exception:
            raise BackendUnavailable from None
