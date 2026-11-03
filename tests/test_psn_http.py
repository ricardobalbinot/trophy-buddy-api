"""Exercise actual HTTPX request construction and malformed provider responses."""

import httpx
import pytest
from trophy_buddy_api.errors import ApplicationError
from trophy_buddy_api.psn import PsnClient


@pytest.mark.parametrize(
    "next_offset,total,items,expected",
    [
        (0, 2, [{}], "invalid_psn_pagination"),
        (None, 2, [{}], "incomplete_psn_page"),
        (None, 0, None, "invalid_psn_page"),
    ],
)
def test_pagination_refuses_incomplete_or_stalled_pages(
    next_offset, total, items, expected
) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, json={"titles": items, "totalItemCount": total, "nextOffset": next_offset}
        )
    )
    with httpx.Client(transport=transport) as http, pytest.raises(ApplicationError) as failure:
        PsnClient(None).pages(
            http, {"accessToken": "test"}, "gamelist/v2/users/123/titles", "titles"
        )
    assert failure.value.code == expected


def test_complete_pages_preserve_query_identity_and_language() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-language"] == "pt-BR"
        assert request.headers["authorization"] == "Bearer test"
        assert request.url.params["npServiceName"] == "trophy2"
        offset = int(request.url.params["offset"])
        return httpx.Response(
            200,
            json={
                "trophies": [{"id": offset}],
                "totalItemCount": 2,
                **({"nextOffset": 1} if offset == 0 else {}),
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        rows = PsnClient(None).pages(
            http,
            {"accessToken": "test"},
            "trophy/example",
            "trophies",
            {"npServiceName": "trophy2"},
        )
    assert rows == [{"id": 0}, {"id": 1}]


@pytest.mark.parametrize(
    "status,expected",
    [(401, "psn_reconnect_required"), (429, "psn_rate_limited"), (403, "psn_data_unavailable")],
)
def test_provider_errors_are_sanitized(status, expected) -> None:
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(status, text="private provider details")
            )
        ) as http,
        pytest.raises(ApplicationError) as failure,
    ):
        PsnClient(None).request(http, {"accessToken": "test"}, "trophy/example")
    assert failure.value.code == expected
    assert "private" not in str(failure.value)
