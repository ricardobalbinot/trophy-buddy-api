"""Exercise real FastAPI authentication dependencies and contracts without live credentials."""

import pytest
from collections.abc import AsyncIterator
from copy import deepcopy
from httpx import ASGITransport, AsyncClient
from trophy_buddy_api.app import create_app
from trophy_buddy_api.config import Settings
from trophy_buddy_api.gateway import BackendUnavailable, InvalidSession
from typing import Any

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def snapshot() -> dict[str, Any]:
    base = {
        "id": "trophy-NPWR00001_00",
        "title": "Example Game",
        "platform": "PS5",
        "imageUrl": None,
        "titleIds": ["PPSA00001_00"],
        "trophySetId": "NPWR00001_00",
        "earned": 4,
        "total": 10,
        "progress": 60,
        "platinum": True,
    }

    return {
        "version": 1,
        "profile": {"onlineId": "Player", "accountId": "123456"},
        "syncedAt": "2026-10-30T12:00:00.000Z",
        "games": [
            base,
            {**base, "id": "zero", "earned": 0, "progress": 0, "platinum": False},
            {
                **base,
                "id": "unknown",
                "earned": None,
                "total": None,
                "progress": None,
                "platinum": False,
            },
        ],
    }


class FakeGateway:
    def __init__(self) -> None:
        self.payloads: dict[str, dict[str, Any]] = {"user-a": snapshot()}
        self.reads: list[str] = []
        self.unavailable = False

    def verify_token(self, token: str) -> str:
        if token not in {"user-a", "user-b"}:
            raise InvalidSession

        return token

    def read_library(self, uid: str) -> dict[str, Any] | None:
        self.reads.append(uid)
        if self.unavailable:
            raise BackendUnavailable

        return deepcopy(self.payloads.get(uid))


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
async def client(gateway: FakeGateway) -> AsyncIterator[AsyncClient]:
    settings = Settings(_env_file=None, firebase_project_id="test-project")

    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings, gateway)), base_url="http://testserver"
    ) as session:
        yield session


@pytest.mark.parametrize("path", ["/api/v1/library", "/api/v1/library/statistics"])
@pytest.mark.parametrize("header", [None, "Basic invalid", "Bearer expired", "Bearer revoked"])
async def test_private_routes_reject_untrusted_sessions(
    client: AsyncClient, gateway: FakeGateway, path: str, header: str | None
) -> None:
    headers = {"Authorization": header} if header else {}
    response = await client.get(path, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"error": "authentication_required"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert gateway.reads == []


async def test_snapshot_contract_preserves_nulls_timestamps_and_official_progress(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/v1/library", headers={"Authorization": "Bearer user-a"})
    data = response.json()["snapshot"]

    assert response.status_code == 200
    assert data["syncedAt"] == "2026-10-30T12:00:00.000Z"
    assert data["games"][0]["progress"] == 60
    assert data["games"][2]["earned"] is None
    assert "trophyService" not in data["games"][0]
    assert response.headers["cache-control"] == "private, no-store"


async def test_client_uid_cannot_select_another_users_snapshot(
    client: AsyncClient,
    gateway: FakeGateway,
) -> None:
    response = await client.get(
        "/api/v1/library?uid=user-a&accountId=123456",
        headers={"Authorization": "Bearer user-b"},
    )

    assert response.json() == {"snapshot": None}
    assert gateway.reads == ["user-b"]


async def test_statistics_separate_unknown_from_zero_and_platinum_from_dlc_completion(
    client: AsyncClient,
) -> None:
    response = await client.get(
        "/api/v1/library/statistics", headers={"Authorization": "Bearer user-a"}
    )

    assert response.json() == {
        "totalGames": 3,
        "gamesWithEarnedTrophies": 1,
        "gamesWithoutEarnedTrophies": 1,
        "gamesWithUnknownProgress": 1,
        "platinumGames": 1,
        "knownEarnedTrophies": 4,
    }


async def test_missing_snapshot_has_empty_statistics(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/library/statistics", headers={"Authorization": "Bearer user-b"}
    )

    assert all(value == 0 for value in response.json().values())


async def test_provider_failure_is_controlled_and_correlated(
    client: AsyncClient,
    gateway: FakeGateway,
) -> None:
    gateway.unavailable = True
    response = await client.get(
        "/api/v1/library", headers={"Authorization": "Bearer user-a", "X-Request-ID": "test-123"}
    )

    assert response.status_code == 503
    assert response.json() == {"error": "service_unavailable"}
    assert response.headers["x-request-id"] == "test-123"


async def test_invalid_stored_progress_is_not_silently_repaired(
    client: AsyncClient,
    gateway: FakeGateway,
) -> None:
    gateway.payloads["user-a"]["games"][0]["progress"] = 101

    response = await client.get("/api/v1/library", headers={"Authorization": "Bearer user-a"})

    assert response.status_code == 503


async def test_cors_accepts_only_configured_frontend_origins(client: AsyncClient) -> None:
    for origin, permitted in [("http://localhost:3000", True), ("https://unknown.invalid", False)]:
        response = await client.options(
            "/api/v1/library",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )

        assert (response.headers.get("access-control-allow-origin") == origin) is permitted


async def test_health_and_documentation_do_not_require_cloud_credentials(
    client: AsyncClient,
) -> None:
    assert (await client.get("/healthz")).json()["status"] == "ok"
    schema = (await client.get("/openapi.json")).json()

    assert "/api/v1/library" in schema["paths"]
    assert schema["paths"]["/api/v1/library"]["get"]["security"]
