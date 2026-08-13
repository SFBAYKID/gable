"""Building a real `Runner`, wired to Google, Slack and Firecrawl.

`runner.py` takes every outside call as an argument so it can be tested without
a network. This is where those arguments become real ones — the only module that
knows both the settings and the concrete clients, which keeps the credentials in
one place and the sequence in another.
"""

from __future__ import annotations

import logging
import urllib.request
import uuid
from collections.abc import Callable
from sqlite3 import Connection
from typing import Any

from gable import spend
from gable.agents.website import lookup_official_profile
from gable.config import Settings
from gable.db import store
from gable.listings.enrich import default_research
from gable.photos.fit import assess, fit_locally, image_dimensions
from gable.photos.headshots import MAX_HEADSHOT_BYTES
from gable.photos.headshots import url_for_agent as headshot_url_for
from gable.photos.store import publish_local, verify_public
from gable.photos.verify import verify as verify_image
from gable.pipeline.runner import Runner
from gable.pipeline.vision import Inspection
from gable.pipeline.vision import inspect as inspect_flyer
from gable.slides import fields as template_fields
from gable.slides import preflight
from gable.slides.elements import descendants, text_content
from gable.slides.hero import find_headshot_frame, find_hero_frame
from gable.slides.library import list_files as list_template_files
from gable.slides.replacement import confirmed_replacement_count, safe_replacement_requests
from gable.slides.selection import template_picker
from gable.voice import is_clean, safe

logger = logging.getLogger("gable.live")


def _restore_replacement_z_order(
    page: dict[str, Any], target_id: str, replacement_id: str
) -> list[dict[str, Any]] | None:
    """Keep a newly created element at the deleted target's original depth.

    Slides creates the replacement independently of the deleted shape. Moving
    it to the back is not equivalent to replacing the shape: it can put a house
    behind a full-slide background. ``pageElements`` is ordered back-to-front,
    and a multi-element z-order request preserves the moved elements' relative
    order, so bringing only the elements that were above the target back to the
    front recreates the target's exact layer boundary in one request.

    Returns:
        The optional z-order request, an empty list when the target was already
        frontmost, or None when the source order is not safe to interpret.
    """
    if not replacement_id or replacement_id == target_id:
        return None
    elements = page.get("pageElements", [])
    target_indexes = [
        index for index, element in enumerate(elements) if element.get("objectId") == target_id
    ]
    if len(target_indexes) != 1:
        return None
    above = elements[target_indexes[0] + 1 :]
    above_ids = [element.get("objectId") for element in above]
    if any(not isinstance(object_id, str) or not object_id for object_id in above_ids):
        return None
    if not above_ids:
        return []
    return [
        {
            "updatePageElementsZOrder": {
                "pageElementObjectIds": above_ids,
                "operation": "BRING_TO_FRONT",
            }
        }
    ]


def template_clearance(
    connection: Connection,
    file_id: str,
    label: str,
    current_modified_time: str = "",
) -> str:
    """Return why proactive source review still blocks this design.

    Args:
        connection: Database holding the template catalogue and verdicts.
        file_id: Current Google Drive source id.
        label: Human-visible source name.
        current_modified_time: Revision timestamp from the same Drive listing
            that selected this source. Blank preserves compatibility with
            isolated callers that have no Drive metadata.

    Returns:
        Empty for a baseline or certified design; otherwise a safe explanation
        that pauses the listing before preflight or copy.

    Raises:
        sqlite3.Error: If the stored verdict cannot be read.
    """
    audit = store.template_audit(connection, file_id)
    if audit is None:
        if not store.template_catalog_adopted(connection):
            # A manual run can precede the poller's first safe catalogue
            # adoption. Listing-specific preflight and final inspection remain
            # mandatory in that narrow bootstrap case.
            return ""
        return safe(
            f"I have not finished checking the {label} design yet, so I have not "
            "built anything. Tell me to check that template again."
        )
    if current_modified_time and current_modified_time != audit.modified_time:
        return safe(
            f"The {label} design changed after its last review, so I have not "
            "built anything from the older verdict. Tell me to check the updated "
            "template again."
        )
    if audit.status in {"baseline", "ready"}:
        return ""
    return audit.summary or safe(
        f"The {label} design still needs attention, so I have not built anything. "
        "Fix it and tell me to check the template again."
    )


def place_hero_photo(
    slides: Any,  # noqa: ANN401 - googleapiclient resource, untyped upstream
    file_id: str,
    url: str,
    template_label: str,
    refit: Callable[[str, int, int], str] = lambda existing, _w, _h: existing,
    slide_px: tuple[int, int] = (1080, 1350),
) -> bool:
    """Replace the measured hero frame and verify the API accepted it.

    Args:
        slides: A Slides v1 resource.
        file_id: The copied presentation to edit.
        url: A public image URL already verified by the runner.
        refit: Re-crops the published photo to an exact pixel size and returns
            a new public URL. **Load-bearing**: `createImage` fits an image
            inside the box it is given rather than filling it, so a photo whose
            aspect differs from the frame's is scaled down and centred with the
            design showing around it.
        slide_px: The slide's pixel size, for converting frame EMU to pixels.
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

        # Crop the photo to the frame's own shape before placing it. Slides
        # preserves an image's aspect ratio inside the box it is given, so a
        # 4:5 photo handed to this design's 2.14:1 top band was scaled to the
        # band's height and centred — a narrow column of photograph with the
        # grey layout showing on both sides and the design's angled mask left
        # exposed. Caught on a finished flyer on 2026-08-12.
        frame_width_px = max(1, round(frame.width / slide_w * slide_px[0]))
        frame_height_px = max(1, round(frame.height / slide_h * slide_px[1]))
        placed_url = refit(url, frame_width_px, frame_height_px)
        if not placed_url:
            logger.error("the hero photo could not be recropped to the frame")
            return False

        hero_id = f"gableHero_{uuid.uuid4().hex}"
        z_order = _restore_replacement_z_order(page, target_id, hero_id)
        if z_order is None:
            logger.error("hero photo placement could not preserve the measured layer order")
            return False
        requests: list[dict[str, Any]] = [
            {"deleteObject": {"objectId": target_id}},
            {
                "createImage": {
                    "objectId": hero_id,
                    "url": placed_url,
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
        ]
        requests.extend(z_order)
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


def place_headshot(
    slides: Any,  # noqa: ANN401 - googleapiclient resource, untyped upstream
    file_id: str,
    url: str,
    refit: Callable[[str, int, int], str] = lambda existing, _w, _h: existing,
    slide_px: tuple[int, int] = (1080, 1350),
) -> bool | None:
    """Put the agent's own face on the flyer.

    A flyer shipped carrying one agent's name beside a different agent's
    photograph. The roster has stored a headshot URL per agent all along and
    nothing ever used it, because replacing it is an image operation and every
    fill until now was text.

    Args:
        slides: A Slides v1 resource.
        file_id: The copied presentation to edit.
        url: A public image URL for this agent's headshot.
        refit: Crops the source once to the measured frame and republishes it.
        slide_px: Pixel dimensions corresponding to the Slides page.

    Returns:
        True when Google accepted the replacement, None when the design has no
        recognisable headshot slot, and False when a slot was found but its
        replacement failed. A headshot-free design is valid; leaving a known
        sample face in place is not.

    Raises:
        Nothing. A missing headshot is a flyer worth reviewing, not a crash.

    Note:
        # ASSUMPTION: the frame is replaced with a plain rectangular image. On a
        # design whose headshot is circular this loses the round mask. Rendering
        # one of those templates would confirm which are affected; the vision
        # pass over the finished flyer is what would catch it today.
    """
    if not url:
        return None
    try:
        presentation = slides.presentations().get(presentationId=file_id).execute()
        pages = presentation.get("slides", [])
        if not pages:
            return False
        page = pages[0]
        slide_w = presentation.get("pageSize", {}).get("width", {}).get("magnitude", 0)
        slide_h = presentation.get("pageSize", {}).get("height", {}).get("magnitude", 0)
        hero = find_hero_frame(page, slide_w, slide_h)
        frame = find_headshot_frame(page, slide_w, slide_h, hero.object_id if hero else "")
        if frame is None:
            logger.info("no headshot frame recognised; leaving the design alone")
            return None
        frame_width_px = max(1, round(frame.width / slide_w * slide_px[0]))
        frame_height_px = max(1, round(frame.height / slide_h * slide_px[1]))
        placed_url = refit(url, frame_width_px, frame_height_px)
        if not placed_url:
            logger.error("the agent headshot could not be fitted to its frame")
            return False
        face_id = f"gableFace_{uuid.uuid4().hex}"
        z_order = _restore_replacement_z_order(page, frame.object_id, face_id)
        if z_order is None:
            logger.error("headshot placement could not preserve the measured layer order")
            return False
        requests: list[dict[str, Any]] = [
            {"deleteObject": {"objectId": frame.object_id}},
            {
                "createImage": {
                    "objectId": face_id,
                    "url": placed_url,
                    "elementProperties": {
                        "pageObjectId": page["objectId"],
                        "size": {
                            "width": {"magnitude": frame.width, "unit": "EMU"},
                            "height": {"magnitude": frame.height, "unit": "EMU"},
                        },
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
        ]
        requests.extend(z_order)
        response = (
            slides.presentations()
            .batchUpdate(presentationId=file_id, body={"requests": requests})
            .execute()
        )
        replies = response.get("replies", []) if isinstance(response, dict) else []
        return len(replies) == len(requests)
    except Exception:
        logger.exception("headshot placement failed")
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
    progress: Callable[[str], None] = lambda _stage: None,
    upscale_photo: Callable[[str, bytes, int, int], bytes] | None = None,
    approved_template_warning_codes: frozenset[str] = frozenset(),
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
        progress: Names the current stage for Slack's waiting indicator.
        upscale_photo: One policy and spend-guarded real-photo enlargement.
        approved_template_warning_codes: Exact measured advisory codes Carmen
            or Chase explicitly accepted for this run.

    Returns:
        A ready `Runner`.

    Raises:
        Nothing.
    """
    # Filled when the aspect-preserved human upload is read for its one frame
    # fit. The final visual gate sees both this source and Google's rendered
    # flyer, so an enhancement cannot change the house and still pass merely
    # because the resulting layout is tidy.
    vision_reference = b""

    def say(text: str, thread: str | None) -> str:
        # The last gate before Slack. A message that breaks the house style is
        # logged and dropped rather than sent, because the alternative is
        # Carmen reading a raw error.
        if not is_clean(text):
            logger.error("refused to post a message that breaks the house style")
            return ""
        return str(slack_post(text, thread) or "")

    template_revisions: dict[str, str] = {}

    def list_templates() -> list[dict[str, str]]:
        files = [
            item
            for item in list_template_files(
                drive,
                settings.drive_id,
                settings.drive_templates_folder_id,
            )
            if item.is_slides
        ]
        template_revisions.clear()
        template_revisions.update({item.file_id: item.modified_time for item in files})
        return [{"id": item.file_id, "name": item.name} for item in files]

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
        return confirmed_replacement_count(reply, len(requests))

    def read_text_boxes(file_id: str) -> list[Any]:
        presentation = slides.presentations().get(presentationId=file_id).execute()
        return preflight.text_boxes(presentation)

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

    def preflight_template(
        file_id: str,
        template_label: str,
        category: str,
        resolution: template_fields.Resolution,
        values: dict[str, str],
    ) -> preflight.Report:
        """Read current Drive content and measure it before creating a copy."""
        presentation = slides.presentations().get(presentationId=file_id).execute()
        photo_size: tuple[int, int] | None = None
        photo_url = values.get("hero_photo", "")
        if photo_url:
            verdict = verify_image(photo_url, "any")
            if not verdict.ok:
                return preflight.Report(
                    issues=(
                        preflight.Issue(
                            "unusable_photo",
                            f"I checked the supplied photo before building, but {verdict.say}. "
                            "Send another image and I will check the design again.",
                            blocking=True,
                            status="needs_photo",
                        ),
                    )
                )
            photo_size = (verdict.width, verdict.height)
        headshot_url = values.get("headshot", "")
        if headshot_url:
            headshot = verify_image(headshot_url, "any")
            if not headshot.ok:
                agent = values.get("agent_name", "the agent").strip() or "the agent"
                return preflight.Report(
                    issues=(
                        preflight.Issue(
                            "unusable_headshot",
                            f"I checked the filed headshot for {agent} before building, "
                            f"but {headshot.say}. Replace it in Head Shots, then tell me "
                            "to run again.",
                            blocking=True,
                            status="needs_info",
                        ),
                    )
                )
        return preflight.analyze(
            presentation,
            template_label,
            category,
            resolution,
            values,
            slide_px=(settings.slide_width_px, settings.slide_height_px),
            photo_size=photo_size,
        )

    def look_at(run_id: str, image_bytes: bytes) -> Inspection:
        """Run the paid visual check only while the hard budget permits it."""
        if not settings.openai_image_api_key or not image_bytes:
            return inspect_flyer(
                image_bytes,
                api_key=settings.openai_image_api_key,
                model=settings.vision_model,
                reference_image_bytes=vision_reference,
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
                    reference_image_bytes=vision_reference,
                ),
                run_id=run_id,
            )
        except spend.BudgetExceededError:
            return Inspection(looks_right=False, confident=False, checked=False)

    def place_photo(run_id: str, file_id: str, url: str, template_label: str) -> bool:
        """Fit the original once to the measured frame and place it.

        Three things were learned the hard way, and each is why a line here
        looks the way it does.

        **Do not choose the largest shape blindly.** Imported photos, white
        card panels, and whole element groups can all look like large text-free
        shapes in the API. The shared hero-frame detector applies the same
        measured structural rules used by preflight and refuses ambiguity.

        **Crop to the frame, not to the canvas.** The upload used to be fitted
        to the slide's 4:5 canvas on arrival, which permanently discarded the
        wrong pixels for a wide top band. It now keeps its composition until
        this function recrops it once to the measured frame's own pixel size.

        **Replace the placeholder, do not sit behind it.** These designs ship
        with sky-and-grass artwork in the photo frame. A photo merely sent to
        the back hides behind it, so the placeholder is removed first.
        """
        return place_hero_photo(
            slides,
            file_id,
            url,
            template_label,
            refit=lambda existing, width, height: refit_to_frame(run_id, existing, width, height),
            slide_px=(settings.slide_width_px, settings.slide_height_px),
        )

    def refit_to_frame(run_id: str, existing: str, width_px: int, height_px: int) -> str:
        """Fit the preserved upload directly to the frame's exact pixel size.

        Args:
            run_id: Run whose paid-call ceiling and audit flag apply.
            existing: Public URL of the aspect-preserved upload.
            width_px: The frame's width in pixels.
            height_px: The frame's height in pixels.

        Returns:
            A new public URL, or an empty string when the recrop or the publish
            fails. `publish_local` is content-addressed, so a frame shape that
            recurs across flyers is written once.

        Raises:
            Nothing. Placement treats an empty URL as a failure and stops for
            review rather than posting a letterboxed flyer.
        """
        nonlocal vision_reference
        used_enhancement = False
        try:
            store.set_status(
                connection,
                run_id,
                "building",
                "fitting the supplied photo to the measured frame",
                ai_enhanced=0,
            )
            with urllib.request.urlopen(existing, timeout=30) as response:
                original = response.read()
            vision_reference = original
            source_width, source_height = image_dimensions(original)
            decision = assess(source_width, source_height, width_px, height_px)
            if decision.needs_model and upscale_photo is not None:
                try:
                    progress("is sharpening and enlarging the photo...")
                    original = upscale_photo(run_id, original, width_px, height_px)
                    used_enhancement = True
                except Exception:
                    logger.exception("automatic photo enlargement fell back to local fitting")
            recropped = fit_locally(original, width_px, height_px)
            url = publish_local(settings.photo_public_root, settings.photo_public_base, recropped)
        except Exception:
            logger.exception("the hero photo could not be recropped to its frame")
            return ""
        usable, detail = verify_public(url)
        if not usable:
            logger.error("the recropped hero photo is not fetchable: %s", detail)
            return ""
        store.set_status(
            connection,
            run_id,
            "building",
            (
                "used one fidelity-checked photo enlargement"
                if used_enhancement
                else "used local photo fitting"
            ),
            ai_enhanced=int(used_enhancement),
        )
        return url

    def refit_headshot(existing: str, width_px: int, height_px: int) -> str:
        """Fit a filed portrait to its measured slot without altering the source."""
        try:
            with urllib.request.urlopen(existing, timeout=30) as response:
                original = response.read(MAX_HEADSHOT_BYTES + 1)
            if not original or len(original) > MAX_HEADSHOT_BYTES:
                logger.error("the filed headshot is empty or too large to fit")
                return ""
            fitted = fit_locally(original, width_px, height_px)
            url = publish_local(
                settings.photo_public_root,
                settings.photo_public_base,
                fitted,
            )
        except Exception:
            logger.exception("the agent headshot could not be fitted to its measured frame")
            return ""
        usable, detail = verify_public(url)
        if not usable:
            logger.error("the fitted headshot is not fetchable: %s", detail)
            return ""
        return url

    return Runner(
        connection=connection,
        say=say,
        pick_template=template_picker(list_templates),
        template_clearance=lambda file_id, label: template_clearance(
            connection,
            file_id,
            label,
            template_revisions.get(file_id, ""),
        ),
        read_slide_text=read_slide_text,
        copy_template=copy_template,
        fill=fill,
        research=default_research(settings.firecrawl_api_key, connection),
        official_contact_lookup=lookup_official_profile,
        place_photo=place_photo,
        place_headshot=lambda fid, url: place_headshot(
            slides,
            fid,
            url,
            refit=refit_headshot,
            slide_px=(settings.slide_width_px, settings.slide_height_px),
        ),
        check_photo=check_photo,
        look_at=look_at,
        read_text_boxes=read_text_boxes,
        preflight_template=preflight_template,
        apply=apply,
        thumbnail=thumbnail,
        hero_photo_url=hero_photo_url,
        origin_thread_ts=origin_thread_ts,
        headshot_for=lambda name: headshot_url_for(
            drive,
            settings.drive_id,
            settings.drive_templates_folder_id,
            name,
            settings.photo_public_root,
            settings.photo_public_base,
        ),
        progress=progress,
        approved_template_warning_codes=approved_template_warning_codes,
    )
