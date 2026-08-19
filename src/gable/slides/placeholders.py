"""What the designs write into a fillable slot, as literal data.

Split out of `fields.py` when that file reached the 800-line ceiling. This
half is what the six designs actually contain — sample agents, real contact
details left in as placeholder content, and the patterns that recognise a
fillable slot. `fields.py` keeps the logic that reads it.

Every entry here was read from a real design rather than guessed. The sample
contacts in particular are **real Corner House agents' real numbers**, left
in the artwork, and one already reached a delivered flyer.
"""

from __future__ import annotations

import re
from typing import Final

#: Names typed into the designs as examples. They are not tokens and look like
#: real data, which is exactly why they must be recognised: an unreplaced sample
#: name reads as a correct flyer for the wrong agent.
SAMPLE_AGENT_NAMES: Final[tuple[str, ...]] = (
    "Kelsey Mahon",
    "Kelli Kulnich",
    "Louis Smith",
    "Kim Hixson",
    "Lolo Simmons",
    "Jason Vetter",
    "Stacey Abbott",
    "Tracey Edwards",
    "Piet de Dreu",
    "Melissa Hargreaves",
    # Client Review Post's footer, read from the live design 2026-08-14. Without
    # it the agent name never resolved, so this name would have printed on
    # somebody else's testimonial flyer.
    "Sebastion Johnson",
    "Sebastian Johnson",
)

#: Field name -> patterns that mean it, most specific first. Bracketed forms are
#: matched before bare words so "[PRICE]" is not consumed by the "price" rule.
#: Every sample contact detail found in Carmen's 69-page master design, read
#: from a PDF export on 2026-08-11 rather than guessed. These are **real Corner
#: House agents' real numbers and addresses**, left in the designs as sample
#: content, and any of them will print on another agent's flyer if the slot is
#: not filled. One already reached a delivered flyer.
SAMPLE_CONTACTS: Final[tuple[str, ...]] = (
    "443.326.7170",
    "410-564-6618",
    "410.952.6193",
    "443-799-6881",
    "443.605.5081",
    "443-986-0789",
    "410.456.6868",
    "808.225.8640",
    "410-999-9999",
    "kelli@cornerhouserealty.com",
    "louis@cornerhouserealty.com",
    "kirby-jay@cornerhouserealty.com",
    "name@cornerhouserealty.com",
    "sabbotthomes@gmail.com",
    "melissasellsmd@gmail.com",
)

#: Sample agent and client names in the master design, beyond the ones already
#: known. Same risk: a real person's name on somebody else's listing.
SAMPLE_PEOPLE: Final[tuple[str, ...]] = (
    "Regina Smith",
    "Louis Smith",
    "Jason Vetter",
    "Kelli Kulnich",
    "Melissa Hargreaves",
    "Realtor Name",
)

#: A weekday date as a design carries it for a sample open house, e.g.
#: "Sunday, Aug 2, 2026". Anchored whole-string so it cannot match body copy.
_SAMPLE_OPEN_HOUSE_DATE: Final[re.Pattern[str]] = re.compile(
    r"^(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s+[A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?"
    r"(?:,?\s+\d{4})?$",
    re.IGNORECASE,
)

#: A bare time range, e.g. "2-4PM" or "11:30 AM - 1 PM". The en and em dashes
#: are deliberate: a designer typing a range in Slides gets one of them from
#: autocorrect as often as a plain hyphen.
_TIME_RANGE_ONLY: Final[re.Pattern[str]] = re.compile(
    r"^\d{1,2}(?::\d{2})?\s*(?:[AP]\.?M\.?)?\s*(?:-|–|—|to)\s*\d{1,2}(?::\d{2})?\s*[AP]\.?M\.?$",  # noqa: RUF001
    re.IGNORECASE,
)

#: The same time range, found anywhere inside a longer sentence, so a supplied
#: "Sunday, Aug 2, 2026 2-4PM" can be split between the two boxes a design uses.
_TIME_RANGE_INSIDE: Final[re.Pattern[str]] = re.compile(
    r"\d{1,2}(?::\d{2})?\s*(?:[AP]\.?M\.?)?\s*(?:-|–|—|to)\s*\d{1,2}(?::\d{2})?\s*[AP]\.?M\.?",  # noqa: RUF001
    re.IGNORECASE,
)

#: A weekday date and a time range in one box, on separate lines, as
#: New Listing with Open House writes them.
_SAMPLE_OPEN_HOUSE_DATE_AND_TIME: Final[re.Pattern[str]] = re.compile(
    _SAMPLE_OPEN_HOUSE_DATE.pattern.rstrip("$") + r"\s+" + _TIME_RANGE_ONLY.pattern.lstrip("^"),
    re.IGNORECASE,
)

#: A comma left with space either side of it once the time between two dates was
#: removed: "08/08/2026  ,  08/09/2026".
_STRANDED_SEPARATOR: Final[re.Pattern[str]] = re.compile(r"\s+,\s*")

#: A word left dangling at the end of a date once its time has been taken out
#: into the design's own separate box.
_TRAILING_JOINER: Final[re.Pattern[str]] = re.compile(
    r"\s+(?:from|at|on|between|starting|beginning|@)\s*$", re.IGNORECASE
)

PATTERNS: Final[dict[str, tuple[re.Pattern[str], ...]]] = {
    "address": (
        re.compile(r"^\[\s*PROPERTY ADDRESS\s*\]$", re.IGNORECASE),
        re.compile(r"^PROPERTY ADDRESS$", re.IGNORECASE),
        re.compile(r"^Address$"),
        re.compile(r"^\d{1,6}\s+Your\s+Street.*$", re.IGNORECASE),
        re.compile(r"^\[\s*ADDRESS\s*\]$", re.IGNORECASE),
        # "123 ANYWHERE ST., ANY CITY" — a stock placeholder address that is
        # neither a token nor a real address, so neither existing rule caught it
        # and it printed on a finished flyer.
        re.compile(r"^\d+\s+ANYWHERE\s+ST\.?,?\s*ANY\s*CITY.*$", re.IGNORECASE),
        # A LIVE SAMPLE address, which is the third convention in the deck:
        # "5066 Winesap Way, Ellicott City, MD 21043" is not a token at all, it
        # is someone's real listing left in the design. Matched the same way
        # intake.address_looks_usable decides an address is real — a street
        # number plus a state or ZIP — so a headline like "JUST LISTED" cannot
        # be mistaken for one.
        re.compile(
            r"^\d{1,6}\s+\S.*\b(?:"
            + "|".join(
                [
                    "AL",
                    "AK",
                    "AZ",
                    "AR",
                    "CA",
                    "CO",
                    "CT",
                    "DE",
                    "DC",
                    "FL",
                    "GA",
                    "HI",
                    "ID",
                    "IL",
                    "IN",
                    "IA",
                    "KS",
                    "KY",
                    "LA",
                    "ME",
                    "MD",
                    "MA",
                    "MI",
                    "MN",
                    "MS",
                    "MO",
                    "MT",
                    "NE",
                    "NV",
                    "NH",
                    "NJ",
                    "NM",
                    "NY",
                    "NC",
                    "ND",
                    "OH",
                    "OK",
                    "OR",
                    "PA",
                    "RI",
                    "SC",
                    "SD",
                    "TN",
                    "TX",
                    "UT",
                    "VT",
                    "VA",
                    "WA",
                    "WV",
                    "WI",
                    "WY",
                ]
            )
            + r")\b|^\d{1,6}\s+\S.*\b\d{5}\b",
            re.IGNORECASE,
        ),
    ),
    "price": (
        re.compile(r"^\[\s*PRICE\s*\]$", re.IGNORECASE),
        re.compile(r"^\[\s*SALE PRICE\s*\]$", re.IGNORECASE),
        re.compile(r"^\$\s?[\d,]{3,12}$"),
    ),
    "beds": (
        re.compile(r"^\[\s*\d*\s*BEDS?\s*\]$", re.IGNORECASE),
        re.compile(r"^\d+\s*/?\s*Bedrooms?$", re.IGNORECASE),
        # Live sample data, as Open House carries it: "5 BEDS" with no bracket.
        # Square footage already accepted its unbracketed form below; without
        # the same for beds and baths, Open House stopped at needs_template
        # complaining about a fillable-looking field it could not name.
        re.compile(r"^\d+\s*/?\s*BEDS?$", re.IGNORECASE),
    ),
    "baths": (
        re.compile(r"^\[\s*\d*\s*BATHS?\s*\]$", re.IGNORECASE),
        re.compile(r"^\d+\s*/?\s*Bathrooms?$", re.IGNORECASE),
        re.compile(r"^\d+\s*/?\s*BATHS?$", re.IGNORECASE),
    ),
    "square_feet": (
        re.compile(r"^\[\s*SQ\.?\s*FT\s*\]$", re.IGNORECASE),
        re.compile(r"^\[\s*SQFT\s*\]$", re.IGNORECASE),
        re.compile(r"^[\d,]+\s*/?\s*Sq\.?\s*FT$", re.IGNORECASE),
    ),
    "agent_name": (
        re.compile(r"^\[\s*AGENT NAME\s*\]$", re.IGNORECASE),
        re.compile(r"^AGENT NAME$", re.IGNORECASE),
        re.compile(r"^Realtor Name$", re.IGNORECASE),
        # Live sample names left in the designs. Without these a flyer for
        # Chase went out carrying "Kelsey Mahon" — the name was never a token,
        # so nothing replaced it, and the result looked entirely deliberate.
        re.compile(r"^(?:" + "|".join(SAMPLE_AGENT_NAMES) + r")$", re.IGNORECASE),
        re.compile(r"^(?:" + "|".join(re.escape(v) for v in SAMPLE_PEOPLE) + r")$", re.IGNORECASE),
    ),
    "client_name": (
        # The reviewer's name, not the agent's. These designs set it in caps
        # under the quote. Treating it as an agent slot puts the agent's name
        # where the client's belongs.
        re.compile(r"^OLIVIA WILSON$", re.IGNORECASE),
        re.compile(r"^\[\s*CLIENT\s*NAME\s*\]$", re.IGNORECASE),
    ),
    "review_quote": (
        # The sample testimonial, carried by every Client Review design with and
        # without its opening quotation mark.
        # Anchored at the start until 2026-08-14, when the live design was read:
        # its sample opens "Review goes here...Working with Corner House Realty",
        # so the anchor missed it and a stranger's testimonial would have
        # survived onto a real agent's flyer.
        re.compile(r".*Working with Corner House Realty was such a smooth.*", re.DOTALL),
        re.compile(r"^\"?Review goes here\.\.\..*", re.DOTALL),
        re.compile(r"^\[\s*(?:REVIEW|TESTIMONIAL|QUOTE)\s*\]$", re.IGNORECASE),
    ),
    "agent_title": (
        # Designs label the role as well as the name, and the sample text is
        # not a token: "REALTOR / TITLE" survived onto two finished flyers.
        re.compile(r"^\[?\s*REALTOR\s*/\s*TITLE\s*\]?$", re.IGNORECASE),
        re.compile(r"^\[\s*TITLE\s*\]$", re.IGNORECASE),
        # REALTOR is a membership credential, not harmless decorative copy.
        # The live Sold design carries this bare word beside the sample agent.
        # Treating it as brand text would let it survive for an agent whose
        # authoritative contact record supplies no title at all.
        re.compile(r"^REALTOR(?:®)?$", re.IGNORECASE),
    ),
    "listing_note": (
        # Under Contract's call-to-action panel, and the only free text block
        # anywhere in that design's Under Contract band. Chase asked on
        # 2026-08-14 for a submission's own note about the deal — "Under
        # contract on the buyer side. Multiple offer situation." — to appear in
        # that section, and this is where it fits. With no note supplied the
        # value is empty, `replacements` skips it, and the design keeps its own
        # words.
        re.compile(r"^Ready to Buy\?\s+DM me to find your next home\.$", re.IGNORECASE),
        re.compile(r"^\[\s*NOTE\s*\]$", re.IGNORECASE),
    ),
    "social_handle": (
        # A stock handle from the design's own sample content. Pointing a real
        # flyer at somebody else's account is the same class of problem as
        # printing their phone number.
        re.compile(r"^@reallygreatsite$", re.IGNORECASE),
        re.compile(r"^\[\s*(?:SOCIAL|HANDLE|INSTAGRAM)\s*\]$", re.IGNORECASE),
    ),
    "neighborhood": (
        re.compile(r"^\[\s*NEIGHBORHOOD(?:\s*NAME)?\s*\]$", re.IGNORECASE),
        re.compile(r"^NEIGHBORHOOD NAME$", re.IGNORECASE),
        re.compile(r"^\[\s*NEIGHBORHOOD\s*NAME\s*\]$", re.IGNORECASE),
        re.compile(r"^\[\s*CITY\s*/\s*AREA NAME\s*\]$", re.IGNORECASE),
        re.compile(r"^\[\s*AREA NAME\s*\]\s*EDITION$", re.IGNORECASE),
    ),
    "agent_phone": (
        # Several designs hold both numbers in one text box, as
        # "C: 410.456.6868" and "O: 443.499.3839" on two lines. An anchored
        # single-number rule cannot match that, so the sample cell number
        # survived onto finished flyers even though it is in SAMPLE_CONTACTS.
        re.compile(
            r"^C:\s*(?:"
            + "|".join(re.escape(v) for v in SAMPLE_CONTACTS if "@" not in v)
            + r")\s+O:\s*[\d().\-\s]+$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:C:\s*)?(?:"
            + "|".join(re.escape(v) for v in SAMPLE_CONTACTS if "@" not in v)
            + r")$"
        ),
        re.compile(r"^\[\s*PHONE(?:\s*NUMBER)?\s*\]$", re.IGNORECASE),
        re.compile(r"^Phone$", re.IGNORECASE),
        re.compile(r"^\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}$"),
    ),
    "agent_email": (
        re.compile(
            r"^(?:" + "|".join(re.escape(v) for v in SAMPLE_CONTACTS if "@" in v) + r")$",
            re.IGNORECASE,
        ),
        re.compile(r"^\[?\s*EMAIL ADDRESS\s*\]?$", re.IGNORECASE),
        re.compile(r"^\[\s*EMAIL(?:\s*ADDRESS)?\s*\]$", re.IGNORECASE),
        re.compile(r"^Email(?:\s*address)?$", re.IGNORECASE),
        re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$"),
    ),
    "website": (
        re.compile(r"^\[\s*WEBSITE\s*\]$", re.IGNORECASE),
        re.compile(r"^Website$", re.IGNORECASE),
    ),
    "open_house": (
        re.compile(r"^\[\s*DAY\s*(?:&|AND|/)?\s*DATE\s*\]$", re.IGNORECASE),
        re.compile(r"^\[\s*TIME\s*\]$", re.IGNORECASE),
        re.compile(r"^\[\s*SATURDAY DATE\s*\]$", re.IGNORECASE),
        re.compile(r"^\[\s*SUNDAY DATE\s*\]$", re.IGNORECASE),
        # Live sample data, as Open House carries it: a weekday date in one box
        # and a time range in another. Leaving these unrecognised was not
        # neutral — the design ships with a real previous listing's date and
        # time, so an unfilled box puts somebody else's open house on the flyer.
        _SAMPLE_OPEN_HOUSE_DATE,
        _TIME_RANGE_ONLY,
        # New Listing with Open House puts both in ONE box, on two lines:
        # "SUNDAY, MAY 24TH\n1 PM - 3 PM". Neither single-line pattern matches
        # that, so the tag kept a previous listing's open house.
        _SAMPLE_OPEN_HOUSE_DATE_AND_TIME,
    ),
}


#: Text that belongs to the design and must never be replaced. Matching one of
#: these is how "Just", "Listed" and the brand line survive a fill.
BRAND_TEXT: Final[frozenset[str]] = frozenset(
    {
        "just",
        "listed",
        "sold",
        "open house",
        "coming",
        "soon",
        "under",
        "contract",
        "corner house realty",
        "local experts. personal service.",
        "exceptional results.",
        "boutique service.",
        "local expertise.",
        "thinking of selling?",
        "let's connect.",
        "reach out today.",
        "sold for",
        "offers from",
        "offered at",
        "hosted by",
        # The heading above Client Review Post's quote. It labels the section
        # rather than naming a value, and Gable stopped at needs_template
        # calling it a fillable field it could not identify.
        "client testimonial",
        "real people",
        "real results",
        # The design uses a curly apostrophe; matching needs the same one.
        "let’s find your corner.",  # noqa: RUF001
        "let's find your corner.",
    }
)
