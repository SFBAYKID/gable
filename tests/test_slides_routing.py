"""What template routing must guarantee.

The property that matters is that Carmen's edits propagate. That is a
consequence of never copying a template, so these tests pin the override rule
and — just as importantly — pin that ambiguity is refused rather than resolved.
"""

from __future__ import annotations

from gable.slides.routing import Source, Template, choose, normalise

MASTER = [
    Template("m-sold", "Just Sold — Sold For, Agent Card", "Sold"),
    Template("m-open", "Open House — Join Us This Weekend", "Open House"),
    Template("m-new", "Just Listed — Bracket Placeholders", "New Listing"),
]


def test_master_is_used_when_the_agent_has_nothing() -> None:
    """The common case: 32 of 38 agents have an empty folder."""
    picked = choose("Sold", [], MASTER)
    assert picked.ok
    assert picked.source is Source.MASTER
    assert picked.template is not None
    assert picked.template.file_id == "m-sold"


def test_the_agent_s_own_template_wins() -> None:
    """A file in the agent's folder is an override and takes priority."""
    mine = [Template("k-sold", "Kelsey — Just Sold", "Sold")]
    picked = choose("Sold", mine, MASTER)
    assert picked.source is Source.AGENT
    assert picked.template is not None
    assert picked.template.file_id == "k-sold"


def test_an_override_for_one_type_does_not_shadow_the_others() -> None:
    """Kelsey overriding Sold must still get the master's Open House."""
    mine = [Template("k-sold", "Kelsey — Just Sold", "Sold")]
    assert choose("Open House", mine, MASTER).source is Source.MASTER


def test_request_type_matching_ignores_case_and_spacing() -> None:
    """The form has produced both "New Listing" and "just listed".

    A case-sensitive match would file the second as unknown and stop a flyer
    that should have rendered.
    """
    assert choose("new listing", [], MASTER).source is Source.MASTER
    assert choose("  Sold  ", [], MASTER).source is Source.MASTER
    assert normalise("  New   Listing ") == "new listing"


def test_nothing_anywhere_is_refused_with_a_usable_reason() -> None:
    """Four of the form's request types have no design at all."""
    picked = choose("Postcard Order", [], MASTER)
    assert not picked.ok
    assert picked.source is Source.NONE
    assert "Postcard Order" in picked.reason


def test_two_templates_for_one_type_is_refused_not_guessed() -> None:
    """A duplicate filing is a mistake, and picking one would hide it.

    Choosing the first alphabetically would render a real flyer off a coin flip
    and look entirely deliberate afterwards.
    """
    ambiguous = [
        Template("a", "Just Sold — Version A", "Sold"),
        Template("b", "Just Sold — Version B", "Sold"),
    ]
    picked = choose("Sold", ambiguous, MASTER)
    assert not picked.ok
    assert "Version A" in picked.reason and "Version B" in picked.reason


def test_ambiguity_in_the_master_is_also_refused() -> None:
    """The same rule applies to the master folder."""
    twice = [*MASTER, Template("m-sold-2", "Just Sold — Let's Connect", "Sold")]
    picked = choose("Sold", [], twice)
    assert not picked.ok
    assert "master folder" in picked.reason


def test_an_empty_request_type_is_refused() -> None:
    """A blank cell must not silently match a blank-typed template."""
    assert not choose("", [], MASTER).ok
    assert not choose("   ", [], MASTER).ok


def test_nothing_is_copied_so_one_edit_reaches_every_agent() -> None:
    """The whole point: every agent without an override resolves to one file.

    If templates were duplicated per agent, Carmen fixing the Sold design would
    mean opening thirty-eight files, and any she missed would keep rendering the
    old layout with nothing to show it was stale.
    """
    agents: list[list[Template]] = [[], [], [Template("k", "Kelsey — Sold", "Sold")], []]
    chosen = [choose("Sold", a, MASTER) for a in agents]
    from_master = {
        c.template.file_id for c in chosen if c.source is Source.MASTER and c.template is not None
    }
    assert from_master == {"m-sold"}, "every non-override agent must resolve to the one file"
