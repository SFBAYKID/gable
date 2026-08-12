"""What name casing must and must not do.

Every input is a real roster entry or a real form submission. A name renders on
a flyer directly under the agent's own photograph, so a lowercase surname reads
as the brokerage not knowing its own people.
"""

from __future__ import annotations

from gable.listings.names import tidy_name


def test_a_lowercase_surname_is_capitalised() -> None:
    """Straight off the roster: `Eric jacobs`, `Julie mayer`."""
    assert tidy_name("Eric jacobs") == "Eric Jacobs"
    assert tidy_name("Julie mayer") == "Julie Mayer"


def test_hyphenated_names_capitalise_both_halves() -> None:
    """The roster carries both `Kirby-Jay John` and `Kirby-jay john`."""
    assert tidy_name("Kirby-jay john") == "Kirby-Jay John"
    assert tidy_name("KIRBY-JAY JOHN") == "Kirby-Jay John"


def test_mc_and_mac_keep_their_inner_capital() -> None:
    """`str.title()` gives `Mcdonald`, which reads as a mistake in print."""
    assert tidy_name("ian mcdonald") == "Ian McDonald"
    assert tidy_name("fiona macleod") == "Fiona MacLeod"


def test_apostrophe_names_capitalise_after_the_apostrophe() -> None:
    assert tidy_name("sean o'brien") == "Sean O'Brien"
    assert tidy_name("SEAN O'BRIEN") == "Sean O'Brien"


def test_particles_stay_lower_case_inside_a_name() -> None:
    """`Piet de Dreu` is on the roster; `Piet De Dreu` is wrong."""
    assert tidy_name("piet de dreu") == "Piet de Dreu"
    assert tidy_name("LUDWIG VAN BEETHOVEN") == "Ludwig van Beethoven"


def test_a_generational_suffix_is_not_title_cased() -> None:
    """`str.title()` renders `III` as `Iii`."""
    assert tidy_name("robert carr jr") == "Robert Carr Jr."
    assert tidy_name("robert carr III") == "Robert Carr III"


def test_an_already_correct_name_is_left_alone() -> None:
    """Casing must be idempotent or repeated passes degrade it."""
    for good in ("Kelsey Mahon", "Ian DePinto", "Lolo Simmons", "Piet de Dreu"):
        assert tidy_name(good) == tidy_name(tidy_name(good))


def test_a_nickname_is_never_expanded() -> None:
    """`Bobby Carr` is on the roster and is what he calls himself.

    Promoting it to `Robert` would put a name on a flyer that the agent did not
    choose, which is a different person as far as a client is concerned.
    """
    assert tidy_name("bobby carr") == "Bobby Carr"


def test_nothing_is_reordered_or_dropped() -> None:
    """Only casing changes; every component survives in order."""
    source = "kirby-jay john"
    assert [w.lower() for w in tidy_name(source).split()] == [w.lower() for w in source.split()]


def test_empty_input_comes_back_empty() -> None:
    assert tidy_name("") == ""
    assert tidy_name("   ") == ""


def test_a_deliberate_inner_capital_is_preserved() -> None:
    """`Ian DePinto` is how he spells it, and recasing it is a correction.

    A capital anywhere but the front is authorial. Applying `.capitalize()`
    yields `Depinto`, which is a different spelling of a real person's name
    printed under their own photograph.
    """
    assert tidy_name("Ian DePinto") == "Ian DePinto"
    assert tidy_name("LaTonya DeShawn") == "LaTonya DeShawn"


def test_all_caps_is_still_recased() -> None:
    """Caps lock is not authorial intent, so it is not preserved."""
    assert tidy_name("IAN DEPINTO") == "Ian Depinto"


def test_a_particle_leading_a_surname_field_stays_lower() -> None:
    """First and last names are stored separately, so `de Dreu` arrives alone."""
    assert tidy_name("de Dreu") == "de Dreu"
    assert tidy_name("van der Berg") == "van der Berg"
