"""Building a real `Runner`, wired to Google, Slack and Firecrawl.

`runner.py` takes every outside call as an argument so it can be tested without
a network. This is where those arguments become real ones — the only module that
knows both the settings and the concrete clients, which keeps the credentials in
one place and the sequence in another.
"""

from __future__ import annotations

import logging
from sqlite3 import Connection
from typing import Any

from gable.config import Settings
from gable.photos.store import PhotoHost
from gable.pipeline.runner import Runner, default_research, template_picker
from gable.slides.edits import replace_text
from gable.voice import is_clean

logger = logging.getLogger("gable.live")


def build_runner(
    settings: Settings,
    connection: Connection,
    drive: Any,  # noqa: ANN401 - googleapiclient resource, untyped upstream
    slides: Any,  # noqa: ANN401
    slack_post: Any,  # noqa: ANN401
) -> Runner:
    """Assemble a `Runner` that talks to the real services.

    Args:
        settings: Parsed configuration.
        connection: An open database connection.
        drive: A Drive v3 resource.
        slides: A Slides v1 resource.
        slack_post: A callable taking `(text, thread_ts)` and returning the
            thread timestamp it landed in.

    Returns:
        A ready `Runner`.

    Raises:
        Nothing.
    """

    def say(text: str, thread: str | None) -> str:
        # The last gate before Slack. A message that breaks the house style is
        # logged and dropped rather than sent, because the alternative is
        # Carmen reading a raw error.
        if not is_clean(text):
            logger.error("refused to post a message that breaks the house style")
            return ""
        return str(slack_post(text, thread) or "")

    def list_templates() -> list[dict[str, str]]:
        found = (
            drive.files()
            .list(
                corpora="drive",
                driveId=settings.drive_id,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                q=("mimeType='application/vnd.google-apps.presentation' and trashed=false"),
                fields="files(id,name,appProperties)",
                pageSize=200,
            )
            .execute()
            .get("files", [])
        )
        return [
            {"id": f["id"], "name": f["name"]}
            for f in found
            if (f.get("appProperties") or {}).get("gable_role") == "template"
        ]

    def read_slide_text(file_id: str) -> list[str]:
        presentation = slides.presentations().get(presentationId=file_id).execute()
        out: list[str] = []
        for page in presentation.get("slides", []):
            for element in page.get("pageElements", []):
                runs = element.get("shape", {}).get("text", {}).get("textElements", [])
                text = "".join(r.get("textRun", {}).get("content", "") for r in runs).strip()
                if text:
                    out.append(text)
        return out

    def copy_template(template_id: str, name: str) -> tuple[str, str]:
        copied = (
            drive.files()
            .copy(
                fileId=template_id,
                body={"name": name, "parents": [settings.drive_output_folder_id]},
                supportsAllDrives=True,
                fields="id,webViewLink",
            )
            .execute()
        )
        return str(copied["id"]), str(copied["webViewLink"])

    def fill(file_id: str, pairs: dict[str, str]) -> int:
        if not pairs:
            return 0
        presentation = slides.presentations().get(presentationId=file_id).execute()
        page_ids = [page["objectId"] for page in presentation.get("slides", [])]
        requests: list[dict[str, Any]] = []
        for literal, value in pairs.items():
            # allow_short because these literals came off the slide itself, so
            # they are known to exist and known to be what we mean.
            requests.extend(replace_text(literal, value, page_ids, allow_short=True))
        reply = (
            slides.presentations()
            .batchUpdate(presentationId=file_id, body={"requests": requests})
            .execute()
        )
        return sum(
            item.get("replaceAllText", {}).get("occurrencesChanged", 0)
            for item in reply.get("replies", [])
        )

    return Runner(
        connection=connection,
        say=say,
        pick_template=template_picker(list_templates),
        read_slide_text=read_slide_text,
        copy_template=copy_template,
        fill=fill,
        research=default_research(settings.firecrawl_api_key),
    )


def photo_host(settings: Settings) -> PhotoHost:
    """The droplet that serves hero photos.

    Args:
        settings: Parsed configuration.

    Returns:
        A configured host.

    Raises:
        Nothing.
    """
    del settings  # the host is fixed infrastructure, not yet configurable
    return PhotoHost(
        ssh_target="root@143.110.146.87",
        ssh_key_path="/root/.ssh/gable_droplet",
        public_base="http://143.110.146.87",
    )
