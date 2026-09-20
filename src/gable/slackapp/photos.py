"""Turn one Slack thread upload into a resumed flyer run.

Slack's private URL is only a transport. The upload is downloaded with the bot
token, checked before any authorization header can leave Slack's own hosts,
normalised without changing its composition, published, verified anonymously,
and attached to the same paused database run. The exact deterministic fit happens
later, after the selected template's real frame is measured, so the human photo
is never cropped twice. No new run or retry is opened.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from gable.db import store
from gable.db.schema import connect
from gable.photos.fit import normalise_for_fitting
from gable.photos.store import PublishError, publish_local, verify_public
from gable.pipeline import questions as run_questions
from gable.pipeline.runner import RunResult
from gable.sheets import repository as repo
from gable.slackapp.answers import carries_a_value
from gable.slackapp.intents import asks_to_run_again
from gable.slackapp.photo_batch import MAX_PHOTOS, batch_id, main_index
from gable.slackapp.shared_photos import shared_file_event as shared_file_event
from gable.slackapp.status import Working
from gable.slackapp.uploads import MAX_UPLOAD_BYTES, PhotoHandoffError, download_private_image
from gable.voice import safe

logger = logging.getLogger("gable.slack.photos")

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

#: The same refusal for a run that has no flyer to leave unchanged. A run that
#: failed or was skipped has an empty `output_file_id`, and the sentence above
#: reassures the sender about a flyer that does not exist -- which reads as
#: Gable having quietly built something it will not show.
NO_FLYER_TO_CHANGE: Final[str] = (
    "This listing is not waiting for a photo, and it has no flyer yet. I left it as it is."
)
#: Returned instead of a message when the upload was declined and the message
#: carried words of its own. Never spoken: it tells `process_file_share` that
#: nothing has been said and the caller should answer the words as an ordinary
#: reply. A sentinel rather than a text comparison, because the production
#: handler posts through the durable outbox and returns an empty string, so the
#: declining sentence never reaches the caller to be recognised.
DECLINED_ANSWER_THE_WORDS: Final[str] = "gable:answer-the-words-instead"

#: More images arrived than any design holds AND the words carry a value. Both
#: need a response: the value is answered by the caller, and this says out loud
#: that the images were not kept. Returning the plain sentinel here discarded
#: them in silence, which is how "Here are 3 for the template." lost three
#: uploads on 2026-08-28 and was then asked which of them to use.
TOO_MANY_ANSWER_THE_WORDS: Final[str] = "gable:too-many-images-answer-the-words"

#: What to say when more images arrive than the largest measured design holds.
#: Three is the ceiling: one main well plus the row of two beneath it.
TOO_MANY_IMAGES: Final[str] = (
    "These designs hold at most three property photos, and that message had "
    "more, so I did not keep any of them. Send up to three together and tell "
    "me which one should be the main photo."
)
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


def _photo_lock(thread_ts: str) -> threading.Lock:
    """Return a bounded process-local lock for one listing thread.

    Bolt runs message listeners concurrently. Two uploads in the same thread can
    otherwise both observe ``needs_photo`` and both download, decode, publish,
    and potentially reserve paid work before the runner's atomic resume claim
    rejects one. Striped locks bound memory while serializing that expensive
    boundary; the database claim remains the cross-process authority.
    """
    return _PHOTO_LOCKS[hash(thread_ts) % _PHOTO_LOCK_STRIPES]


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
            if spoken == TOO_MANY_ANSWER_THE_WORDS:
                # The words still get answered by the caller, but the images
                # were dropped and Carmen has to hear it. Saying nothing here
                # is what let "Here are 3 for the template." throw away three
                # uploads and then ask which of the three to use.
                say(text=safe(TOO_MANY_IMAGES), thread_ts=thread)
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
    #: Takes the submission id as well as the address, because an address is
    #: which property this is rather than a fact about one: without the id
    #: `answers.record_stated` has nothing to attach a corrected address to and
    #: discards it. It did, on 2026-08-21 -- Carmen sent the whole address with
    #: her photo, the log recorded "a stated address arrived with no submission
    #: to attach it to", and Gable asked her for that same address again.
    record_caption: Callable[[sqlite3.Connection, str, str, str], int] = (
        lambda _connection, _address, _text, _response_row_id: 0
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
        if not files:
            return "I did not find a photo on that message, so I left the flyer unchanged."
        if len(files) > MAX_PHOTOS:
            # The words still get answered. Dropping the images in silence is
            # the 2026-08-28 failure; the ceiling moved from one to three but
            # the reason the sentence exists did not.
            if carries_a_value(str(event.get("text") or "")):
                return TOO_MANY_ANSWER_THE_WORDS
            return TOO_MANY_IMAGES
        file_ids = [str(item.get("id") or "") for item in files]
        if any(not item for item in file_ids) or len(set(file_ids)) != len(file_ids):
            return "Slack did not identify each photo uniquely. Please send the photos again."
        selected = event.get("property_main_index")
        if not isinstance(selected, int) or isinstance(selected, bool):
            selected = main_index(str(event.get("text") or ""), len(files))
        if selected is not None and not 0 <= selected < len(files):
            return "Which uploaded photo should be the main one: first, second or third?"
        # Placement order: the chosen main photograph, then the rest in upload
        # order for the smaller spaces left to right. Built here so the ingress
        # record names the picture that becomes the main one rather than
        # whichever attachment Slack happened to list first. With no choice yet
        # this is plain upload order and the staging branch returns before it
        # is used.
        main = 0 if selected is None else selected
        ordered_ids = [file_ids[main], *(item for i, item in enumerate(file_ids) if i != main)]
        file_id = ordered_ids[0]

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
            event_id = batch_id(file_ids, selected)
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
                # `awaiting_photo` on a DELIVERED run means the visual check
                # concluded that another photograph is the whole remedy -- the
                # image shows a different house -- and Gable said so above the
                # link. It is set nowhere else on a delivered run, so it is not
                # the general "send another if you want it framed differently"
                # invitation, and a stray image still needs the words.
                #
                # Without this the one case where a replacement is certainly
                # wanted was also the case that required magic words, and the
                # `file_shared` route carries none by construction.
                invited = current is not None and current.awaiting_photo
                if (
                    current is not None
                    and current.status == "delivered"
                    and (invited or asks_to_run_again(str(event.get("text") or "")))
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
                    # Whatever Gable asked for, Gable can receive. A run that
                    # needs a design widened AND needs its photo parks in
                    # `needs_template`, because the widening is the part only a
                    # person can do; the photo ask rode along in the same
                    # message. On 2026-08-20 an Open House run asked exactly
                    # that, and the screenshot answering it was refused with
                    # "this listing is not waiting for a photo" -- a dead end
                    # whose only exit was starting the row over.
                    #
                    # `awaiting_photo` is the ask itself rather than the status
                    # it parked in, so this holds for every paused state. Safe
                    # in all of them for the reason review is safe: none has
                    # sent a flyer, so there is nothing to overwrite.
                    or (current.is_paused and current.awaiting_photo)
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
                    if selected is None:
                        # Several photographs and no stated main one. Keep the
                        # ids, ask, and leave the run exactly where it is: the
                        # photo question is NOT retired and the run is NOT
                        # moved, so if the answer never comes the ordinary
                        # re-ask still owns this listing. Staging after the
                        # question was satisfied parked the run where only the
                        # selection tool could release it -- the shape of the
                        # 4.3 item 15 failure, reached by a different route.
                        state = store.run_by_id(connection, run.run_id)
                        caption = str(event.get("text") or "")
                        stored_request = store.load_submission(connection, run.response_row_id)
                        if stored_request is not None and caption and carries_a_value(caption):
                            self.record_caption(
                                connection,
                                stored_request.intake.address,
                                caption,
                                run.response_row_id,
                            )
                        store.set_status(
                            connection,
                            run_id,
                            state.status if state is not None else run.status,
                            "kept uploaded photo ids pending a main-photo choice",
                            pending_photo_files=json.dumps(file_ids),
                        )
                        return finish(
                            f"I kept your {len(file_ids)} photos. Which should be the main "
                            "one: "
                            + (
                                "first or second?"
                                if len(file_ids) == 2
                                else "first, second or third?"
                            )
                            + " I will place the rest left to right in the order you sent "
                            "them.",
                            "waiting for an explicit main-photo choice",
                        )
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
                held_a_flyer = current is not None and bool(current.output_file_id)
                return finish(
                    NOT_WAITING_FOR_A_PHOTO if held_a_flyer else NO_FLYER_TO_CHANGE,
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
                    recorded = self.record_caption(
                        connection, stored.intake.address, caption, run.response_row_id
                    )
                except Exception:
                    # A caption that cannot be read must never cost Carmen the
                    # photograph she just sent.
                    logger.exception("the values sent with a photo could not be recorded")
                else:
                    if recorded:
                        logger.info("recorded %d value(s) stated with the photo", recorded)
                        # A caption may correct the address, which is stored over
                        # the submission rather than beside it, so the build that
                        # follows has to read the reloaded row. Reloading always
                        # is cheaper than deciding whether an address was among
                        # the values -- the same reason `supplied.py` does it.
                        reloaded = store.load_submission(connection, run.response_row_id)
                        if reloaded is not None:
                            stored = reloaded

            try:
                prepared_photos: list[dict[str, str]] = []
                for upload_id in ordered_ids:
                    progress("is reading the photo...")
                    response = slack_client.files_info(file=upload_id)
                    file_info = response.get("file", {})
                    mime_type = str(file_info.get("mimetype") or "")
                    if mime_type and not mime_type.startswith("image/"):
                        return finish(
                            "That upload is not an image. Please send property photos.",
                            "an uploaded file was not an image",
                        )
                    private_url = str(
                        file_info.get("url_private_download") or file_info.get("url_private") or ""
                    )
                    image_bytes = self.download(private_url, self.bot_token, MAX_UPLOAD_BYTES)
                    progress("is preparing the photo...")
                    prepared = normalise_for_fitting(
                        image_bytes, max_edge_px=self.max_edge_px, quality=self.jpeg_quality
                    )
                    public_url = self.publish(self.public_root, self.public_base, prepared)
                    usable, _detail = self.verify(public_url)
                    if not usable:
                        return finish(
                            "I prepared the photo, but the flyer service could not fetch it. "
                            "I left the run paused.",
                            "a published photo could not be verified",
                        )
                    prepared_photos.append({"id": upload_id, "url": public_url})
                public_url = prepared_photos[0]["url"]
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
                    "property_photos": json.dumps(prepared_photos),
                    "pending_photo_files": "[]",
                    "ai_enhanced": 0,
                    # Answered. Cleared with the provenance in the same claim,
                    # never in a second write that a crash could skip and leave
                    # this run accepting a stray upload forever.
                    "awaiting_photo": 0,
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
