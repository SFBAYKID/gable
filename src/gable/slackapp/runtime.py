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
from gable.google_client import build_google_service
from gable.logging_setup import configure_logging
from gable.pipeline.live import build_runner
from gable.pipeline.poller import BatchOutcome, Poller
from gable.pipeline.questions import (
    ReconcilePost,
    ReconcileState,
    Reconciliation,
    RunNotificationRetryLoop,
    deliver_pending,
)
from gable.pipeline.runner import Runner
from gable.pipeline.template_triage import TemplateTriage, drain_template_notifications
from gable.pipeline.template_vision import inspect_source_template
from gable.runtime import RuntimeComponents, serve
from gable.sheets import repository as repo
from gable.sheets.client import SheetClient
from gable.slackapp.answers import record_stated
from gable.slackapp.app import build_app
from gable.slackapp.batches import summarize as summarize_batch
from gable.slackapp.brain import Decision, think
from gable.slackapp.context import listing_context, waiting_summary
from gable.slackapp.editing import SlideEditor
from gable.slackapp.outbox import SlackOutboxReconciler, notification_blocks
from gable.slackapp.photos import PhotoHandoff
from gable.slackapp.recovery import (
    notify_interrupted_runs,
    prepare_pending_notifications,
)
from gable.slackapp.resume import may_rebuild, resume_with_current_sources
from gable.slackapp.source_refresh import SourceRefreshError, refresh_submission_sources
from gable.slackapp.supplied import record_and_resume
from gable.slides.library import list_files as list_template_files
from gable.voice import is_clean, safe

logger = logging.getLogger("gable.slack.runtime")

GOOGLE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/presentations",
)


def _owed_a_photo(connection: Connection, run: store.RunRow) -> bool:
    """Return whether this run still has to receive a property photograph.

    True when the run's own state says it is waiting for one, when the ask that
    went out included one, or during the acknowledgement gap where a durable
    photo question exists but the run remains needs_review until Slack confirms.
    """
    return (
        run.status == "needs_photo"
        # The ask, not the status: a run blocked on a design it needs widened
        # and owed its photograph parks in `needs_template` and is owed both.
        or (run.is_paused and run.awaiting_photo)
        or store.has_pending_photo_question(
            connection,
            run.run_id,
            run.slack_thread_ts,
        )
    )


def _may_build_without_a_photo(connection: Connection, run: store.RunRow) -> bool:
    """Whether "build it with the blanks" may proceed on this run.

    The release covers values nobody has. It never covers the photograph: a
    flyer with no property photo is not a flyer.
    """
    return not _owed_a_photo(connection, run)


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
    sheets = build_google_service("sheets", "v4", credentials)
    drive = build_google_service("drive", "v3", credentials)
    slides = build_google_service("slides", "v1", credentials)
    sheet_client = SheetClient(spreadsheet_id=settings.sheet_id, service=sheets)

    app: Any = None

    # Rebound to the configured Slack history reader before any runtime work or
    # startup notice can invoke a runner.
    slack_reconcile: ReconcilePost | None = None

    def reconcile_slack(
        text: str,
        thread_ts: str | None,
        created_at: str,
        notification_id: str,
    ) -> Reconciliation:
        """Resolve the history reader lazily after Bolt creates its client."""
        if slack_reconcile is None:
            return Reconciliation(ReconcileState.UNKNOWN)
        return slack_reconcile(text, thread_ts, created_at, notification_id)

    def slack_post(text: str, thread_ts: str | None) -> str:
        """Post only to Gable's configured channel and return the message id."""
        if not is_clean(text):
            logger.error("blocked a Slack message that violates Gable's house style")
            return ""
        response = app.client.chat_postMessage(
            channel=settings.slack_channel_id,
            text=text,
            thread_ts=thread_ts,
        )
        # A requested root proves where we tried to post, not that Slack
        # accepted the new message. Callers advance delivery state only from
        # the timestamp of the response itself.
        return str(response.get("ts") or "")

    def slack_post_once(text: str, thread_ts: str | None, client_msg_id: str) -> str:
        """Post one durable outbox item using its stable Slack identity."""
        if not is_clean(text):
            logger.error("blocked a durable Slack message that violates Gable's house style")
            return ""
        response = app.client.chat_postMessage(
            channel=settings.slack_channel_id,
            text=text,
            thread_ts=thread_ts,
            client_msg_id=client_msg_id,
            blocks=notification_blocks(text, client_msg_id),
        )
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
            post_once=slack_post_once,
            reconcile=reconcile_slack,
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
        event_drive = build_google_service("drive", "v3", event_credentials)
        event_slides = build_google_service("slides", "v1", event_credentials)

        def post_in_origin_thread(text: str, requested_thread: str | None) -> str:
            """Keep every resumed-run message in its originating thread."""
            return slack_post(text, requested_thread or thread_ts)

        return build_runner(
            settings,
            connection_for_event,
            event_drive,
            event_slides,
            post_in_origin_thread,
            post_once=slack_post_once,
            reconcile=slack_reconcile,
            hero_photo_url=photo_url,
            origin_thread_ts=thread_ts,
            progress=progress,
        )

    def refresh_for_photo(
        event_connection: Connection,
        run: store.RunRow,
    ) -> store.StoredSubmission:
        """Read current form/contact sources on the upload worker's clients."""
        event_credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
            str(settings.google_service_account_file), scopes=list(GOOGLE_SCOPES)
        )
        event_sheets = build_google_service("sheets", "v4", event_credentials)
        event_drive = build_google_service("drive", "v3", event_credentials)
        return refresh_submission_sources(
            event_connection,
            run,
            SheetClient(spreadsheet_id=settings.sheet_id, service=event_sheets),
            event_drive,
            settings.drive_id,
            settings.drive_templates_folder_id,
        )

    def deliver_photo_notification(
        event_connection: Connection,
        pending: store.PendingRunQuestion,
    ) -> None:
        deliver_pending(
            event_connection,
            pending,
            slack_post,
            post_once=slack_post_once,
            reconcile=reconcile_slack,
        )

    def record_photo_caption(
        caption_connection: Connection,
        address: str,
        text: str,
        response_row_id: str,
    ) -> int:
        """Store any listing values stated in the message carrying the photo.

        Args:
            caption_connection: The photo handoff's own open connection.
            address: The freshly re-read property address to file them against.
            text: The upload message's text.
            response_row_id: The submission, so a corrected address in the
                caption is accepted rather than discarded. Omitting it dropped
                the whole address Carmen sent with her photo on 2026-08-21.

        Returns:
            How many values were recorded.

        Raises:
            Nothing that should reach the upload path; the caller logs and
            continues so a caption never costs Carmen her photograph.
        """
        decision = guarded_think(text)
        if decision.tool != "supply_listing_value":
            return 0
        return record_stated(caption_connection, address, decision.arguments, response_row_id)

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
        deliver_notification=deliver_photo_notification,
        record_caption=record_photo_caption,
    )

    def execute_action(
        decision: Decision,
        thread_ts: str,
        progress: Callable[[str], None],
        action_id: str,
    ) -> str:
        """Use thread-owned clients to apply one conversational edit."""
        action_connection = connect(settings.db_path)
        try:
            action_credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                str(settings.google_service_account_file), scopes=list(GOOGLE_SCOPES)
            )
            action_slides = build_google_service("slides", "v1", action_credentials)
            if decision.tool == "build_with_blank_fields":
                run = store.run_for_thread(action_connection, thread_ts)
                if run is None:
                    return waiting_summary(action_connection)
                if not _may_build_without_a_photo(action_connection, run):
                    # The release covers values nobody has, never the photograph.
                    # A flyer with no property photo is not a flyer.
                    return "I still need the property image in this thread before I can build."
                store.approve_blank_fields(action_connection, run.run_id)
                action_drive = build_google_service("drive", "v3", action_credentials)
                action_sheets = build_google_service("sheets", "v4", action_credentials)
                try:
                    released = refresh_submission_sources(
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
                        "so I left the run paused."
                    )
                return resume_with_current_sources(
                    settings=settings,
                    connection=action_connection,
                    drive=action_drive,
                    slides=action_slides,
                    stored=released,
                    run=run,
                    thread_ts=thread_ts,
                    progress=progress,
                    post_once=slack_post_once,
                    reconcile=slack_reconcile,
                )
            if decision.tool == "supply_listing_value":
                return record_and_resume(
                    settings=settings,
                    connection=action_connection,
                    drive=build_google_service("drive", "v3", action_credentials),
                    sheets=build_google_service("sheets", "v4", action_credentials),
                    slides=action_slides,
                    arguments=decision.arguments,
                    thread_ts=thread_ts,
                    action_id=action_id,
                    progress=progress,
                    post_once=slack_post_once,
                    reconcile=slack_reconcile,
                    may_rebuild=may_rebuild,
                    resume=resume_with_current_sources,
                )
            if decision.tool == "check_templates":
                # A folder sweep is its own instruction now. It used to ride on
                # rebuild_flyer, which meant "yes build it" in a channel thread
                # spent six paid vision calls re-measuring designs and answered
                # with a template report -- which is not what was asked.
                action_drive = build_google_service("drive", "v3", action_credentials)
                return template_triage_for(
                    action_connection,
                    action_drive,
                    action_slides,
                ).recheck_catalog(progress)
            if decision.tool == "rebuild_flyer":
                run = store.run_for_thread(action_connection, thread_ts)
                if run is None:
                    template = store.template_for_thread(action_connection, thread_ts)
                    checking_update = str(decision.arguments.get("mode") or "") == "check_updated"
                    if template is None:
                        # A build asked outside any listing thread is a real
                        # instruction with no listing attached. Name the ones
                        # that are actually waiting and what each still needs;
                        # that is the answer, and it costs nothing.
                        return waiting_summary(action_connection)
                    if not checking_update:
                        return (
                            "This thread is about a source template, not a listing. Update "
                            "the design and tell me to check it again."
                        )
                    action_drive = build_google_service("drive", "v3", action_credentials)
                    return template_triage_for(
                        action_connection,
                        action_drive,
                        action_slides,
                    ).recheck_action(thread_ts, action_id, progress)
                pending_before_rebuild = tuple(
                    item
                    for item in store.pending_run_questions(action_connection)
                    if item.run_id == run.run_id
                )
                if pending_before_rebuild:
                    for item in pending_before_rebuild:
                        deliver_pending(
                            action_connection,
                            item,
                            slack_post,
                            post_once=slack_post_once,
                            reconcile=reconcile_slack,
                        )
                    return ""
                # Re-measuring a design needs no photograph, and refusing the
                # recheck here would refuse the exact action Gable's own blocker
                # instructs: "Widen that section, then tell me to check the
                # updated template again." A run owed BOTH must still be able to
                # do the template half, or the pause has no exit at all.
                #
                # `needs_photo` alone still refuses, because that state means
                # the template was already cleared and only the image is
                # outstanding; rechecking there would re-measure nothing and
                # answer a question nobody asked.
                if run.status == "needs_photo" or store.has_pending_photo_question(
                    action_connection, run.run_id, run.slack_thread_ts
                ):
                    # The retained URL is audit evidence for the rejected or
                    # superseded image, not permission to reuse it.  Only the
                    # next Slack upload may atomically replace that provenance
                    # and clear this exact pause.
                    if run.photo_url:
                        return "Send me the correct property image in this thread."
                    return "Send me the property image in this thread."
                action_drive = build_google_service("drive", "v3", action_credentials)
                action_sheets = build_google_service("sheets", "v4", action_credentials)
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
                if mode != "check_updated":
                    return (
                        "I only rebuild this way after the source design is updated. I left "
                        "the current flyer unchanged."
                    )
                if run.template_file_id:
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
                if not may_rebuild(action_connection, run, action_id, thread_ts):
                    return ""
                return resume_with_current_sources(
                    settings=settings,
                    connection=action_connection,
                    drive=action_drive,
                    slides=action_slides,
                    stored=stored,
                    run=run,
                    thread_ts=thread_ts,
                    progress=progress,
                    post_once=slack_post_once,
                    reconcile=slack_reconcile,
                )
            run = store.run_for_thread(action_connection, thread_ts)
            if run is None:
                # "Gable is this now built?" asked outside a listing thread is a
                # real question with a real answer. Chase asked it on
                # 2026-08-26 and was told only that nothing had been changed --
                # true, and no help at all. Say what is actually waiting.
                return waiting_summary(action_connection)
            pending_before_edit = tuple(
                item
                for item in store.pending_run_questions(action_connection)
                if item.run_id == run.run_id
            )
            if pending_before_edit:
                for item in pending_before_edit:
                    deliver_pending(
                        action_connection,
                        item,
                        slack_post,
                        post_once=slack_post_once,
                        reconcile=reconcile_slack,
                    )
                return ""
            if action_id and store.has_run_action_notification(
                action_connection,
                run.run_id,
                action_id,
            ):
                return ""
            if (
                decision.tool == "replace_photo"
                and str(decision.arguments.get("which") or "").strip().casefold() == "hero"
            ):
                pending_action = store.prepare_photo_replacement_action(
                    action_connection,
                    run.run_id,
                    action_id,
                    thread_ts,
                )
                if pending_action is None:
                    return ""
                deliver_pending(
                    action_connection,
                    pending_action,
                    slack_post,
                    post_once=slack_post_once,
                    reconcile=reconcile_slack,
                )
                return ""
            if (
                decision.tool == "replace_photo"
                and str(decision.arguments.get("which") or "").strip().casefold() == "headshot"
            ):
                action_submission = store.load_submission(
                    action_connection,
                    run.response_row_id,
                )
                agent = (
                    action_submission.intake.agent_name
                    if action_submission is not None
                    else "the agent"
                )
                instruction = (
                    f"Replace {agent}'s image in Head Shots, then tell me to rebuild the flyer."
                )
                pending_action = store.prepare_headshot_replacement_action(
                    action_connection,
                    run.run_id,
                    action_id,
                    thread_ts,
                    instruction,
                )
                if pending_action is None:
                    return ""
                deliver_pending(
                    action_connection,
                    pending_action,
                    slack_post,
                    post_once=slack_post_once,
                    reconcile=reconcile_slack,
                )
                return ""
            if decision.tool != "report_status":
                # Geometry/content edits are deliberately rejected before any
                # Slides request until copy-render-inspect-promote is connected.
                # They need no mutation claim because this path cannot mutate.
                return SlideEditor(action_connection, action_slides).execute(
                    decision,
                    thread_ts,
                )
            before_event = int(
                action_connection.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM run_events WHERE run_id = ?",
                    (run.run_id,),
                ).fetchone()[0]
            )
            outcome = SlideEditor(action_connection, action_slides).execute(decision, thread_ts)
            after_event = int(
                action_connection.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM run_events WHERE run_id = ?",
                    (run.run_id,),
                ).fetchone()[0]
            )
            if after_event == before_event:
                return outcome
            try:
                pending_action = store.prepare_run_action_notification(
                    action_connection,
                    run.run_id,
                    action_id,
                    safe(outcome),
                    thread_ts,
                )
                deliver_pending(
                    action_connection,
                    pending_action,
                    slack_post,
                    post_once=slack_post_once,
                    reconcile=reconcile_slack,
                )
            except Exception:
                # Slides/state already changed and the audit event proves it.
                # A false "unchanged" fallback is worse than an honest pending
                # repair, so suppress the outer conversational post.
                logger.exception("could not persist a verified conversational change outcome")
            return ""
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
        runner = build_runner(
            settings,
            connection,
            drive,
            slides,
            slack_post,
            post_once=slack_post_once,
            reconcile=slack_reconcile,
        )
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
    slack_reconcile = SlackOutboxReconciler(app.client, settings.slack_channel_id)
    if interrupted:
        notified = notify_interrupted_runs(
            connection,
            interrupted,
            slack_post,
            slack_post_once,
            slack_reconcile,
        )
        if notified != len(interrupted):
            logger.warning(
                "%d interrupted-run notice(s) remain pending",
                len(interrupted) - notified,
            )
    socket = SocketModeHandler(app, settings.slack_app_token)
    notification_retries = RunNotificationRetryLoop(
        settings.db_path,
        slack_post,
        slack_post_once,
        reconcile=slack_reconcile,
        prepare_pending=prepare_pending_notifications,
        drain_additional=lambda retry_connection: drain_template_notifications(
            retry_connection,
            slack_post,
            slack_post_once,
            reconcile_slack,
        ),
    )
    return RuntimeComponents(
        poller=poller,
        socket=socket,
        connection=connection,
        notifications=notification_retries,
        poll_enabled=settings.poll_enabled,
    )


def main() -> int:
    """Validate configuration and start the production process."""
    try:
        settings = Settings.load()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    configure_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        redact_secrets=settings.log_redact_secrets,
    )
    # Applied once, from the frozen settings. Omitting it leaves the default
    # ceiling in place, which stops early rather than overspending.
    spend.configure_ceiling(settings.spend_ceiling_usd)
    try:
        components = build_components(settings)
    except Exception:
        logger.exception("Gable could not construct its runtime clients")
        return 2
    return serve(components)


if __name__ == "__main__":
    sys.exit(main())
