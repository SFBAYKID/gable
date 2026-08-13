"""Batch summaries count only work that is actually ready."""

from __future__ import annotations

from gable.pipeline.poller import BatchOutcome
from gable.slackapp.batches import summarize
from gable.voice import violations


def test_one_listing_has_no_redundant_batch_message() -> None:
    assert summarize((BatchOutcome("123 Main St", "delivered"),)) == ""


def test_ready_count_excludes_held_failed_and_skipped_work() -> None:
    message = summarize(
        (
            BatchOutcome("123 Main St", "delivered"),
            BatchOutcome("456 Oak Ave", "needs_photo"),
            BatchOutcome("789 Pine Rd", "failed"),
            BatchOutcome("321 Elm St", "skipped"),
        )
    )

    assert message.startswith("1 post ready")
    assert "Included  123 Main St" in message
    assert "Held back  1 listing" in message
    assert "Failed  1 listing" in message
    assert "Skipped  1 non-flyer request" in message
    assert not violations(message)


def test_zero_deliveries_never_claim_a_post_is_ready() -> None:
    message = summarize(
        (
            BatchOutcome("456 Oak Ave", "needs_template"),
            BatchOutcome("789 Pine Rd", "failed"),
        )
    )

    assert message.startswith("0 posts ready")
    assert "Included" not in message
    assert not violations(message)
