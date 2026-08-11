"""Turn one Slack thread upload into a resumed flyer run.

Slack's private URL is only a transport. The upload is downloaded with the bot
token, checked before any authorization header can leave Slack's own hosts,
fitted deterministically to the 1080 by 1350 hero frame, published into the
droplet's nginx directory, verified anonymously, and attached to the same
paused database run. No new run or retry is opened.
"""

from __future__ import annotations

import io
import logging
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection
from typing import Any, Final, Protocol

from gable.db import store
from gable.db.schema import connect
from gable.photos.fit import assess, fit_locally, image_dimensions
from gable.photos.store import PublishError, publish_local, verify_public
from gable.pipeline.runner import RunResult
from gable.sheets import repository as repo

logger = logging.getLogger("gable.slack.photos")

MAX_UPLOAD_BYTES: Final[int] = 25 * 1024 * 1024
_SLACK_HOST_SUFFIXES: Final[tuple[str, ...]] = (".slack.com", ".slack-edge.com")


class PhotoHandoffError(Exception):
    """A private upload could not safely become a public fitted image."""


class ResumesRun(Protocol):
    """The narrow runner surface the handoff needs."""

    def resume(self, submission: repo.Submission, run_id: str) -> RunResult:
        """Continue one existing run."""
        ...


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
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    is_slack_host = any(host.endswith(suffix) for suffix in _SLACK_HOST_SUFFIXES)
    if parsed.scheme != "https" or not is_slack_host:
        msg = "the upload link did not come from Slack"
        raise PhotoHandoffError(msg)
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {bot_token}"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
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
    return downloaded


def _submission(stored: store.StoredSubmission) -> repo.Submission:
    """Restore the repository type expected by ``Runner.resume``."""
    return repo.Submission(
        response_row_id=stored.response_row_id,
        sheet_row=stored.sheet_row,
        submitted_at=stored.submitted_at,
        intake=stored.intake,
        content_hash=stored.content_hash,
    )


@dataclass(frozen=True, slots=True)
class PhotoHandoff:
    """Dependencies and policy for handling a Slack hero-photo upload."""

    db_path: Path
    bot_token: str
    allowed_channel: str
    target_width: int
    target_height: int
    public_root: Path
    public_base: str
    runner_for: Callable[[Connection, str, str], ResumesRun]
    download: Callable[[str, str, int], bytes] = download_private_image
    publish: Callable[[Path, str, bytes], str] = publish_local
    verify: Callable[[str], tuple[bool, str]] = verify_public

    def handle(self, event: dict[str, Any], slack_client: Any) -> str:  # noqa: ANN401
        """Fit one thread upload and resume the exact run waiting there.

        Args:
            event: Slack's message event with subtype ``file_share``.
            slack_client: The authenticated Bolt web client.

        Returns:
            A house-style-safe outcome for the progress message.

        Raises:
            Nothing. Every failure becomes a precise, non-technical sentence.
        """
        if event.get("channel") != self.allowed_channel:
            return "I only handle listing photos in the Gable channel, so I left that upload alone."
        thread_ts = str(event.get("thread_ts") or "")
        if not thread_ts:
            return (
                "Reply with the photo inside the listing thread so I know which "
                "flyer it belongs to."
            )
        files = event.get("files") or []
        if len(files) != 1:
            return (
                "Please upload exactly one image in the listing thread so I do not "
                "guess which is the hero."
            )
        file_id = str(files[0].get("id") or "")
        if not file_id:
            return "Slack did not identify that upload, so I left the flyer unchanged."

        connection = connect(self.db_path)
        try:
            run = store.run_for_thread(connection, thread_ts)
            if run is None:
                return "I could not match this thread to a listing, so I left the upload alone."
            if run.status != "needs_photo":
                return (
                    "This listing is not waiting for a photo, so I left the current "
                    "flyer unchanged."
                )
            stored = store.load_submission(connection, run.response_row_id)
            if stored is None:
                return "I found the listing thread but not its request details, so I stopped there."

            try:
                response = slack_client.files_info(file=file_id)
                file_info = response.get("file", {})
                mime_type = str(file_info.get("mimetype") or "")
                if mime_type and not mime_type.startswith("image/"):
                    return "That upload is not an image. Please send a photo for the hero."
                private_url = str(
                    file_info.get("url_private_download") or file_info.get("url_private") or ""
                )
                image_bytes = self.download(private_url, self.bot_token, MAX_UPLOAD_BYTES)
                width, height = image_dimensions(image_bytes)
                assessment = assess(
                    width,
                    height,
                    self.target_width,
                    self.target_height,
                )
                if assessment.needs_model:
                    return (
                        "That image is too small to stay sharp in the hero space. "
                        "Please send a larger version."
                    )
                fitted = fit_locally(image_bytes, self.target_width, self.target_height)
                public_url = self.publish(self.public_root, self.public_base, fitted)
                usable, _detail = self.verify(public_url)
                if not usable:
                    return (
                        "I fitted the photo, but the flyer service could not fetch it. "
                        "I left the run paused."
                    )
            except (PhotoHandoffError, PublishError, OSError, ValueError):
                logger.exception("a Slack photo could not be prepared")
                return "I could not prepare that photo safely. Please send a different image."
            except Exception:
                logger.exception("Slack file metadata could not be read")
                return "I could not read that Slack upload. Please try sending it again."

            store.set_status(
                connection,
                run.run_id,
                "needs_photo",
                "Carmen supplied a fitted and verified hero photo",
                photo_url=public_url,
                photo_source="carmen",
            )
            runner = self.runner_for(connection, public_url, thread_ts)
            result = runner.resume(_submission(stored), run.run_id)
            if result.status == "delivered":
                return "I fitted the photo and finished the flyer."
            if result.status == "needs_review":
                return "I fitted the photo and resumed the flyer, but it still needs review."
            if result.needs_a_human:
                return "I fitted the photo. The flyer is waiting on the other detail I asked for."
            return "I fitted the photo, but I could not finish the flyer. I stopped there."
        finally:
            connection.close()
