"""Production assembly for Slack Socket Mode and the Sheet poller.

All Slack dependencies stay under ``gable.slackapp``. The generic lifecycle in
``gable.runtime`` knows only that one listener connects without blocking and
that the poll loop stays on the main thread. Importing this module performs no
I/O; ``build_components`` constructs clients only after settings validate.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from sqlite3 import Connection
from typing import Any

from gable import spend
from gable.agents.contacts import sync_contacts
from gable.config import ConfigError, Settings
from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.logging_setup import configure_logging
from gable.pipeline.live import build_runner
from gable.pipeline.poller import BatchOutcome, Poller
from gable.pipeline.runner import Runner
from gable.pipeline.template_triage import TemplateTriage
from gable.pipeline.template_vision import inspect_source_template
from gable.runtime import RuntimeComponents, serve
from gable.sheets import repository as repo
from gable.sheets.client import ReadsRanges, SheetClient
from gable.slackapp.app import build_app
from gable.slackapp.batches import summarize as summarize_batch
from gable.slackapp.brain import Decision, think
from gable.slackapp.context import listing_context
from gable.slackapp.editing import SlideEditor
from gable.slackapp.photos import PhotoHandoff
from gable.slides.library import list_files as list_template_files
from gable.voice import safe

logger = logging.getLogger("gable.slack.runtime")

GOOGLE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/presentations",
)


class SourceRefreshError(RuntimeError):
    """The authoritative roster or original read-only form row could not be refreshed."""


def refresh_submission_sources(
    connection: Connection,
    run: store.RunRow,
    sheet_client: ReadsRanges,
    drive: Any,  # noqa: ANN401 - googleapiclient resource
    drive_id: str,
    templates_folder_id: str,
) -> store.StoredSubmission:
    """Reload the contact workbook and exact form tab behind a paused run.

    The form remains read-only. Remembering its tab is what distinguishes, for
    example, Testing_1 row 48 from production row 48; a row number alone is not
    an identity.
    """
    try:
        sync_contacts(drive, connection, drive_id, templates_folder_id)
        stored = store.load_submission(connection, run.response_row_id)
        if stored is None:
            raise SourceRefreshError("the saved request no longer exists")
        if not stored.source_tab:
            # Rows created before source-tab provenance was deployed still get
            # the current roster. Their form payload remains the last exact
            # value read, rather than guessing which tab a row number belongs to.
            return stored
        matches = [
            submission
            for submission in repo.read_submissions(sheet_client, stored.source_tab)
            if submission.submitted_at == stored.submitted_at
        ]
        if len(matches) != 1:
            raise SourceRefreshError("the original form response could not be identified once")
        current = repo.reconcile_identity(connection, matches[0])
        if current.response_row_id != run.response_row_id:
            raise SourceRefreshError("the refreshed form response did not match this run")
        store.record_submission(
            connection,
            current.response_row_id,
            current.sheet_row,
            current.submitted_at,
            current.intake,
            current.content_hash,
            current.source_tab,
        )
        refreshed = store.load_submission(connection, run.response_row_id)
        if refreshed is None:
            raise SourceRefreshError("the refreshed request could not be saved")
        return refreshed
    except SourceRefreshError:
        raise
    except Exception as exc:
        raise SourceRefreshError("the request sources could not be refreshed") from exc


def notify_interrupted_runs(
    connection: Connection,
    interrupted: tuple[store.RunRow, ...],
    say: Callable[[str, str | None], str],
) -> int:
    """Post durable startup-recovery outcomes and acknowledge confirmations.

    Runs with an owned thread were paused for review and receive the notice in
    that thread. Runs interrupted before a root existed are failed and receive
    one channel notice. A failed or unconfirmed Slack call leaves the database
    marker untouched, so the next startup retries the notice without retrying
    the flyer or consuming another attempt.

    Returns:
        Number of notices Slack confirmed and the database acknowledged.

    Raises:
        Nothing. Startup must continue even while Slack is temporarily down.
    """
    confirmed = 0
    for run in interrupted:
        try:
            stored = store.load_submission(connection, run.response_row_id)
            address = stored.intake.address.strip() if stored is not None else ""
            listing = address or "This listing"
            if run.slack_thread_ts:
                message = safe(
                    f"{listing} — I was interrupted while building this flyer, so I "
                    "paused it for review instead of saying it was finished. Tell me to "
                    "run it again."
                )
                thread_ts: str | None = run.slack_thread_ts
            else:
                message = safe(
                    f"{listing} — I was interrupted while building this flyer, and there "
                    "was no listing thread I could safely resume. I marked that attempt "
                    "failed so Chase can check it before retrying."
                )
                thread_ts = None
            posted_ts = say(message, thread_ts)
            if not posted_ts.strip():
                logger.error("Slack did not confirm an interrupted-run notice")
                continue
            if store.acknowledge_interrupted_run(connection, run.run_id, posted_ts):
                confirmed += 1
        except Exception:
            logger.exception("could not report an interrupted run in Slack")
    return confirmed


def build_components(settings: Settings) -> RuntimeComponents:
    """Construct the real database, Google, Slack, runner and poller clients.

    Args:
        settings: Validated production settings.

    Returns:
        Resources ready for the shared runtime lifecycle.

    Raises:
        Exception: when a credential or external client cannot be constructed.
            Startup logs this through the mandatory redacting formatter.
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    connection = connect(settings.db_path)
    apply_migrations(connection)
    interrupted = store.recover_interrupted_runs(connection)
    if interrupted:
        logger.warning("recovered %d interrupted run(s) during startup", len(interrupted))

    # Vendor contract: service-account credentials accept an explicit scope
    # list. https://google-auth.readthedocs.io/en/latest/reference/google.oauth2.service_account.html
    credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        str(settings.google_service_account_file), scopes=list(GOOGLE_SCOPES)
    )
    # Discovery clients are reused by sequential poll callbacks on the main
    # thread. https://googleapis.github.io/google-api-python-client/docs/epy/googleapiclient.discovery-module.html
    sheets = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    slides = build("slides", "v1", credentials=credentials, cache_discovery=False)
    sheet_client = SheetClient(spreadsheet_id=settings.sheet_id, service=sheets)

    app: Any = None

    def slack_post(text: str, thread_ts: str | None) -> str:
        """Post only to Gable's configured channel and return the message id."""
        response = app.client.chat_postMessage(
            channel=settings.slack_channel_id,
            text=text,
            thread_ts=thread_ts,
        )
        # A requested root proves where we tried to post, not that Slack
        # accepted the new message. Callers advance delivery state only from
        # the timestamp of the response itself.
        return str(response.get("ts") or "")

    def template_triage_for(
        triage_connection: Connection,
        triage_drive: Any,  # noqa: ANN401 - googleapiclient resource
        triage_slides: Any,  # noqa: ANN401
    ) -> TemplateTriage:
        """Bind source-template measurement to one thread's Google clients."""
        return TemplateTriage(
            connection=triage_connection,
            list_templates=lambda: list_template_files(
                triage_drive,
                settings.drive_id,
                settings.drive_templates_folder_id,
            ),
            read_presentation=lambda file_id: (
                triage_slides.presentations().get(presentationId=file_id).execute()
            ),
            say=slack_post,
            look_at=lambda file_id: inspect_source_template(
                triage_connection,
                triage_slides,
                file_id,
                api_key=settings.openai_image_api_key,
                model=settings.vision_model,
            ),
            slide_px=(settings.slide_width_px, settings.slide_height_px),
        )

    template_triage = template_triage_for(connection, drive, slides)

    def runner_for_photo(
        connection_for_event: Connection,
        photo_url: str,
        thread_ts: str,
        progress: Callable[[str], None] = lambda _stage: None,
    ) -> Runner:
        """Build thread-owned Google clients and a runner for one Slack upload."""
        event_credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
            str(settings.google_service_account_file), scopes=list(GOOGLE_SCOPES)
        )
        event_drive = build("drive", "v3", credentials=event_credentials, cache_discovery=False)
        event_slides = build("slides", "v1", credentials=event_credentials, cache_discovery=False)

        def post_in_origin_thread(text: str, requested_thread: str | None) -> str:
            """Keep every resumed-run message in its originating thread."""
            return slack_post(text, requested_thread or thread_ts)

        run = store.run_for_thread(connection_for_event, thread_ts)
        approved = (
            store.decode_warning_codes(run.approved_warning_codes)
            if run is not None
            else frozenset()
        )
        return build_runner(
            settings,
            connection_for_event,
            event_drive,
            event_slides,
            post_in_origin_thread,
            hero_photo_url=photo_url,
            origin_thread_ts=thread_ts,
            progress=progress,
            approved_template_warning_codes=approved,
        )

    def refresh_for_photo(
        event_connection: Connection,
        run: store.RunRow,
    ) -> store.StoredSubmission:
        """Read current form/contact sources on the upload worker's clients."""
        event_credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
            str(settings.google_service_account_file), scopes=list(GOOGLE_SCOPES)
        )
        event_sheets = build("sheets", "v4", credentials=event_credentials, cache_discovery=False)
        event_drive = build("drive", "v3", credentials=event_credentials, cache_discovery=False)
        return refresh_submission_sources(
            event_connection,
            run,
            SheetClient(spreadsheet_id=settings.sheet_id, service=event_sheets),
            event_drive,
            settings.drive_id,
            settings.drive_templates_folder_id,
        )

    photo_handoff = PhotoHandoff(
        db_path=settings.db_path,
        bot_token=settings.slack_bot_token,
        allowed_channel=settings.slack_channel_id,
        max_edge_px=settings.photo_max_edge_px,
        jpeg_quality=settings.photo_jpeg_quality,
        public_root=settings.photo_public_root,
        public_base=settings.photo_public_base,
        runner_for=runner_for_photo,
        load_current=refresh_for_photo,
    )

    def execute_action(
        decision: Decision,
        thread_ts: str,
        progress: Callable[[str], None],
    ) -> str:
        """Use thread-owned clients to apply one conversational edit."""
        action_connection = connect(settings.db_path)
        try:
            action_credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                str(settings.google_service_account_file), scopes=list(GOOGLE_SCOPES)
            )
            action_slides = build(
                "slides", "v1", credentials=action_credentials, cache_discovery=False
            )
            if decision.tool == "rebuild_flyer":
                run = store.run_for_thread(action_connection, thread_ts)
                if run is None:
                    template = store.template_for_thread(action_connection, thread_ts)
                    if template is None:
                        return (
                            "I could not match this thread to a listing or template, so I "
                            "have not changed anything."
                        )
                    if str(decision.arguments.get("mode") or "") != "check_updated":
                        return (
                            "This thread is about a source template, not a listing. Update "
                            "the design and tell me to check it again."
                        )
                    progress("is measuring the updated template...")
                    action_drive = build(
                        "drive", "v3", credentials=action_credentials, cache_discovery=False
                    )
                    return template_triage_for(
                        action_connection,
                        action_drive,
                        action_slides,
                    ).recheck(thread_ts, progress)
                action_drive = build(
                    "drive", "v3", credentials=action_credentials, cache_discovery=False
                )
                action_sheets = build(
                    "sheets", "v4", credentials=action_credentials, cache_discovery=False
                )
                try:
                    stored = refresh_submission_sources(
                        action_connection,
                        run,
                        SheetClient(spreadsheet_id=settings.sheet_id, service=action_sheets),
                        action_drive,
                        settings.drive_id,
                        settings.drive_templates_folder_id,
                    )
                except SourceRefreshError:
                    logger.exception("a listing's current form or contact source could not be read")
                    return (
                        "I could not refresh this listing from its form and contact record, "
                        "so I left the run paused without rebuilding it."
                    )
                mode = str(decision.arguments.get("mode") or "")
                if mode not in {"check_updated", "run_anyway"}:
                    return (
                        "Tell me whether you updated the template or want me to use the "
                        "current design as it is."
                    )
                if mode == "check_updated" and run.template_file_id:
                    # The person has said the source changed. Always reload its
                    # current Drive revision; a stored ready verdict may belong
                    # to the version from one second before this reply, before
                    # the scheduled scanner has observed the edit.
                    progress("is measuring the updated template...")
                    verdict = template_triage_for(
                        action_connection,
                        action_drive,
                        action_slides,
                    ).recheck_file(run.template_file_id, progress)
                    refreshed = store.template_audit(action_connection, run.template_file_id)
                    if refreshed is None or refreshed.status != "ready":
                        return verdict
                captured: list[str] = []

                def capture(text: str, _requested_thread: str | None) -> str:
                    captured.append(text)
                    return thread_ts

                approved = store.decode_warning_codes(run.approved_warning_codes)
                resume_fields: dict[str, str | int] = {"pending_warning_code": ""}
                if mode == "run_anyway":
                    if not run.pending_warning_code:
                        return (
                            "There is no current template warning waiting for approval, so I "
                            "left the listing unchanged."
                        )
                    approved = approved | {run.pending_warning_code}
                else:
                    # An edited source is new evidence. Old geometry approvals
                    # do not transfer to it.
                    approved = frozenset()
                resume_fields["approved_warning_codes"] = store.encode_warning_codes(approved)

                runner = build_runner(
                    settings,
                    action_connection,
                    action_drive,
                    action_slides,
                    capture,
                    hero_photo_url=run.photo_url,
                    origin_thread_ts=thread_ts,
                    progress=progress,
                    approved_template_warning_codes=approved,
                )
                submission = repo.Submission(
                    response_row_id=stored.response_row_id,
                    sheet_row=stored.sheet_row,
                    submitted_at=stored.submitted_at,
                    intake=stored.intake,
                    content_hash=stored.content_hash,
                    source_tab=stored.source_tab,
                )
                result = runner.resume(submission, run.run_id, resume_fields=resume_fields)
                if captured:
                    return captured[-1]
                return (
                    "I rechecked the template, but the run did not produce an outcome I "
                    "could report. I left the listing paused."
                    if result.needs_a_human
                    else "I could not finish the rebuild, so I left the current flyer unchanged."
                )
            return SlideEditor(action_connection, action_slides).execute(decision, thread_ts)
        except Exception:
            logger.exception("a Slack edit could not construct its Google client")
            return "I could not open the flyer to make that change, so I left it unchanged."
        finally:
            action_connection.close()

    def guarded_think(
        message: str,
        speaker: str = "",
        history: list[tuple[str, str]] | None = None,
        context: str = "",
    ) -> Decision:
        """Run a conversation call only while the shared budget permits it.

        Args:
            message: What was said, with the mention already stripped.
            speaker: The first name of whoever asked, when it could be resolved.
            history: Bounded prior turns from the same Slack thread.
            context: Persisted facts for the listing owned by that thread.

        Returns:
            A `Decision`.

        Raises:
            Nothing.
        """
        if not settings.openai_image_api_key:
            return think(
                message,
                api_key=settings.openai_image_api_key,
                model=settings.conversation_model,
                speaker=speaker,
                history=history,
                context=context,
            )
        thought_connection = connect(settings.db_path)
        estimate = spend.Estimate(
            service="openai",
            model=settings.conversation_model,
            usd=spend.CONVERSATION_RESERVE_USD,
            detail="conservative conversation-call reservation",
        )
        try:
            return spend.guarded_call(
                thought_connection,
                estimate,
                lambda: think(
                    message,
                    api_key=settings.openai_image_api_key,
                    model=settings.conversation_model,
                    speaker=speaker,
                    history=history,
                    context=context,
                ),
            )
        except spend.BudgetExceededError:
            return Decision(
                reply=(
                    "Testing has reached its spending limit, so I did not call the "
                    "language model or change the flyer."
                )
            )
        finally:
            thought_connection.close()

    def conversation_context(thread_ts: str) -> str:
        """Return persisted listing facts without keeping a shared DB cursor."""
        context_connection = connect(settings.db_path)
        try:
            return listing_context(context_connection, thread_ts)
        except Exception:
            logger.exception("could not load listing context for a Slack reply")
            return ""
        finally:
            context_connection.close()

    def on_submission(submission: repo.Submission) -> str:
        """Give one new submission a fresh runner bound to the live clients."""
        runner = build_runner(settings, connection, drive, slides, slack_post)
        return runner.run(submission).status

    def on_batch(outcomes: tuple[BatchOutcome, ...]) -> None:
        """Post one aggregate only when this pass attempted several listings."""
        message = summarize_batch(outcomes)
        if message:
            slack_post(message, None)

    poller = Poller(
        client=sheet_client,
        connection=connection,
        responses_tab=settings.tab_responses,
        sync_roster=lambda: sync_contacts(
            drive, connection, settings.drive_id, settings.drive_templates_folder_id
        ),
        on_submission=on_submission,
        on_batch=on_batch,
        scan_templates=template_triage.scan_new,
        schedule=settings.poll_schedule,
        max_per_pass=settings.max_batch,
    )

    app = build_app(
        settings.slack_bot_token,
        file_share_handler=photo_handoff.handle,
        action_handler=execute_action,
        allowed_channel=settings.slack_channel_id,
        allowed_user_ids=settings.slack_allowed_user_ids,
        thinker=guarded_think,
        context_provider=conversation_context,
    )
    if interrupted:
        notified = notify_interrupted_runs(connection, interrupted, slack_post)
        if notified != len(interrupted):
            logger.warning(
                "%d interrupted-run notice(s) remain pending",
                len(interrupted) - notified,
            )
    socket = SocketModeHandler(app, settings.slack_app_token)
    return RuntimeComponents(
        poller=poller,
        socket=socket,
        connection=connection,
        poll_enabled=settings.poll_enabled,
    )


def main() -> int:
    """Validate configuration, build clients, and start the production process."""
    try:
        settings = Settings.load()
    except ConfigError as exc:
        # ConfigError never includes secret values. Logging is not configured
        # yet because log settings are part of the object that failed to load.
        print(str(exc), file=sys.stderr)
        return 2

    configure_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        redact_secrets=settings.log_redact_secrets,
    )
    try:
        components = build_components(settings)
    except Exception:
        logger.exception("Gable could not construct its runtime clients")
        return 2
    return serve(components)


if __name__ == "__main__":
    sys.exit(main())
