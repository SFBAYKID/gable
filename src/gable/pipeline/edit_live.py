"""Concrete Drive, thumbnail, and vision edges for verified flyer edits.

An edit never touches the currently linked presentation.  The source is copied
with a private, stable edit identity, and only that copy is mutated.  If Drive
accepts a copy but its acknowledgement is lost, the private properties can
recover the one exact draft; absence or multiplicity fails closed and never
authorizes a second blind copy.

This module does not resolve natural language, write SQLite state, post Slack
messages, or promote a draft.  Those decisions live around these bounded I/O
edges.
"""

from __future__ import annotations

import logging
import urllib.request
from sqlite3 import Connection
from typing import Any, Final

from gable import spend
from gable.pipeline.vision import Inspection
from gable.pipeline.vision import inspect as inspect_flyer

logger = logging.getLogger("gable.edit.live")

MAX_DRIVE_SEARCH_PAGES: Final[int] = 10
DRIVE_SEARCH_PAGE_SIZE: Final[int] = 100
THUMBNAIL_TIMEOUT_SECONDS: Final[int] = 60
REFERENCE_TIMEOUT_SECONDS: Final[int] = 30
MAX_REFERENCE_BYTES: Final[int] = 25 * 1024 * 1024


class DraftCopyError(RuntimeError):
    """Drive could not prove exactly one private copy for an edit."""


def _property_literal(value: str) -> str:
    """Quote a trusted Drive property value for its query grammar."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def find_edit_drafts(
    drive: Any,  # noqa: ANN401 - generated googleapiclient resource
    drive_id: str,
    output_folder_id: str,
    edit_id: str,
    source_file_id: str,
) -> list[dict[str, str]]:
    """Return every exact private draft marker from a complete bounded search.

    Args:
        drive: Drive v3 discovery resource on Gable's bounded transport.
        drive_id: Shared-drive id restricting the query.
        output_folder_id: Folder where finished and draft Slides files live.
        edit_id: Stable database edit id stored in private app properties.
        source_file_id: Canonical presentation copied for this edit.

    Returns:
        Matching id, link, and name records.

    Raises:
        DraftCopyError: If pagination is malformed, repeats, or exceeds its
            explicit page budget.
        Exception: Drive transport or API errors.
    """
    query = (
        f"'{_property_literal(output_folder_id)}' in parents and trashed = false and "
        "appProperties has { key='gable_edit_id' and "
        f"value='{_property_literal(edit_id)}' }} and "
        "appProperties has { key='gable_source_id' and "
        f"value='{_property_literal(source_file_id)}' }}"
    )
    results: list[dict[str, str]] = []
    page_token = ""
    seen_tokens: set[str] = set()
    for _page in range(MAX_DRIVE_SEARCH_PAGES):
        # Drive query/appProperties and shared-drive parameters:
        # https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list
        request = drive.files().list(
            q=query,
            corpora="drive",
            driveId=drive_id,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            pageSize=DRIVE_SEARCH_PAGE_SIZE,
            pageToken=page_token or None,
            fields="nextPageToken,files(id,name,webViewLink,appProperties)",
        )
        response = request.execute()
        files = response.get("files", []) if isinstance(response, dict) else []
        if not isinstance(files, list) or not all(isinstance(item, dict) for item in files):
            raise DraftCopyError("Drive returned a malformed edit-draft search")
        for item in files:
            results.append(
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("name") or ""),
                    "webViewLink": str(item.get("webViewLink") or ""),
                }
            )
        next_token = str(response.get("nextPageToken") or "")
        if not next_token:
            return results
        if next_token in seen_tokens:
            raise DraftCopyError("Drive repeated an edit-draft page")
        seen_tokens.add(next_token)
        page_token = next_token
    raise DraftCopyError("Drive edit-draft search exceeded its page budget")


def copy_edit_draft(
    drive: Any,  # noqa: ANN401 - generated googleapiclient resource
    drive_id: str,
    output_folder_id: str,
    source_file_id: str,
    edit_id: str,
) -> tuple[str, str]:
    """Create or recover the sole versioned copy for one claimed edit.

    The initial lookup handles a replay after an acknowledgement loss.  A copy
    exception triggers one read-only reconciliation, never another write.

    Raises:
        DraftCopyError: If the source, returned copy, or reconciliation cannot
            prove one editable draft.
        Exception: A source-name read error before any copy attempt.
    """

    def resolved() -> tuple[str, str] | None:
        matches = find_edit_drafts(
            drive,
            drive_id,
            output_folder_id,
            edit_id,
            source_file_id,
        )
        if len(matches) > 1:
            raise DraftCopyError("Drive found multiple copies for one flyer edit")
        if not matches:
            return None
        file_id = matches[0]["id"].strip()
        url = matches[0]["webViewLink"].strip()
        if not file_id or not url:
            raise DraftCopyError("the recovered edit copy has no usable link")
        return file_id, url

    existing = resolved()
    if existing is not None:
        return existing
    # Source metadata read:
    # https://developers.google.com/workspace/drive/api/reference/rest/v3/files/get
    metadata = (
        drive.files().get(fileId=source_file_id, supportsAllDrives=True, fields="id,name").execute()
    )
    source_name = str(metadata.get("name") or "").strip() if isinstance(metadata, dict) else ""
    if not source_name:
        raise DraftCopyError("the current flyer has no usable Drive name")
    body = {
        "name": f"{source_name} — Updated {edit_id[-8:]}",
        "parents": [output_folder_id],
        "appProperties": {
            "gable_edit_id": edit_id,
            "gable_source_id": source_file_id,
        },
    }
    try:
        # Drive copy body, private appProperties, and shared-drive support:
        # https://developers.google.com/workspace/drive/api/reference/rest/v3/files/copy
        copied = (
            drive.files()
            .copy(
                fileId=source_file_id,
                body=body,
                supportsAllDrives=True,
                fields="id,name,webViewLink,appProperties",
            )
            .execute()
        )
    except Exception as exc:
        recovered = resolved()
        if recovered is not None:
            return recovered
        raise DraftCopyError("Drive did not confirm the separate edit copy") from exc
    file_id = str(copied.get("id") or "") if isinstance(copied, dict) else ""
    url = str(copied.get("webViewLink") or "") if isinstance(copied, dict) else ""
    if file_id and url:
        return file_id, url
    recovered = resolved()
    if recovered is not None:
        return recovered
    raise DraftCopyError("Drive returned no usable link for the separate edit copy")


def render_thumbnail(
    slides: Any,  # noqa: ANN401 - generated googleapiclient resource
    file_id: str,
) -> bytes:
    """Render the first slide and download its bounded thumbnail URL."""
    presentation = slides.presentations().get(presentationId=file_id).execute()
    pages = presentation.get("slides", []) if isinstance(presentation, dict) else []
    if len(pages) != 1 or not str(pages[0].get("objectId") or ""):
        raise ValueError("the edited flyer is not exactly one renderable slide")
    # Slides' thumbnail URL is short-lived and must be downloaded promptly:
    # https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/getThumbnail
    rendered = (
        slides.presentations()
        .pages()
        .getThumbnail(
            presentationId=file_id,
            pageObjectId=str(pages[0]["objectId"]),
            thumbnailProperties_thumbnailSize="LARGE",
        )
        .execute()
    )
    url = str(rendered.get("contentUrl") or "") if isinstance(rendered, dict) else ""
    if not url:
        raise ValueError("Google Slides returned no rendered flyer image")
    with urllib.request.urlopen(url, timeout=THUMBNAIL_TIMEOUT_SECONDS) as response:
        image: bytes = response.read(MAX_REFERENCE_BYTES + 1)
    if not image or len(image) > MAX_REFERENCE_BYTES:
        raise ValueError("the rendered flyer image was empty or too large")
    return image


def download_reference(url: str) -> bytes:
    """Download the retained human property photo with an explicit byte cap."""
    if not url.strip():
        return b""
    with urllib.request.urlopen(url, timeout=REFERENCE_TIMEOUT_SECONDS) as response:
        content: bytes = response.read(MAX_REFERENCE_BYTES + 1)
    if not content or len(content) > MAX_REFERENCE_BYTES:
        return b""
    return content


def inspect_edit(
    connection: Connection,
    run_id: str,
    rendered: bytes,
    reference: bytes,
    api_key: str,
    model: str,
) -> Inspection:
    """Run the existing strict visual gate under the shared spend ceiling."""
    if not api_key or not rendered:
        return Inspection(looks_right=False, confident=False, checked=False)
    estimate = spend.Estimate(
        service="openai",
        model=model,
        usd=spend.VISION_RESERVE_USD,
        detail="conservative post-edit vision-call reservation",
    )
    try:
        return spend.guarded_call(
            connection,
            estimate,
            lambda: inspect_flyer(
                rendered,
                api_key=api_key,
                model=model,
                reference_image_bytes=reference,
            ),
            run_id=run_id,
        )
    except spend.BudgetExceededError:
        logger.warning("the shared spend ceiling blocked post-edit visual inspection")
        return Inspection(looks_right=False, confident=False, checked=False)
