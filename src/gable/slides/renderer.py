"""Builds the Slides `batchUpdate` request body that turns a template into a flyer.

Every function here is **pure**: domain objects in, JSON-serializable request
dicts out. No network, no credentials, no Google client. That is deliberate —
it means the entire fill behaviour is unit-tested before a service account
exists, and `slides/client.py` is left with nothing but I/O.

The template contract:

* Text placeholders are `{{name}}` tokens sitting in text boxes over the
  background — `{{price}}`, `{{address}}`, `{{agent_name}}`, and so on.
* The hero photo is a **shape containing the literal text** `{{hero_photo}}`.
  `replaceAllShapesWithImage` swaps that shape for the image and scales it into
  the shape's bounds preserving aspect ratio, which is why the shape's size and
  position define the photo's frame.

Verified against Google's API reference (2026-08-10):
https://developers.google.com/workspace/slides/api/guides/merge
https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/request

* `imageUrl` must be publicly accessible, max **2 kB** of URL, max **50 MB**,
  max **25 megapixels**, and PNG / JPEG / GIF only.
* The image is fetched once at insertion and stored inside the presentation, so
  a flyer does not break later if the source URL expires.

Assumes: the template's placeholder names match `PlaceholderMap`. A placeholder
present in the template but absent from the data is left as-is rather than
blanked — see `unfilled_placeholders`.

Does not handle: creating the copy, sending the batch, or exporting. That is
`slides/client.py`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from gable.models import AgentProfile, Listing

#: A Slides API request. The shape is a large open union defined by Google, so
#: a precise TypedDict would be dozens of members that Google can extend at any
#: time. `Any` is the honest annotation.
Request = dict[str, Any]

#: Slides caps the image URL at 2 kB. Enforced before the request is sent so a
#: long signed URL fails with a sentence instead of a Google error code.
MAX_IMAGE_URL_BYTES: Final[int] = 2048

#: Formats `replaceAllShapesWithImage` accepts.
ALLOWED_IMAGE_SUFFIXES: Final[frozenset[str]] = frozenset({".png", ".jpg", ".jpeg", ".gif"})

#: The shape text that marks where the hero photo goes.
HERO_PHOTO_TAG: Final[str] = "{{hero_photo}}"

#: Matches any `{{token}}` so a template can be inspected for what it expects.
PLACEHOLDER_PATTERN: Final[re.Pattern[str]] = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")


class TemplateError(Exception):
    """Raised when a fill request cannot be built safely."""


@dataclass(frozen=True, slots=True)
class PlaceholderMap:
    """Maps a placeholder name to the value that replaces it.

    Kept as data rather than hardcoded so a template can rename `{{price}}` to
    `{{list_price}}` without a code change — the same reasoning as
    `listings.normalize.ColumnMap`.
    """

    values: dict[str, str]

    @classmethod
    def from_listing(cls, listing: Listing, agent: AgentProfile | None = None) -> PlaceholderMap:
        """Build the standard placeholder set from a listing and its agent.

        **Only non-empty values are included.** A placeholder the data cannot
        fill is deliberately left out, so that:

        1. no `replaceAllText` is emitted for it and the literal `{{price}}`
           stays visible on the rendered slide, and
        2. `unfilled_placeholders` reports it, because it reports names present
           in the template but absent from this map.

        Both halves matter, and an earlier version of this method broke both by
        including empty strings. The consequence was specific and bad: a New
        Listing has no price column on the live form, so `price_display` is
        `""`, `replaceAllText` blanked the token, and `unfilled_placeholders`
        returned nothing because `price` *was* in the map. The §4.7b vision pass
        looks for a visible token, found none, and the flyer shipped with an
        empty price box looking finished. That is ARCHITECTURE.md §4.7 and
        AGENTS.md §4 prohibition 8 violated by construction.

        An ugly visible token is the point. It is how a missing field survives
        long enough for a human to be asked about it.
        """
        candidates = {
            "address": listing.address,
            "price": listing.price_display,
            "agent_name": listing.agent_name,
            "agent_email": listing.agent_email,
            "agent_phone": listing.agent_phone,
            "description": listing.description,
            "beds": listing.beds,
            "baths": listing.baths,
            "sqft": listing.sqft,
        }
        if agent is not None:
            # The Salespeople tab is the authority on display name and
            # brokerage, since the form's free-text name field is whatever was
            # typed.
            candidates["agent_name"] = agent.agent_name or listing.agent_name
            candidates["brokerage_url"] = agent.brokerage_url
            candidates["template_label"] = agent.template_label
        return cls(values={k: v for k, v in candidates.items() if v})

    def token(self, name: str) -> str:
        """The literal placeholder token for a name."""
        return f"{{{{{name}}}}}"


def find_placeholders(text: str) -> set[str]:
    """Return every `{{token}}` name appearing in some text.

    Used to inspect a template and report what it expects, so a mismatch
    surfaces as a clear message rather than a flyer with `{{price}}` printed
    on it.
    """
    return {match.group(1) for match in PLACEHOLDER_PATTERN.finditer(text)}


def unfilled_placeholders(template_text: str, placeholders: PlaceholderMap) -> set[str]:
    """Placeholders the template uses that the data cannot fill.

    Args:
        template_text: All text extracted from the template.
        placeholders: The values available.

    Returns:
        Names present in the template but absent from the map. `hero_photo` is
        excluded — it is filled by an image request, not a text one.
    """
    expected = find_placeholders(template_text)
    return {name for name in expected - set(placeholders.values) if name != "hero_photo"}


def validate_image_url(url: str) -> None:
    """Check a photo URL against Slides' documented constraints.

    Only what is checkable without fetching: scheme, length, and extension.
    Byte size and megapixels are enforced by Google at insertion time.

    Raises:
        TemplateError: if the URL is not https, exceeds `MAX_IMAGE_URL_BYTES`,
            or does not end in a format Slides accepts.
    """
    if not url:
        raise TemplateError("hero photo URL is empty")
    if not url.startswith("https://"):
        # Slides requires a publicly accessible URL; http:// would also leak the
        # request in transit for no benefit.
        raise TemplateError(f"hero photo URL must be https://, got {url[:60]!r}")
    if len(url.encode("utf-8")) > MAX_IMAGE_URL_BYTES:
        raise TemplateError(
            f"hero photo URL is {len(url.encode('utf-8'))} bytes, "
            f"over the Slides limit of {MAX_IMAGE_URL_BYTES}"
        )
    lowered = url.split("?", 1)[0].lower()
    if not any(lowered.endswith(suffix) for suffix in ALLOWED_IMAGE_SUFFIXES):
        raise TemplateError(
            f"hero photo must be PNG, JPEG, or GIF for Slides; got {lowered[-8:]!r}"
        )


def build_text_requests(
    placeholders: PlaceholderMap, page_object_ids: Sequence[str] | None = None
) -> list[Request]:
    """One `replaceAllText` request per placeholder.

    `matchCase: True` because the tokens are lowercase by convention and a
    case-insensitive match could collide with body copy.

    Args:
        placeholders: The values available. Only names present here are
            replaced; a missing name deliberately leaves its token visible.
        page_object_ids: Restrict replacement to these slides. Omitting it means
            the request applies to **every slide in the presentation**, which is
            only safe on a single-slide template. See `build_fill_requests`.

    Returns:
        One request per placeholder, ordered by name so output is deterministic.

    Raises:
        Nothing.
    """
    scope: dict[str, object] = {"pageObjectIds": list(page_object_ids)} if page_object_ids else {}
    return [
        {
            "replaceAllText": {
                "containsText": {"text": placeholders.token(name), "matchCase": True},
                "replaceText": value,
                **scope,
            }
        }
        for name, value in sorted(placeholders.values.items())
    ]


def build_image_request(
    image_url: str,
    tag: str = HERO_PHOTO_TAG,
    *,
    was_reprocessed: bool = True,
    page_object_ids: Sequence[str] | None = None,
) -> Request:
    """One `replaceAllShapesWithImage` request for the hero photo.

    The replace method follows whether the photo was fitted to the frame first:

    - **Reprocessed** (§4.5b already cropped it to the frame's aspect ratio) →
      `CENTER_INSIDE`, which scales to fit without stretching.
    - **Raw fallback** (the image model was unavailable — ARCHITECTURE.md §6
      says never block on an AI call) → `CENTER_CROP`, which fills the frame.

    That distinction is not cosmetic. `CENTER_INSIDE` on an unfitted 4:3 phone
    photo in a 4:5 frame leaves visible bands of template background above and
    below it, and every API call still returns 200. Cropping loses some of the
    photo; letterboxing looks broken. A raw fallback must also be flagged to
    Carmen — this function cannot do that, so the caller must.

    Args:
        image_url: A public HTTPS URL to the photo.
        tag: The shape text marking the photo's frame.
        was_reprocessed: Whether the photo was already fitted to the frame.
        page_object_ids: Restrict replacement to these slides. See
            `build_text_requests`.

    Returns:
        A single Slides request.

    Raises:
        TemplateError: if the URL fails `validate_image_url`.
    """
    validate_image_url(image_url)
    scope: dict[str, object] = {"pageObjectIds": list(page_object_ids)} if page_object_ids else {}
    return {
        "replaceAllShapesWithImage": {
            "imageUrl": image_url,
            "replaceMethod": "CENTER_INSIDE" if was_reprocessed else "CENTER_CROP",
            "containsText": {"text": tag, "matchCase": True},
            **scope,
        }
    }


def build_fill_requests(
    listing: Listing,
    agent: AgentProfile | None = None,
    hero_photo_url: str | None = None,
    hero_photo_tag: str = HERO_PHOTO_TAG,
    *,
    was_reprocessed: bool = True,
    page_object_ids: Sequence[str] | None = None,
) -> list[Request]:
    """Build the complete `batchUpdate` request list for one flyer.

    Text first, then the image. The ordering is **not** defence — it is only
    safe because image tags are excluded from `PlaceholderMap`. If a tag name
    were ever added to the map, the text pass would rewrite the tag into a URL
    string and destroy the shape before the image pass could match it. That
    invariant is asserted below rather than left to a comment.

    Args:
        listing: The normalized submission.
        agent: The matched agent, if any.
        hero_photo_url: Public HTTPS URL to the photo. `None` leaves the tag
            shape in place, which is what `needs_photo` looks like on a flyer.
        hero_photo_tag: Shape text marking the photo frame.
        was_reprocessed: Whether the photo was fitted to the frame first. See
            `build_image_request`.
        page_object_ids: Restrict every replacement to these slides. Omit only
            when the target is a single-slide template.

    Returns:
        Requests ready to send as a single `batchUpdate`.

    Raises:
        TemplateError: if `hero_photo_url` is present but unusable, or if a
            placeholder name collides with an image tag.
    """
    placeholders = PlaceholderMap.from_listing(listing, agent)
    _reject_image_tag_collision(placeholders, hero_photo_tag)
    requests = build_text_requests(placeholders, page_object_ids)
    if hero_photo_url:
        requests.append(
            build_image_request(
                hero_photo_url,
                hero_photo_tag,
                was_reprocessed=was_reprocessed,
                page_object_ids=page_object_ids,
            )
        )
    return requests


def _reject_image_tag_collision(placeholders: PlaceholderMap, *tags: str) -> None:
    """Fail loudly if a text placeholder would clobber an image tag.

    Args:
        placeholders: The map about to drive `replaceAllText`.
        tags: Image tags such as `{{hero_photo}}` that must survive the text
            pass so `replaceAllShapesWithImage` can still match them.

    Returns:
        None.

    Raises:
        TemplateError: if any tag's placeholder name appears in the map.
    """
    for tag in tags:
        name = tag.strip("{} ").strip()
        if name in placeholders.values:
            raise TemplateError(
                f"placeholder {name!r} collides with the image tag {tag!r}: the text pass "
                "would replace the tag before the image request could match it"
            )


def flyer_filename(listing: Listing) -> str:
    """A human-scannable name for the generated flyer in Drive.

    Address first, because that is what Carmen scans a folder for. Characters
    Drive tolerates but humans find confusing in a filename are stripped.
    """
    address = listing.address or "unknown address"
    agent = listing.agent_name or "unknown agent"
    raw = f"{address} — {agent}"
    cleaned = re.sub(r"[/\\\n\r\t]", " ", raw)
    return re.sub(r"\s+", " ", cleaned).strip()[:200]
