"""A value Gable writes must never be caught by a later replacement."""

from __future__ import annotations

from typing import Any

from gable.slides.replacement import SENTINEL_MARK, safe_replacement_requests


def slide(*texts: str) -> dict[str, Any]:
    """A one-page presentation with one text box per string."""
    return {
        "slides": [
            {
                "objectId": "page-1",
                "pageElements": [
                    {
                        "objectId": f"box-{index}",
                        "shape": {"text": {"textElements": [{"textRun": {"content": text}}]}},
                    }
                    for index, text in enumerate(texts)
                ],
            }
        ]
    }


def apply(requests: list[dict[str, Any]], texts: list[str]) -> list[str]:
    """Replay `replaceAllText` requests the way Slides does: in order, everywhere."""
    out = list(texts)
    for request in requests:
        find = request["replaceAllText"]["containsText"]["text"]
        replace = request["replaceAllText"]["replaceText"]
        out = [text.replace(find, replace) for text in out]
    return out


def test_a_name_ending_in_a_field_literal_survives_the_fill() -> None:
    """The Bobby Carr regression: Under Contract's title literal is "Realtor".

    His brokerage name ends in the same word, so filling the name and then the
    title rewrote the last word of his name and the flyer read
    "Bobby Carr The Dog Walking REALTOR". Every literal was standalone in the
    design; the collision only existed once the name was on the slide.
    """
    texts = ["Kelli Kulnich", "Realtor", "443.790.4765"]
    pairs = {
        "Kelli Kulnich": "Bobby Carr The Dog Walking Realtor",
        "Realtor": "REALTOR",
    }

    requests = safe_replacement_requests(slide(*texts), pairs)

    assert apply(requests, texts) == [
        "Bobby Carr The Dog Walking Realtor",
        "REALTOR",
        "443.790.4765",
    ]


def test_the_order_of_the_pairs_cannot_change_the_result() -> None:
    """Filling the title first happened to work; that must not be what saves it."""
    texts = ["Kelli Kulnich", "Realtor"]
    forwards = safe_replacement_requests(
        slide(*texts),
        {"Kelli Kulnich": "Bobby Carr The Dog Walking Realtor", "Realtor": "REALTOR"},
    )
    backwards = safe_replacement_requests(
        slide(*texts),
        {"Realtor": "REALTOR", "Kelli Kulnich": "Bobby Carr The Dog Walking Realtor"},
    )

    assert sorted(apply(forwards, texts)) == sorted(apply(backwards, texts))


def test_two_values_that_each_contain_the_other_literal_still_fill() -> None:
    """A cycle has no safe ordering, so ordering is not what this relies on."""
    texts = ["ALPHA", "BETA"]
    pairs = {"ALPHA": "now BETA", "BETA": "now ALPHA"}

    requests = safe_replacement_requests(slide(*texts), pairs)

    assert apply(requests, texts) == ["now BETA", "now ALPHA"]


def test_a_literal_that_is_not_standalone_is_still_refused() -> None:
    """The original proof is unchanged: a substring match refuses the whole fill."""
    assert safe_replacement_requests(slide("Phone Number"), {"Phone": "410.555.0000"}) == []


def test_a_literal_missing_from_the_design_is_still_refused() -> None:
    """Filling a field the design does not have would silently do nothing."""
    assert safe_replacement_requests(slide("Realtor"), {"[PRICE]": "$1"}) == []


def test_a_value_carrying_the_sentinel_mark_is_refused() -> None:
    """Nothing that could survive as a sentinel is ever written to a flyer."""
    marked = f"REAL{SENTINEL_MARK}TOR"
    assert safe_replacement_requests(slide("Realtor"), {"Realtor": marked}) == []


def test_a_design_carrying_the_sentinel_mark_is_refused() -> None:
    """A design already holding the mark would make the two passes ambiguous."""
    odd = f"odd{SENTINEL_MARK}text"
    assert safe_replacement_requests(slide("Realtor", odd), {"Realtor": "REALTOR"}) == []


def test_no_sentinel_is_a_substring_of_another() -> None:
    """Fixed-width sentinels are what stops the collision reappearing between them."""
    pairs = {f"LIT{index:02d}": f"V{index}" for index in range(12)}
    requests = safe_replacement_requests(slide(*pairs), pairs)
    sentinels = [r["replaceAllText"]["replaceText"] for r in requests[: len(pairs)]]

    assert len(set(sentinels)) == len(pairs)
    assert not any(a != b and a in b for a in sentinels for b in sentinels)


def test_the_sentinel_is_plain_ascii_because_slides_strips_private_use() -> None:
    """Slides dropped U+E000 from the document while reporting success.

    The first pass wrote the marks, the document stored the bare index, and the
    second pass then matched nothing. Any codepoint Slides may normalise away is
    unusable here, so the sentinel is ordinary characters that no design and no
    listing field contains.
    """
    from gable.slides.replacement import _sentinel

    assert SENTINEL_MARK.isascii()
    assert all(_sentinel(index).isascii() for index in range(5))
    assert all(_sentinel(index).isprintable() for index in range(5))
    assert len({len(_sentinel(index)) for index in range(200)}) == 1
