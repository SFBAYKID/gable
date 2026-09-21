"""The official site's silence: a retry, an honest pause, and the brokerage credential.

Brittney Bushee's Under Contract recheck on 2026-09-01 lost a listing to one
twenty-second timeout on a site that answered in half a second when tried
again, and the pause it produced sent Carmen to correct a request and a roster
row that were both right.
"""

from __future__ import annotations

import json
import urllib.parse

from gable.agents.contacts import Contact
from gable.agents.profile_lookup import lookup_official_profile
from gable.agents.profile_page import OFFICIAL_PAGES_API
from gable.agents.website import (
    BROKERAGE_SOURCE,
    WORKBOOK_SOURCE,
    ProfileLookup,
    validate_contact,
)


def test_a_silent_site_prints_the_brokerage_credential_when_the_row_is_complete() -> None:
    """Brittney Bushee's recheck, 2026-09-01: one timeout, one stuck listing.

    Every contact detail was already proven from the roster; the site was
    wanted only for a credential Chase settled as brokerage-wide. Its silence
    is not evidence about her, so the flyer goes out with the default and says
    so.
    """
    from gable.agents.website import unavailable_lookup

    checked = validate_contact(
        "Brittney Bushee",
        "brittney@cornerhouserealty.com",
        Contact("brittney@cornerhouserealty.com", "Brittney", "Bushee", "443.562.8226"),
        lambda _name, _email: unavailable_lookup(),
        require_title=True,
        default_title="Realtor",
    )

    assert checked.ready is True
    assert checked.title == "Realtor"
    assert checked.title_source == BROKERAGE_SOURCE
    assert checked.phone_source == WORKBOOK_SOURCE
    assert "did not answer" in checked.note
    assert "Realtor" in checked.note


def _no_such_profile(_name: str, _email: str) -> ProfileLookup:
    """The site answered, and it has no page matching this agent."""
    return ProfileLookup(
        problem="the official Corner House Realty website has no exact profile for this agent"
    )


def test_a_silent_site_names_a_true_remedy_when_it_must_still_stop() -> None:
    """A pause the site's silence still forces names a remedy that can work.

    With no filed phone the site was needed for a contact detail. "Correct the
    request or Agents Contact Information" sent Carmen to fix two things that
    were right; the remedy is to try again.
    """
    from gable.agents.website import unavailable_lookup

    checked = validate_contact(
        "Brittney Bushee",
        "brittney@cornerhouserealty.com",
        Contact("brittney@cornerhouserealty.com", "Brittney", "Bushee", ""),
        lambda _name, _email: unavailable_lookup(),
        require_title=True,
        default_title="Realtor",
    )

    assert checked.ready is False
    assert "did not answer" in checked.problem
    assert "run again" in checked.problem
    assert "Correct the request or Agents Contact Information" not in checked.problem


def test_a_site_with_no_page_for_a_filed_agent_still_builds_the_credential() -> None:
    """Melanie Kim, 2026-09-21. She changed her name; the site had not.

    Carmen corrected the request and Agents Contact Information, which is
    exactly what the old pause told her to do, and then got the same pause
    again -- the brokerage site still listed the agent under her previous name,
    so the page search matched nothing. The filed row proves name, email and
    direct phone, so the only thing the profile was wanted for is a credential
    every agent on that roster holds.
    """
    checked = validate_contact(
        "Melanie Kim",
        "melanie@cornerhouserealty.com",
        Contact("melanie@cornerhouserealty.com", "Melanie", "Kim", "443.562.8226"),
        _no_such_profile,
        require_title=True,
        default_title="Realtor",
    )

    assert checked.ready is True
    assert checked.title == "Realtor"
    assert checked.title_source == BROKERAGE_SOURCE
    assert checked.name_source == WORKBOOK_SOURCE
    assert checked.phone_source == WORKBOOK_SOURCE
    # The note says which of the two cases this was, because one of them is a
    # durable gap somebody may want to close and the other is a transient
    # nothing. "did not answer" belongs to the silent site, not to this one.
    assert "no profile I could match" in checked.note
    assert "did not answer" not in checked.note
    assert "Realtor" in checked.note


def test_a_site_with_no_page_still_stops_an_agent_the_roster_does_not_carry() -> None:
    """The relief is roster membership, never the name on the request."""
    checked = validate_contact(
        "Melanie Kim",
        "melanie@cornerhouserealty.com",
        None,
        _no_such_profile,
        require_title=True,
        default_title="Realtor",
    )

    assert checked.ready is False
    assert "no exact profile" in checked.problem


def test_a_site_with_no_page_still_stops_a_credential_without_a_default() -> None:
    """Emptying the variable restores the old refusal, as documented."""
    checked = validate_contact(
        "Melanie Kim",
        "melanie@cornerhouserealty.com",
        Contact("melanie@cornerhouserealty.com", "Melanie", "Kim", "443.562.8226"),
        _no_such_profile,
        require_title=True,
        default_title="",
    )

    assert checked.ready is False
    assert "no exact profile" in checked.problem


def test_a_site_with_no_page_still_stops_a_row_missing_a_direct_line() -> None:
    """Then the profile was wanted for a contact detail, not a credential."""
    checked = validate_contact(
        "Melanie Kim",
        "melanie@cornerhouserealty.com",
        Contact("melanie@cornerhouserealty.com", "Melanie", "Kim", ""),
        _no_such_profile,
        require_title=True,
        default_title="Realtor",
    )

    assert checked.ready is False
    assert "no exact profile" in checked.problem


def test_without_a_default_a_silent_site_still_stops_a_credential() -> None:
    from gable.agents.website import unavailable_lookup

    checked = validate_contact(
        "Brittney Bushee",
        "brittney@cornerhouserealty.com",
        Contact("brittney@cornerhouserealty.com", "Brittney", "Bushee", "443.562.8226"),
        lambda _name, _email: unavailable_lookup(),
        require_title=True,
        default_title="",
    )

    assert checked.ready is False
    assert "did not answer" in checked.problem


def _mike_site() -> dict[str, tuple[bytes, str]]:
    """The two official-site responses that resolve Mike Kulnich's profile."""
    api_url = f"{OFFICIAL_PAGES_API}?" + urllib.parse.urlencode(
        {"search": "Mike Kulnich", "per_page": "20", "_fields": "link,title"}
    )
    profile_url = "https://cornerhouserealty.com/mike-kulnich/"
    return {
        api_url: (
            json.dumps([{"link": profile_url, "title": {"rendered": "Mike Kulnich"}}]).encode(),
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
            """,
            profile_url,
        ),
    }


def test_the_lookup_tries_once_more_after_a_timeout() -> None:
    """One bounded retry, with a pause, and the cause logged.

    The site that timed out at 20:02 on 2026-09-01 answered in half a second
    when asked again.
    """
    responses = _mike_site()
    calls: list[str] = []
    paused: list[float] = []

    def flaky(url: str) -> tuple[bytes, str]:
        calls.append(url)
        if len(calls) == 1:
            raise TimeoutError("timed out")
        return responses[url]

    found = lookup_official_profile(
        "Mike Kulnich",
        "mike@cornerhouserealty.com",
        fetch=flaky,
        sleep=paused.append,
    )

    assert found.profile is not None
    assert found.profile.phone == "410.456.3564"
    assert len(paused) == 1
    assert 0 < paused[0] < 5


def test_the_lookup_gives_up_after_the_second_transient_failure() -> None:
    calls: list[str] = []

    def down(url: str) -> tuple[bytes, str]:
        calls.append(url)
        raise OSError("connection refused")

    found = lookup_official_profile(
        "Mike Kulnich", "mike@cornerhouserealty.com", fetch=down, sleep=lambda _s: None
    )

    assert found.profile is None
    assert found.unavailable is True
    assert len(calls) == 2


def test_the_lookup_does_not_retry_an_answer_that_will_not_change() -> None:
    """An off-domain redirect or unparseable page is the same the second time."""
    calls: list[str] = []

    def wrong_shape(url: str) -> tuple[bytes, str]:
        calls.append(url)
        return b"not json", url

    found = lookup_official_profile(
        "Mike Kulnich", "mike@cornerhouserealty.com", fetch=wrong_shape, sleep=lambda _s: None
    )

    assert found.profile is None
    assert found.unavailable is True
    assert len(calls) == 1
