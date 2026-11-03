"""Dedicated PSN identity, renewable OAuth and authenticated encryption in PostgreSQL."""

import base64
import hashlib
import httpx
import json
import os
import re
import time
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from trophy_buddy_api.config import Settings
from trophy_buddy_api.database import Database
from trophy_buddy_api.errors import ApplicationError, ReconnectRequired
from typing import Any
from urllib.parse import parse_qs, urlsplit

OAUTH = "https://ca.account.sony.com/api/authz/v3/oauth"
PROFILE = "https://m.np.playstation.com/api/userProfile/v1/internal/users"
CLIENT_ID = "09515159-7237-4370-9b40-3806e67c0891"
# Public client identification used by the existing community adapter, not an app secret.
CLIENT_AUTH = "MDk1MTUxNTktNzIzNy00MzcwLTliNDAtMzgwNmU2N2MwODkxOnVjUGprYTV0bnRCMktxc1A="
CONTEXT = b"trophy-buddy:psn-service-session:v1"


def decode64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class PsnSessionManager:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database

    def cipher(self) -> AESGCM:
        try:
            key = base64.b64decode(
                self.settings.psn_token_encryption_key.get_secret_value(), validate=True
            )
            if len(key) != 32:
                raise ValueError

            return AESGCM(key)
        except Exception:
            raise ApplicationError("psn_encryption_not_configured") from None

    def encrypt(self, session: dict[str, Any]) -> str:
        """Use fresh nonces and the same versioned envelope as the previous Node implementation."""
        nonce = os.urandom(12)
        sealed = self.cipher().encrypt(nonce, json.dumps(session).encode(), CONTEXT)
        parts = [nonce, sealed[-16:], sealed[:-16]]

        return "v1." + ".".join(
            base64.urlsafe_b64encode(part).decode().rstrip("=") for part in parts
        )

    def decrypt(self, encrypted: str) -> dict[str, Any]:
        try:
            version, nonce, tag, payload = encrypted.split(".")
            if version != "v1":
                raise ValueError
            result: dict[str, Any] = json.loads(
                self.cipher().decrypt(decode64(nonce), decode64(payload) + decode64(tag), CONTEXT)
            )
            for key in (
                "accountId",
                "onlineId",
                "accessToken",
                "refreshToken",
                "accessExpiresAt",
                "refreshExpiresAt",
            ):
                if not result.get(key):
                    raise ValueError

            return result
        except Exception:
            raise ReconnectRequired from None

    def exchange(self, client: httpx.Client, data: dict[str, str]) -> dict[str, Any]:
        """Require reconnection for invalid OAuth grants; preserve transport failures."""
        response = client.post(
            f"{OAUTH}/token",
            data={**data, "token_format": "jwt"},
            headers={"Authorization": f"Basic {CLIENT_AUTH}"},
        )
        if response.status_code in (400, 401, 403):
            raise ReconnectRequired
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        if (
            not body.get("access_token")
            or not body.get("refresh_token")
            or not isinstance(body.get("expires_in"), int)
            or not isinstance(body.get("refresh_token_expires_in"), int)
        ):
            raise ReconnectRequired

        return body

    def get(self) -> dict[str, Any]:
        """Serialize session renewal across API processes without holding SQL locks during OAuth."""
        npsso = self.settings.psn_service_npsso.get_secret_value().strip()
        digest = hashlib.sha256(npsso.encode()).hexdigest() if npsso else None
        state = self.database.session_state()
        now = int(time.time() * 1000)
        if (
            state
            and state["accessExpiresAt"] > now + 60_000
            and (not digest or digest == state["bootstrapDigest"])
        ):
            return self.decrypt(state["encrypted"])

        operation = self.database.acquire("psn-session", 90)
        try:
            # Re-read after acquiring: another process may have just renewed successfully.
            state = self.database.session_state()
            if (
                state
                and state["accessExpiresAt"] > now + 60_000
                and (not digest or digest == state["bootstrapDigest"])
            ):
                self.database.release("psn-session", operation, 0)

                return self.decrypt(state["encrypted"])
            with httpx.Client(timeout=15, follow_redirects=False) as client:
                previous = self.decrypt(state["encrypted"]) if state else None
                if previous is None or (state and digest and digest != state["bootstrapDigest"]):
                    if not re.fullmatch(r"[A-Za-z0-9_-]{64}", npsso):
                        raise ReconnectRequired
                    response = client.get(
                        f"{OAUTH}/authorize",
                        params={
                            "access_type": "offline",
                            "client_id": CLIENT_ID,
                            "redirect_uri": "com.scee.psxandroid.scecompcall://redirect",
                            "response_type": "code",
                            "scope": "psn:mobile.v2.core psn:clientapp",
                        },
                        headers={"Cookie": f"npsso={npsso}"},
                    )
                    code = parse_qs(urlsplit(response.headers.get("location", "")).query).get(
                        "code", [""]
                    )[0]
                    if not code:
                        raise ReconnectRequired
                    body = self.exchange(
                        client,
                        {
                            "code": code,
                            "grant_type": "authorization_code",
                            "redirect_uri": "com.scee.psxandroid.scecompcall://redirect",
                        },
                    )
                    account = json.loads(decode64(body["id_token"].split(".")[1])).get("sub")
                    if not isinstance(account, str) or not account.isdigit():
                        raise ReconnectRequired
                    profile_response = client.get(
                        f"{PROFILE}/{account}/profiles",
                        headers={"Authorization": f"Bearer {body['access_token']}"},
                    )
                    profile_response.raise_for_status()
                    profile = profile_response.json()
                    if profile.get("isMe") is not True or not profile.get("onlineId"):
                        raise ReconnectRequired
                    session = {"accountId": account, "onlineId": profile["onlineId"]}
                else:
                    if previous["refreshExpiresAt"] <= now:
                        raise ReconnectRequired
                    body = self.exchange(
                        client,
                        {
                            "refresh_token": previous["refreshToken"],
                            "grant_type": "refresh_token",
                            "scope": "psn:mobile.v2.core psn:clientapp",
                        },
                    )
                    if (
                        body.get("id_token")
                        and json.loads(decode64(body["id_token"].split(".")[1])).get("sub")
                        != previous["accountId"]
                    ):
                        raise ReconnectRequired
                    session = previous
                issued = int(time.time() * 1000)
                session.update(
                    accessToken=body["access_token"],
                    refreshToken=body["refresh_token"],
                    accessExpiresAt=issued + body["expires_in"] * 1000,
                    refreshExpiresAt=issued + body["refresh_token_expires_in"] * 1000,
                )
            self.database.save_session(
                {
                    "encrypted": self.encrypt(session),
                    "accessExpiresAt": session["accessExpiresAt"],
                    "bootstrapDigest": digest or (state["bootstrapDigest"] if state else None),
                },
                operation,
            )

            return session
        except Exception as error:
            self.database.release("psn-session", operation, 5)
            if isinstance(error, ApplicationError):
                raise
            raise ApplicationError("psn_session_unavailable") from None
