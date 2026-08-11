"""Checking a rendered flyer against what was supposed to be on it.

The vision pass reads layout. It is good at noticing that something looks wrong
on a particular flyer and unreliable at deciding whether a value is *correct* —
a plausible-looking wrong number is not a layout problem, and a model asked the
same question twice does not always answer the same way.

So these checks are deterministic and they are all pure functions of the text on
the finished slide. Every one exists because something reached a delivered flyer:

* a price reading `$460,0000` against a submission that supplied `$685,000`
* `410.952.6193` and `sabbotthomes@gmail.com`, which belong to a real Corner
  House agent who was not on that listing
* `@reallygreatsite`, filler from whoever built the designs

Two of those three came from sample content in Carmen's master design rather
than from anything Gable did wrong, and Gable reproduced them faithfully. That
is the point: faithful reproduction of a template's own mistake still reaches a
client, so the flyer is checked rather than the process trusted.

Does not handle: whether the flyer *looks* right. That stays with the vision
pass, and with Carmen.
"""

from __future__ import annotations

import re
from typing import Final

#: A phone number in any shape these designs use: 443.499.3839, (443) 555-0142,
#: 410-305-9006.
PHONE_ON_FLYER: Final[re.Pattern[str]] = re.compile(r"\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]\d{4}")

#: An email address, which on a listing flyer is always somebody's real one.
EMAIL_ON_FLYER: Final[re.Pattern[str]] = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+")

#: Sample content that is neither a phone number nor an email, so the contact
#: sweep cannot see it. Each has been observed on a rendered flyer.
SAMPLE_TOKENS: Final[tuple[str, ...]] = (
    "@reallygreatsite",
    "Your Office Name",
    "yourwebsite.com",
)

#: A price carrying four or more digits after the comma. The master design
#: contains "$525 , 0000", so this is a template defect that reproduces onto
#: flyers rather than a fill error — but it must not reach a client either way.
MALFORMED_PRICE: Final[re.Pattern[str]] = re.compile(r"\$\s?\d{1,3}\s?,\s?\d{4,}")


def _digits(value: str) -> str:
    """Just the digits, so 443.499.3839 and (443) 499-3839 compare equal."""
    return "".join(character for character in value if character.isdigit())


def values_missing_from(text: str, values: dict[str, str], sent: set[str]) -> list[str]:
    """Which supplied values do not appear verbatim on the flyer.

    Args:
        text: All text read back from the rendered slide.
        values: What the run intended to put on the flyer.
        sent: The values actually sent to the design. A value the template has
            no slot for is not expected to appear.

    Returns:
        Field names whose value is missing or corrupted, worst first. Empty when
        every value reads back exactly.

    Raises:
        Nothing.
    """
    missing: list[str] = []
    for name, value in values.items():
        candidate = value.strip()
        if not candidate or candidate not in sent:
            continue
        # `headshot` is an image URL, never text on the flyer.
        if name == "headshot":
            continue
        if candidate not in text:
            missing.append(name.replace("_", " "))
    return missing


def foreign_content_in(text: str, values: dict[str, str], office_phone: str) -> list[str]:
    """Contact details and sample content this run did not put on the flyer.

    Args:
        text: All text read back from the rendered slide.
        values: What the run supplied.
        office_phone: The brokerage's own line, which belongs on any flyer
            whether or not this run supplied it.

    Returns:
        A description of each stray value, worst first. Empty when everything on
        the flyer came from this submission or from the brokerage.

    Raises:
        Nothing.
    """
    supplied_phones = {_digits(value) for value in values.values() if _digits(value)}
    supplied_phones.add(_digits(office_phone))
    supplied_emails = {value.strip().lower() for value in values.values() if "@" in value}

    stray: list[str] = []
    for found in PHONE_ON_FLYER.findall(text):
        if _digits(found) and _digits(found) not in supplied_phones:
            stray.append(f"phone number {found} is not this listing's")
    for found in EMAIL_ON_FLYER.findall(text):
        if found.strip().lower() not in supplied_emails:
            stray.append(f"email address {found} is not this listing's")

    lowered = text.lower()
    for token in SAMPLE_TOKENS:
        if token.lower() in lowered:
            stray.append(f"{token} is sample text from the design, not this listing's")

    broken = MALFORMED_PRICE.search(text)
    if broken:
        stray.append(f"price {broken.group(0).strip()} is not a real number")
    return stray
