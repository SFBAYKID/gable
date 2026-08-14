"""Building a real `Runner`, wired to Google, Slack and Firecrawl.

`runner.py` takes every outside call as an argument so it can be tested without
a network. This is where those arguments become real ones — the only module that
knows both the settings and the concrete clients, which keeps the credentials in
one place and the sequence in another.
"""

from __future__ import annotations

import logging
import urllib.request
from collections.abc import Callable
from sqlite3 import Connection
from typing import Any

from gable import spend
from gable.agents.website import lookup_official_profile
from gable.config import Settings
from gable.db import store
from gable.listings.enrich import default_research
from gable.photos.fit import (
    assess,
    fit_bounded_portrait_locally,
    fit_locally,
    fit_small_source,
    image_dimensions,
)
from gable.photos.headshots import MAX_HEADSHOT_BYTES
from gable.photos.headshots import url_for_agent as headshot_url_for
from gable.photos.store import content_name, publish_local, verify_public
from gable.photos.verify import verify as verify_image
from gable.pipeline import run_reporting
from gable.pipeline.placement import place_headshot, place_hero_photo, template_clearance
from gable.pipeline.questions import Reconciliation
from gable.pipeline.runner import Runner
from gable.pipeline.vision import Inspection
from gable.pipeline.vision import inspect as inspect_flyer
from gable.slides import fields as template_fields
from gable.slides import preflight
from gable.slides.elements import descendants, text_content
from gable.slides.library import list_files as list_template_files
from gable.slides.replacement import confirmed_replacement_count, safe_replacement_requests
from gable.slides.selection import template_picker
from gable.voice import is_clean

logger = logging.getLogger("gable.live")


def build_runner(
    settings: Settings,
    connection: Connection,
    drive: Any,  # noqa: ANN401 - googleapiclient resource, untyped upstream
    slides: Any,  # noqa: ANN401
    slack_post: Any,  # noqa: ANN401
    *,
    post_once: Callable[[str, str | None, str], str] | None = None,
    reconcile: Callable[[str, str | None, str, str], Reconciliation] | None = None,
    hero_photo_url: str = "",
    origin_thread_ts: str = "",
    progress: Callable[[str], None] = lambda _stage: None,
) -> Runner:
    """Assemble a `Runner` that talks to the real services.

    Args:
        settings: Parsed configuration.
        connection: An open database connection.
        drive: A Drive v3 resource.
        slides: A Slides v1 resource.
        slack_post: A callable taking `(text, thread_ts)` and returning the
            thread timestamp it landed in.
        post_once: Optional Slack outbox seam taking a stable client message id.
        reconcile: Optional Slack history proof after an acknowledgement loss.
        hero_photo_url: A fitted, published photo when resuming a paused run.
        origin_thread_ts: Root Slack thread that a resumed run must preserve.
        progress: Names the current stage for Slack's waiting indicator.

    Returns:
        A ready `Runner`.

    Raises:
        Nothing.
    """
    # Filled when the aspect-preserved human upload is read for its one frame
    # fit. The final visual gate sees both this source and Google's rendered
    # flyer, so even a deterministic crop or contained fit cannot hide a
    # material loss of the supplied property's identity or composition.
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
        # Two requests per field — literal to sentinel, then sentinel to value —
        # so nothing Gable writes can be caught by a later replacement.
        requests = safe_replacement_requests(presentation, pairs)
        if len(requests) != len(pairs) * 2:
            return -1
        reply = (
            slides.presentations()
            .batchUpdate(presentationId=file_id, body={"requests": requests})
            .execute()
        )
        return confirmed_replacement_count(reply, len(pairs))

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

    def look_at(
        run_id: str,
        image_bytes: bytes,
        expected_placeholders: tuple[str, ...] = (),
    ) -> Inspection:
        """Run the paid visual check only while the hard budget permits it."""
        if not settings.openai_image_api_key or not image_bytes:
            return inspect_flyer(
                image_bytes,
                api_key=settings.openai_image_api_key,
                model=settings.vision_model,
                reference_image_bytes=vision_reference,
                expected_placeholders=expected_placeholders,
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
                    expected_placeholders=expected_placeholders,
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
        used_small_source_fit = False
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
            if decision.needs_small_source_fit:
                progress("is fitting the small photo without changing the property...")
                recropped = fit_small_source(original, width_px, height_px)
                used_small_source_fit = True
            else:
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
                run_reporting.SMALL_SOURCE_DETAIL
                if used_small_source_fit
                else "used local photo fitting"
            ),
            ai_enhanced=0,
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
            # The portrait is a transparent cut-out that sits over the address
            # panel. Fitting it through the JPEG property path mattes that alpha
            # onto white and the cut-out becomes an opaque rectangle covering
            # the address box, which is what the 2026-08-13 Louis Smith flyer
            # showed. Keep the alpha and publish PNG.
            fitted = fit_bounded_portrait_locally(
                original,
                width_px,
                height_px,
                max_source_edge_px=settings.photo_max_edge_px,
            )
            url = publish_local(
                settings.photo_public_root,
                settings.photo_public_base,
                fitted,
                content_name(fitted, ".png"),
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
        post_once=post_once,
        reconcile=reconcile,
        research=default_research(settings.firecrawl_api_key, connection),
        official_contact_lookup=lookup_official_profile,
        place_photo=place_photo,
        place_headshot=lambda fid, url, values: place_headshot(
            slides,
            fid,
            url,
            values,
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
    )
