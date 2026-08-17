"""Every real hero frame against every plausible photo shape.

This exists because of how the 2026-08-17 crop was found: Chase read a Slack
thread and said "the image is so cropped in the flyer". Nothing in the suite
had ever put a tall photograph into a wide frame and checked what survived, so
`assess` discarded 64% of one upload and no test objected.

The frames below are measured, not invented — the hero frame of all 45 slides in
the source workbook that have one, deduplicated and sorted by aspect. Their
range is the reason this file is necessary: **0.478 to 2.726**. Any single photo
shape is a severe mismatch for one end of that range, so "the photo is roughly
the right shape" is never a safe assumption for this design set.

Two invariants, and they pull in opposite directions on purpose:

1. No frame and photo pair may silently discard more than
   `MAX_TOLERABLE_CROP_LOSS`. That is the guard on the failure that happened.
2. An ordinary landscape photo in a landscape frame must still FILL it. That is
   the guard on over-correcting — if everything were contained, every flyer
   would carry blurred bands and the fix would be a second regression.

Does not handle: the pixel work, or where a crop takes its rows from. Those are
`test_photo_fit` and `test_photo_fit_shape`.
"""

from __future__ import annotations

from typing import Final

from gable.photos.fit import MAX_TOLERABLE_CROP_LOSS, FitAction, assess

#: Measured hero frames in points, deduplicated, ascending by aspect. Captured
#: from the live source workbook on 2026-08-17 through the service account.
REAL_FRAMES_PT: Final[tuple[tuple[float, float], ...]] = (
    (364.7, 762.6),
    (341.5, 604.4),
    (440.3, 772.7),
    (460.6, 764.6),
    (555.7, 798.2),
    (348.4, 490.1),
    (807.3, 1009.2),
    (810.0, 1012.5),
    (713.5, 885.9),
    (594.0, 703.3),
    (538.6, 633.5),
    (951.7, 1020.8),
    (813.0, 821.9),
    (693.3, 700.8),
    (765.7, 769.1),
    (810.0, 810.0),
    (513.8, 513.8),
    (810.8, 773.9),
    (767.0, 731.8),
    (710.1, 677.5),
    (774.4, 665.5),
    (413.1, 345.4),
    (810.0, 629.2),
    (641.7, 481.3),
    (656.1, 413.8),
    (810.0, 506.2),
    (812.6, 476.8),
    (810.0, 462.7),
    (808.8, 378.1),
    (731.9, 337.0),
    (763.6, 280.1),
)

#: Photo shapes a person actually sends. The two that caused the incident are
#: named: a 1320x1918 phone portrait and a 1000x1080 near-square.
REAL_SOURCES_PX: Final[dict[str, tuple[int, int]]] = {
    "phone portrait 9:16": (1080, 1920),
    "the Wycombe upload": (1320, 1918),
    "portrait 3:4": (1320, 1760),
    "the Monastery upload": (1000, 1080),
    "square": (1200, 1200),
    "landscape 4:3": (1600, 1200),
    "landscape 3:2": (1800, 1200),
    "landscape 16:9": (1920, 1080),
    "panorama 2:1": (2400, 1200),
}


def _target_px(frame_pt: tuple[float, float]) -> tuple[int, int]:
    """Convert a measured frame to the pixel target the pipeline renders at.

    Args:
        frame_pt: Frame width and height in points.

    Returns:
        Width and height in pixels at the 2x the placement step uses.

    Raises:
        Nothing.
    """
    return (max(1, int(frame_pt[0] * 2)), max(1, int(frame_pt[1] * 2)))


def test_the_frames_really_do_span_portrait_to_panorama() -> None:
    """If this narrows, the risk this file guards has changed shape."""
    aspects = [w / h for w, h in REAL_FRAMES_PT]
    assert min(aspects) < 0.5, "a very tall frame is in the set"
    assert max(aspects) > 2.7, "a panoramic frame is in the set"


def test_no_real_frame_and_photo_pair_quietly_guts_the_photo() -> None:
    """The invariant the 64% crop violated, across every real combination."""
    offenders: list[str] = []
    for frame_pt in REAL_FRAMES_PT:
        target = _target_px(frame_pt)
        for label, source in REAL_SOURCES_PX.items():
            decision = assess(*source, *target)
            if decision.crop_loss <= MAX_TOLERABLE_CROP_LOSS:
                continue
            if decision.needs_contained_fit:
                continue
            offenders.append(
                f"{label} {source} into {frame_pt} would cut "
                f"{decision.crop_loss:.0%} away as {decision.action}"
            )
    assert not offenders, "photos cropped past the line:\n" + "\n".join(offenders)


def test_an_ordinary_landscape_photo_still_fills_a_landscape_frame() -> None:
    """The other half: containing everything would be its own regression.

    A 3:2 or 4:3 photograph in a frame between 1.2:1 and 2.2:1 is the ordinary
    case, and it must still fill the frame rather than sit inside blurred bands.
    Filling covers cropping, downscaling and enlarging — a 4:3 photo in a 4:3
    frame needs no crop at all, which is better, not worse.
    """
    checked = 0
    for frame_pt in REAL_FRAMES_PT:
        aspect = frame_pt[0] / frame_pt[1]
        if not 1.2 <= aspect <= 2.2:
            continue
        target = _target_px(frame_pt)
        for source in (REAL_SOURCES_PX["landscape 3:2"], REAL_SOURCES_PX["landscape 4:3"]):
            decision = assess(*source, *target)
            assert not decision.needs_contained_fit, (
                f"{source} into {frame_pt} (aspect {aspect:.2f}) stopped filling the frame"
            )
            checked += 1
    assert checked >= 10, "the ordinary case must actually be covered, not skipped"


def test_the_two_uploads_from_the_incident_are_contained_in_their_own_frames() -> None:
    """The exact pair Carmen sent, in the exact frames they were built on."""
    wycombe = assess(*REAL_SOURCES_PX["the Wycombe upload"], *_target_px((809.0, 420.0)))
    assert wycombe.action is FitAction.CONTAIN_WHOLE
    assert wycombe.crop_loss > 0.6

    monastery = assess(*REAL_SOURCES_PX["the Monastery upload"], *_target_px((648.0, 337.0)))
    assert monastery.action is FitAction.CONTAIN_WHOLE
    assert monastery.crop_loss > 0.5


def test_a_photo_is_never_stretched_to_fit_any_real_frame() -> None:
    """No combination may resolve to distorting the property's proportions.

    Stretching is the one outcome that changes what the house looks like, so it
    must be unreachable rather than merely unlikely.
    """
    allowed = {
        FitAction.USE_AS_IS,
        FitAction.LOCAL_ENLARGE,
        FitAction.DOWNSCALE,
        FitAction.CROP,
        FitAction.SMALL_SOURCE,
        FitAction.CONTAIN_WHOLE,
    }
    for frame_pt in REAL_FRAMES_PT:
        target = _target_px(frame_pt)
        for source in REAL_SOURCES_PX.values():
            assert assess(*source, *target).action in allowed
