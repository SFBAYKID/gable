"""Putting a photograph onto a design without disturbing anything else.

Everything here is about the moment a supplied image replaces the shape a
designer drew for it: proving the source template is still the one that was
audited, deleting the sample photograph, creating the replacement at exactly the
frame's size and transform, and putting it back at the depth the original sat
at. `live.py` holds the credentials and the sequence; this holds the traps.

Does not handle: measuring the frame, which is `slides/hero.py`, or cropping the
image, which is `photos/fit.py`.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from sqlite3 import Connection
from typing import Any

from gable.db import store
from gable.slides.designs import extra_deletions
from gable.slides.elements import text_content
from gable.slides.hero import find_hero_frame, headshot_frames
from gable.voice import safe

logger = logging.getLogger("gable.live")


def _restore_replacement_z_order(
    page: dict[str, Any],
    target_id: str,
    replacement_id: str,
    also_deleted: tuple[str, ...] = (),
) -> list[dict[str, Any]] | None:
    """Keep a newly created element at the deleted target's original depth.

    Slides creates the replacement independently of the deleted shape. Moving
    it to the back is not equivalent to replacing the shape: it can put a house
    behind a full-slide background. ``pageElements`` is ordered back-to-front,
    and a multi-element z-order request preserves the moved elements' relative
    order, so bringing only the elements that were above the target back to the
    front recreates the target's exact layer boundary in one request.

    Args:
        page: The copied slide, as read before any edit.
        target_id: The shape being replaced.
        replacement_id: The new image's id.
        also_deleted: Other shapes this same batch removes. They must not be
            named in the z-order request: New Listing with Open House deletes a
            sample-photo layer that sits ABOVE its well, and asking Slides to
            reorder an object the same batch deleted failed the whole update —
            the flyer built with no photograph on it.

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
    removed = set(also_deleted)
    above_ids = [
        element.get("objectId") for element in above if element.get("objectId") not in removed
    ]
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

        # The frame is measured, not trusted. `hero.HERO_OBJECT_IDS` names the
        # photo well for the six Carmen-maintained designs, because each PPTX
        # import leaves a second unfilled shape overlapping the photo band and
        # the geometric search correctly refuses to choose between them. The
        # named shape is re-measured every time and an absent or implausible one
        # falls back to that search, so a redesign degrades to "ask".
        frame = find_hero_frame(page, slide_w, slide_h, template_label)
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
        # A design whose sample photograph is split across more than one shape
        # keeps showing the rest of it when only the well is replaced. Worked
        # out before the layer order, because a shape this batch deletes must
        # not then be named in the reorder.
        also_delete = extra_deletions(page, template_label, target_id)
        if also_delete:
            logger.info(
                "removing %d extra sample photo layer(s) from %s", len(also_delete), template_label
            )
        z_order = _restore_replacement_z_order(page, target_id, hero_id, also_delete)
        if z_order is None:
            logger.error("hero photo placement could not preserve the measured layer order")
            return False
        requests: list[dict[str, Any]] = [
            {"deleteObject": {"objectId": target_id}},
            *({"deleteObject": {"objectId": extra}} for extra in also_delete),
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
    agent_values: dict[str, str] | None = None,
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
        agent_values: Exact current contact values used to recognise an already
            filled agent card beside an existing Slides image.
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
        # No template label here, so this stays on the geometric search. The
        # frame is used only to exclude the hero from headshot candidates, and
        # on all six designs the hero is a 99-100% wide band while a headshot
        # well must be 10-60% wide, so the exclusion is redundant for them.
        # Client Review Post is the one design whose hero is narrow enough to
        # matter, and its search already resolves without a hint.
        hero = find_hero_frame(page, slide_w, slide_h)
        frames = headshot_frames(
            page,
            slide_w,
            slide_h,
            hero.object_id if hero else "",
            agent_values,
        )
        if not frames:
            logger.info("no headshot frame recognised; leaving the design alone")
            return None
        if len(frames) != 1:
            logger.error("headshot placement found %d plausible portrait slots", len(frames))
            return False
        frame = frames[0]
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
