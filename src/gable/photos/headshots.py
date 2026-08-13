"""The agent's own face, found in Drive by their name.

Headshots live in `Templates / Head Shots`, one image per agent, named exactly
as the agent is named — `Andy Jang.jpg`. Matching is on the name with the
extension removed, case- and spacing-insensitive, and **nothing fuzzier than
that**: a flyer carrying one agent's name beside another agent's face is worse
than one carrying the design's own placeholder, and only the second is obvious
to whoever reviews it.

**A Drive URL cannot go to Slides.** `CLAUDE.md` §4.3 item 1: Slides rejects
every Google Drive URL form, even for a world-readable file that serves valid
bytes to an anonymous client. So the image is downloaded with the service
account and republished through the droplet's nginx root, exactly as a hero
photo is. `publish_local` is content-addressed, so an agent's face is written
once and reused by every later flyer.

Does not handle: choosing between two files for the same agent. That is a
filing question, and it refuses rather than picking one.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Final

from gable.photos.store import PublishError, publish_local, verify_public

logger = logging.getLogger("gable.photos.headshots")

#: The folder holding the faces, as a child of the configured Templates folder.
HEADSHOTS_FOLDER: Final[str] = "Head Shots"

#: Extensions worth treating as a headshot. Slides fetches PNG and JPEG.
IMAGE_SUFFIXES: Final[tuple[str, ...]] = (".jpg", ".jpeg", ".png")

#: A generous ceiling for one portrait, well under the droplet's memory.
MAX_HEADSHOT_BYTES: Final[int] = 10 * 1024 * 1024


def match_key(name: str) -> str:
    """Reduce a name or filename to its comparable form.

    Args:
        name: An agent's name, or a Drive filename with its extension.

    Returns:
        Casefolded with runs of whitespace collapsed, and any image extension
        removed.

    Raises:
        Nothing.
    """
    text = " ".join(name.split())
    lowered = text.casefold()
    for suffix in IMAGE_SUFFIXES:
        if lowered.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return " ".join(text.split()).casefold()


def find_file(files: list[dict[str, str]], agent_name: str) -> dict[str, str] | None:
    """Pick the one file belonging to this agent.

    Args:
        files: Drive files from the Head Shots folder, with `id` and `name`.
        agent_name: The agent's full name, as the roster writes it.

    Returns:
        The matching file, or None when there is no match or more than one.

    Raises:
        Nothing.
    """
    wanted = match_key(agent_name)
    if not wanted:
        return None
    matches = [f for f in files if match_key(str(f.get("name") or "")) == wanted]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        logger.error("%d headshots are named %r; refusing to choose", len(matches), agent_name)
    return None


def list_headshots(drive: Any, drive_id: str, templates_folder_id: str) -> list[dict[str, str]]:  # noqa: ANN401
    """Every image in the Head Shots folder.

    Args:
        drive: A Drive v3 resource.
        drive_id: The shared drive.
        templates_folder_id: The configured Templates folder.

    Returns:
        Files with `id` and `name`, empty when the folder is missing.

    Raises:
        Nothing. A missing folder leaves the design's own face in place, which
        is a flyer worth reviewing rather than a failed run.
    """
    try:
        # Drive query grammar: https://developers.google.com/drive/api/guides/search-files
        folders = (
            drive.files()
            .list(
                corpora="drive",
                driveId=drive_id,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                q=(
                    f"'{templates_folder_id}' in parents"
                    " and mimeType='application/vnd.google-apps.folder'"
                    f" and name='{HEADSHOTS_FOLDER}' and trashed=false"
                ),
                fields="files(id,name)",
                pageSize=10,
            )
            .execute()
            .get("files", [])
        )
        if not folders:
            logger.error("the %s folder is not in the drive", HEADSHOTS_FOLDER)
            return []
        out: list[dict[str, str]] = []
        page_token: str | None = None
        while True:
            response = (
                drive.files()
                .list(
                    corpora="drive",
                    driveId=drive_id,
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                    q=f"'{folders[0]['id']}' in parents and trashed=false",
                    fields="nextPageToken,files(id,name,mimeType)",
                    pageSize=200,
                    pageToken=page_token,
                )
                .execute()
            )
            out.extend(
                {"id": str(f["id"]), "name": str(f["name"])}
                for f in response.get("files", [])
                if str(f.get("mimeType", "")).startswith("image/")
            )
            page_token = response.get("nextPageToken")
            if not page_token:
                return out
    except Exception:
        logger.exception("the headshots folder could not be listed")
        return []


def url_for_agent(
    drive: Any,  # noqa: ANN401 - googleapiclient resource, untyped upstream
    drive_id: str,
    templates_folder_id: str,
    agent_name: str,
    public_root: Path,
    public_base: str,
) -> str:
    """Publish this agent's headshot and return a URL Slides can fetch.

    Args:
        drive: A Drive v3 resource.
        drive_id: The shared drive.
        templates_folder_id: The configured Templates folder.
        agent_name: The agent's full name.
        public_root: nginx's writable document root on the droplet.
        public_base: The public origin serving that root.

    Returns:
        A public image URL, or an empty string when this agent has no file,
        the download fails, or the published URL does not verify. Empty means
        the design keeps its own face, which the caller already handles.

    Raises:
        Nothing. A missing face must never fail a run that is otherwise ready.
    """
    if not agent_name.strip():
        return ""
    chosen = find_file(list_headshots(drive, drive_id, templates_folder_id), agent_name)
    if chosen is None:
        logger.info("no headshot is filed for %s", agent_name)
        return ""
    try:
        data = drive.files().get_media(fileId=chosen["id"], supportsAllDrives=True).execute()
    except Exception:
        logger.exception("the headshot for %s could not be downloaded", agent_name)
        return ""
    if not data or len(data) > MAX_HEADSHOT_BYTES:
        logger.error("the headshot for %s is empty or too large to publish", agent_name)
        return ""
    try:
        url = publish_local(public_root, public_base, data)
    except PublishError:
        logger.exception("the headshot for %s could not be published", agent_name)
        return ""
    usable, detail = verify_public(url)
    if not usable:
        logger.error("the published headshot for %s is not fetchable: %s", agent_name, detail)
        return ""
    logger.info("published the headshot for %s", agent_name)
    return url
