"""What address tidying must and must not do.

Every input below is a real submission from the form. The tests split evenly
between "make it presentable" and "do not invent anything", and the second half
matters more: this text is set at 44pt across a flyer for a real property, so a
helpful guess is a confident error nobody catches.
"""

from __future__ import annotations

from gable.listings.address import tidy


def test_lowercase_submission_is_capitalised_and_punctuated() -> None:
    """The case that prompted this: row 49, exactly as typed."""
    assert (
        tidy("1225 canberwell rd baltimore md 21228") == "1225 Canberwell Rd, Baltimore, MD 21228"
    )


def test_state_code_is_upper_cased() -> None:
    """`Md` is how the form receives it; `MD` is how it must render."""
    assert tidy("2808 Berwick Ave, Baltimore, Md 21234") == "2808 Berwick Ave, Baltimore, MD 21234"


def test_a_comma_is_added_before_the_state_when_missing() -> None:
    """Row 63 arrived with no comma between the city and the state."""
    assert (
        tidy("300 Commerce St Havre De Grace MD 21078")
        == "300 Commerce St, Havre de Grace, MD 21078"
    )


def test_doubled_commas_are_collapsed() -> None:
    """Row 62 arrived with a comma before the postcode as well."""
    assert (
        tidy("10205 Douglas Ave, Silver Spring, MD, 20902")
        == "10205 Douglas Ave, Silver Spring, MD 20902"
    )


def test_an_already_correct_address_is_left_alone() -> None:
    """Tidying must be idempotent, or a second pass slowly degrades it."""
    good = "703 Perception Way, Aberdeen, MD 21001"
    assert tidy(good) == good
    assert tidy(tidy(good)) == good


def test_compass_directions_stay_upper_case() -> None:
    """`str.title` turns NW into Nw, which reads as a typo on a flyer."""
    assert tidy("32 s prospect ave baltimore md 21228") == "32 S Prospect Ave, Baltimore, MD 21228"
    assert tidy("1400 nw 7th st miami fl 33125") == "1400 NW 7th St, Miami, FL 33125"


def test_ordinals_stay_lower_case() -> None:
    """`107 Fifth Avenue` and `1400 7th St` are both real submissions."""
    assert (
        tidy("107 fifth avenue, halethorpe, md 21227") == "107 Fifth Avenue, Halethorpe, MD 21227"
    )
    assert "7th" in tidy("22 7TH ST BALTIMORE MD 21230")


def test_unit_identifiers_are_preserved_verbatim() -> None:
    """Row 63's `# D` and row 78's `unit 118` are identifiers, not prose."""
    assert tidy("300 Commerce St # D, Havre De Grace MD 21078").endswith("Havre de Grace, MD 21078")
    assert "# D" in tidy("300 Commerce St # D, Havre De Grace MD 21078")
    assert "Unit 118" in tidy("23 pierside ave unit 118 Baltimore Md 21230")


def test_mc_names_keep_their_inner_capital() -> None:
    """`str.title` yields `Mccarthy`, which looks like a mistake."""
    assert "McCarthy" in tidy("18 mccarthy lane towson md 21204")
    assert "MacArthur" in tidy("9 macarthur blvd bethesda md 20816")


def test_a_missing_state_is_not_invented() -> None:
    """Row 35 has no state. Guessing MD would be right here and wrong elsewhere.

    Inferring a state from a city name puts a different address on a flyer for
    a real property, and nothing downstream would catch it.
    """
    result = tidy("4265 bright bay way Ellicott city 21042")
    assert result == "4265 Bright Bay Way, Ellicott City 21042"
    assert "MD" not in result


def test_a_misspelling_is_left_misspelled() -> None:
    """Tidying is presentation only. Correcting the street is a guess."""
    assert tidy("1225 canberwel rd baltimore md 21228").startswith("1225 Canberwel Rd")


def test_nothing_is_reordered_or_dropped() -> None:
    """Every token survives, in order — only casing and punctuation change."""
    source = "23 pierside ave unit 118 Baltimore Md 21230"
    before = [t.strip(",").lower() for t in source.split()]
    after = [t.strip(",").lower() for t in tidy(source).split()]
    assert before == after


def test_empty_input_comes_back_empty() -> None:
    """A blank address is the submission's problem to report, not a crash."""
    assert tidy("") == ""
    assert tidy("   ") == ""


def test_a_unit_stays_with_the_street_and_the_city_still_gets_its_comma() -> None:
    """A condo address stopped a run that had everything else it needed.

    "23 pierside ave unit 118 Baltimore Md 21230" tidied to a single comma,
    which is not "street, city, ST ZIP", so the flyer's address check refused it
    and asked Carmen to retype an address she had already supplied correctly.
    """
    assert tidy("23 pierside ave unit 118 Baltimore Md 21230") == (
        "23 Pierside Ave Unit 118, Baltimore, MD 21230"
    )
    assert tidy("100 main st #d baltimore md 21201") == "100 Main St #d, Baltimore, MD 21201"
    assert tidy("100 main st # d baltimore md 21201") == "100 Main St # d, Baltimore, MD 21201"


def test_a_unit_with_no_city_after_it_is_left_alone() -> None:
    """Nothing follows the unit, so there is no city to separate."""
    assert tidy("100 Main St Unit 5") == "100 Main St Unit 5"


def test_a_trailing_country_does_not_cost_a_round_trip() -> None:
    """Address autocomplete appends it; every shape check ends at the ZIP.

    A real submission arrived as "225 N Wycombe Ave Upper Darby, PA 19082
    United States". The country left the text ending past the ZIP, the shape
    check failed, and Gable asked Carmen for an address the form already held.
    """
    for written in (
        "225 N Wycombe Ave Upper Darby, PA  19082 United States",
        "225 N Wycombe Ave Upper Darby, PA 19082 USA",
        "225 N Wycombe Ave Upper Darby, PA 19082, United States of America",
    ):
        assert tidy(written) == "225 N Wycombe Ave Upper Darby, PA 19082"


def test_a_street_is_not_mistaken_for_a_country() -> None:
    """The pattern is anchored at the end, so these are untouched."""
    assert tidy("100 US Highway 1, Trenton, NJ 08608").endswith("NJ 08608")
    assert tidy("12 Usaquen Way, Baltimore, MD 21201") == "12 Usaquen Way, Baltimore, MD 21201"


def test_a_state_written_out_becomes_the_code_every_design_prints() -> None:
    """Deborah Manarin's Sold, 2026-08-21: `Bowie Maryland 20716`.

    The state was there and every check downstream reads a postal code, so
    Gable told Carmen the address "has no state" while printing the state in
    the same sentence, and did it again after she answered.
    """
    assert tidy("2519 Ann Arbor Lane Bowie Maryland 20716") == (
        "2519 Ann Arbor Lane, Bowie, MD 20716"
    )
    assert tidy("10 Elm St Charleston West Virginia 25301") == "10 Elm St, Charleston, WV 25301"
    assert tidy("1 K St NW, Washington, District of Columbia 20001") == (
        "1 K St NW, Washington, DC 20001"
    )


def test_a_street_named_after_a_state_is_not_read_as_the_state() -> None:
    """Maryland Avenue runs through Baltimore, and Georgia Avenue through DC."""
    assert tidy("3701 Maryland Ave Baltimore MD 21218") == "3701 Maryland Ave, Baltimore, MD 21218"
    assert tidy("8720 georgia ave silver spring md 20910") == (
        "8720 Georgia Ave, Silver Spring, MD 20910"
    )


def test_a_town_named_after_a_state_keeps_its_own_state() -> None:
    """California, MD is a real town in St Mary's County."""
    assert tidy("23120 Three Notch Rd California MD 20619") == (
        "23120 Three Notch Rd, California, MD 20619"
    )


def test_a_state_name_is_never_the_whole_address() -> None:
    """`Maryland 20716` names no property, so nothing is folded into a state."""
    assert tidy("Maryland 20716") == "Maryland 20716"


def test_a_five_digit_house_number_is_not_a_zip() -> None:
    """Lina Mariner's condo, 2026-09-01: one property, asked about three times.

    "10600" is the house number. Counted as a ZIP it made the address look like
    two properties, and earlier in the same thread it made "10600 partridge
    lane b3" look as though it carried a ZIP and lacked only a state.
    """
    from gable.listings.address import incomplete_address, zip_codes

    assert zip_codes("10600 Partridge Ln Apt B3, Cockeysville, MD 21030") == ["21030"]
    assert zip_codes("10600 partridge lane b3") == []
    assert zip_codes("10600-10602 Partridge Ln, Cockeysville, MD 21030") == ["21030"]
    assert zip_codes("1234A Main St, Baltimore, MD 21230-1234") == ["21230-1234"]
    assert "it has no state or ZIP code" in incomplete_address("10600 Partridge Lane, B3")


def test_a_second_property_with_a_five_digit_house_number_still_counts() -> None:
    """Only the leading house number is exempt; a second one is a second property."""
    from gable.listings.address import zip_codes

    two = "10600 Partridge Ln, Cockeysville, MD 21030 10602 Partridge Ln, Cockeysville, MD 21030"

    assert len(zip_codes(two)) >= 2
