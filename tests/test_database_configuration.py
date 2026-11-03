"""Provider connection strings must select psycopg without rewriting encoded credentials."""

import pytest
from trophy_buddy_api.config import Settings
from trophy_buddy_api.database import Database
from trophy_buddy_api.gateway import BackendUnavailable


@pytest.mark.parametrize("scheme", ["postgresql", "postgres", "postgresql+psycopg"])
def test_provider_urls_preserve_credentials_and_select_psycopg(scheme: str) -> None:
    database = Database(
        Settings(
            _env_file=None,
            database_url=(
                f"{scheme}://example:p%40ss%2Fword@postgres.railway.internal:5432/railway?sslmode=require"
            ),
        )
    )
    try:
        assert database.engine.url.drivername == "postgresql+psycopg"
        assert database.engine.url.password == "p@ss/word"
        assert database.engine.url.query["sslmode"] == "require"
    finally:
        database.engine.dispose()


def test_non_postgres_database_is_rejected() -> None:
    with pytest.raises(BackendUnavailable):
        _ = Database(Settings(_env_file=None, database_url="sqlite:///example.db")).engine
