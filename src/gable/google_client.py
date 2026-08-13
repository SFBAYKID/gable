"""Build Google discovery clients on an explicit bounded HTTP transport.

Google's discovery resources otherwise inherit library defaults for request
timeouts and credential-refresh retries.  Every Gable edge uses this factory so
a Sheet, Drive, or Slides call has the same visible ceiling even if a dependency
upgrade changes its defaults.

The factory creates a fresh transport per service.  ``httplib2.Http`` is not
thread-safe, and Slack event workers may use Drive and Slides concurrently.
Business-level retries remain at the caller: Sheet reads have their own bounded
jittered policy, while callers do not schedule retries for mutating Drive and
Slides requests. ``httplib2`` still has a bounded low-level stale-connection
recovery inside that transport; no application retry is hidden here.
"""

from __future__ import annotations

from typing import Any, Final

import httplib2 as httplib2
from google_auth_httplib2 import AuthorizedHttp as AuthorizedHttp
from googleapiclient.discovery import build

GOOGLE_HTTP_TIMEOUT_SECONDS: Final[float] = 30.0
GOOGLE_CREDENTIAL_REFRESH_ATTEMPTS: Final[int] = 1
GOOGLE_DISCOVERY_RETRY_COUNT: Final[int] = 0
# httplib2 exposes no per-instance retry setting. Assert the installed
# library's process-wide bounded value at construction so an upgrade cannot
# silently widen a Google request's transport window. This is one internal
# retry after the first attempt; it is not a claim that mutations make only one
# network attempt.
GOOGLE_HTTPLIB2_RETRIES: Final[int] = 1


def build_google_service(name: str, version: str, credentials: Any) -> Any:  # noqa: ANN401
    """Build one Google API service with explicit timeout and retry ceilings.

    Args:
        name: Discovery service name, such as ``sheets`` or ``drive``.
        version: Discovery version, such as ``v4`` or ``v3``.
        credentials: Google-auth credentials; upstream exposes no stable
            protocol covering refresh and signing behavior.

    Returns:
        An untyped googleapiclient discovery resource.  Discovery resources are
        generated dynamically from Google's service documents.

    Raises:
        ValueError: If a caller supplies a blank service name or version.
        RuntimeError: If the installed client's low-level retry constant no
            longer matches the reviewed transport budget.
        Exception: Credential or discovery construction errors from the Google
            libraries are intentionally left for the startup boundary.
    """
    if not name.strip() or not version.strip():
        raise ValueError("Google service name and version must be nonempty")
    if httplib2.RETRIES != GOOGLE_HTTPLIB2_RETRIES:
        raise RuntimeError("httplib2 retry behavior changed; review the Google transport budget")
    # httplib2 timeout contract:
    # https://httplib2.readthedocs.io/en/latest/libhttplib2.html#httplib2.Http
    transport = httplib2.Http(timeout=GOOGLE_HTTP_TIMEOUT_SECONDS)
    # AuthorizedHttp refresh ceiling:
    # https://googleapis.dev/python/google-auth-httplib2/latest/google_auth_httplib2.html
    authorized = AuthorizedHttp(
        credentials,
        http=transport,
        max_refresh_attempts=GOOGLE_CREDENTIAL_REFRESH_ATTEMPTS,
    )
    # Discovery ``num_retries`` governs only discovery retrieval.  The bundled
    # static documents normally avoid that request, but its budget is explicit.
    # https://googleapis.github.io/google-api-python-client/docs/epy/googleapiclient.discovery-module.html#build
    return build(
        name,
        version,
        http=authorized,
        cache_discovery=False,
        num_retries=GOOGLE_DISCOVERY_RETRY_COUNT,
    )
