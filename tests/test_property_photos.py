"""Three-photo regression coverage using sanitized measured source geometry."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from gable.db import store
from gable.db.schema import connect
from gable.photos.batch import pending_uploads, stored_photos
from gable.pipeline.property_photos import place_property_photos
from gable.pipeline.run_reporting import photo_note
from gable.slackapp.photo_batch import main_index
from gable.slackapp.photo_selection import select_photos
from gable.slackapp.photos import TOO_MANY_ANSWER_THE_WORDS
from gable.slackapp.shared_photos import shared_file_event
from gable.slides.hero import find_hero_frame
from gable.slides.property_frames import secondary_frames
from tests.photo_support import (
    CHANNEL,
    THREAD,
    FakeSlackClient,
    _event,
    _handoff,
    _paused_database,
)


class Slides:
    """Apply just the real batch operations used by property placement."""

    def __init__(self, presentation: dict[str, Any]) -> None:
        """Keep a mutable copy of the measured template."""
        self.presentation = copy.deepcopy(presentation)
        self.requests: list[dict[str, Any]] = []
        self.answer: dict[str, Any] = {}
        self.incomplete = False
        self.shift = False
        self.drift_emu = 0

    def presentations(self) -> Slides:
        """Return the presentation resource."""
        return self

    def get(self, **_kwargs: Any) -> Slides:  # noqa: ANN401
        """Read the presentation."""
        self.answer = self.presentation
        return self

    def batchUpdate(self, **kwargs: Any) -> Slides:  # noqa: ANN401, N802
        """Apply a placement batch and report its replies."""
        self.requests = kwargs["body"]["requests"]
        elements = self.presentation["slides"][0]["pageElements"]
        for request in self.requests:
            if "deleteObject" in request:
                elements[:] = [
                    e for e in elements if e["objectId"] != request["deleteObject"]["objectId"]
                ]
            elif "createImage" in request:
                image = request["createImage"]
                props = copy.deepcopy(image["elementProperties"])
                if self.drift_emu:
                    # How Slides really letterboxes: the picture loses width and
                    # is centred, so position moves by half of what width lost.
                    props["size"]["width"]["magnitude"] -= self.drift_emu
                    props["transform"]["translateX"] += self.drift_emu // 2
                if self.shift:
                    # 8.16pt: what a picture that was never cropped to its well
                    # actually measured, live, on 2026-09-20. The letterbox
                    # Slides leaves when the crop DID happen is under a rendered
                    # pixel, and the check has to tell those two apart.
                    props["transform"]["translateX"] += round(8.16 * 12700)
                elements.append(
                    {
                        "objectId": image["objectId"],
                        "image": {"sourceUrl": image["url"]},
                        "size": props["size"],
                        "transform": props["transform"],
                    }
                )
            elif "updatePageElementsZOrder" in request:
                ids = request["updatePageElementsZOrder"]["pageElementObjectIds"]
                moving = [e for e in elements if e["objectId"] in ids]
                assert len(moving) == len(ids)
                elements[:] = [e for e in elements if e["objectId"] not in ids] + moving
        self.answer = {"replies": [{}] * (len(self.requests) - int(self.incomplete))}
        return self

    def execute(self) -> dict[str, Any]:
        """Return the isolated API reply."""
        return copy.deepcopy(self.answer)


def sources() -> list[dict[str, Any]]:
    return json.loads((Path(__file__).parent / "fixtures/property_frames.json").read_text())  # type: ignore[no-any-return]


@pytest.mark.parametrize("source", sources(), ids=lambda r: r["name"])
def test_all_three_photos_preserve_measured_geometry_and_layer_order(
    source: dict[str, Any],
) -> None:
    slides = Slides(source["presentation"])
    before = copy.deepcopy(slides.presentation)
    page = before["slides"][0]
    w = before["pageSize"]["width"]["magnitude"]
    h = before["pageSize"]["height"]["magnitude"]
    hero = find_hero_frame(page, w, h, source["name"])
    assert hero is not None
    row = secondary_frames(page, w, h, hero, source["name"])
    dimensions: list[tuple[int, int]] = []

    def fit(url: str, width: int, height: int) -> str:
        dimensions.append((width, height))
        return url

    assert place_property_photos(
        slides, "copy", ["main", "left", "right"], source["name"], fit, (1080, 1350)
    ) == (3, 0)
    elements = slides.presentation["slides"][0]["pageElements"]
    assert [e["image"]["sourceUrl"] for e in elements if "image" in e] == ["main", "left", "right"]
    for frame, url in zip(row, ["left", "right"], strict=True):
        element = next(e for e in elements if e.get("image", {}).get("sourceUrl") == url)
        assert element["transform"]["translateX"] == frame.x
        assert element["transform"]["translateY"] == frame.y
        assert element["size"]["width"]["magnitude"] == frame.width
    untouched = {
        e["objectId"]: e
        for e in page["pageElements"]
        if e["objectId"] not in {hero.object_id, *(f.object_id for f in row)}
    }
    assert all(e == untouched[e["objectId"]] for e in elements if e["objectId"] in untouched)
    assert len(dimensions) == 3


def test_no_slides_mutation_if_any_fit_fails() -> None:
    source = sources()[1]
    slides = Slides(source["presentation"])
    with pytest.raises(ValueError, match="could not be fitted"):
        place_property_photos(
            slides,
            "copy",
            ["one", "two", "three"],
            source["name"],
            lambda url, _w, _h: "" if url == "two" else url,
            (1080, 1350),
        )
    assert slides.requests == []


@pytest.mark.parametrize("failure", ["incomplete", "shift"])
def test_incomplete_confirmation_or_wrong_geometry_is_not_success(failure: str) -> None:
    source = sources()[1]
    slides = Slides(source["presentation"])
    setattr(slides, failure, True)
    with pytest.raises(ValueError):
        place_property_photos(
            slides,
            "copy",
            ["one", "two", "three"],
            source["name"],
            lambda u, _w, _h: u,
            (1080, 1350),
        )


def test_missing_secondary_photos_are_cleared_not_left_as_sample_houses() -> None:
    source = sources()[1]
    slides = Slides(source["presentation"])
    assert place_property_photos(
        slides, "copy", ["one"], source["name"], lambda u, _w, _h: u, (1080, 1350)
    ) == (1, 2)
    elements = slides.presentation["slides"][0]["pageElements"]
    assert len([e for e in elements if "image" in e]) == 1
    assert not {"p1_i26", "p1_i28"} & {e["objectId"] for e in elements}


def test_explicit_first_photo_builds_once_and_persists_all_sources(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    seen: list[str] = []
    handoff = _handoff(path, seen)
    client = FakeSlackClient()
    event = _event(
        files=[{"id": "F1"}, {"id": "F2"}, {"id": "F3"}],
        text="Make the first photo the main photo on the graphic.",
    )
    assert handoff.handle(event, client) == ""
    assert seen == ["response-1", run_id]
    with connect(path) as connection:
        run = store.run_by_id(connection, run_id)
        assert run is not None
        assert [p["id"] for p in stored_photos(run.property_photos)] == ["F1", "F2", "F3"]
    assert handoff.handle(event, client) == ""
    assert seen == ["response-1", run_id]


def test_ambiguous_batch_is_retained_then_resumed_with_second_photo(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    seen: list[str] = []
    handoff = _handoff(path, seen)
    client = FakeSlackClient()
    event = _event(files=[{"id": "F1"}, {"id": "F2"}, {"id": "F3"}])
    assert "I kept your 3 photos" in handoff.handle(event, client)
    assert not seen and not client.calls
    with connect(path) as connection:
        run = store.run_by_id(connection, run_id)
        assert run is not None
        assert pending_uploads(run.pending_photo_files) == ["F1", "F2", "F3"]
        # The run is still genuinely waiting: staging must not retire the
        # photo question, or an unanswered choice strands the listing.
        assert run.is_paused and store.has_pending_photo_question(
            connection, run_id, run.slack_thread_ts
        )
    event["property_main_index"] = 1
    assert handoff.handle(event, client) == ""
    with connect(path) as connection:
        run = store.run_by_id(connection, run_id)
        assert run is not None
        assert [p["id"] for p in stored_photos(run.property_photos)] == ["F2", "F1", "F3"]
        assert run.pending_photo_files == "[]"


def test_order_parser_does_not_choose_from_a_vague_or_conflicting_caption() -> None:
    assert main_index("Make the first photo the main photo on the graphic.", 3) == 0
    assert main_index("Make the second photo the main photo.", 3) == 1
    assert main_index("Use all 3.", 3) is None
    assert main_index("Do not use the first as the main photo.", 3) is None
    assert main_index("The road should be the large photo.", 3) is None


def test_file_shared_recovers_all_siblings_in_the_same_order() -> None:
    class Client:
        def files_info(self, *, file: str) -> dict[str, Any]:
            return {
                "file": {
                    "id": file,
                    "user": "Carmen",
                    "mimetype": "image/jpeg",
                    "shares": {"public": {"channel": [{"ts": "2", "thread_ts": "1"}]}},
                }
            }

        def conversations_replies(self, **_kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
            return {
                "messages": [
                    {
                        "ts": "2",
                        "text": "Make the first photo the main photo.",
                        "files": [{"id": f"F{i}", "mimetype": "image/jpeg"} for i in range(1, 4)],
                    }
                ]
            }

    for file in ("F1", "F2", "F3"):
        event = shared_file_event({"file_id": file}, Client())
        assert event is not None
        assert [f["id"] for f in event["files"]] == ["F1", "F2", "F3"]


def test_a_known_row_design_refuses_a_layout_it_cannot_measure_uniquely() -> None:
    """A redesign that breaks the pair is refused, never guessed at.

    `secondary_frames` is the only thing standing between a changed source and
    a photograph placed over the wrong part of somebody's flyer, so widening a
    well until the pair is ambiguous must raise rather than pick two.
    """
    source = copy.deepcopy(sources()[1])
    page = source["presentation"]["slides"][0]
    w = source["presentation"]["pageSize"]["width"]["magnitude"]
    h = source["presentation"]["pageSize"]["height"]["magnitude"]
    hero = find_hero_frame(page, w, h, source["name"])
    assert hero is not None
    element = next(e for e in page["pageElements"] if e["objectId"] == "p1_i28")
    element["size"]["width"]["magnitude"] *= 3

    with pytest.raises(ValueError):
        secondary_frames(page, w, h, hero, source["name"])


def test_an_unrecorded_design_has_no_certified_row() -> None:
    """Only measured designs get a row. A name is not a measurement."""
    source = sources()[1]
    page = source["presentation"]["slides"][0]
    w = source["presentation"]["pageSize"]["width"]["magnitude"]
    h = source["presentation"]["pageSize"]["height"]["magnitude"]
    hero = find_hero_frame(page, w, h, source["name"])
    assert hero is not None

    assert secondary_frames(page, w, h, hero, "Sold") == ()
    assert secondary_frames(page, w, h, hero, "Client Review Post") == ()


@pytest.mark.parametrize(
    "value",
    ["", "not json", "{}", '[{"id": "F1"}]', '[{"id": "", "url": "u"}]', '["F1"]'],
)
def test_a_malformed_photo_record_costs_the_batch_and_never_the_build(value: str) -> None:
    """Only Gable writes this column, so malformed means a defect, not input.

    Raising took the whole build down with it. Returning none falls back to the
    run's single photo URL, which is what every flyer did before batches, so
    the cost is the smaller photographs.
    """
    assert stored_photos(value) == []


@pytest.mark.parametrize("value", ["", "not json", '{"a": 1}', '["F1", "F1"]', "[1, 2]"])
def test_a_malformed_retained_upload_record_reads_as_nothing_retained(value: str) -> None:
    assert pending_uploads(value) == []


def test_more_photos_than_any_design_holds_still_answers_the_words(tmp_path: Path) -> None:
    """Four images is over the ceiling; the words beside them are still owed a reply.

    Dropping images in silence is the 2026-08-28 failure. The ceiling moved
    from one to three, and the reason that sentence exists did not move at all.
    """
    path = tmp_path / "gable.db"
    _paused_database(path)
    files = [{"id": f"F{i}"} for i in range(1, 5)]

    spoken = _handoff(path, []).handle(
        _event(files=files, text="1011 Winged Foot Dr, Baltimore, MD 21201"), FakeSlackClient()
    )
    assert spoken == TOO_MANY_ANSWER_THE_WORDS

    silent = _handoff(path, []).handle(_event(files=files, text=""), FakeSlackClient())
    assert "at most three property photos" in silent


def test_resending_kept_photos_is_never_met_with_silence(tmp_path: Path) -> None:
    """Carmen changing her mind about the main photo must get an answer.

    A subset check treated any re-send of already-kept photographs as already
    handled and returned the "the run already spoke" sentinel, so "actually
    make the second one the main photo" did nothing and said nothing -- the
    worst of the two ways to be wrong. Whether the re-send rebuilds is the
    ordinary replacement policy's call; saying something is not optional.
    """
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    handoff = _handoff(path, [])
    client = FakeSlackClient()
    files = [{"id": "F1"}, {"id": "F2"}, {"id": "F3"}]

    assert handoff.handle(_event(files=files, property_main_index=0), client) == ""
    with connect(path) as connection:
        run = store.run_by_id(connection, run_id)
        assert run is not None
        assert [p["id"] for p in stored_photos(run.property_photos)] == ["F1", "F2", "F3"]

    again = handoff.handle(_event(files=files, property_main_index=1), client)
    assert again.strip()


def test_the_selection_tool_refuses_a_number_it_is_not_holding_uploads_for(
    tmp_path: Path,
) -> None:
    """A stale or invented tool call says what to do instead of selecting nothing."""
    path = tmp_path / "gable.db"
    _paused_database(path)
    handoff = _handoff(path, [])
    with connect(path) as connection:
        assert "Send the photos here together" in select_photos(
            connection,
            handoff,
            FakeSlackClient(),
            THREAD,
            CHANNEL,
            {"main_index": 2},
            lambda _s: None,
        )


def test_the_sub_pixel_letterbox_slides_always_leaves_is_not_a_failure() -> None:
    """`createImage` fits and centres; an integer pixel crop cannot match exactly.

    Measured live across all nine wells of the three row designs on 2026-09-20:
    the worst was 0.443pt of width and 0.221pt of position. A readback that
    demanded exact EMU therefore failed every real build, which is how this was
    found -- the canary refused all three designs.
    """
    source = sources()[1]
    slides = Slides(source["presentation"])
    slides.drift_emu = round(0.443 * 12700)  # Open House's worst measured well

    assert place_property_photos(
        slides, "copy", ["one", "two", "three"], source["name"], lambda u, _w, _h: u, (1080, 1350)
    ) == (3, 0)


def test_the_delivered_count_comes_from_the_file_being_delivered(tmp_path: Path) -> None:
    """The run row has no output file id yet when this sentence is composed.

    `output_file_id` is written by the same transition that records the
    delivery, which happens after the message is built. Reading it off the run
    therefore matched nothing on every real build, and a three-photo flyer was
    delivered saying "I resized and fitted the photo" — found in the playground
    on 2026-09-20, with a green suite.
    """
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    with connect(path) as connection:
        store.set_status(
            connection,
            run_id,
            "building",
            "placed and verified 3 property photos on COPY-A",
            photo_source="slack_upload",
            property_photos=json.dumps(
                [{"id": f"F{i}", "url": f"http://h/{i}.jpg"} for i in "123"]
            ),
        )
        # The run row still carries no output file id, exactly as it does not
        # at the moment the delivery message is assembled.
        run = store.run_by_id(connection, run_id)
        assert run is not None and run.output_file_id == ""

        assert photo_note(connection, run_id, "COPY-A") == (
            "I resized and fitted 3 property photos and finished the flyer."
        )
        # A different copy is a different build, and cannot borrow the count.
        assert photo_note(connection, run_id, "COPY-B") == (
            "I resized and fitted the photo and finished the flyer."
        )


def test_an_unused_smaller_space_is_said_out_loud(tmp_path: Path) -> None:
    path = tmp_path / "gable.db"
    run_id = _paused_database(path)
    with connect(path) as connection:
        store.set_status(
            connection,
            run_id,
            "building",
            "placed and verified 2 property photos on COPY-A",
            photo_source="slack_upload",
            property_photos=json.dumps([{"id": f"F{i}", "url": f"http://h/{i}.jpg"} for i in "12"]),
        )
        store.set_status(
            connection,
            run_id,
            "building",
            "left smaller property photo spaces empty on COPY-A",
        )

        assert photo_note(connection, run_id, "COPY-A") == (
            "I resized and fitted 2 property photos and finished the flyer. "
            "I left the unused smaller photo spaces empty."
        )
