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
from gable.photos.enhance import EnhancementError, upscale_real_photo
from gable.pipeline.live import build_runner
from gable.pipeline.poller import BatchOutcome, Poller
from gable.pipeline.runner import Runner
from gable.pipeline.template_triage import TemplateTriage
from gable.pipeline.template_vision import inspect_source_template
from gable.runtime import RuntimeComponents, serve
from gable.sheets import repository as repo
from gable.sheets.client import SheetClient
from gable.slackapp.app import build_app
from gable.slackapp.batches import summarize as summarize_batch
from gable.slackapp.brain import Decision, think
from gable.slackapp.editing import SlideEditor
from gable.slackapp.photos import PhotoHandoff
from gable.slides.library import list_files as list_template_files

logger = logging.getLogger("gable.slack.runtime")

GOOGLE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/presentations",
)

UpscaleProvider = Callable[[bytes, str, str, int, int], bytes]


def guarded_upscale_photo(
    connection: Connection,
    run_id: str,
    image_bytes: bytes,
    target_width: int,
    target_height: int,
    *,
    enabled: bool,
    max_calls: int,
    api_key: str,
    model: str,
    provider: UpscaleProvider = upscale_real_photo,
) -> bytes:
    """Run one real-photo edit behind the budget and per-listing ceilings.

    Args:
        connection: The Slack event's database connection.
        run_id: Existing paused flyer run.
        image_bytes: Original human-supplied photograph.
        target_width: Flyer width in pixels.
        target_height: Flyer height in pixels.
        enabled: Whether the configured photo policy permits reprocessing.
        max_calls: Hard image-call allowance per listing.
        api_key: Existing provider credential.
        model: High-fidelity edit model.
        provider: Injectable image-edit implementation.

    Returns:
        Faithful enlarged image bytes.

    Raises:
        EnhancementError: when policy or the listing call limit refuses it.
        BudgetExceededError: when the shared $50 ceiling refuses it.
        Exception: a provider failure, after its reservation is recorded.
    """
    if not enabled:
        raise EnhancementError("automatic enlargement is disabled by the photo policy")
    prior_calls = spend.operation_count(connection, run_id, spend.IMAGE_UPSCALE_DETAIL)
    if prior_calls >= max_calls:
        raise EnhancementError("this listing has already used its image-edit allowance")
    estimate = spend.Estimate(
        service="openai",
        model=model,
        usd=spend.IMAGE_EDIT_RESERVE_USD,
        detail=spend.IMAGE_UPSCALE_DETAIL,
    )
    return spend.guarded_call(
        connection,
        estimate,
        lambda: provider(image_bytes, api_key, model, target_width, target_height),
        run_id=run_id,
    )


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
        logger.warning("marked %d interrupted run(s) failed during startup", interrupted)

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
        return str(response.get("ts") or thread_ts or "")

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

        return build_runner(
            settings,
            connection_for_event,
            event_drive,
            event_slides,
            post_in_origin_thread,
            hero_photo_url=photo_url,
            origin_thread_ts=thread_ts,
            progress=progress,
            upscale_photo=lambda run_id, image, width, height: upscale_photo(
                connection_for_event,
                run_id,
                image,
                width,
                height,
            ),
        )

    def upscale_photo(
        event_connection: Connection,
        run_id: str,
        image_bytes: bytes,
        target_width: int,
        target_height: int,
    ) -> bytes:
        """Use the one permitted high-fidelity edit behind both hard guards."""
        return guarded_upscale_photo(
            event_connection,
            run_id,
            image_bytes,
            target_width,
            target_height,
            enabled=settings.reprocessing_enabled,
            max_calls=settings.max_image_calls_per_listing,
            api_key=settings.openai_image_api_key,
            model=settings.image_model_hq,
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
                stored = store.load_submission(action_connection, run.response_row_id)
                if stored is None:
                    return (
                        "I found the listing thread but not its request details, so I "
                        "have not rebuilt anything."
                    )
                mode = str(decision.arguments.get("mode") or "")
                if mode not in {"check_updated", "run_anyway"}:
                    return (
                        "Tell me whether you updated the template or want me to use the "
                        "current design as it is."
                    )
                action_drive = build(
                    "drive", "v3", credentials=action_credentials, cache_discovery=False
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

                runner = build_runner(
                    settings,
                    action_connection,
                    action_drive,
                    action_slides,
                    capture,
                    hero_photo_url=run.photo_url,
                    origin_thread_ts=thread_ts,
                    progress=progress,
                    upscale_photo=lambda run_id, image, width, height: upscale_photo(
                        action_connection,
                        run_id,
                        image,
                        width,
                        height,
                    ),
                    allow_template_warnings=mode == "run_anyway",
                )
                submission = repo.Submission(
                    response_row_id=stored.response_row_id,
                    sheet_row=stored.sheet_row,
                    submitted_at=stored.submitted_at,
                    intake=stored.intake,
                    content_hash=stored.content_hash,
                )
                result = runner.resume(submission, run.run_id)
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

    def guarded_think(message: str, speaker: str = "") -> Decision:
        """Run a conversation call only while the shared budget permits it.

        Args:
            message: What was said, with the mention already stripped.
            speaker: The first name of whoever asked, when it could be resolved.

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
