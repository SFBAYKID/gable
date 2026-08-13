"""Official-domain contact fallback and prerequisite validation."""

from __future__ import annotations

import json
import urllib.parse

from gable.agents.contacts import Contact
from gable.agents.website import (
    OFFICIAL_PAGES_API,
    WEBSITE_SOURCE,
    OfficialProfile,
    ProfileLookup,
    lookup_official_profile,
    validate_contact,
)


def _profile(
    *,
    name: str = "Mike Kulnich",
    email: str = "mike@cornerhouserealty.com",
    phone: str = "410.456.3564",
) -> ProfileLookup:
    return ProfileLookup(
        profile=OfficialProfile(
            name=name,
            email=email,
            phone=phone,
            title="REALTOR®",
            source_url="https://cornerhouserealty.com/mike-kulnich/",
        )
    )


def test_complete_workbook_contact_is_cross_checked_but_keeps_its_own_values() -> None:
    """An agreeing profile confirms the row; the workbook still supplies every value.

    This replaces an earlier contract in which a complete row was returned
    without consulting the website at all. That trusted a filled-in row to be a
    correct one, which is how a wrong direct line survived to production.
    """
    calls: list[tuple[str, str]] = []

    def lookup(name: str, email: str) -> ProfileLookup:
        calls.append((name, email))
        return _profile()

    checked = validate_contact(
        "Mike Kulnich",
        "mike@cornerhouserealty.com",
        Contact("mike@cornerhouserealty.com", "Mike", "Kulnich", "410.456.3564"),
        lookup,
    )

    assert checked.ready is True
    assert checked.phone == "410.456.3564"
    assert checked.phone_source == "contact_workbook"
    assert calls == [("Mike Kulnich", "mike@cornerhouserealty.com")]


def test_complete_workbook_row_with_another_agents_phone_is_refused() -> None:
    """The Sam Johnson defect: a complete row carrying someone else's direct line.

    The row is internally consistent and the submitted name agrees with it, so
    every pre-existing check passes. Only the official profile reveals that the
    phone belongs to a different agent.
    """
    checked = validate_contact(
        "Mike Kulnich",
        "mike@cornerhouserealty.com",
        Contact("mike@cornerhouserealty.com", "Mike", "Kulnich", "443.509.4299"),
        lambda _name, _email: _profile(phone="410.456.3564"),
    )

    assert checked.ready is False
    assert "does not match the contact-workbook phone" in checked.problem
    assert checked.phone == "", "a refused check must not hand back either phone"


def test_cross_check_yields_to_the_workbook_when_the_site_cannot_answer() -> None:
    """A site outage must not stop every listing; the workbook stays authoritative."""
    for outcome in (
        ProfileLookup(problem="the official website has no exact profile for this agent"),
        ProfileLookup(problem="I could not complete the check"),
    ):

        def lookup(_name: str, _email: str, result: ProfileLookup = outcome) -> ProfileLookup:
            return result

        checked = validate_contact(
            "Mike Kulnich",
            "mike@cornerhouserealty.com",
            Contact("mike@cornerhouserealty.com", "Mike", "Kulnich", "410.456.3564"),
            lookup,
        )

        assert checked.ready is True
        assert checked.phone == "410.456.3564"
        assert checked.phone_source == "contact_workbook"


def test_cross_check_yields_to_the_workbook_when_the_lookup_raises() -> None:
    """A raising lookup is an unavailable cross-check, not a failed listing."""

    def lookup(_name: str, _email: str) -> ProfileLookup:
        raise RuntimeError("network down")

    checked = validate_contact(
        "Mike Kulnich",
        "mike@cornerhouserealty.com",
        Contact("mike@cornerhouserealty.com", "Mike", "Kulnich", "410.456.3564"),
        lookup,
    )

    assert checked.ready is True
    assert checked.phone == "410.456.3564"


def test_cross_check_ignores_a_branded_name_variant() -> None:
    """Bobby Carr brands himself; the official profile does not. That is not an error.

    Strict on the direct line, silent on the name. A check that pauses on
    branding teaches its reader to ignore it.
    """
    checked = validate_contact(
        "Bobby Carr The Dog Walking Realtor",
        "robertfcarrjr@gmail.com",
        Contact("robertfcarrjr@gmail.com", "Bobby", "Carr The Dog Walking Realtor", "443.790.4765"),
        lambda _name, _email: _profile(
            name="Bobby Carr",
            email="robertfcarrjr@gmail.com",
            phone="443.790.4765",
        ),
    )

    assert checked.ready is True
    assert checked.name == "Bobby Carr The Dog Walking Realtor"
    assert checked.name_source == "contact_workbook"


def test_cross_check_accepts_a_matching_phone_written_differently() -> None:
    """Punctuation is not a conflict: 443-790-4765 and 443.790.4765 are one number."""
    checked = validate_contact(
        "Bobby Carr",
        "robertfcarrjr@gmail.com",
        Contact("robertfcarrjr@gmail.com", "Bobby", "Carr", "443.790.4765"),
        lambda _name, _email: _profile(
            name="Bobby Carr",
            email="robertfcarrjr@gmail.com",
            phone="(443) 790-4765",
        ),
    )

    assert checked.ready is True
    assert checked.phone == "443.790.4765", "the workbook's own formatting is preserved"


def test_missing_workbook_phone_uses_one_exact_official_profile_for_this_run() -> None:
    workbook = Contact("mike@cornerhouserealty.com", "Mike", "Kulnich", "")

    checked = validate_contact(
        "Mike Kulnich",
        "mike@cornerhouserealty.com",
        workbook,
        lambda _name, _email: _profile(),
    )

    assert checked.ready is True
    assert checked.name_source == "contact_workbook"
    assert checked.email_source == "contact_workbook"
    assert checked.phone_source == WEBSITE_SOURCE
    assert checked.phone == "410.456.3564"
    assert workbook.phone == "", "the human-owned workbook value must remain untouched"


def test_required_title_uses_exact_profile_even_when_contact_row_is_complete() -> None:
    calls: list[tuple[str, str]] = []

    def lookup(name: str, email: str) -> ProfileLookup:
        calls.append((name, email))
        return _profile()

    checked = validate_contact(
        "Mike Kulnich",
        "mike@cornerhouserealty.com",
        Contact("mike@cornerhouserealty.com", "Mike", "Kulnich", "410.456.3564"),
        lookup,
        require_title=True,
    )

    assert checked.ready is True
    assert checked.title == "REALTOR®"
    assert checked.title_source == WEBSITE_SOURCE
    assert calls == [("Mike Kulnich", "mike@cornerhouserealty.com")]


def test_required_title_is_not_inferred_when_exact_profile_has_none() -> None:
    missing_title = ProfileLookup(
        profile=OfficialProfile(
            name="Mike Kulnich",
            email="mike@cornerhouserealty.com",
            phone="410.456.3564",
            title="",
            source_url="https://cornerhouserealty.com/mike-kulnich/",
        )
    )

    checked = validate_contact(
        "Mike Kulnich",
        "mike@cornerhouserealty.com",
        Contact("mike@cornerhouserealty.com", "Mike", "Kulnich", "410.456.3564"),
        lambda _name, _email: missing_title,
        require_title=True,
    )

    assert checked.ready is False
    assert "no title or credential" in checked.problem
    assert checked.title == ""


def test_required_title_lookup_flags_website_and_workbook_phone_conflict() -> None:
    checked = validate_contact(
        "Mike Kulnich",
        "mike@cornerhouserealty.com",
        Contact("mike@cornerhouserealty.com", "Mike", "Kulnich", "410.456.3564"),
        lambda _name, _email: _profile(phone="443.326.7170"),
        require_title=True,
    )

    assert checked.ready is False
    assert "website profile phone does not match" in checked.problem
    assert "unchanged" in checked.problem
    assert checked.title == ""


def test_cross_source_phone_comparison_ignores_print_punctuation() -> None:
    checked = validate_contact(
        "Mike Kulnich",
        "mike@cornerhouserealty.com",
        Contact("mike@cornerhouserealty.com", "Mike", "Kulnich", "+1 410-456-3564"),
        lambda _name, _email: _profile(phone="(410) 456.3564"),
        require_title=True,
    )

    assert checked.ready is True
    assert checked.phone == "+1 410-456-3564"
    assert checked.title == "REALTOR®"


def test_unknown_workbook_agent_requires_website_name_email_and_phone_to_agree() -> None:
    checked = validate_contact(
        "Mike Kulnich",
        "mike@cornerhouserealty.com",
        None,
        lambda _name, _email: _profile(),
    )

    assert checked.ready is True
    assert {checked.name_source, checked.email_source, checked.phone_source} == {WEBSITE_SOURCE}


def test_workbook_name_conflict_is_flagged_without_calling_or_correcting_from_web() -> None:
    calls = 0

    def lookup(_name: str, _email: str) -> ProfileLookup:
        nonlocal calls
        calls += 1
        return _profile()

    checked = validate_contact(
        "Mike Kulnich",
        "mike@cornerhouserealty.com",
        Contact("mike@cornerhouserealty.com", "Kelli", "Kulnich", "443.326.7170"),
        lookup,
    )

    assert checked.ready is False
    assert "does not match" in checked.problem
    assert "unchanged" in checked.problem
    assert calls == 0


def test_official_profile_disagreement_is_flagged_not_substituted() -> None:
    checked = validate_contact(
        "Mike Kulnich",
        "mike@cornerhouserealty.com",
        Contact("mike@cornerhouserealty.com", "Mike", "Kulnich", ""),
        lambda _name, _email: _profile(email="another@cornerhouserealty.com"),
    )

    assert checked.ready is False
    assert "website profile email does not match" in checked.problem
    assert checked.phone == ""


def test_official_lookup_extracts_profile_contact_but_ignores_footer_phones() -> None:
    api_url = f"{OFFICIAL_PAGES_API}?" + urllib.parse.urlencode(
        {
            "search": "Mike Kulnich",
            "per_page": "20",
            "_fields": "link,title",
        }
    )
    profile_url = "https://cornerhouserealty.com/mike-kulnich/"
    responses = {
        api_url: (
            json.dumps(
                [
                    {
                        "link": profile_url,
                        "title": {"rendered": "Mike Kulnich"},
                    },
                    {
                        "link": "https://cornerhouserealty.com/mike-kulnich-open-houses/",
                        "title": {"rendered": "Mike Kulnich open houses"},
                    },
                ]
            ).encode(),
            api_url,
        ),
        profile_url: (
            b"""
            <div class="cbl__widget cbl__widget--job_title">
              <div class="cb-title">REALTOR&#174;</div>
            </div>
            <div class="contact-button__dropdown">
              <div><a href="mailto:mike%40cornerhouserealty.com">email</a></div>
              <div><a href="tel:410.456.3564">phone</a></div>
            </div>
            <footer><a href="tel:443.499.3839">office</a></footer>
            """,
            profile_url,
        ),
    }

    found = lookup_official_profile(
        "Mike Kulnich",
        "mike@cornerhouserealty.com",
        fetch=lambda url: responses[url],
    )

    assert found.profile is not None
    assert found.profile.phone == "410.456.3564"
    assert found.profile.title == "REALTOR®"


def test_official_lookup_fails_closed_on_an_off_domain_profile_link() -> None:
    api_url = f"{OFFICIAL_PAGES_API}?" + urllib.parse.urlencode(
        {
            "search": "Mike Kulnich",
            "per_page": "20",
            "_fields": "link,title",
        }
    )

    found = lookup_official_profile(
        "Mike Kulnich",
        "mike@cornerhouserealty.com",
        fetch=lambda _url: (
            json.dumps(
                [
                    {
                        "link": "https://lookalike.example/mike-kulnich/",
                        "title": {"rendered": "Mike Kulnich"},
                    }
                ]
            ).encode(),
            api_url,
        ),
    )

    assert found.profile is None
    assert "no exact profile" in found.problem


def test_official_lookup_refuses_two_exact_profiles_instead_of_picking_one() -> None:
    api_url = f"{OFFICIAL_PAGES_API}?" + urllib.parse.urlencode(
        {
            "search": "Mike Kulnich",
            "per_page": "20",
            "_fields": "link,title",
        }
    )
    payload = [
        {
            "link": f"https://cornerhouserealty.com/mike-kulnich-{suffix}/",
            "title": {"rendered": "Mike Kulnich"},
        }
        for suffix in ("one", "two")
    ]

    found = lookup_official_profile(
        "Mike Kulnich",
        "mike@cornerhouserealty.com",
        fetch=lambda _url: (json.dumps(payload).encode(), api_url),
    )

    assert found.profile is None
    assert "more than one exact profile" in found.problem
