"""Application factory, authentication dependencies and stable REST error contracts."""

import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from trophy_buddy_api.config import Settings, get_settings
from trophy_buddy_api.database import Library
from trophy_buddy_api.errors import ApplicationError
from trophy_buddy_api.gateway import (
    BackendUnavailable,
    InvalidSession,
    LibraryGateway,
)
from trophy_buddy_api.library import calculate_statistics, read_library
from trophy_buddy_api.models import (
    DetailsRequest,
    DetailsResponse,
    LibraryResponse,
    LibrarySort,
    LibraryStatistics,
    SortDirection,
    SyncRequest,
)
from trophy_buddy_api.service import ProductService
from typing import Annotated, Any, cast
from uuid import uuid4

logger = logging.getLogger("trophy_buddy_api.requests")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.propagate = False
bearer = HTTPBearer(auto_error=False)


def get_gateway(request: Request) -> LibraryGateway:
    """Resolve the app-scoped adapter without accepting a user or account ID from the caller."""

    return cast(LibraryGateway, request.app.state.gateway)


def authenticated_uid(
    credential: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    gateway: Annotated[LibraryGateway, Depends(get_gateway)],
) -> str:
    """Authenticate in FastAPI's thread pool because the Firebase SDK performs synchronous I/O."""

    if credential is None:
        raise InvalidSession

    return gateway.verify_token(credential.credentials)


def create_app(settings: Settings | None = None, gateway: LibraryGateway | None = None) -> FastAPI:
    """Build production and test applications through the same authentication and routing paths."""

    configuration = settings or get_settings()
    service = ProductService(configuration)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if "engine" in service.database.__dict__:
            service.database.engine.dispose()

    app = FastAPI(title="Trophy Buddy API", version="0.2.0", lifespan=lifespan)
    app.state.gateway = gateway if gateway is not None else service
    app.state.service = service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=configuration.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Correlate requests without logging headers, token values, queries or provider errors."""

        supplied_id = request.headers.get("x-request-id", "")
        request_id = (
            supplied_id if re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", supplied_id) else uuid4().hex
        )
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["Cache-Control"] = "private, no-store"
        logger.info(
            "request_completed method=%s path=%s status=%s duration_ms=%.1f request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
            request_id,
        )

        return response

    @app.exception_handler(InvalidSession)
    async def invalid_session(_request: Request, _error: InvalidSession) -> JSONResponse:
        return JSONResponse(
            {"error": "authentication_required"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(BackendUnavailable)
    async def backend_unavailable(_request: Request, _error: BackendUnavailable) -> JSONResponse:
        return JSONResponse({"error": "service_unavailable"}, status_code=503)

    @app.exception_handler(ApplicationError)
    async def application_error(_request: Request, error: ApplicationError) -> JSONResponse:
        headers = {"Retry-After": "15"} if error.status == 429 else None

        return JSONResponse({"error": error.code}, status_code=error.status, headers=headers)

    @app.get("/healthz", tags=["Health"])
    def health() -> dict[str, str]:
        """Report process liveness without pretending that external integrations are healthy."""

        return {"status": "ok", "service": "trophy-buddy-api"}

    @app.get("/readyz", tags=["Health"])
    def readiness() -> dict[str, str]:
        """Check database connectivity and a migrated table without contacting Sony or Twitch."""
        with service.database.transaction() as session:
            session.execute(select(Library.uid).limit(0))

        return {"status": "ready"}

    @app.get(
        "/api/v1/library",
        response_model=LibraryResponse,
        response_model_exclude_unset=True,
        tags=["Library"],
    )
    def library(
        uid: Annotated[str, Depends(authenticated_uid)],
        adapter: Annotated[LibraryGateway, Depends(get_gateway)],
        sort: LibrarySort = "last_played_at",
        direction: SortDirection = "desc",
    ) -> LibraryResponse:
        """Return the signed-in user's last committed snapshot using the existing React contract."""

        return LibraryResponse(snapshot=read_library(adapter, uid, sort, direction))

    @app.get("/api/v1/library/statistics", response_model=LibraryStatistics, tags=["Library"])
    def statistics(
        uid: Annotated[str, Depends(authenticated_uid)],
        adapter: Annotated[LibraryGateway, Depends(get_gateway)],
    ) -> LibraryStatistics:
        """Summarize confirmed trophy progress without exposing another user's saved library."""

        return calculate_statistics(read_library(adapter, uid))

    @app.post("/api/v1/library/sync", response_model=LibraryResponse, tags=["Library"])
    def synchronize(
        body: SyncRequest, uid: Annotated[str, Depends(authenticated_uid)]
    ) -> LibraryResponse:
        """Import a public profile for the verified user, without client PSN credentials."""

        return LibraryResponse(snapshot=app.state.service.synchronize(uid, body.online_id))

    @app.post("/api/v1/library/games/{game_id}", response_model=DetailsResponse, tags=["Library"])
    def game_details(
        game_id: str, body: DetailsRequest, uid: Annotated[str, Depends(authenticated_uid)]
    ) -> DetailsResponse:
        """Reuse saved details; contact providers only on a miss or explicit refresh."""

        return DetailsResponse(details=app.state.service.details(uid, game_id, body.refresh))

    @app.get("/api/v1/catalog", tags=["Catalogue"])
    def catalog(title: str, platform: str = "PS5") -> dict[str, Any]:
        """Supply bounded public metadata for preview pages without provider secrets."""
        if not 1 <= len(title) <= 200 or len(platform) > 64:
            raise ApplicationError("invalid_catalog_query", 400)
        result = app.state.service.catalog.get(title, platform)

        return {"catalog": result.model_dump(by_alias=True, mode="json") if result else None}

    return app
