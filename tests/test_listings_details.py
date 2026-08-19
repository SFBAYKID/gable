"""Reading the counts an agent wrote into their own post details."""

from __future__ import annotations

from gable.listings.details import counts_in

# Both 1921 Lincoln Ave requests, exactly as submitted on 2026-08-19.
NUGENT = "Just Listed In Halethrope\n3Bed/2 Bath\n2nd Kitchen\nBackyard Oasis with Pool\n$379,000 "
CAMPBELL = "3Bed/2 Bath\n2nd Kitchen\nBackyard Oasis with Pool\n$379,000 "


def test_the_two_flyers_that_printed_a_sample_bathroom_count() -> None:
    """The right answer was on both submissions the whole time.

    One flyer printed three bathrooms and the other five, each from its
    design's sample content, for the same three-bed two-bath house.
    """
    assert counts_in(NUGENT) == {"beds": "3", "baths": "2"}
    assert counts_in(CAMPBELL) == {"beds": "3", "baths": "2"}


def test_prose_around_the_counts_does_not_become_a_count() -> None:
    """Phrases like 2nd Kitchen and Backyard Oasis sit beside the real numbers."""
    assert counts_in("2nd Kitchen") == {}
    assert counts_in("Backyard Oasis with Pool") == {}
    assert counts_in("Priced at $379,000") == {}
    assert counts_in("") == {}
    assert counts_in("   ") == {}


def test_two_different_counts_are_treated_as_unstated() -> None:
    """A main house and a guest suite is not something to choose between."""
    assert counts_in("3 bed main house plus a 1 bed guest suite") == {}
    # The unambiguous field beside the ambiguous one still reads.
    assert counts_in("3 bed and a 1 bed suite, 2 baths") == {"baths": "2"}


def test_the_ways_agents_actually_write_it() -> None:
    assert counts_in("4BR/2.5BA") == {"beds": "4", "baths": "2.5"}
    assert counts_in("3 bedrooms, 2 bathrooms") == {"beds": "3", "baths": "2"}
    assert counts_in("5 bd 3 ba") == {"beds": "5", "baths": "3"}
    assert counts_in("1,880 sq ft") == {"square_feet": "1,880"}
    assert counts_in("2,430 SQFT") == {"square_feet": "2,430"}
    assert counts_in("1880 sf") == {"square_feet": "1880"}


def test_the_same_count_written_twice_is_still_one_answer() -> None:
    """Agents repeat themselves; that is agreement, not ambiguity."""
    assert counts_in("3 bed / 2 bath. Lovely 3 bedroom home.") == {"beds": "3", "baths": "2"}
