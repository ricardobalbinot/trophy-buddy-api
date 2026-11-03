"""Check SDK security options without contacting Firebase or loading real credentials."""

import pytest
from trophy_buddy_api.config import Settings
from trophy_buddy_api.gateway import BackendUnavailable, FirebaseGateway, InvalidSession
from typing import Any


def test_firebase_verifier_checks_revocation_and_uses_only_verified_uid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = FirebaseGateway(Settings(_env_file=None))
    sentinel = object()
    gateway.__dict__["app"] = sentinel
    calls: list[dict[str, Any]] = []

    def verify(token: str, **options: Any) -> dict[str, str]:
        calls.append({"token": token, **options})

        return {"uid": "verified-user"}

    monkeypatch.setattr("trophy_buddy_api.gateway.auth.verify_id_token", verify)

    assert gateway.verify_token("opaque-token") == "verified-user"
    assert calls == [{"token": "opaque-token", "app": sentinel, "check_revoked": True}]


@pytest.mark.parametrize("identity", [{}, {"uid": ""}, {"uid": "../other-user"}])
def test_invalid_verified_identity_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    identity: dict[str, str],
) -> None:
    gateway = FirebaseGateway(Settings(_env_file=None))
    gateway.__dict__["app"] = object()
    monkeypatch.setattr(
        "trophy_buddy_api.gateway.auth.verify_id_token", lambda *_args, **_kwargs: identity
    )

    with pytest.raises(InvalidSession):
        gateway.verify_token("token")


def test_certificate_provider_failure_is_not_reported_as_an_invalid_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = FirebaseGateway(Settings(_env_file=None))
    gateway.__dict__["app"] = object()

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Simulated certificate service outage")

    monkeypatch.setattr("trophy_buddy_api.gateway.auth.verify_id_token", fail)

    with pytest.raises(BackendUnavailable):
        gateway.verify_token("opaque-token")
