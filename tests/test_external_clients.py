"""The Slack and Google SDK defaults cannot silently widen network budgets."""

from __future__ import annotations

from typing import Any

import pytest

from gable import google_client
from gable.slackapp.client import (
    SLACK_HTTP_TIMEOUT_SECONDS,
    SLACK_TRANSPORT_RETRY_COUNT,
    build_web_client,
)


def test_slack_client_has_one_bounded_transport_attempt() -> None:
    """Durable retry belongs to the outbox, not an implicit SDK handler."""
    client = build_web_client("xoxb-test")

    assert client.timeout == SLACK_HTTP_TIMEOUT_SECONDS == 10
    assert client.retry_handlers == []
    assert SLACK_TRANSPORT_RETRY_COUNT == 0


def test_google_service_owns_timeout_refresh_and_discovery_budgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every generated resource is built over the explicit authorized transport."""
    seen: dict[str, Any] = {}
    raw_http = object()
    authorized_http = object()

    def http(*, timeout: float) -> object:
        seen["timeout"] = timeout
        return raw_http

    def authorize(
        credentials: object,
        *,
        http: object,
        max_refresh_attempts: int,
    ) -> object:
        seen["credentials"] = credentials
        seen["raw_http"] = http
        seen["refresh_attempts"] = max_refresh_attempts
        return authorized_http

    def build(name: str, version: str, **kwargs: Any) -> str:  # noqa: ANN401
        seen["name"] = name
        seen["version"] = version
        seen.update(kwargs)
        return "service"

    monkeypatch.setattr(google_client.httplib2, "Http", http)
    monkeypatch.setattr(google_client, "AuthorizedHttp", authorize)
    monkeypatch.setattr(google_client, "build", build)
    credentials = object()

    assert google_client.build_google_service("slides", "v1", credentials) == "service"
    assert google_client.httplib2.RETRIES == google_client.GOOGLE_HTTPLIB2_RETRIES
    assert seen == {
        "timeout": google_client.GOOGLE_HTTP_TIMEOUT_SECONDS,
        "credentials": credentials,
        "raw_http": raw_http,
        "refresh_attempts": google_client.GOOGLE_CREDENTIAL_REFRESH_ATTEMPTS,
        "name": "slides",
        "version": "v1",
        "http": authorized_http,
        "cache_discovery": False,
        "num_retries": google_client.GOOGLE_DISCOVERY_RETRY_COUNT,
    }


@pytest.mark.parametrize(("name", "version"), [("", "v1"), ("drive", "")])
def test_google_service_rejects_an_empty_discovery_identity(name: str, version: str) -> None:
    with pytest.raises(ValueError, match="nonempty"):
        google_client.build_google_service(name, version, object())


def test_google_service_fails_closed_if_the_library_widens_its_hidden_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        google_client.httplib2,
        "RETRIES",
        google_client.GOOGLE_HTTPLIB2_RETRIES + 1,
    )

    with pytest.raises(RuntimeError, match="retry behavior changed"):
        google_client.build_google_service("drive", "v3", object())
