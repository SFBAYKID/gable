"""Block Kit builders for every message shape in AGENTS.md section 2.

Pure functions: domain objects in, a list of blocks out. No network, no client,
no configuration lookups — which is what makes the entire Slack contract
unit-testable without a workspace.

The rule these builders exist to enforce is AGENTS.md section 5: every message
must preserve Carmen's ability to catch the bad one. Concretely that means
provenance is always shown, confidence is always shown when it is below
certainty, and the AI-generated warning is **never** softened, shortened, or
dropped under any policy setting. `AI_WARNING_HEADLINE` and `AI_WARNING_BODY`
are module constants precisely so a test can assert they reach the message
verbatim.

Assumes: Block Kit's documented JSON schema — `section`, `context`, `actions`,
`divider`, `header`, with `mrkdwn` text and an `image` accessory. Every builder
is shaped against the examples in AGENTS.md section 2.

Does not handle: posting. `handlers.py` and `app.py` own the client. Nor does it
decide *which* message to send; that is the orchestrator's call.
"""

from __future__ import annotations

from typing import Any, Final

from gable.models import AgentProfile, Listing, PhotoResult, PhotoSource

#: A Block Kit block is open-ended JSON whose shape varies by block type, so a
#: precise TypedDict would be a dozen union members that Slack can extend at
#: any time. `Any` is the honest annotation here.
Block = dict[str, Any]

#: Action ids. Stable strings — `handlers.py` dispatches on these, so renaming
#: one breaks every button already sitting in Carmen's channel history.
ACTION_APPROVE: Final[str] = "gable_approve"
ACTION_REPLACE_PHOTO: Final[str] = "gable_replace_photo"
ACTION_SKIP: Final[str] = "gable_skip"
ACTION_USE_PHOTO: Final[str] = "gable_use_photo"
ACTION_UPLOAD_PHOTO: Final[str] = "gable_upload_photo"
ACTION_USE_ANYWAY: Final[str] = "gable_use_anyway"
ACTION_PICK_TEMPLATE: Final[str] = "gable_pick_template"

#: AGENTS.md 2.3, verbatim. Never softened, shortened, or dropped — under any
#: value of GABLE_PHOTO_POLICY. If a synthetic image reaches a flyer, the record
#: of that must be impossible to miss.
AI_WARNING_HEADLINE: Final[str] = (
    "⚠️ THIS IMAGE IS AI-GENERATED. It is not a photograph of this property."
)
AI_WARNING_BODY: Final[str] = (
    "Do not use this on a public listing without confirming it is acceptable."
)

#: Slack truncates button text; these are already short enough to survive.
_BUTTON_TEXT_LIMIT: Final[int] = 75

#: How each provenance reads to a human. Order matches CLAUDE.md section 8.
_SOURCE_PHRASES: Final[dict[PhotoSource, str]] = {
    PhotoSource.FORM: "From the form upload",
    PhotoSource.DRIVE: "From the Drive folder",
    PhotoSource.BROKERAGE: "From the agent's brokerage site",
    PhotoSource.WEB: "From a web search",
    PhotoSource.CARMEN: "Supplied by Carmen",
    PhotoSource.GENERATED: "AI-GENERATED — not a real photograph",
}


def _text(body: str) -> Block:
    """A plain section block carrying mrkdwn."""
    return {"type": "section", "text": {"type": "mrkdwn", "text": body}}


def _context(body: str) -> Block:
    """A small-print context block."""
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": body}]}


def _button(text: str, action_id: str, value: str, *, style: str | None = None) -> Block:
    """One action button.

    Args:
        text: Label. Truncated to Slack's limit rather than rejected, because a
            label being long must never be the reason a listing cannot be
            approved.
        action_id: Stable dispatch key.
        value: Payload. Always the `run_id`, so a click stays correct after the
            message has scrolled far up the channel.
        style: `primary` or `danger`. Omitted for neutral buttons.
    """
    element: Block = {
        "type": "button",
        "text": {"type": "plain_text", "text": text[:_BUTTON_TEXT_LIMIT], "emoji": True},
        "action_id": action_id,
        "value": value,
    }
    if style:
        element["style"] = style
    return element


def _actions(*buttons: Block) -> Block:
    """An actions block wrapping the given buttons."""
    return {"type": "actions", "elements": list(buttons)}


def _photo_accessory(photo: PhotoResult | None, alt: str) -> Block | None:
    """An image accessory for a section, or None when there is no photo."""
    if photo is None or not photo.url:
        return None
    return {"type": "image", "image_url": photo.url, "alt_text": alt[:2000] or "listing photo"}


def describe_photo(photo: PhotoResult | None) -> str:
    """One line describing where a photo came from and how sure Gable is.

    Confidence is shown whenever it is below certainty. AGENTS.md section 5: if
    provenance is uncertain, say "I think" and give the number — a bare "photo
    found" invites Carmen to stop checking.
    """
    if photo is None:
        return "None found"
    phrase = _SOURCE_PHRASES.get(photo.source, photo.source.value)
    if photo.source.is_synthetic:
        return f"🤖 {phrase}"
    marker = "✅" if photo.source.is_human_supplied else "🔎"
    if photo.confidence >= 1.0 or photo.source.is_human_supplied:
        detail = phrase
    else:
        detail = f"I think — {phrase} (confidence {photo.confidence:.2f})"
    enhanced = " · AI-enhanced" if photo.ai_enhanced else ""
    return f"{marker} {detail}{enhanced}"


def _agent_line(listing: Listing) -> str:
    """Agent name, email, and phone on one line, skipping what is absent."""
    parts = [p for p in (listing.agent_name, listing.agent_email, listing.agent_phone) if p]
    return " · ".join(parts) if parts else "_unknown_"


def _notes(listing: Listing) -> list[str]:
    """Everything Carmen should glance at before approving."""
    return list(listing.problems)


def listing_ready_blocks(
    listing: Listing,
    agent: AgentProfile | None,
    photo: PhotoResult | None,
    run_id: str,
) -> list[Block]:
    """The "listing ready" card from AGENTS.md 2.1.

    Args:
        listing: The normalized submission.
        agent: The matched agent profile, or None if unmapped.
        photo: The resolved photo, or None.
        run_id: Carried in every button value.

    Returns:
        Block Kit blocks ready to post.
    """
    template = agent.display_template if agent else "(no template mapped)"
    summary: Block = _text(
        f"🏠  *{listing.address or '_address missing_'}*\n"
        f"*Agent*  {_agent_line(listing)}\n"
        f"*Template*  {template}\n"
        f"*Photo*  {describe_photo(photo)}\n"
        f"*Price*  {listing.price_display or '_price missing_'}"
    )
    accessory = _photo_accessory(photo, listing.address)
    if accessory:
        summary["accessory"] = accessory

    blocks: list[Block] = [summary]
    notes = _notes(listing)
    if notes:
        blocks.append(_context("*Notes*  " + " · ".join(notes)))
    blocks.append(
        _actions(
            _button("Approve", ACTION_APPROVE, run_id, style="primary"),
            _button("Replace photo", ACTION_REPLACE_PHOTO, run_id),
            _button("Skip", ACTION_SKIP, run_id),
        )
    )
    return blocks


def needs_photo_blocks(
    listing: Listing,
    searched: tuple[str, ...],
    best_candidate: PhotoResult | None,
    threshold: float,
    run_id: str,
) -> list[Block]:
    """The "photo needs attention" card from AGENTS.md 2.2.

    States plainly that Gable is not confident and has therefore *not* used the
    candidate. ARCHITECTURE.md 4.4: a flyer with no photo gets caught, a flyer
    with the wrong house ships.

    Args:
        listing: The submission.
        searched: Human names of the sources already tried.
        best_candidate: The best below-threshold match, if any.
        threshold: The configured minimum confidence, shown for context.
        run_id: Carried in every button value.

    Returns:
        Block Kit blocks ready to post.
    """
    lines = [
        f"⚠️  *{listing.address or '_address missing_'}*",
        "No photo on the form submission.",
    ]
    if searched:
        lines.append(f"\n*Searched*  {', '.join(searched)}")
    if best_candidate is not None:
        source = _SOURCE_PHRASES.get(best_candidate.source, best_candidate.source.value)
        lines.append(
            f"*Best candidate*  {source} "
            f"(confidence {best_candidate.confidence:.2f} — below the {threshold:.2f} threshold)"
        )
        lines.append("\nI'm not confident this is the right house, so I haven't used it.")
    else:
        lines.append("\nNothing came back that I'd be willing to put on a flyer.")

    summary: Block = _text("\n".join(lines))
    accessory = _photo_accessory(best_candidate, listing.address)
    if accessory:
        summary["accessory"] = accessory

    buttons = []
    if best_candidate is not None:
        buttons.append(_button("Use this photo", ACTION_USE_PHOTO, run_id))
    buttons.append(_button("Upload one", ACTION_UPLOAD_PHOTO, run_id))
    buttons.append(_button("Skip listing", ACTION_SKIP, run_id))
    return [summary, _actions(*buttons)]


def ai_generated_blocks(listing: Listing, photo: PhotoResult, run_id: str) -> list[Block]:
    """The AI-generated warning card from AGENTS.md 2.3.

    The warning is a module constant and is emitted verbatim. It is never
    softened, shortened, or dropped, whatever `GABLE_PHOTO_POLICY` says.

    Args:
        listing: The submission.
        photo: The generated photo. Must be flagged `ai_generated`.
        run_id: Carried in every button value.

    Returns:
        Block Kit blocks ready to post.

    Raises:
        ValueError: if `photo` is not flagged as AI-generated. Reaching this
            builder with an unflagged image means an invariant failed upstream,
            and posting it would erase the only trace.
    """
    if not photo.ai_generated:
        raise ValueError("ai_generated_blocks called with a photo not flagged ai_generated")

    summary: Block = _text(
        f"🤖  *{listing.address or '_address missing_'}*\n"
        f"*{AI_WARNING_HEADLINE}*\n\n"
        "No real photo was found after checking the form, Drive, the brokerage "
        "site, and the web. Under the current policy I generated one.\n\n"
        f"*{AI_WARNING_BODY}*"
    )
    accessory = _photo_accessory(photo, f"AI-generated image for {listing.address}")
    if accessory:
        summary["accessory"] = accessory

    return [
        summary,
        _actions(
            _button("Use anyway", ACTION_USE_ANYWAY, run_id, style="danger"),
            _button("Upload the real photo", ACTION_UPLOAD_PHOTO, run_id),
            _button("Skip listing", ACTION_SKIP, run_id),
        ),
    ]


def unknown_agent_blocks(
    listing: Listing, template_labels: tuple[str, ...], run_id: str
) -> list[Block]:
    """The "unknown agent" card from AGENTS.md 2.4.

    An unmapped agent is never swallowed. The listing pauses and Gable asks.

    Args:
        listing: The submission.
        template_labels: Templates Carmen can pick from.
        run_id: Carried in every button value.

    Returns:
        Block Kit blocks ready to post.
    """
    summary = _text(
        f"❓  *{listing.address or '_address missing_'}* — submitted by "
        f"{listing.agent_email or '_unknown sender_'}\n"
        "I don't have a template mapped for this agent.\n\n"
        "Add a row to the `Agents` tab, or tell me which template to use."
    )
    buttons = [
        # The label is the payload: handlers need to know which template was
        # chosen, and run_id alone cannot carry that.
        _button(label, ACTION_PICK_TEMPLATE, f"{run_id}|{label}")
        for label in template_labels
    ]
    buttons.append(_button("Skip", ACTION_SKIP, run_id))
    return [summary, _actions(*buttons)]


def batch_delivered_blocks(
    ready_addresses: tuple[str, ...],
    filename: str,
    held_back: int = 0,
) -> list[Block]:
    """The batch summary from AGENTS.md 2.5.

    The headline count is `len(ready_addresses)` and nothing else. AGENTS.md 2.5
    is explicit: never report a count that includes held-back listings. "4
    flyers ready" must mean four are actually ready.

    Args:
        ready_addresses: Addresses actually included in the file.
        filename: The attached Bulk Create file.
        held_back: How many listings are waiting on something.

    Returns:
        Block Kit blocks ready to post.
    """
    count = len(ready_addresses)
    noun = "flyer" if count == 1 else "flyers"
    blocks: list[Block] = [
        _text(f"📦  *{count} {noun} ready* — `{filename}`"),
        _text(
            "Open Canva ▸ your flyer template ▸ Apps ▸ Bulk create ▸ Upload data\n"
            "Connect each field once; Canva remembers the mapping next time."
        ),
    ]
    if ready_addresses:
        blocks.append(_context("*Included*  " + " · ".join(ready_addresses)))
    if held_back:
        listing_word = "listing" if held_back == 1 else "listings"
        blocks.append(_context(f"*Held back*  {held_back} {listing_word} still waiting"))
    return blocks


def failure_blocks(address: str, reason: str, run_id: str) -> list[Block]:
    """A per-listing failure notice.

    AGENTS.md 5: name what failed. "Something went wrong" is not a report.

    Args:
        address: The listing's address, for human scanning.
        reason: What actually failed.
        run_id: Carried in the retry button.

    Returns:
        Block Kit blocks ready to post.
    """
    return [
        _text(f"❌  *{address or '_address missing_'}*\n{reason or 'no reason recorded'}"),
        _context(f"`{run_id}` · retry with `/gable retry {run_id}`"),
    ]
