"""Every address the form has ever received, replayed through the runner's reader.

The suite's invented addresses never carried a five-digit house number, a
court called "Ct", a trailing slash, or a condo with a unit — the form did, and
each one reached Carmen as a question or a wrong flyer before anyone here saw
it. `tests/fixtures/address_corpus.tsv` holds what Gable makes of each real
address today; `tools/refresh_address_corpus.py` rebuilds it from the live
sheet. A verdict that changes fails here, and the person changing the code
decides in the diff whether it is a fix or a regression.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from tools.refresh_address_corpus import HEADER, verdict_for

from gable.listings.address import tidy

CORPUS: Path = Path(__file__).resolve().parent / "fixtures" / "address_corpus.tsv"


def _rows() -> list[tuple[str, str, str]]:
    """Every corpus row as (raw, tidied, verdict)."""
    with CORPUS.open(encoding="utf-8", newline="") as handle:
        lines = list(csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE))
    assert lines and "\t".join(lines[0]) == HEADER
    return [(raw, tidied, verdict) for raw, tidied, verdict in lines[1:]]


@pytest.mark.parametrize(("raw", "tidied", "verdict"), _rows(), ids=lambda value: value[:40])
def test_a_real_address_reads_the_way_the_corpus_records(
    raw: str, tidied: str, verdict: str
) -> None:
    """The code reproduces the reviewed verdict for every real address."""
    assert verdict_for(raw) == (tidied, verdict), raw


@pytest.mark.parametrize(("raw", "tidied", "verdict"), _rows(), ids=lambda value: value[:40])
def test_tidying_a_real_address_twice_changes_nothing(raw: str, tidied: str, verdict: str) -> None:
    """A stated correction is re-read from the sheet; it must not drift."""
    del raw, verdict
    assert tidy(tidied) == tidied


def test_the_corpus_is_not_small() -> None:
    """A truncated fixture would pass every row and prove nothing."""
    assert len(_rows()) >= 100
