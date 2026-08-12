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
