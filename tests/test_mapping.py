"""Provider identity and progress regressions migrated from the former Node backend."""

import base64
import pytest
from trophy_buddy_api.catalog import match_catalog
from trophy_buddy_api.config import Settings
from trophy_buddy_api.database import Database
from trophy_buddy_api.errors import ApplicationError, ReconnectRequired
from trophy_buddy_api.psn_mapping import artwork, normalize_library, normalize_trophies
from trophy_buddy_api.psn_session import PsnSessionManager


def definition(identity: int, **changes: object) -> dict:
    return {"trophyId": identity, "trophyType": "bronze", "trophyName": "Example", **changes}


def test_trophies_join_by_id_with_unknown_and_dlc_groups() -> None:
    trophies = normalize_trophies(
        [definition(0), definition(1, trophyGroupId="001"), definition(2)],
        [
            {"trophyId": 1, "earned": False, "trophyEarnedRate": "12.5"},
            {"trophyId": 0, "earned": True, "earnedDateTime": "2026-10-29T12:00:00Z"},
        ],
    )
    assert [row.earned for row in trophies] == [True, False, None]
    assert trophies[0].earned_at == "2026-10-29T12:00:00.000Z"
    assert trophies[1].group_id == "001"
    assert trophies[1].earned_rate == 12.5
    with pytest.raises(ApplicationError):
        normalize_trophies([definition(0), definition(0)], [])


def test_invalid_artwork_dates_and_rarity_remain_unknown() -> None:
    row = normalize_trophies(
        [definition(0, trophyIconUrl="javascript:alert(1)")],
        [{"trophyId": 0, "earned": True, "earnedDateTime": "invalid", "trophyEarnedRate": "101"}],
    )[0]
    assert row.image_url is row.earned_at is row.earned_rate is None
    assert artwork("https://user:password@example.com/image.png") is None


def test_library_never_joins_versions_by_display_name() -> None:
    trophy = {
        "npCommunicationId": "NPWR00001_00",
        "trophyTitleName": "Same Game",
        "trophyTitlePlatform": "PS4",
        "earnedTrophies": {"bronze": 0, "platinum": 0},
        "definedTrophies": {"bronze": 10},
        "progress": 0,
    }
    played = [{"titleId": "PPSA00001_00", "name": "Same Game", "category": "ps5_native_game"}]
    rows = normalize_library([trophy], played, {})
    assert len(rows) == 2
    assert rows[0].earned == 0 and rows[1].earned is None
    linked = normalize_library([trophy], played, {"PPSA00001_00": ["NPWR00001_00"]})
    assert len(linked) == 1 and linked[0].title_ids == ["PPSA00001_00"]


def test_catalog_requires_unique_compatible_name_and_preserves_edition() -> None:
    row = {
        "id": 1,
        "name": "Example Game",
        "slug": "example-game",
        "platforms": [{"id": 167, "name": "PS5"}],
    }
    assert match_catalog([row], "Example Game™", [167]) is not None
    assert match_catalog([row], "Example Game Remastered", [167]) is None
    assert match_catalog([row], "Example Game", [48]) is None
    assert match_catalog([row, {**row, "id": 2}], "Example Game", [167]) is None
    assert match_catalog([{**row, "slug": "../../unsafe"}], "Example Game", [167]) is None


def test_encrypted_sessions_are_nondeterministic_and_tamper_evident() -> None:
    settings = Settings(
        _env_file=None, psn_token_encryption_key=base64.b64encode(b"x" * 32).decode()
    )
    manager = PsnSessionManager(settings, Database(settings))
    session = {
        "accountId": "123",
        "onlineId": "Service",
        "accessToken": "access",
        "refreshToken": "refresh",
        "accessExpiresAt": 123,
        "refreshExpiresAt": 456,
    }
    first = manager.encrypt(session)
    assert first != manager.encrypt(session)
    assert manager.decrypt(first) == session
    parts = first.split(".")
    parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
    with pytest.raises(ReconnectRequired):
        manager.decrypt(".".join(parts))
