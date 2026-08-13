"""Build one truthful Slack summary for a multi-listing poll cycle."""

from __future__ import annotations

from gable.pipeline.poller import BatchOutcome
from gable.voice import quote_rail, safe, strip_to_plain

_PAUSED = frozenset({"needs_photo", "needs_info", "needs_template", "needs_review"})


def summarize(outcomes: tuple[BatchOutcome, ...]) -> str:
    """Summarize actual terminal and held states without inflating readiness.

    Args:
        outcomes: Work attempted in one poll pass, after each listing has
            already received its own thread outcome.

    Returns:
        A safe batch message for two or more listings, otherwise empty. Only
        ``delivered`` contributes to the ready count.

    Raises:
        Nothing.
    """
    if len(outcomes) < 2:
        return ""
    ready = [item for item in outcomes if item.status == "delivered"]
    held = [item for item in outcomes if item.status in _PAUSED]
    failed = [item for item in outcomes if item.status == "failed"]
    skipped = [item for item in outcomes if item.status == "skipped"]

    count = len(ready)
    headline = f"{count} post{'s' if count != 1 else ''} ready"
    facts = ["Each ready item is a Google Slides file linked in its own listing thread."]
    if ready:
        facts.append(f"Included  {_addresses(ready)}")
    if held:
        noun = "listing" if len(held) == 1 else "listings"
        facts.append(f"Held back  {len(held)} {noun} waiting for a person")
    if skipped:
        noun = "request" if len(skipped) == 1 else "requests"
        facts.append(f"Skipped  {len(skipped)} non-flyer {noun}")
    if failed:
        noun = "listing" if len(failed) == 1 else "listings"
        facts.append(
            f"Failed  {len(failed)} {noun} during processing; Chase can check "
            "Gable's log before retrying"
        )
    return safe(f"{headline}\n\n{quote_rail(facts)}")


def _addresses(outcomes: list[BatchOutcome]) -> str:
    """Return bounded, reader-safe address labels for the ready set."""
    labels: list[str] = []
    for item in outcomes:
        cleaned = strip_to_plain(" ".join(item.address.split()))[:100].strip()
        labels.append(cleaned or "Unnamed listing")
    return " · ".join(labels)
