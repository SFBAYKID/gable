"""Read the one Drive folder that is allowed to contain source templates."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger("gable.template_library")

GENERIC_TEMPLATES_FOLDER: Final[str] = "Generic Templates"
SLIDES_MIME_TYPE: Final[str] = "application/vnd.google-apps.presentation"
FOLDER_MIME_TYPE: Final[str] = "application/vnd.google-apps.folder"


@dataclass(frozen=True, slots=True)
class TemplateFile:
    """Drive identity and revision metadata for one source design."""

    file_id: str
    name: str
    modified_time: str = ""
    mime_type: str = SLIDES_MIME_TYPE

    @property
    def is_slides(self) -> bool:
        """Whether the file can be read and copied through the Slides API."""
        return self.mime_type == SLIDES_MIME_TYPE


def generic_folder_id(
    drive: Any,  # noqa: ANN401 - googleapiclient resource, untyped upstream
    drive_id: str,
    templates_folder_id: str,
) -> str:
    """Resolve Generic Templates under the configured Templates folder."""
    response = (
        drive.files()
        .list(
            corpora="drive",
            driveId=drive_id,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            q=(
                f"'{templates_folder_id}' in parents"
                " and mimeType='application/vnd.google-apps.folder'"
                f" and name='{GENERIC_TEMPLATES_FOLDER}'"
                " and trashed=false"
            ),
            fields="files(id,name)",
            pageSize=10,
        )
        .execute()
    )
    folders = response.get("files", [])
    if len(folders) != 1:
        logger.error(
            "expected one %s folder and found %d",
            GENERIC_TEMPLATES_FOLDER,
            len(folders),
        )
        return ""
    return str(folders[0]["id"])


def list_files(
    drive: Any,  # noqa: ANN401 - googleapiclient resource, untyped upstream
    drive_id: str,
    templates_folder_id: str,
) -> list[TemplateFile]:
    """List every source presentation, following all Drive pages."""
    folder_id = generic_folder_id(drive, drive_id, templates_folder_id)
    if not folder_id:
        return []
    templates: list[TemplateFile] = []
    page_token: str | None = None
    while True:
        response = (
            drive.files()
            .list(
                corpora="drive",
                driveId=drive_id,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                q=(
                    f"'{folder_id}' in parents and mimeType!='{FOLDER_MIME_TYPE}' and trashed=false"
                ),
                fields="nextPageToken,files(id,name,modifiedTime,mimeType)",
                pageSize=200,
                pageToken=page_token,
            )
            .execute()
        )
        templates.extend(
            TemplateFile(
                file_id=str(item.get("id") or ""),
                name=str(item.get("name") or ""),
                modified_time=str(item.get("modifiedTime") or ""),
                mime_type=str(item.get("mimeType") or ""),
            )
            for item in response.get("files", [])
            if item.get("id") and item.get("name")
        )
        page_token = response.get("nextPageToken")
        if not page_token:
            return templates
