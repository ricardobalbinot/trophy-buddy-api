"""Guide discovery must retain uncertainty, provenance and explicit platform applicability."""

import pytest
from trophy_buddy_api.guide_index import parse_index


def record(**changes: object) -> dict:
    return {
        "a": 1 | 32 | 512,
        "n": "Example Game Trophy Guide",
        "g": [1],
        "r": [2, 1, 120],
        "u": ["Guide author"],
        "t": [1, 1, 7, 44],
        **changes,
    }


def test_index_preserves_unknown_estimates_and_provider_identity() -> None:
    rows = parse_index({"123": record(r=[-1, None, -1])}, "a" * 64)
    assert len(rows) == 1
    assert rows[0]["entry_id"] == "psnprofiles:123"
    assert rows[0]["game_name"] == "example game"
    assert rows[0]["difficulty"] is rows[0]["playthroughs"] is rows[0]["platinum_hours"] is None
    assert rows[0]["trophy_count"] == 53


@pytest.mark.parametrize("path", ["https://evil.example/", "../guide", "//evil.example", "123?x=1"])
def test_index_rejects_untrusted_source_paths(path: str) -> None:
    assert parse_index({path: record()}, "a" * 64) == []


def test_non_trophy_guides_dlc_and_ambiguous_sources_are_not_platinum_guides() -> None:
    for flags in [1 | 512, 1 | 32 | 64 | 512, 1 | 16 | 32 | 512]:
        assert parse_index({"123": record(a=flags)}, "a" * 64) == []
