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

from gable import spend
from gable.config import Settings
from gable.photos.store import PhotoHost
from gable.photos.verify import verify as verify_image
from gable.pipeline.runner import Runner, default_research, template_picker
from gable.pipeline.vision import Inspection
from gable.pipeline.vision import inspect as inspect_flyer
from gable.slides import fitting
from gable.slides.edits import replace_text
from gable.slides.elements import descendants, font_size_pt, text_content
from gable.slides.hero import find_hero_frame
from gable.voice import is_clean

logger = logging.getLogger("gable.live")


def safe_replacement_requests(
    presentation: dict[str, Any],
    pairs: dict[str, str],
) -> list[dict[str, Any]]:
    """Build replacements only when every literal occurs exactly once.

    ``replaceAllText`` matches substrings. A literal such as ``Phone`` can
    therefore hit both ``Phone`` and ``Phone Number`` while Google still
    returns success. Reading the complete recursive text first turns that
    silent corruption into a refusal before any batch is sent.

    Args:
        presentation: Current Slides presentation payload.
        pairs: Literal text to replacement value.

    Returns:
        One request per pair, or an empty list when any literal is absent or
        appears more than once.

    Raises:
        Nothing.
    """
    page_ids = [str(page.get("objectId") or "") for page in presentation.get("slides", [])]
    texts = [
        text_content(element)
        for page in presentation.get("slides", [])
        for element in descendants(page.get("pageElements", []))
    ]
    requests: list[dict[str, Any]] = []
    for literal, value in pairs.items():
        occurrences = sum(text.count(literal) for text in texts)
        if occurrences != 1:
            logger.error(
                "refused an unsafe text replacement with %d occurrence(s)",
                occurrences,
            )
            return []
        requests.extend(replace_text(literal, value, page_ids, allow_short=True))
    return requests


def _implied_font_size_pt(height_emu: float) -> float:
    """Estimate a text box's font size from its height.

    Args:
        height_emu: The box's rendered height in EMU.

    Returns:
        A point size, or 0.0 when the height is unusable.

    Raises:
        Nothing.

    Note:
        # ASSUMPTION: a single-line box is laid out at roughly 1.2x leading, so
        # the type is about 0.8 of the box height. Confirmed well enough to
        # catch overflow rather than to reproduce the designer's exact size —
        # this feeds the fitter, which only ever shrinks text that does not fit.
        # A rendered flyer comparing designed and fitted sizes would refine it.
    """
    if height_emu <= 0:
        return 0.0
    return max(1.0, (height_emu / fitting.EMU_PER_POINT) / 1.2)


def place_hero_photo(
    slides: Any,  # noqa: ANN401 - googleapiclient resource, untyped upstream
    file_id: str,
    url: str,
    template_label: str,
) -> bool:
    """Replace the measured hero frame and verify the API accepted it.

    Args:
        slides: A Slides v1 resource.
        file_id: The copied presentation to edit.
        url: A public image URL already verified by the runner.
        template_label: Catalogue filename. Kept for logging and for the
            caller's signature; the frame itself is now measured from the
            presentation rather than looked up by name, because a stored id
            goes stale the moment a template is re-exported.

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

        # Measure the frame rather than looking up a hand-read object id. Only
        # three of the 45 designs ever had one recorded, and one of those three
        # was wrong — `Just Listed — Plus Open House — Offered At` named a band
        # inside the photo instead of the photo. See `slides/hero.py` for how
        # the templates are actually built and why an image-element search finds
        # nothing on 44 of them.
        frame = find_hero_frame(page, slide_w, slide_h)
        if frame is None:
            logger.error("hero photo placement could not find a photo frame in %s", template_label)
            return False
        target_id = frame.object_id
        matches = [
            element
            for element in page.get("pageElements", [])
            if element.get("objectId") == target_id
        ]
        if len(matches) != 1 or "elementGroup" in matches[0] or text_content(matches[0]):
            logger.error("hero photo placement could not confirm the measured layer")
            return False

        hero_id = f"gableHero_{uuid.uuid4().hex}"
        requests: list[dict[str, Any]] = [
            {"deleteObject": {"objectId": target_id}},
            {
                "createImage": {
                    "objectId": hero_id,
                    "url": url,
                    "elementProperties": {
                        "pageObjectId": page["objectId"],
                        # The frame's own bounds, not the whole slide. Sizing to
                        # the slide letterboxed a landscape photo into a
                        # portrait design and painted over the layout.
                        "size": {
                            "width": {"magnitude": frame.width, "unit": "EMU"},
                            "height": {"magnitude": frame.height, "unit": "EMU"},
                        },
                        # ABSOLUTE, and position restated explicitly: RELATIVE
                        # multiplies translation as well as scale (§4.3).
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": frame.x,
                            "translateY": frame.y,
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
    *,
    hero_photo_url: str = "",
    origin_thread_ts: str = "",
) -> Runner:
    """Assemble a `Runner` that talks to the real services.

    Args:
        settings: Parsed configuration.
        connection: An open database connection.
        drive: A Drive v3 resource.
        slides: A Slides v1 resource.
        slack_post: A callable taking `(text, thread_ts)` and returning the
            thread timestamp it landed in.
        hero_photo_url: A fitted, published photo when resuming a paused run.
        origin_thread_ts: Root Slack thread that a resumed run must preserve.

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
            # PPTX imports often wrap the design in elementGroup; descendants
            # follows its children so placeholders inside a group stay visible.
            for element in descendants(page.get("pageElements", [])):
                text = text_content(element)
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
        requests = safe_replacement_requests(presentation, pairs)
        if len(requests) != len(pairs):
            return -1
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
            for element in descendants(page.get("pageElements", [])):
                text = text_content(element)
                if not text:
                    continue
                size_pt = font_size_pt(element)
                transform = element.get("transform", {})
                size = element.get("size", {})
                width = size.get("width", {}).get("magnitude", 0) * transform.get("scaleX", 1)
                height = size.get("height", {}).get("magnitude", 0) * transform.get("scaleY", 1)

                # Slides reports no fontSize on a run that inherits its size from
                # the theme or placeholder, which is most of them on an imported
                # deck. `plan_fits` skips any box reporting zero, so the boxes
                # most likely to overflow were the exact ones never checked — a
                # rendered flyer showed $685,000 clipped to "$685,00" with the
                # last digit wrapped, and the agent name overlapping the line
                # beneath it. Estimating from the box geometry brings those back
                # under the fitter.
                if size_pt <= 0 and height > 0:
                    size_pt = _implied_font_size_pt(height)

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

    def look_at(image_bytes: bytes) -> Inspection:
        """Run the paid visual check only while the hard budget permits it."""
        if not settings.openai_image_api_key or not image_bytes:
            return inspect_flyer(
                image_bytes,
                api_key=settings.openai_image_api_key,
                model=settings.vision_model,
            )
        estimate = spend.Estimate(
            service="openai",
            model=settings.vision_model,
            usd=spend.VISION_RESERVE_USD,
            detail="conservative vision-call reservation",
        )
        try:
            return spend.guarded_call(
                connection,
                estimate,
                lambda: inspect_flyer(
                    image_bytes,
                    api_key=settings.openai_image_api_key,
                    model=settings.vision_model,
                ),
            )
        except spend.BudgetExceededError:
            return Inspection(looks_right=False, confident=False, checked=False)

    def place_photo(file_id: str, url: str, template_label: str) -> bool:
        """Put a centred 4:5 hero behind a template's measured masks.

        Three things were learned the hard way, and each is why a line here
        looks the way it does.

        **Do not infer a frame from size.** Imported photos, white card panels,
        and whole element groups can all look like large text-free shapes in
        the API. The manifest names the exact removable raster-art layer.

        **Use the full slide bounds.** Slack already fits the photo to the
        template's 1080 by 1350 canvas. Matching that 4:5 box keeps it centred
        without letterboxing; the surviving design panels mask the parts that
        are not meant to show.

        **Replace the placeholder, do not sit behind it.** These designs ship
        with sky-and-grass artwork in the photo frame. A photo merely sent to
        the back hides behind it, so the placeholder is removed first.
        """
        return place_hero_photo(slides, file_id, url, template_label)

    return Runner(
        connection=connection,
        say=say,
        pick_template=template_picker(list_templates),
        read_slide_text=read_slide_text,
        copy_template=copy_template,
        fill=fill,
        research=default_research(settings.firecrawl_api_key, connection),
        place_photo=place_photo,
        check_photo=check_photo,
        look_at=look_at,
        read_text_boxes=read_text_boxes,
        apply=apply,
        thumbnail=thumbnail,
        hero_photo_url=hero_photo_url,
        origin_thread_ts=origin_thread_ts,
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
