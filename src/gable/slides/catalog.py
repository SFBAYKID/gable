"""The Corner House template catalogue: which slide is which, and what to call it.

Kept as **data**, not as files, for the reason ARCHITECTURE.md §9 already
records: one template per agent per request type is ~45 x N and unmaintainable.
The rep-specific parts of a post — name, phone, headshot — are render-time
values, not separate designs.

So a rep folder here means only "this slide has that rep's name typed into it as
sample content", which is what Chase asked for and is genuinely useful for
finding things. It is not an ownership model, and nothing in the pipeline routes
by it.

Slide numbers are 1-based, matching the source deck. The nine section dividers,
the brand board, the fourteen text-free EDU graphics and the six blank slides
are deliberately absent — they are not templates and nothing can render into
them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: The folder name for designs carrying no rep's name.
GENERIC_FOLDER: Final[str] = "Corner House Realty (generic)"


@dataclass(frozen=True, slots=True)
class TemplateEntry:
    """One renderable design in the source deck."""

    slide: int
    category: str
    label: str
    folder: str = GENERIC_FOLDER
    dual_agent: bool = False

    @property
    def filename(self) -> str:
        """What the Drive file is called, so a human can scan the folder."""
        return f"{self.category} — {self.label}"


#: Every renderable slide, in deck order.
CATALOG: Final[tuple[TemplateEntry, ...]] = (
    # --- Client Review (slides 3-7) ---
    TemplateEntry(3, "Client Review", "Photo Left, Quote Right"),
    TemplateEntry(4, "Client Review", "Real People Real Results"),
    TemplateEntry(5, "Client Review", "Quote with Agent Card"),
    TemplateEntry(6, "Client Review", "As Unique As You Are"),
    TemplateEntry(7, "Client Review", "What Our Clients Say — Call or DM"),
    # --- Just Listed (10-17) ---
    TemplateEntry(10, "Just Listed", "Offers From, Agent Card Right", "Kelsey Mahon"),
    TemplateEntry(11, "Just Listed", "Schedule a Private Tour", "Kelsey Mahon"),
    TemplateEntry(12, "Just Listed", "Book a Tour, DM for Details", "Kelsey Mahon"),
    TemplateEntry(13, "Just Listed", "Compact Stats Bar", "Kelsey Mahon"),
    TemplateEntry(14, "Just Listed", "Boutique Service", "Kelsey Mahon"),
    TemplateEntry(15, "Just Listed", "Bracket Placeholders (cleanest)"),
    TemplateEntry(16, "Just Listed", "Plus Open House — Hosted By", "Kelsey Mahon", True),
    TemplateEntry(17, "Just Listed", "Plus Open House — Offered At", "Kelsey Mahon"),
    # --- Just Sold (19-24) ---
    TemplateEntry(19, "Just Sold", "Thinking of Selling", "Kelli Kulnich"),
    TemplateEntry(20, "Just Sold", "Sold For, Agent Card"),
    TemplateEntry(21, "Just Sold", "Let's Connect"),
    # Slide 22 reads "$3,300 Per Month" — a rental, filed under Just Sold in the
    # source deck. Labelled for what it is rather than where it sits.
    TemplateEntry(22, "Just Rented", "Per Month (mis-filed under Just Sold)"),
    TemplateEntry(23, "Just Sold", "Local Experts, Address Twice"),
    TemplateEntry(24, "Just Sold", "With Beds, Baths and SqFt"),
    # --- Open House (26-31) ---
    TemplateEntry(26, "Open House", "Full Details, Tour With Us", "Louis Smith"),
    TemplateEntry(27, "Open House", "Two Agents, DM Me", "Kim Hixson", True),
    TemplateEntry(28, "Open House", "Two Agents, Two Dates", "Jason Vetter", True),
    TemplateEntry(29, "Open House", "Book a Private Tour"),
    TemplateEntry(30, "Open House", "Join Us This Weekend"),
    TemplateEntry(31, "Open House", "Weekend — Come Tour With Us (Sat + Sun)"),
    # --- Meet the Agent (49-54) ---
    TemplateEntry(49, "Meet the Agent", "Local Expert, Three Value Props"),
    TemplateEntry(50, "Meet the Agent", "Market Area and Years of Experience"),
    TemplateEntry(51, "Meet the Agent", "Fun Fact and Why Clients Love Me"),
    TemplateEntry(52, "Meet the Agent", "Let's Connect, Local Roots"),
    TemplateEntry(54, "Meet the Agent", "Find Your Next Home, Specialty"),
    # --- Neighborhood Spotlight (57-61) ---
    TemplateEntry(57, "Neighborhood", "Coffee, Eats, Weekend, Schools, Commute"),
    TemplateEntry(58, "Neighborhood", "Living In — Vibe, At a Glance, Top Pick"),
    TemplateEntry(59, "Neighborhood", "Local Favorites — Coffee to Community"),
    TemplateEntry(60, "Neighborhood", "Explore — Three Local Favorites"),
    TemplateEntry(61, "Neighborhood", "Local Guide — Dining, Parks, Coffee"),
    # --- Coming Soon (64-69) ---
    TemplateEntry(64, "Coming Soon", "Exclusive Preview, Get on the List"),
    TemplateEntry(65, "Coming Soon", "Beds, Baths, SqFt, Garage"),
    TemplateEntry(66, "Coming Soon", "Fall in Love First"),
    TemplateEntry(67, "Coming Soon", "VIP Exclusive Preview"),
    TemplateEntry(68, "Coming Soon", "Be the First to Know"),
    TemplateEntry(69, "Coming Soon", "Something Great Is On the Way"),
    # --- Under Contract (71-74) ---
    TemplateEntry(71, "Under Contract", "Thinking of Selling"),
    TemplateEntry(72, "Under Contract", "Sold For"),
    TemplateEntry(73, "Under Contract", "After Multiple Offers"),
    TemplateEntry(74, "Under Contract", "Agent Card"),
)

#: Deck slides that are deliberately NOT templates, and why. Kept so the next
#: reader does not go looking for the missing 29.
NOT_TEMPLATES: Final[dict[str, tuple[int, ...]]] = {
    "brand board": (1,),
    "section dividers": (2, 9, 18, 25, 33, 48, 56, 63, 70),
    "EDU graphics, no text to fill": tuple(range(34, 48)),
    "blank": (8, 32, 47, 53, 55, 62),
}


def categories() -> tuple[str, ...]:
    """Every distinct category, in first-appearance order.

    Returns:
        Category names such as `Just Listed`.

    Raises:
        Nothing.
    """
    seen: list[str] = []
    for entry in CATALOG:
        if entry.category not in seen:
            seen.append(entry.category)
    return tuple(seen)


def folders() -> tuple[str, ...]:
    """Every folder the catalogue files into, generic first.

    Returns:
        Folder names.

    Raises:
        Nothing.
    """
    rest = sorted({e.folder for e in CATALOG} - {GENERIC_FOLDER})
    return (GENERIC_FOLDER, *rest)


def for_category(category: str) -> tuple[TemplateEntry, ...]:
    """Every design available for a request type.

    Args:
        category: A name from `categories()`.

    Returns:
        Matching entries in deck order; empty if the category is unknown.

    Raises:
        Nothing.
    """
    return tuple(e for e in CATALOG if e.category == category)
