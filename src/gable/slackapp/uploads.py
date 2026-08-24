"""Bringing a private Slack upload into memory without leaking the bot token.

Slack serves an uploaded file only to an authenticated caller, so the bot token
travels with the request. Everything here exists to make sure it travels no
further than Slack, and that what comes back is a real image small enough for
the 1 GB droplet to open.

Kept apart from `photos.py`, which orchestrates around this boundary rather than
implementing it.

Does not handle: deciding whether an upload was wanted, fitting it to a frame,
publishing it, or anything about the run it answers.
"""

from __future__ import annotations

import io
import urllib.parse
import urllib.request
from typing import Any, Final

from PIL import Image

from gable.photos.fit import MAX_SOURCE_PIXELS

MAX_UPLOAD_BYTES: Final[int] = 25 * 1024 * 1024
_SLACK_HOST_SUFFIXES: Final[tuple[str, ...]] = (".slack.com", ".slack-edge.com")


class PhotoHandoffError(Exception):
    """A private upload could not safely become a public fitted image."""


def _is_slack_url(url: str) -> bool:
    """Return whether an HTTPS URL is owned by Slack's file service."""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(
        host.endswith(suffix) for suffix in _SLACK_HOST_SUFFIXES
    )


class _SlackOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent urllib from forwarding the bot token to a redirect off Slack."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,  # noqa: ANN401 - urllib's response type is intentionally private
        code: int,
        msg: str,
        headers: Any,  # noqa: ANN401 - email.message.Message at runtime
        newurl: str,
    ) -> urllib.request.Request | None:
        """Follow only redirects whose destination is another Slack HTTPS host."""
        if not _is_slack_url(newurl):
            raise PhotoHandoffError("Slack redirected the upload outside its file service")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_private_image(
    url: str,
    bot_token: str,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> bytes:
    """Download a Slack file without ever sending the bot token elsewhere.

    Args:
        url: Slack's ``url_private_download`` value.
        bot_token: Existing bot credential from validated settings.
        max_bytes: Hard in-memory ceiling for the 1 GB droplet.

    Returns:
        The private file bytes.

    Raises:
        PhotoHandoffError: for a non-Slack URL, empty file, oversized file, or
            transport failure.
    """
    if not _is_slack_url(url):
        msg = "the upload link did not come from Slack"
        raise PhotoHandoffError(msg)
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {bot_token}"})
    opener = urllib.request.build_opener(_SlackOnlyRedirectHandler())
    try:
        with opener.open(request, timeout=60) as response:
            declared = response.headers.get("Content-Length", "")
            if declared.isdigit() and int(declared) > max_bytes:
                msg = "the uploaded image is larger than the safe processing limit"
                raise PhotoHandoffError(msg)
            out = io.BytesIO()
            while True:
                chunk = response.read(min(1024 * 1024, max_bytes + 1 - out.tell()))
                if not chunk:
                    break
                out.write(chunk)
                if out.tell() > max_bytes:
                    msg = "the uploaded image is larger than the safe processing limit"
                    raise PhotoHandoffError(msg)
    except PhotoHandoffError:
        raise
    except Exception as exc:
        msg = "Slack could not provide the uploaded image"
        raise PhotoHandoffError(msg) from exc
    downloaded = out.getvalue()
    if not downloaded:
        msg = "the uploaded image was empty"
        raise PhotoHandoffError(msg)

    # Slack can serve a file before it has finished processing it, and what
    # comes back then is not an image. Caught live on 2026-08-11 during a real
    # upload: three files in the same run downloaded as valid JPEG and the
    # fourth returned bytes Pillow could not identify.
    #
    # Without this the bad bytes travel to `fit_locally`, which raises deep in
    # the render path, hits the runner's outer exception boundary and reaches
    # Carmen as "Something went wrong while I was building this one" — with the
    # real cause nowhere in the message. Failing here says what actually
    # happened and what she can do about it.
    try:
        with Image.open(io.BytesIO(downloaded)) as probe:
            if probe.width * probe.height > MAX_SOURCE_PIXELS:
                msg = "the uploaded image dimensions exceed the safe processing limit"
                raise PhotoHandoffError(msg)
            probe.verify()
    except Exception as exc:
        msg = "that upload did not arrive as a readable image"
        raise PhotoHandoffError(msg) from exc
    return downloaded
