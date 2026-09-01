"""Wire source-template triage to the live Google clients and Slack.

Split out of `slackapp.runtime` on 2026-09-01 when that module reached the
800-line ceiling. Nothing here decides anything: it binds `TemplateTriage` to
the Drive listing, the Slides reader, the visual inspection, the test build
and the Slack posters one runtime owns.

Does not handle: when triage runs, which the scheduler and the handlers do.
"""

from __future__ import annotations

from collections.abc import Callable
from sqlite3 import Connection
from typing import Any

from gable.config import Settings
from gable.pipeline.canary_live import dry_builder
from gable.pipeline.questions import PostOnce, ReconcilePost
from gable.pipeline.template_triage import TemplateTriage
from gable.pipeline.template_vision import inspect_source_template
from gable.slides.library import list_files as list_template_files


def build_template_triage(
    settings: Settings,
    connection: Connection,
    drive: Any,  # noqa: ANN401 - googleapiclient resource
    slides: Any,  # noqa: ANN401 - googleapiclient resource
    *,
    say: Callable[[str, str | None], str],
    post_once: PostOnce | None,
    reconcile: ReconcilePost | None,
) -> TemplateTriage:
    """Bind source-template measurement to one thread's Google clients.

    Args:
        settings: Parsed configuration.
        connection: This thread's own database connection.
        drive: A Drive v3 resource.
        slides: A Slides v1 resource.
        say: Posts one message to the configured channel.
        post_once: Durable outbox poster, when the runtime has one.
        reconcile: Confirms a delivery whose acknowledgement was lost.

    Returns:
        A ready `TemplateTriage`.

    Raises:
        Nothing.
    """
    return TemplateTriage(
        connection=connection,
        list_templates=lambda: list_template_files(
            drive,
            settings.drive_id,
            settings.drive_templates_folder_id,
        ),
        read_presentation=lambda file_id: (
            slides.presentations().get(presentationId=file_id).execute()
        ),
        say=say,
        post_once=post_once,
        reconcile=reconcile,
        look_at=lambda file_id: inspect_source_template(
            connection,
            slides,
            file_id,
            api_key=settings.openai_image_api_key,
            model=settings.vision_model,
        ),
        slide_px=(settings.slide_width_px, settings.slide_height_px),
        dry_build=dry_builder(settings, drive, slides),
    )
