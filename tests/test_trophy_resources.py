"""External help is reviewed content, not arbitrary executable or authenticated URLs."""

import pytest
from pydantic import ValidationError
from trophy_buddy_api.trophy_resources import ResourceDraft


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "http://example.com", "https://user:password@example.com/video"]
)
def test_resource_requires_https_without_embedded_credentials(url: str) -> None:
    with pytest.raises(ValidationError):
        ResourceDraft(
            service="trophy2",
            set_id="NPWR00001_00",
            trophy_id=0,
            title="Video",
            source_name="Author",
            source_url=url,
            kind="video",
            language="en",
        )
