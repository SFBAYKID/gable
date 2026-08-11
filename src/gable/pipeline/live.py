"""Building a real `Runner`, wired to Google, Slack and Firecrawl.

`runner.py` takes every outside call as an argument so it can be tested without
a network. This is where those arguments become real ones — the only module that
knows both the settings and the concrete clients, which keeps the credentials in
one place and the sequence in another.
"""

from __future__ import annotations

import logging
import uuid
from sqlite3 import Connection
from typing import Any

from gable.config import Settings
from gable.photos.store import PhotoHost
from gable.photos.verify import verify as verify_image
from gable.pipeline.runner import Runner, default_research, template_picker
from gable.slides import fitting
from gable.slides.edits import replace_text
from gable.voice import is_clean

logger = logging.getLogger("gable.live")


def place_hero_photo(
    slides: Any,  # noqa: ANN401 - googleapiclient resource, untyped upstream
    file_id: str,
    url: str,
) -> bool:
    """Replace the design's photo placeholder and verify the API accepted it.

    Args:
        slides: A Slides v1 resource.
        file_id: The copied presentation to edit.
        url: A public image URL already verified by the runner.

    Returns:
        True only when Google reports a reply for every placement request.
        False when no plausible frame exists or any Slides call fails.

    Raises:
        Nothing. Placement failure is a normal runner outcome and must reach
        its precise ``needs_review`` branch rather than the generic exception
        boundary.
    """
    try:
        presentation = slides.presentations().get(presentationId=file_id).execute()
        pages = presentation.get("slides", [])
        if not pages:
            logger.error("hero photo placement found no slide")
            return False
        page = pages[0]
        slide_w = presentation.get("pageSize", {}).get("width", {}).get("magnitude", 0)
        slide_h = presentation.get("pageSize", {}).get("height", {}).get("magnitude", 0)
        if slide_w <= 0 or slide_h <= 0:
            logger.error("hero photo placement found no usable slide size")
            return False

        best: tuple[float, dict[str, Any]] | None = None
        for element in page.get("pageElements", []):
            if element.get("shape", {}).get("text"):
                continue
            transform = element.get("transform", {})
            size = element.get("size", {})
            width = size.get("width", {}).get("magnitude", 0) * transform.get("scaleX", 1)
            height = size.get("height", {}).get("magnitude", 0) * transform.get("scaleY", 1)
            if width <= 0 or height <= 0:
                continue
            area = width * height
            if area < (slide_w * slide_h) * 0.12:
                continue
            if best is None or area > best[0]:
                best = (area, element)

        if best is None:
            logger.error("hero photo placement found no plausible frame")
            return False

        _, frame = best
        transform = frame.get("transform", {})
        size = frame.get("size", {})
        width = size.get("width", {}).get("magnitude", 0) * transform.get("scaleX", 1)
        height = size.get("height", {}).get("magnitude", 0) * transform.get("scaleY", 1)
        left = transform.get("translateX", 0)
        top = transform.get("translateY", 0)
        hero_id = f"gableHero_{uuid.uuid4().hex}"
        requests: list[dict[str, Any]] = [
            {"deleteObject": {"objectId": frame["objectId"]}},
            {
                "createImage": {
                    "objectId": hero_id,
                    "url": url,
                    "elementProperties": {
                        "pageObjectId": page["objectId"],
                        "size": {
                            "width": {"magnitude": width, "unit": "EMU"},
                            "height": {"magnitude": height, "unit": "EMU"},
                        },
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": left,
                            "translateY": top,
                            "unit": "EMU",
                        },
                    },
                }
            },
            {
                "updatePageElementsZOrder": {
                    "pageElementObjectIds": [hero_id],
                    "operation": "SEND_TO_BACK",
                }
            },
        ]
        response = (
            slides.presentations()
            .batchUpdate(presentationId=file_id, body={"requests": requests})
            .execute()
        )
        replies = response.get("replies", []) if isinstance(response, dict) else []
        if len(replies) != len(requests):
            logger.error("hero photo placement returned an incomplete reply")
            return False
        return bool(replies)
    except Exception:
        logger.exception("hero photo placement failed")
        return False


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

    def read_text_boxes(file_id: str) -> list[fitting.TextBox]:
        presentation = slides.presentations().get(presentationId=file_id).execute()
        boxes: list[fitting.TextBox] = []
        for page in presentation.get("slides", []):
            for element in page.get("pageElements", []):
                shape = element.get("shape", {})
                runs = shape.get("text", {}).get("textElements", [])
                text = "".join(r.get("textRun", {}).get("content", "") for r in runs).strip()
                if not text:
                    continue
                size_pt = 0.0
                for run in runs:
                    magnitude = (
                        run.get("textRun", {}).get("style", {}).get("fontSize", {}).get("magnitude")
                    )
                    if magnitude:
                        size_pt = float(magnitude)
                        break
                transform = element.get("transform", {})
                size = element.get("size", {})
                width = size.get("width", {}).get("magnitude", 0) * transform.get("scaleX", 1)
                height = size.get("height", {}).get("magnitude", 0) * transform.get("scaleY", 1)
                # How many lines the box can hold at this size, so a two-line
                # box is not shrunk as though it were one.
                lines = 1
                if size_pt > 0 and height > 0:
                    lines = max(1, int((height / fitting.EMU_PER_POINT) // (size_pt * 1.2)))
                boxes.append(
                    fitting.TextBox(
                        object_id=element["objectId"],
                        text=text,
                        font_size_pt=size_pt,
                        width_emu=float(width),
                        lines=lines,
                    )
                )
        return boxes

    def apply(file_id: str, requests: list[dict[str, Any]]) -> None:
        if not requests:
            return
        slides.presentations().batchUpdate(
            presentationId=file_id, body={"requests": requests}
        ).execute()

    def thumbnail(file_id: str) -> bytes:
        import urllib.request

        presentation = slides.presentations().get(presentationId=file_id).execute()
        page_id = presentation["slides"][0]["objectId"]
        rendered = (
            slides.presentations()
            .pages()
            .getThumbnail(
                presentationId=file_id,
                pageObjectId=page_id,
                thumbnailProperties_thumbnailSize="LARGE",
            )
            .execute()
        )
        with urllib.request.urlopen(rendered["contentUrl"], timeout=60) as response:
            data: bytes = response.read()
            return data

    def check_photo(url: str, slot: str) -> tuple[bool, str]:
        """Adapt the concrete image verdict to the runner's injected seam."""
        verdict = verify_image(url, slot)
        return verdict.ok, verdict.say

    def place_photo(file_id: str, url: str) -> bool:
        """Put the hero photo exactly where the design leaves room for it.

        Three things were learned the hard way, and each is why a line here
        looks the way it does.

        **Do not inherit the frame's transform.** An imported PPTX element can
        carry a 2160% scale against a tiny intrinsic size; passing both to
        `createImage` produced an image rendered at zero by zero — present in
        the file, invisible on the slide. Absolute EMU throughout.

        **Do not go full bleed.** A photo stretched over the whole slide covers
        the headline and the card. The frame's own bounds are the design's
        answer to where the photo goes, so they are used.

        **Replace the placeholder, do not sit behind it.** These designs ship
        with sky-and-grass artwork in the photo frame. A photo merely sent to
        the back hides behind it, so the placeholder is removed first.
        """
        return place_hero_photo(slides, file_id, url)

    return Runner(
        connection=connection,
        say=say,
        pick_template=template_picker(list_templates),
        read_slide_text=read_slide_text,
        copy_template=copy_template,
        fill=fill,
        research=default_research(settings.firecrawl_api_key),
        place_photo=place_photo,
        check_photo=check_photo,
        read_text_boxes=read_text_boxes,
        apply=apply,
        thumbnail=thumbnail,
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
