"""Render and visually inspect one source template behind the spend guard."""

from __future__ import annotations

import logging
import urllib.request
from collections.abc import Callable
from sqlite3 import Connection
from typing import Any

from gable import spend
from gable.pipeline.vision import Inspection
from gable.pipeline.vision import inspect_template as inspect_template_image

logger = logging.getLogger("gable.template_vision")

TemplateInspector = Callable[[bytes, str, str], Inspection]
ImageDownloader = Callable[[str], bytes]


def _download(url: str) -> bytes:
    """Download one short-lived Google thumbnail URL with a hard timeout."""
    with urllib.request.urlopen(url, timeout=60) as response:
        data: bytes = response.read()
        return data


def inspect_source_template(
    connection: Connection,
    slides: Any,  # noqa: ANN401 - googleapiclient resource, untyped upstream
    file_id: str,
    *,
    api_key: str,
    model: str,
    provider: TemplateInspector = inspect_template_image,
    download: ImageDownloader = _download,
) -> Inspection:
    """Render one source and inspect it behind the shared hard spend ceiling.

    Args:
        connection: SQLite connection holding the spend ledger.
        slides: Google Slides v1 resource.
        file_id: Source presentation id.
        api_key: OpenAI credential. Empty fails closed without a Google call.
        model: Configured visual model.
        provider: Injectable strict visual inspector.
        download: Injectable thumbnail downloader.

    Returns:
        A checked verdict, or an unchecked verdict when rendering, inspection,
        configuration, or the budget cannot prove the template is safe.

    Raises:
        Nothing.
    """
    if not api_key:
        return Inspection(False, False, checked=False)
    try:
        presentation = slides.presentations().get(presentationId=file_id).execute()
        pages = presentation.get("slides", [])
        if len(pages) != 1:
            return Inspection(False, False, checked=False)
        rendered = (
            slides.presentations()
            .pages()
            .getThumbnail(
                presentationId=file_id,
                pageObjectId=pages[0]["objectId"],
                thumbnailProperties_thumbnailSize="LARGE",
            )
            .execute()
        )
        image_bytes = download(str(rendered.get("contentUrl") or ""))
        if not image_bytes:
            return Inspection(False, False, checked=False)
    except Exception:
        logger.exception("a source template could not be rendered for visual inspection")
        return Inspection(False, False, checked=False)

    estimate = spend.Estimate(
        service="openai",
        model=model,
        usd=spend.VISION_RESERVE_USD,
        detail="conservative source-template vision reservation",
    )
    try:
        return spend.guarded_call(
            connection,
            estimate,
            lambda: provider(image_bytes, api_key, model),
        )
    except spend.BudgetExceededError:
        # Silence here reads downstream as "the inspection was inconclusive",
        # which sends whoever is debugging to look at the model, the thumbnail,
        # and the Drive read before the ledger. Say which one it was.
        logger.warning(
            "the shared spend ceiling blocked source-template visual inspection: %s",
            spend.summary(connection),
        )
        return Inspection(False, False, checked=False)
    except Exception:
        logger.exception("the source-template visual inspection did not complete")
        return Inspection(False, False, checked=False)
