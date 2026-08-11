"""Tests for row normalization.

Every function under test is pure, so these are exhaustive on edge cases rather
than representative. The bias is toward inputs a real-estate agent would
plausibly type into a form at 11pm.
"""

from __future__ import annotations

import pytest

from gable.listings.normalize import (
    DEFAULT_COLUMN_MAP,
    ColumnMap,
    build_agent_name,
    clean_text,
    describe_unmatched_headers,
    fold_header,
    format_price,
    normalize_email,
    normalize_phone,
    parse_price,
    row_to_listing,
    title_case_address,
    truncate_on_word_boundary,
)

# --- header folding and diagnostics -----------------------------------------


def test_fold_header_absorbs_cosmetic_differences() -> None:
    assert fold_header("  Agent   Email ") == fold_header("agent email")


def test_unmatched_headers_are_reported_not_guessed() -> None:
    """The diagnostic that answers "what are the real column names?"."""
    real_row = {"Timestamp": "", "Email Address": "", "Street address": ""}
    missing = describe_unmatched_headers(real_row)
    assert "Agent email" in missing
    assert "Property address" in missing
    assert "Timestamp" not in missing


def test_unmatched_headers_empty_when_map_is_correct() -> None:
    headers = DEFAULT_COLUMN_MAP.all_headers()
    assert describe_unmatched_headers(headers) == ()


def test_unmatched_headers_accepts_a_tuple_or_a_mapping() -> None:
    headers = DEFAULT_COLUMN_MAP.all_headers()
    assert describe_unmatched_headers(dict.fromkeys(headers, "")) == ()


def test_empty_header_means_the_column_does_not_exist() -> None:
    """The form may not collect a photo at all (ARCHITECTURE.md 5)."""
    column_map = ColumnMap(photo="")
    assert "" not in column_map.all_headers()
    assert describe_unmatched_headers(DEFAULT_COLUMN_MAP.all_headers(), column_map) == ()


# --- text -------------------------------------------------------------------


def test_clean_text_collapses_spaces_but_keeps_paragraphs() -> None:
    assert clean_text("a   b\n\nc") == "a b\n\nc"


def test_clean_text_collapses_excessive_blank_lines() -> None:
    assert clean_text("a\n\n\n\n\nb") == "a\n\nb"


def test_clean_text_normalizes_windows_line_endings() -> None:
    assert clean_text("a\r\nb") == "a\nb"


# --- email ------------------------------------------------------------------


def test_email_is_lowercased() -> None:
    assert normalize_email("  Jane@Brokerage.COM ") == ("jane@brokerage.com", None)


def test_missing_email_is_a_problem() -> None:
    email, problem = normalize_email("")
    assert email == ""
    assert problem is not None


def test_malformed_email_is_flagged_but_preserved() -> None:
    """Gable flags; it never "corrects" a contact detail (AGENTS.md 4.7)."""
    email, problem = normalize_email("jane at brokerage dot com")
    assert problem is not None
    assert email == "jane at brokerage dot com"


# --- phone ------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["8182597432", "(818) 259-7432", "818-259-7432", "818.259.7432", " 818 259 7432 "],
)
def test_ten_digit_us_numbers_get_the_display_format(raw: str) -> None:
    """Chase specified `(818) 259-7432`. It is what a human reads on a flyer."""
    assert normalize_phone(raw) == ("(818) 259-7432", None)


def test_leading_country_code_is_stripped() -> None:
    assert normalize_phone("1-818-259-7432") == ("(818) 259-7432", None)


def test_e164_input_is_reformatted_for_display() -> None:
    """E.164 is right for dialling and wrong for print."""
    assert normalize_phone("+18182597432") == ("(818) 259-7432", None)


def test_missing_phone_is_not_a_problem() -> None:
    """The field is optional; absence must not flag the listing."""
    assert normalize_phone("") == ("", None)


def test_phone_with_an_extension_is_flagged_and_preserved() -> None:
    phone, problem = normalize_phone("555-123-4567 x89")
    assert problem is not None
    assert phone == "555-123-4567 x89"


def test_vanity_phone_is_flagged() -> None:
    phone, problem = normalize_phone("555-CALL-NOW")
    assert problem is not None
    assert phone == "555-CALL-NOW"


# --- price ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        ("$1,200,000", 1_200_000),
        ("1200000", 1_200_000),
        ("$1,200,000.00", 1_200_000),
        ("  845000 ", 845_000),
        ("$0", 0),
    ],
)
def test_price_parses_common_spellings(raw: str, value: int) -> None:
    parsed, display, problem = parse_price(raw)
    assert parsed == value
    assert problem is None
    assert display == format_price(value)


def test_display_is_regenerated_for_consistency_across_a_batch() -> None:
    assert parse_price("1200000")[1] == "$1,200,000"


def test_unparseable_price_is_preserved_not_invented() -> None:
    """A fabricated figure on a listing flyer is worse than the agent's words."""
    value, display, problem = parse_price("Price upon request")
    assert value is None
    assert display == "Price upon request"
    assert problem is not None


def test_missing_price_is_a_problem() -> None:
    assert parse_price("")[2] is not None


def test_negative_price_is_rejected() -> None:
    value, _display, problem = parse_price("-500")
    assert value is None
    assert problem is not None


# --- address ----------------------------------------------------------------


def test_shouty_address_is_title_cased() -> None:
    assert title_case_address("123 MAIN STREET") == "123 Main Street"


def test_lowercase_address_is_title_cased() -> None:
    assert title_case_address("123 main street") == "123 Main Street"


def test_mixed_case_address_is_left_exactly_as_typed() -> None:
    """`str.title()` would turn McDonald into Mcdonald on a printed flyer."""
    assert title_case_address("123 McDonald Ave") == "123 McDonald Ave"


def test_ordinals_keep_their_lowercase_suffix() -> None:
    assert title_case_address("123 ne 4th st") == "123 NE 4th St"


def test_directionals_stay_uppercase() -> None:
    assert title_case_address("456 sw harbor blvd") == "456 SW Harbor Blvd"


def test_empty_address_stays_empty() -> None:
    assert title_case_address("   ") == ""


# --- truncation -------------------------------------------------------------


def test_short_text_is_untouched() -> None:
    assert truncate_on_word_boundary("short", 400) == ("short", False)


def test_truncation_never_exceeds_the_limit() -> None:
    """The limit exists because of the template's text box."""
    text = "word " * 200
    result, truncated = truncate_on_word_boundary(text, 50)
    assert truncated is True
    assert len(result) <= 50


def test_truncation_cuts_on_a_word_boundary() -> None:
    result, _ = truncate_on_word_boundary("alpha beta gamma delta", 14)
    assert result.rstrip("…").strip() in {"alpha beta", "alpha"}
    assert "gam" not in result


def test_truncation_marks_the_cut() -> None:
    result, _ = truncate_on_word_boundary("alpha beta gamma delta", 14)
    assert result.endswith("…")


def test_single_long_word_is_hard_cut_rather_than_overflowing() -> None:
    result, truncated = truncate_on_word_boundary("a" * 100, 20)
    assert truncated is True
    assert len(result) <= 20


def test_zero_limit_is_handled() -> None:
    assert truncate_on_word_boundary("anything", 0) == ("", True)
    assert truncate_on_word_boundary("", 0) == ("", False)


def test_trailing_punctuation_is_stripped_before_the_ellipsis() -> None:
    result, _ = truncate_on_word_boundary("alpha beta, gamma delta epsilon", 16)
    assert ",…" not in result


# --- name -------------------------------------------------------------------


def test_agent_name_joins_both_parts() -> None:
    assert build_agent_name("Jane", "Doe") == "Jane Doe"


def test_agent_name_tolerates_a_missing_part() -> None:
    assert build_agent_name("Jane", "") == "Jane"
    assert build_agent_name("", "Doe") == "Doe"
    assert build_agent_name("", "") == ""


# --- full row ---------------------------------------------------------------


def _row(**overrides: str) -> dict[str, str]:
    row = {
        "Timestamp": "2026-08-10 14:30:00",
        "Agent first name": "Jane",
        "Agent last name": "Doe",
        "Agent email": "Jane@Brokerage.com",
        "Agent phone": "(818) 259-7432",
        "Property address": "123 ANYWHERE ST, ANY CITY, ST 12345",
        "Price": "$1,200,000",
        "Description": "A lovely home.",
    }
    row.update(overrides)
    return row


def test_clean_row_produces_a_flyer_ready_listing() -> None:
    listing = row_to_listing(_row())
    assert listing.problems == ()
    assert listing.is_flyer_ready is True
    assert listing.agent_email == "jane@brokerage.com"
    assert listing.agent_phone == "(818) 259-7432"
    assert listing.price_value == 1_200_000
    assert listing.agent_name == "Jane Doe"


def test_row_matching_is_case_and_whitespace_insensitive() -> None:
    row = {f"  {key.upper()} ": value for key, value in _row().items()}
    assert row_to_listing(row).is_flyer_ready is True


def test_malformed_row_never_raises() -> None:
    """One bad row must not stop a batch (ARCHITECTURE.md 4.2)."""
    listing = row_to_listing({})
    assert listing.has_problems is True
    assert listing.is_flyer_ready is False


def test_every_problem_is_collected_not_just_the_first() -> None:
    listing = row_to_listing(_row(Price="ask me", **{"Agent phone": "nope"}))
    assert len(listing.problems) >= 2


def test_missing_photo_column_is_not_an_error() -> None:
    """The current form may not collect a photo at all."""
    listing = row_to_listing(_row(), column_map=ColumnMap(photo=""))
    assert listing.form_photo_url == ""
    assert listing.problems == ()


def test_description_truncation_is_flagged_for_carmen() -> None:
    listing = row_to_listing(_row(Description="word " * 200), max_description_chars=100)
    assert listing.description_truncated is True
    assert len(listing.description) <= 100
    assert any("truncated" in problem for problem in listing.problems)


def test_row_id_is_stable_across_cosmetic_edits_to_the_row() -> None:
    """A re-typed address must not rebuild a flyer that already shipped."""
    a = row_to_listing(_row())
    b = row_to_listing(_row(**{"Property address": "123  anywhere st,  any city, st 12345"}))
    assert a.response_row_id == b.response_row_id


def test_row_id_differs_for_a_different_property() -> None:
    a = row_to_listing(_row())
    b = row_to_listing(_row(**{"Property address": "456 Oak Ave"}))
    assert a.response_row_id != b.response_row_id


def test_custom_column_map_is_honored() -> None:
    column_map = ColumnMap(
        timestamp="When",
        agent_email="Email Address",
        address="Street address",
        agent_first_name="First",
        agent_last_name="Last",
        price="Asking",
    )
    listing = row_to_listing(
        {
            "When": "2026-08-10 14:30:00",
            "Email Address": "jane@brokerage.com",
            "Street address": "123 Main St",
            "First": "Jane",
            "Last": "Doe",
            "Asking": "$845,000",
        },
        column_map=column_map,
    )
    assert listing.is_flyer_ready is True
    assert listing.price_value == 845_000
