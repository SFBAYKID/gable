"""The real seams behind a test build: Slides, Drive, and two sample images.

Kept beside `pipeline.live` rather than inside it so the runner's module stays
under its ceiling. The Slides and Drive closures are `live.slides_seams`, the
same ones every listing is built with; this adds the sample images and the
placement wrappers a run would otherwise bind to its own state.

Does not handle: deciding when to build, or posting the result.
"""

from __future__ import annotations

import io
import logging
import urllib.request
from collections.abc import Callable
from typing import Any, Final

from PIL import Image, ImageDraw

from gable.config import Settings
from gable.photos.fit import fit_bounded_portrait_locally, fit_locally
from gable.photos.store import content_name, publish_local
from gable.pipeline import canary
from gable.pipeline.live import slides_seams
from gable.pipeline.placement import place_headshot, place_hero_photo
from gable.slides.library import TemplateFile

logger = logging.getLogger("gable.canary")

#: Sample image sizes: a landscape property photo and a portrait cut-out.
_HERO_PX: Final[tuple[int, int]] = (1600, 1200)
_FACE_PX: Final[tuple[int, int]] = (600, 800)


def sample_hero_bytes() -> bytes:
    """A plainly synthetic landscape JPEG, so nobody mistakes it for a house."""
    image = Image.new("RGB", _HERO_PX, (196, 210, 224))
    draw = ImageDraw.Draw(image)
    for offset in range(0, _HERO_PX[0], 160):
        draw.rectangle((offset, 0, offset + 80, _HERO_PX[1]), fill=(174, 190, 206))
    draw.rectangle((60, 60, _HERO_PX[0] - 60, _HERO_PX[1] - 60), outline=(90, 110, 130), width=12)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=85)
    return out.getvalue()


def sample_face_bytes() -> bytes:
    """A plainly synthetic portrait PNG with an alpha margin, like a cut-out."""
    image = Image.new("RGBA", _FACE_PX, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((150, 80, 450, 380), fill=(120, 140, 160, 255))
    draw.rounded_rectangle((90, 380, 510, 800), radius=80, fill=(90, 110, 130, 255))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def sample_images(settings: Settings) -> tuple[str, str]:
    """Publish the two sample images and return their public URLs.

    Content-addressed, so this writes each once and returns the same URLs on
    every later call.

    Args:
        settings: For the photo host root and public base.

    Returns:
        The hero URL and the face URL.

    Raises:
        PublishError: when the photo host cannot be written.
    """
    hero = sample_hero_bytes()
    face = sample_face_bytes()
    root, base = settings.photo_public_root, settings.photo_public_base
    return (
        publish_local(root, base, hero, content_name(hero, ".jpg")),
        publish_local(root, base, face, content_name(face, ".png")),
    )


def dry_builder(
    settings: Settings,
    drive: Any,  # noqa: ANN401 - googleapiclient resource
    slides: Any,  # noqa: ANN401 - googleapiclient resource
) -> Callable[[TemplateFile], str]:
    """Bind a test build to the real clients.

    Args:
        settings: Parsed configuration.
        drive: A Drive v3 resource.
        slides: A Slides v1 resource.

    Returns:
        A callable taking one design and returning the paragraph for its
        thread, or "" when the build was clean.

    Raises:
        Nothing here; the returned callable reports its own failures as notes.
    """
    base = slides_seams(settings, drive, slides)
    slide_px = (settings.slide_width_px, settings.slide_height_px)

    def refit_hero(existing: str, width_px: int, height_px: int) -> str:
        with urllib.request.urlopen(existing, timeout=30) as response:
            original = response.read()
        fitted = fit_locally(original, width_px, height_px)
        return publish_local(settings.photo_public_root, settings.photo_public_base, fitted)

    def refit_face(existing: str, width_px: int, height_px: int) -> str:
        with urllib.request.urlopen(existing, timeout=30) as response:
            original = response.read()
        fitted = fit_bounded_portrait_locally(
            original, width_px, height_px, max_source_edge_px=settings.photo_max_edge_px
        )
        return publish_local(
            settings.photo_public_root,
            settings.photo_public_base,
            fitted,
            content_name(fitted, ".png"),
        )

    def trash(file_id: str) -> None:
        moved = (
            drive.files()
            .update(
                fileId=file_id, body={"trashed": True}, supportsAllDrives=True, fields="id,trashed"
            )
            .execute()
        )
        if not bool(moved.get("trashed")):
            raise RuntimeError("Drive did not confirm the test copy was trashed")

    def build(item: TemplateFile) -> str:
        try:
            hero_url, face_url = sample_images(settings)
        except Exception:
            logger.exception("the sample images for a test build could not be published")
            return "I could not publish the sample images for a test flyer, so I did not build one."
        seams = canary.Seams(
            read_slide_text=base.read_slide_text,
            read_presentation=base.read_presentation,
            read_text_boxes=base.read_text_boxes,
            copy_template=base.copy_template,
            fill=base.fill,
            apply=base.apply,
            place_photo=lambda _run, fid, url, label: place_hero_photo(
                slides, fid, url, label, refit=refit_hero, slide_px=slide_px
            ),
            place_headshot=lambda fid, url, values, label: place_headshot(
                slides, fid, url, values, refit=refit_face, slide_px=slide_px, template_label=label
            ),
            trash=trash,
            hero_url=hero_url,
            face_url=face_url,
        )
        result = canary.dry_build(item.name, item.file_id, seams)
        logger.info("test build of %s: %s", item.name, result.detail or "clean")
        return canary.report(item.name, result)

    return build
