"""Turn one Slack thread upload into a resumed flyer run.

Slack's private URL is only a transport. The upload is downloaded with the bot
token, checked before any authorization header can leave Slack's own hosts,
normalised without changing its composition, published, verified anonymously,
and attached to the same paused database run. The exact deterministic fit happens
later, after the selected template's real frame is measured, so the human photo
is never cropped twice. No new run or retry is opened.
"""

from __future__ import annotations

import io
import logging
import sqlite3
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from PIL import Image

from gable.db import store
from gable.db.schema import connect
from gable.photos.fit import MAX_SOURCE_PIXELS, normalise_for_fitting
from gable.photos.store import PublishError, publish_local, verify_public
from gable.pipeline import questions as run_questions
from gable.pipeline.runner import RunResult
from gable.sheets import repository as repo
from gable.slackapp.answers import carries_a_value
from gable.slackapp.intents import asks_to_run_again
from gable.slackapp.status import Working
from gable.voice import safe

logger = logging.getLogger("gable.slack.photos")

MAX_UPLOAD_BYTES: Final[int] = 25 * 1024 * 1024
# The durable claim family for one answering upload. Startup recovery reads it
# to find uploads this process accepted but never finished.
PHOTO_INGRESS_ROUTE: Final[str] = "file_share"
#: Said when an upload reaches a run that is not waiting for one, and also the
#: signal that the message's own words still deserve an ordinary answer. A
#: person who replies "1011 Winged Foot Dr, Westminster, MD 21158" and attaches
#: the photo has answered the question that was asked; routing the whole message
#: to the photo path and declining it threw that answer away silently.
NOT_WAITING_FOR_A_PHOTO: Final[str] = (
    "This listing is not waiting for a photo, so I left the current flyer unchanged."
)
#: Returned instead of a message when the upload was declined and the message
#: carried words of its own. Never spoken: it tells `process_file_share` that
#: nothing has been said and the caller should answer the words as an ordinary
#: reply. A sentinel rather than a text comparison, because the production
#: handler posts through the durable outbox and returns an empty string, so the
#: declining sentence never reaches the caller to be recognised.
DECLINED_ANSWER_THE_WORDS: Final[str] = "gable:answer-the-words-instead"
_SLACK_HOST_SUFFIXES: Final[tuple[str, ...]] = (".slack.com", ".slack-edge.com")
_PHOTO_LOCK_STRIPES: Final[int] = 32
_PHOTO_LOCKS: Final[tuple[threading.Lock, ...]] = tuple(
    threading.Lock() for _ in range(_PHOTO_LOCK_STRIPES)
)

ProgressReporter = Callable[[str], None]
SubmissionLoader = Callable[[sqlite3.Connection, store.RunRow], store.StoredSubmission | None]
FileShareHandler = Callable[[dict[str, Any], Any, ProgressReporter], str]
NotificationDelivery = Callable[[sqlite3.Connection, store.PendingRunQuestion], None]


def _ignore_progress(_stage: str) -> None:
    """Default progress sink for direct calls outside the Slack event wrapper."""


class PhotoHandoffError(Exception):
    """A private upload could not safely become a public fitted image."""


def _photo_lock(thread_ts: str) -> threading.Lock:
    """Return a bounded process-local lock for one listing thread.

    Bolt runs message listeners concurrently. Two uploads in the same thread can
    otherwise both observe ``needs_photo`` and both download, decode, publish,
    and potentially reserve paid work before the runner's atomic resume claim
    rejects one. Striped locks bound memory while serializing that expensive
    boundary; the database claim remains the cross-process authority.
    """
    return _PHOTO_LOCKS[hash(thread_ts) % _PHOTO_LOCK_STRIPES]


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


class ResumesRun(Protocol):
    """The narrow runner surface the handoff needs."""

    def resume(
        self,
        submission: repo.Submission,
        run_id: str,
        *,
        resume_fields: dict[str, str | int] | None = None,
        expected_status: str | None = None,
    ) -> RunResult:
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


def _resume_state(connection: sqlite3.Connection, run: store.RunRow) -> str:
    """The paused state this run is in right now, for the resume claim.

    Args:
        connection: The open database.
        run: The run as it was read before the pending photo question, if any,
            was satisfied.

    Returns:
        Its current status, or the status already in hand when the row cannot
        be re-read. Either way a paused state, because the caller only reaches
        here after establishing that the run is waiting.

    Raises:
        Nothing.
    """
    current = store.run_by_id(connection, run.run_id)
    return current.status if current is not None else run.status


def _submission(stored: store.StoredSubmission) -> repo.Submission:
    """Restore the repository type expected by ``Runner.resume``."""
    return repo.Submission(
        response_row_id=stored.response_row_id,
        sheet_row=stored.sheet_row,
        submitted_at=stored.submitted_at,
        intake=stored.intake,
        content_hash=stored.content_hash,
        source_tab=stored.source_tab,
    )


def shared_file_event(event: dict[str, Any], client: Any) -> dict[str, Any] | None:  # noqa: ANN401
    """Turn a ``file_shared`` notice into the message-shaped event the handoff reads.

    Slack's current upload flow can post the message first and attach the file a
    moment later. The ``message`` event then arrives with no ``files`` array at
    all, and the upload is announced only by ``file_shared`` — which Gable did
    not subscribe to until 2026-08-19. That is why Caleb Olawuyi's photo never
    reached it while Carmen's next one did: a race, not a broken path.

    The result is deliberately shaped like the message event, so exactly one
    code path fits and places a photograph however Slack chose to announce it.

    Args:
        event: Slack's ``file_shared`` event.
        client: Slack Web API client.

    Returns:
        A message-shaped event carrying the file, its channel and its thread, or
        ``None`` when the file cannot be placed in exactly one thread. ``None``
        is the safe answer: the ordinary message path may still carry it, and
        guessing a thread would put somebody's photo on another listing.

    Raises:
        Nothing. A lookup failure is logged and becomes ``None``.
    """
    file_id = str(event.get("file_id") or (event.get("file") or {}).get("id") or "").strip()
    if not file_id:
        return None
    try:
        # https://docs.slack.dev/reference/methods/files.info/
        answer = client.files_info(file=file_id)
    except Exception:
        logger.exception("could not read the details of shared file %s", file_id)
        return None
    info = answer.get("file") if isinstance(answer, dict) else None
    if not isinstance(info, dict):
        return None
    if not str(info.get("mimetype") or "").startswith("image/"):
        return None

    shares = info.get("shares")
    placements: list[tuple[str, dict[str, Any]]] = []
    if isinstance(shares, dict):
        for group in shares.values():
            if not isinstance(group, dict):
                continue
            for channel_id, entries in group.items():
                if not isinstance(entries, list):
                    continue
                placements.extend(
                    (str(channel_id), entry) for entry in entries if isinstance(entry, dict)
                )
    # One share is the ordinary case. Several means the same file sits in more
    # than one place, and choosing between them is the guess this refuses.
    if len(placements) != 1:
        return None
    channel_id, placement = placements[0]
    thread_ts = str(placement.get("thread_ts") or placement.get("ts") or "")
    if not thread_ts:
        return None
    return {
        "channel": channel_id,
        "thread_ts": thread_ts,
        "ts": str(placement.get("ts") or ""),
        "user": str(info.get("user") or event.get("user_id") or ""),
        "parent_user_id": str(placement.get("parent_user_id") or ""),
        "text": "",
        "files": [{"id": file_id, "mimetype": str(info.get("mimetype") or "")}],
    }


def process_file_share(
    event: dict[str, Any],
    say: Any,  # noqa: ANN401 - Bolt injection, untyped upstream
    client: Any,  # noqa: ANN401 - Slack WebClient, untyped upstream
    handler: FileShareHandler | None,
) -> bool:
    """Fit a shared photo under the native waiting state and report its outcome.

    Fitting and rendering is the longest user-triggered path. The native status
    covers the wait without leaving a placeholder message behind, and every
    failure becomes a plain sentence rather than a silent dead job.

    Args:
        event: Slack's file-bearing message or app-mention event.
        say: Bolt's thread-aware posting helper.
        client: Slack Web API client used for native status.
        handler: The real photo workflow, or ``None`` in isolated checks.

    Returns:
        True when the upload was dealt with. False only when the run was not
        waiting for a photo AND the message carried words of its own, which the
        caller should answer as an ordinary reply — nothing has been said yet in
        that case, so the reply is the whole response rather than a second one.

    Raises:
        Nothing. Every outcome, including failure, is said out loud.
    """
    thread = event.get("thread_ts") or event.get("ts")
    if handler is None:
        say(
            text=safe("I received the photo, but photo processing is not available right now."),
            thread_ts=thread,
        )
        return True
    with Working(
        client,
        str(event.get("channel") or ""),
        str(thread or ""),
        "is building the flyer...",
    ) as waiting:
        try:
            spoken = handler(event, client, waiting.stage)
            # An empty outcome means the run already posted its link or precise
            # failure in this thread. Another line would only duplicate it.
            if not spoken.strip():
                return True
            if spoken == DECLINED_ANSWER_THE_WORDS:
                # Nothing has been said. The words that came with the photo are
                # an answer to whatever Gable last asked, and answering them is
                # a better response than a line about the photo.
                return False
            outcome = safe(spoken)
        except Exception:
            logger.exception("the photo workflow failed")
            outcome = (
                "I could not fit that photo to the flyer. The flyer is unchanged — "
                "send it again, or tell me which listing it belongs to."
            )
        say(text=outcome, thread_ts=thread)
    return True


@dataclass(frozen=True, slots=True)
class PhotoHandoff:
    """Dependencies and policy for handling a Slack hero-photo upload."""

    db_path: Path
    bot_token: str
    allowed_channel: str
    max_edge_px: int
    jpeg_quality: int
    public_root: Path
    public_base: str
    runner_for: Callable[..., ResumesRun]
    load_current: SubmissionLoader = lambda connection, run: store.load_submission(
        connection, run.response_row_id
    )
    download: Callable[[str, str, int], bytes] = download_private_image
    publish: Callable[[Path, str, bytes], str] = publish_local
    verify: Callable[[str], tuple[bool, str]] = verify_public
    deliver_notification: NotificationDelivery | None = None
    #: Records listing values stated in the upload's own message, before the run
    #: resumes and builds with them. Gable asks for the photo and the missing
    #: values in one message, so the natural reply is one message carrying both;
    #: without this, the caption was silently discarded and the flyer built with
    #: placeholders for values the person had just supplied. Injected so this
    #: module stays free of the conversational model.
    record_caption: Callable[[sqlite3.Connection, str, str], int] = (
        lambda _connection, _address, _text: 0
    )

    def handle(
        self,
        event: dict[str, Any],
        slack_client: Any,  # noqa: ANN401 - Slack WebClient, untyped upstream
        progress: ProgressReporter = _ignore_progress,
    ) -> str:
        """Fit one thread upload and resume the exact run waiting there.

        Args:
            event: Slack's message event with subtype ``file_share``.
            slack_client: The authenticated Bolt web client.
            progress: Updates the native waiting state with the actual stage.

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
            # A message can carry several images AND the value Gable asked for —
            # "1011 Winged Foot Dr..." with a front and a back photo attached.
            # This early return used to discard those words with the images, the
            # same swallowing the sentinel fix cured one guard lower. When the
            # words carry an answer, the answer is the response; the run will
            # ask for its photo again if it still needs one.
            if carries_a_value(str(event.get("text") or "")):
                return DECLINED_ANSWER_THE_WORDS
            return (
                "Please upload exactly one image in the listing thread so I do not "
                "guess which is the hero."
            )
        file_id = str(files[0].get("id") or "")
        if not file_id:
            return "Slack did not identify that upload, so I left the flyer unchanged."

        connection = connect(self.db_path)
        photo_lock = _photo_lock(thread_ts)
        photo_lock.acquire()
        try:
            run = store.run_for_thread(connection, thread_ts)
            if run is None:
                return "I could not match this thread to a listing, so I left the upload alone."
            run_id = run.run_id
            # The file id is the one identifier both announcement paths share.
            # An upload can be announced twice — by the message that carried it,
            # and by the `file_shared` event Slack sends when it attaches the
            # file a moment later — and keying on the message would let one
            # photo rebuild the flyer twice. Re-sending the same image to the
            # same run therefore reads as already handled, which is correct:
            # that photo is already on the flyer.
            event_id = file_id
            if not event_id:
                return (
                    "Slack did not identify that photo message, so I left the listing "
                    "unchanged. Send it again in this thread."
                )
            if run.photo_event_id == event_id or store.has_run_action_notification(
                connection,
                run_id,
                event_id,
            ):
                return ""

            def finish(message: str, detail: str) -> str:
                spoken = message
                if message:
                    try:
                        pending = store.prepare_run_action_notification(
                            connection,
                            run_id,
                            event_id,
                            safe(message),
                            thread_ts,
                        )
                    except Exception:
                        logger.exception("could not persist the Slack photo outcome")
                    else:
                        if self.deliver_notification is not None:
                            # Once the outbox row exists, the outbox owns the
                            # message — cleared BEFORE delivery is attempted,
                            # not after it succeeds. Delivery raising after the
                            # row was persisted used to leave `spoken` intact,
                            # so the Bolt wrapper said the sentence and the
                            # retry worker said it again within the minute.
                            spoken = ""
                            try:
                                self.deliver_notification(connection, pending)
                            except Exception:
                                # The persisted row is the guarantee; the
                                # process-lifetime worker delivers it.
                                logger.exception("photo outcome delivery deferred to the outbox")
                # Release the durable ingress only once its outcome is stored. A
                # crash before this line still reads as unfinished, so startup
                # recovery asks again instead of leaving the listing waiting on
                # an image that already arrived and was lost.
                store.complete_slack_event(
                    connection,
                    PHOTO_INGRESS_ROUTE,
                    event_id,
                    run_id,
                    detail,
                )
                return spoken

            # Retiring the question before refresh/download/publish is what stops
            # a delivery worker posting it while this upload is still being
            # fetched. That is only safe because the durable ingress claim below
            # records that the upload was accepted first: preparation is
            # content-addressed and safe to repeat, and an abandoned claim is
            # recovered at startup rather than silently dropping Carmen's photo.
            accepted_in = ""
            with run_questions.run_question_guard(run.run_id):
                current = store.run_by_id(connection, run.run_id)
                # A finished flyer plus a new image and a plain request to run
                # it again is a replacement, not a stray upload. Requiring the
                # words keeps an image dropped into a delivered thread by
                # accident from silently rebuilding what Carmen already has.
                if (
                    current is not None
                    and current.status == "delivered"
                    and asks_to_run_again(str(event.get("text") or ""))
                    and store.prepare_photo_replacement_action(
                        connection, current.run_id, event_id, thread_ts
                    )
                    is not None
                ):
                    current = store.run_by_id(connection, current.run_id)
                waiting_for_photo = current is not None and (
                    current.status == "needs_photo"
                    # A flyer parked in review is one Gable built and refused to
                    # send, holding its draft and saying so. An Open House run
                    # on 2026-08-15 stopped because the supplied photo showed a
                    # house number contradicting the address — a problem whose
                    # only remedy is another photo — and then refused the
                    # replacement, because review is not needs_photo. That is a
                    # dead end: the one thing that fixes the run is the one
                    # thing the run will not take, and the only way out is to
                    # start the row over.
                    #
                    # Nothing is at risk here that is not at risk for
                    # needs_photo. Review means unsent by definition, so there
                    # is no finished flyer to overwrite and no need for the
                    # words that guard a delivered one.
                    or current.status == "needs_review"
                    or store.has_pending_photo_question(
                        connection,
                        current.run_id,
                        thread_ts,
                    )
                )
                if current is not None:
                    run = current
                if waiting_for_photo:
                    if not store.claim_slack_event(
                        connection,
                        PHOTO_INGRESS_ROUTE,
                        event_id,
                        run.run_id,
                        thread_ts,
                        file_id,
                    ):
                        # This exact upload was already accepted here or before a
                        # restart. Whatever that pass reported still stands.
                        return ""
                    store.satisfy_pending_photo_question(connection, run.run_id, thread_ts)
                    # The state this upload was accepted in, read inside the
                    # guard and after the question it answers is satisfied,
                    # because satisfying one moves the run. The resume claim
                    # below requires this exact state, so a run that pauses for
                    # some other reason during the source refresh refuses the
                    # photo instead of taking a stale one.
                    accepted_in = _resume_state(connection, run)
            if not waiting_for_photo:
                if (
                    current is not None
                    and current.status in store.PAUSED
                    and str(event.get("text") or "").strip()
                ):
                    # The words beside the photo answer whatever was last asked.
                    # Release the ingress claim, say nothing, and let the caller
                    # answer them: one reply deserves one response, and it
                    # should be about the question rather than the attachment.
                    #
                    # Only while the run is paused. A delivered flyer asked
                    # nothing, so an image dropped into its thread is a stray
                    # upload and "I left the current flyer unchanged" is the
                    # reassurance that belongs there.
                    finish("", "the run was not waiting for a photo; its words were answered")
                    return DECLINED_ANSWER_THE_WORDS
                return finish(
                    NOT_WAITING_FOR_A_PHOTO,
                    "the run was not waiting for a photo",
                )
            try:
                stored = self.load_current(connection, run)
            except Exception:
                logger.exception("the current form row or contact record could not be refreshed")
                return finish(
                    "I could not refresh this listing from its form and contact record, "
                    "so I left the upload and run unchanged.",
                    "source refresh failed before photo preparation",
                )
            if stored is None:
                return finish(
                    "I found the listing thread but not its request details, so I stopped there.",
                    "stored request details were unavailable",
                )

            # Answers sent with the photo are recorded before the run resumes,
            # so the build that follows uses them.
            caption = str(event.get("text") or "").strip()
            if caption and carries_a_value(caption):
                try:
                    recorded = self.record_caption(connection, stored.intake.address, caption)
                except Exception:
                    # A caption that cannot be read must never cost Carmen the
                    # photograph she just sent.
                    logger.exception("the values sent with a photo could not be recorded")
                else:
                    if recorded:
                        logger.info("recorded %d value(s) stated with the photo", recorded)

            try:
                progress("is reading the photo...")
                response = slack_client.files_info(file=file_id)
                file_info = response.get("file", {})
                mime_type = str(file_info.get("mimetype") or "")
                if mime_type and not mime_type.startswith("image/"):
                    return finish(
                        "That upload is not an image. Please send a photo for the hero.",
                        "the uploaded file was not an image",
                    )
                private_url = str(
                    file_info.get("url_private_download") or file_info.get("url_private") or ""
                )
                image_bytes = self.download(private_url, self.bot_token, MAX_UPLOAD_BYTES)
                progress("is preparing the photo...")
                prepared = normalise_for_fitting(
                    image_bytes,
                    max_edge_px=self.max_edge_px,
                    quality=self.jpeg_quality,
                )
                public_url = self.publish(self.public_root, self.public_base, prepared)
                usable, _detail = self.verify(public_url)
                if not usable:
                    return finish(
                        "I prepared the photo, but the flyer service could not fetch it. "
                        "I left the run paused.",
                        "the published photo could not be verified",
                    )
            except PublishError:
                logger.exception("a prepared Slack photo could not be published")
                return finish(
                    "I prepared the photo, but I could not save it to the flyer service. "
                    "I left this listing paused and reported the problem for repair.",
                    "photo publication failed",
                )
            except (PhotoHandoffError, OSError, ValueError):
                logger.exception("a Slack photo could not be prepared")
                return finish(
                    "I could not prepare that photo safely. Please send a different image.",
                    "photo preparation failed",
                )
            except Exception:
                logger.exception("Slack file metadata could not be read")
                return finish(
                    "I could not read that Slack upload. Please try sending it again.",
                    "Slack file metadata could not be read",
                )

            progress("is building the flyer...")
            runner = self.runner_for(connection, public_url, thread_ts, progress)
            result = runner.resume(
                _submission(stored),
                run.run_id,
                resume_fields={
                    "photo_url": public_url,
                    "photo_source": "slack_upload",
                    "photo_event_id": event_id,
                    "ai_enhanced": 0,
                },
                # The state this upload was accepted in, not a fixed one.
                # Pinning it to needs_photo made a review-state replacement
                # fail the claim after the upload was accepted and stored.
                expected_status=accepted_in,
            )
            # The run speaks for itself. Adding a line here after it has posted
            # its outcome and its link gives one event four messages, and the
            # last one restates what the thread already says.
            if result.said:
                return finish("", "the resumed run posted its outcome")
            current = store.run_by_id(connection, run.run_id)
            if current is not None and current.photo_event_id == event_id:
                return finish("", "the resumed run recorded this upload")
            if current is not None and store.has_pending_run_notification(
                connection, current.run_id
            ):
                # The exact outcome or next question owns its durable Slack
                # retry. A generic handoff failure here would contradict it.
                return finish("", "the resumed run persisted a durable outcome")
            return finish(
                "I prepared the photo, but I could not finish the flyer. I stopped there.",
                "the resumed run produced no reportable outcome",
            )
        finally:
            try:
                connection.close()
            finally:
                photo_lock.release()
