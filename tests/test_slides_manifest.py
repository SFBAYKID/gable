"""Tests for template field validation and address canonicalization."""

from __future__ import annotations

from gable.slides.manifest import ADDRESS_SHAPE, normalise_address


def test_a_form_address_missing_only_the_city_comma_is_canonicalized() -> None:
    address = normalise_address("7631 OLD COLUMBIA ROAD LAUREL, MD 20723")

    assert address == "7631 OLD COLUMBIA ROAD, LAUREL, MD 20723"
    assert ADDRESS_SHAPE.fullmatch(address)


def test_an_existing_canonical_address_is_unchanged() -> None:
    address = "7631 Old Columbia Road, Laurel, MD 20723"

    assert normalise_address(address) == address


def test_normalization_never_invents_a_missing_zip() -> None:
    address = normalise_address("7631 Old Columbia Road Laurel, MD")

    assert address == "7631 Old Columbia Road Laurel, MD"
    assert ADDRESS_SHAPE.fullmatch(address) is None


def test_an_address_without_a_city_stays_invalid_instead_of_being_guessed() -> None:
    address = normalise_address("7631 Old Columbia Road, MD 20723")

    assert address == "7631 Old Columbia Road, MD 20723"
    assert ADDRESS_SHAPE.fullmatch(address) is None
