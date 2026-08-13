"""Submit one recoverable template copy through the real Drive triage path.

This never reads or writes the form-response sheet and never posts to Slack.
It adopts the current Generic Templates catalogue in a temporary SQLite file,
copies one named source into that folder, runs the same new-file triage used in
production, prints the measured outcome, and moves only that copy to Drive's
trash in a ``finally`` block. If deterministic checks pass, the real visual
inspection uses the configured shared spend ledger rather than a fresh test
budget.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gable.config import ConfigError, Settings
from gable.db import store
from gable.db.schema import apply_migrations, connect
from gable.pipeline.template_triage import TemplateTriage
from gable.pipeline.template_vision import inspect_source_template
from gable.pipeline.vision import Inspection
from gable.slides.library import generic_folder_id, list_files

GOOGLE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/presentations",
)


def _clients(settings: Settings) -> tuple[Any, Any]:
    """Build Drive and Slides clients from the configured service account."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        str(settings.google_service_account_file), scopes=list(GOOGLE_SCOPES)
    )
    return (
        build("drive", "v3", credentials=credentials, cache_discovery=False),
        build("slides", "v1", credentials=credentials, cache_discovery=False),
    )


def main(argv: list[str] | None = None) -> int:
    """Copy, inspect, and trash one temporary template; return a shell status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="New Listing", help="exact source template name")
    args = parser.parse_args(argv)

    try:
        settings = Settings.load(require_credentials=False)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not settings.google_service_account_file.is_file():
        print("The configured Google service-account file is missing.", file=sys.stderr)
        return 2

    drive, slides = _clients(settings)
    folder_id = generic_folder_id(
        drive,
        settings.drive_id,
        settings.drive_templates_folder_id,
    )
    if not folder_id:
        print("Generic Templates could not be resolved uniquely.", file=sys.stderr)
        return 2

    sources = [
        item
        for item in list_files(
            drive,
            settings.drive_id,
            settings.drive_templates_folder_id,
        )
        if item.name == args.source
    ]
    if len(sources) != 1:
        print(f"Expected one source named {args.source}; found {len(sources)}.", file=sys.stderr)
        return 2

    temporary_id = ""
    try:
        with tempfile.TemporaryDirectory(prefix="gable-template-smoke-") as directory:
            connection = connect(Path(directory) / "gable.db")
            apply_migrations(connection)
            messages: list[str] = []

            def say(text: str, _thread: str | None) -> str:
                messages.append(text)
                return "local-template-thread"

            def look_at(file_id: str) -> Inspection:
                """Use production's fail-closed visual path and shared spend ledger."""
                budget_connection = connect(settings.db_path)
                try:
                    apply_migrations(budget_connection)
                    return inspect_source_template(
                        budget_connection,
                        slides,
                        file_id,
                        api_key=settings.openai_image_api_key,
                        model=settings.vision_model,
                    )
                finally:
                    budget_connection.close()

            triage = TemplateTriage(
                connection=connection,
                list_templates=lambda: list_files(
                    drive,
                    settings.drive_id,
                    settings.drive_templates_folder_id,
                ),
                read_presentation=lambda file_id: (
                    slides.presentations().get(presentationId=file_id).execute()
                ),
                say=say,
                look_at=look_at,
                slide_px=(settings.slide_width_px, settings.slide_height_px),
            )
            adopted = len(triage.list_templates())
            triage.scan_new()

            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            copied = (
                drive.files()
                .copy(
                    fileId=sources[0].file_id,
                    body={
                        "name": f"Gable Preflight Smoke Test {stamp}",
                        "parents": [folder_id],
                    },
                    supportsAllDrives=True,
                    fields="id,name,modifiedTime",
                )
                .execute()
            )
            temporary_id = str(copied["id"])
            reviewed = triage.scan_new()
            audit = store.template_audit(connection, temporary_id)
            print(f"Adopted existing templates: {adopted}")
            print(f"New templates reviewed: {reviewed}")
            print(f"Triage status: {audit.status if audit else 'not recorded'}")
            print(f"Triage message: {messages[-1] if messages else 'none'}")
            connection.close()
        return 0 if temporary_id and audit is not None and reviewed == 1 else 2
    finally:
        if temporary_id:
            moved = (
                drive.files()
                .update(
                    fileId=temporary_id,
                    body={"trashed": True},
                    supportsAllDrives=True,
                    fields="id,trashed",
                )
                .execute()
            )
            if not bool(moved.get("trashed")):
                raise RuntimeError("Drive did not confirm cleanup of the temporary template")
            confirmed = (
                drive.files()
                .get(
                    fileId=temporary_id,
                    supportsAllDrives=True,
                    fields="id,trashed",
                )
                .execute()
            )
            if not bool(confirmed.get("trashed")):
                raise RuntimeError("the temporary template is still active in Drive")
            print("Temporary template is in Drive trash; no active copy remains.")


if __name__ == "__main__":
    sys.exit(main())
