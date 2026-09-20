"""Replace every measured property well in one batch, preserving its geometry.

Used only when a person supplied more than one property photograph. A single
photograph keeps the long-standing hero path in `pipeline/placement.py`, so the
flyers Carmen already reviews are unchanged by this module's existence.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from gable.pipeline.placement import _snapped_to_page
from gable.slides.designs import extra_deletions
from gable.slides.hero import _element_bounds, find_hero_frame
from gable.slides.property_frames import secondary_frames

logger = logging.getLogger("gable.property_photos")


def _readback_tolerance(width: float, height: float, slide_px: tuple[int, int]) -> float:
    """How far a placed picture may sit from its well before it is a defect.

    `createImage` does not stretch a picture into the box it is given: it fits
    it inside, preserving the source aspect ratio, rewrites `size` to the
    picture's natural size, and **centres** what is left over. A photograph is
    cropped to the well's pixel size before it is sent, but a pixel count is an
    integer and a well's aspect ratio is not, so a sub-pixel letterbox is
    unavoidable and Slides splits it across both edges.

    Measured live 2026-09-20 across all nine wells of the three row designs:
    the largest deviation was 0.443pt of width and 0.221pt of position, on Open
    House's right-hand well. Every one is under a single rendered pixel, which
    is what the cause predicts. The same measurement against a picture that had
    NOT been cropped to its well was 8.16pt out — eighteen times the tolerance —
    so this still fails a photograph that arrives unfitted.

    Args:
        width: Page width in EMU.
        height: Page height in EMU.
        slide_px: The rendering size the fitted pixels are measured against.

    Returns:
        One rendered pixel in EMU, on whichever axis is coarser.

    Raises:
        Nothing.
    """
    return max(width / max(1, slide_px[0]), height / max(1, slide_px[1]))


def measured_wells(
    presentation: dict[str, Any], label: str
) -> tuple[tuple[Any, ...], float, float, dict[str, Any]]:
    """Measure a design's property wells, main first then the row left to right.

    Args:
        presentation: A `presentations.get` response for a one-slide file.
        label: The design's Drive file name.

    Returns:
        The frames, the page width and height in EMU, and the page itself.

    Raises:
        ValueError: If the file is not one slide, the main well cannot be
            measured, or a known row design's smaller wells are no longer an
            unambiguous pair. Refusing beats guessing at somebody's flyer.
        KeyError: If the response omits the page size, which would mean the
            Slides contract changed under us.
    """
    if len(presentation.get("slides", [])) != 1:
        raise ValueError("property placement requires one slide")
    page = presentation["slides"][0]
    width = presentation["pageSize"]["width"]["magnitude"]
    height = presentation["pageSize"]["height"]["magnitude"]
    hero = find_hero_frame(page, width, height, label)
    if hero is None:
        raise ValueError("the main photo space could not be measured")
    frames = (
        _snapped_to_page(hero, width, height),
        *secondary_frames(page, width, height, hero, label),
    )
    return frames, width, height, page


def place_property_photos(
    slides: Any,  # noqa: ANN401 - Google discovery resource
    file_id: str,
    urls: Sequence[str],
    label: str,
    refit: Callable[[str, int, int], str],
    slide_px: tuple[int, int],
) -> tuple[int, int]:
    """Fit each supplied picture and replace the measured wells in one batch.

    The main picture comes first, then the smaller pictures left to right. A
    measured well with no picture for it is emptied rather than left showing
    the design's sample house, because a stranger's living room beside two real
    ones reads as finished and is wrong.

    Args:
        slides: A Slides v1 resource.
        file_id: The copied presentation to edit.
        urls: Public photo URLs in placement order, main first.
        label: The design's Drive file name.
        refit: Crops one URL to an exact pixel size and returns a new URL.
        slide_px: The rendering size the fitted pixels are measured against.

    Returns:
        How many pictures were placed, and how many measured wells were left
        empty. Both only after complete replies and a geometry readback.

    Raises:
        ValueError: On an unmeasurable layout, a failed fit, an incomplete
            reply, a missing image, geometry that moved, or a sample picture
            that survived. The caller cannot claim a placement that did not
            happen.
        Exception: Whatever the Slides client raises on a transport failure.
    """
    # https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/batchUpdate
    presentation = slides.presentations().get(presentationId=file_id).execute()
    frames, width, height, page = measured_wells(presentation, label)
    if not urls or len(urls) > len(frames):
        raise ValueError("the supplied pictures do not fit this design's photo count")

    replaced: dict[str, str] = {}
    expected: dict[str, tuple[float, float, float, float]] = {}
    requests: list[dict[str, Any]] = []
    for index, frame in enumerate(frames):
        requests.append({"deleteObject": {"objectId": frame.object_id}})
        if index >= len(urls):
            continue
        fitted = refit(
            urls[index],
            max(1, round(frame.width / width * slide_px[0])),
            max(1, round(frame.height / height * slide_px[1])),
        )
        if not fitted:
            raise ValueError("a supplied picture could not be fitted")
        object_id = (
            f"gableHero_{uuid.uuid4().hex}" if index == 0 else f"gableProperty_{uuid.uuid4().hex}"
        )
        replaced[frame.object_id] = object_id
        expected[object_id] = (frame.x, frame.y, frame.width, frame.height)
        requests.append(
            {
                "createImage": {
                    "objectId": object_id,
                    "url": fitted,
                    "elementProperties": {
                        "pageObjectId": page["objectId"],
                        # The frame's own bounds, never the slide's.
                        "size": {
                            "width": {"magnitude": frame.width, "unit": "EMU"},
                            "height": {"magnitude": frame.height, "unit": "EMU"},
                        },
                        # ABSOLUTE with position restated: RELATIVE multiplies
                        # translation as well as scale (CLAUDE.md 4.3 item 5).
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": frame.x,
                            "translateY": frame.y,
                            "unit": "EMU",
                        },
                    },
                }
            }
        )
    extras = extra_deletions(page, label, frames[0].object_id)
    requests.extend({"deleteObject": {"objectId": item}} for item in extras)
    removed = {frame.object_id for frame in frames} | set(extras)

    # `pageElements` is ordered back to front, so bringing each surviving
    # element to the front in that order reproduces the original depth exactly
    # — including the three new images, which Slides appends at the top.
    #
    # One element per request, and that is load-bearing. The Slides v1
    # discovery document says of a multi-element operation: "the relative
    # Z-orders within these page elements before the operation is maintained"
    # -- BEFORE the operation, not the order they are listed in. Slides appends
    # all three new images at the front, so one BRING_TO_FRONT naming every
    # element in the order wanted would have left all three images on top of
    # the title band and reported success. The one-well hero path can use a
    # single request because there the two orders are the same. Verified
    # 2026-09-20 against
    # https://slides.googleapis.com/$discovery/rest?version=v1
    #
    # Requests never name an object this batch deleted: doing that once failed
    # a whole update and shipped a flyer with no photograph on it.
    for element in page["pageElements"]:
        old = str(element["objectId"])
        if old in removed and old not in replaced:
            continue
        requests.append(
            {
                "updatePageElementsZOrder": {
                    "pageElementObjectIds": [replaced.get(old, old)],
                    "operation": "BRING_TO_FRONT",
                }
            }
        )

    # batchUpdate is atomic: no partial photo swap if any request is invalid.
    answer = (
        slides.presentations()
        .batchUpdate(presentationId=file_id, body={"requests": requests})
        .execute()
    )
    if len(answer.get("replies", [])) != len(requests):
        raise ValueError("Slides did not confirm every property picture change")

    readback = slides.presentations().get(presentationId=file_id).execute()
    actual = {str(e["objectId"]): e for e in readback["slides"][0]["pageElements"]}
    tolerance = _readback_tolerance(width, height, slide_px)
    for object_id, bounds in expected.items():
        element = actual.get(object_id, {})
        if "image" not in element:
            raise ValueError("a placed property picture was missing on readback")
        drift = [abs(a - b) for a, b in zip(_element_bounds(element), bounds, strict=True)]
        if any(value > tolerance for value in drift):
            logger.error(
                "a property picture is %s EMU from its well (tolerance %s)",
                [round(value) for value in drift],
                round(tolerance),
            )
            raise ValueError("a property picture changed size or position on readback")
    if removed.intersection(actual):
        raise ValueError("a sample property picture survived replacement")
    logger.info("placed %d property photos on %s (%s)", len(urls), file_id, label)
    return len(urls), len(frames) - len(urls)
