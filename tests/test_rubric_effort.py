"""The effort model (roadmap item 12a): costs, bands, the near-boundary marker, overrides.

What this file proves, in the order the item states it:

* the bands are the instrument's own, pack data, and nothing can band a row Sufficiently Present;
* the per-defect costs live in one file, every entry is marked uncalibrated, an override file
  changes them without a code change, and an unpriced detector is not free;
* **Major is hard to reach on the shipped table** — a property computed from the table and the
  pack's bands through the real arithmetic, not a restatement of the constants;
* ``NOT_YET_IMPLEMENTED`` and ``NO_DETERMINISTIC_COVERAGE`` cannot come out of the effort model as
  Sufficiently Present;
* the near-boundary marker is computed, persisted with the row, and wider at the lowest boundary;
* override records keep the machine proposal and the human rating side by side, append-only;
* nothing in the effort path does the arithmetic that would produce a course-level score.
"""

from __future__ import annotations

import io
import re
import tokenize
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

from detector_fixtures import course, page
from sightline.config import Settings
from sightline.detect import run_detectors
from sightline.detect.base import (
    Detector,
    Finding,
    Location,
    Severity,
    Survey,
    Tier,
    detectors,
    registry_snapshot,
)
from sightline.model import ArtifactKind
from sightline.paths import DataPaths
from sightline.rubric.coverage import pack_coverage
from sightline.rubric.effort import (
    COSTS_FILENAME,
    EffortConfigError,
    EffortModel,
    Margins,
    default_effort_model,
    load_effort_model,
)
from sightline.rubric.overrides import MachineProposal, record_override
from sightline.rubric.pack import BAND_RATINGS, Band, RatingScale, loads
from sightline.rubric.project import Coverage, Projection, Rating, TagIndex, project
from sightline.rubric.shipped import shipped_pack
from sightline.store import RunStore

EFFORT_SOURCE = (
    Path(__file__).resolve().parent.parent / "src" / "sightline" / "rubric" / "effort.py"
)
COSTS_SOURCE = EFFORT_SOURCE.with_name(COSTS_FILENAME)
REFERENCE = "uccs-oscqr-3-1-3"

#: THE PROPERTY THE SHIPPED TABLE MUST HAVE. Five hand-completed UCCS OSCQR reviews rated 326
#: criterion instances and said Major Revision **twice** (0.6%); Minor 15%, Moderate 2.8%. A table
#: that reaches Major on one criterion from a page's worth of defects — ten images, a dozen links —
#: contradicts that before any fitting is attempted. Twenty is the floor: twice a page's worth, from
#: the single costliest detector feeding the row. It is a bound on the table's shape, not a fit.
MAJOR_FLOOR = 20


# -- synthetic detectors, none of which ship -----------------------------------------------------


with registry_snapshot():

    class _Twice(Detector):
        """Two observations per page."""

        id = "Q01"
        title = "A probe that reports two observations per page"
        collections = ("pages",)
        capability_tag = "alt_text"
        capability_description = "Two observations per page, for the effort tests."

        def examine(self, survey: Survey) -> Iterable[Finding]:
            for body in survey.bodies():
                survey.counted(2)
                for position in range(2):
                    yield survey.finding(
                        code="probe",
                        severity=Severity.BARRIER,
                        location=survey.location(body.artifact, within=f"item {position + 1}"),
                        observed=f"probe {position + 1}",
                        evidence="<p>as written</p>",
                    )

    class _Quiet(Detector):
        """Opens every page and finds nothing."""

        id = "Q02"
        title = "A probe that opens every page and reports nothing"
        collections = ("pages",)
        capability_tag = "alt_text"
        capability_description = "Opens everything and reports nothing, for the clean case."

        def examine(self, survey: Survey) -> Iterable[Finding]:
            for _ in survey.bodies():
                survey.counted()
            return ()


PROBES = {probe.id: probe for probe in (_Twice(), _Quiet())}


def probe_index(*ids: str) -> TagIndex:
    return TagIndex.build([PROBES[probe_id] for probe_id in ids])


def probe_report(*ids: str, pages: int = 1):
    document = course([page(f"p{number}", f"<p>Page {number}</p>") for number in range(pages)])
    return run_detectors(document, only=[PROBES[probe_id] for probe_id in ids])


def pack_from(criteria: list[dict[str, object]]):
    payload = {
        "schema_version": 1,
        "id": "probe",
        "name": "Probe",
        "version": "7",
        "sections": [{"code": "S", "title": "A section"}],
        "criteria": criteria,
    }
    return loads(yaml.safe_dump(payload, sort_keys=False), origin="probe.yaml")


def finding(detector: str) -> Finding:
    return Finding(
        detector=detector,
        code="probe",
        severity=Severity.BARRIER,
        tier=Tier.A,
        location=Location(
            artifact_id="p0",
            artifact_kind=ArtifactKind.PAGE,
            title="Page 0",
            canvas_path="/courses/1/pages/p0",
        ),
        observed="probe",
    )


def only_row(projection: Projection, code: str = "S 1"):
    found = projection.row(code)
    assert found is not None
    return found


@lru_cache(maxsize=1)
def reference_scale() -> RatingScale:
    return shipped_pack(REFERENCE).rating_scale


def band_of(hours: float) -> str:
    return reference_scale().band_for(hours).rating


# ================================================================================================
# Bands: pack data, the instrument's own, and never Sufficiently Present
# ================================================================================================


def test_the_default_bands_cite_the_cells_they_were_read_from() -> None:
    """So the next reader does not re-measure them. ``tests/test_rubric_packs.py`` reads the same
    cells off the workbook itself; this pins that the citation is where a reader will look."""
    from sightline.rubric import pack as pack_module

    doc = pack_module.default_rating_scale.__doc__ or ""
    for cell in ("S3", "T3", "U3", "V3", "R4", "T4", "U4", "V4", "S4"):
        assert f"`{cell}`" in doc, cell
    assert "CONFIG!$H$15:$K$19" in doc
    bands = pack_module.default_rating_scale().bands
    assert [(band.rating, band.up_to_hours) for band in bands] == [
        ("minor", 0.5),
        ("moderate", 2.0),
        ("major", None),
    ]


def test_no_band_can_be_named_sufficiently_present() -> None:
    """Sufficiently Present is "no qualifying defects found", not a range of hours. A Band built in
    code never passes through the pack schema, so the refusal lives on the Band itself."""
    with pytest.raises(ValueError, match="no band"):
        Band(rating=Rating.SUFFICIENTLY_PRESENT.value, label="Sufficiently Present", up_to_hours=0)
    for name in ("no_response", "not_applicable", "clean"):
        with pytest.raises(ValueError):
            Band(rating=name, label=name, up_to_hours=None)


def test_banding_arithmetic_never_returns_a_clean_reading() -> None:
    """Swept across every hour figure from zero to far past the top band, including zero itself."""
    scale = shipped_pack(REFERENCE).rating_scale
    for step in range(0, 1001):
        rating = scale.band_for(step / 100).rating
        assert rating in BAND_RATINGS
        assert rating != Rating.SUFFICIENTLY_PRESENT.value


# ================================================================================================
# The cost table: one file, every entry marked, overridable, nothing free
# ================================================================================================


def test_the_shipped_table_is_uncalibrated_and_says_so() -> None:
    model = default_effort_model()
    assert model.calibrated is False
    assert "uncalibrated" in model.note
    assert model.sources == (COSTS_FILENAME,)


def test_every_entry_in_the_cost_file_is_marked_as_an_uncalibrated_estimate() -> None:
    """Read as text, because the marking is a YAML comment on each line."""
    text = COSTS_SOURCE.read_text(encoding="utf-8")
    entries = [line for line in text.splitlines() if re.match(r"^\s+D\d+:\s", line)]
    assert len(entries) == len(default_effort_model().costs)
    for line in entries:
        assert "UNCALIBRATED estimate" in line, line
    # The two scalars that are also costs carry the same marking in their comment blocks.
    for key in ("default_hours:", "near_boundary:"):
        block = text.split(key)[0].rsplit("\n\n", 1)[-1]
        assert "UNCALIBRATED" in block, key


def test_the_shipped_table_prices_every_registered_detector_and_nothing_else() -> None:
    """A detector added without a cost entry still works — it costs the default and the coverage
    report names it — but the shipped table should not be where that gap lives. Add a line to
    ``src/sightline/rubric/costs.yaml`` marked as an uncalibrated estimate."""
    model = default_effort_model()
    registered = {detector.id for detector in detectors()}
    assert registered - set(model.costs) == set(), "unpriced detector(s) in the shipped table"
    assert set(model.costs) - registered == set(), "cost entries for detectors that do not exist"


def test_costs_are_keyed_on_the_detector_not_the_severity() -> None:
    model = EffortModel(costs={"D21": 0.05, "D27": 0.1})
    assert model.cost_of(finding("D21")) == pytest.approx(0.05)
    assert model.cost_of(finding("D27")) == pytest.approx(0.1)


def test_an_unpriced_detector_costs_the_documented_default_and_never_zero() -> None:
    model = default_effort_model()
    assert model.default_hours > 0
    assert not model.priced("D99")
    assert model.cost_of(finding("D99")) == pytest.approx(model.default_hours)
    assert model.unpriced(["D21", "D99", "D99", "D98"]) == ("D98", "D99")


@pytest.mark.parametrize("value", [0, 0.0, -0.1, "0.1", True, float("nan"), float("inf")])
def test_a_cost_that_is_not_a_positive_number_is_refused(value: object) -> None:
    with pytest.raises(EffortConfigError):
        EffortModel(costs={"D21": value})  # type: ignore[dict-item]
    with pytest.raises(EffortConfigError):
        EffortModel(default_hours=value)  # type: ignore[arg-type]


def test_an_override_file_changes_one_cost_without_a_code_change(tmp_path: Path) -> None:
    override = tmp_path / "costs.yaml"
    override.write_text("costs:\n  D21: 0.02\n  D99: 0.3\n", encoding="utf-8")
    shipped = default_effort_model()
    model = load_effort_model(override)

    assert model.costs["D21"] == pytest.approx(0.02)
    assert model.costs["D99"] == pytest.approx(0.3)
    # Everything the override did not name keeps the shipped value.
    assert {key: value for key, value in model.costs.items() if key not in {"D21", "D99"}} == {
        key: value for key, value in shipped.costs.items() if key != "D21"
    }
    assert model.default_hours == shipped.default_hours
    assert model.margins == shipped.margins
    assert model.calibrated is False
    assert model.sources == (COSTS_FILENAME, str(override))


def test_an_override_file_may_move_the_default_the_margins_and_the_calibrated_flag(
    tmp_path: Path,
) -> None:
    override = tmp_path / "costs.yaml"
    override.write_text(
        "calibrated: true\ndefault_hours: 0.2\nnear_boundary:\n  between_bands: 0.05\n",
        encoding="utf-8",
    )
    model = load_effort_model(override)
    assert model.calibrated is True
    assert "uncalibrated" not in model.note
    assert "estimate" in model.note.lower()  # a fitted table is still an estimate
    assert model.default_hours == pytest.approx(0.2)
    assert model.margins == Margins(
        lowest=default_effort_model().margins.lowest, between_bands=0.05
    )


@pytest.mark.parametrize(
    "text, message",
    [
        ("default_hour: 0.2\n", "unknown key"),
        ("costs:\n  D21: 0\n", "may not cost nothing"),
        ("costs: [1, 2]\n", "detector id"),
        ("near_boundary:\n  widest: 1\n", "near_boundary"),
        ("calibrated: yes please\n", "true or false"),
        ("costs: {D21: [\n", "not valid YAML"),
    ],
)
def test_a_malformed_override_is_refused_at_load_time(
    tmp_path: Path, text: str, message: str
) -> None:
    override = tmp_path / "costs.yaml"
    override.write_text(text, encoding="utf-8")
    with pytest.raises(EffortConfigError, match=message):
        load_effort_model(override)


def test_an_override_path_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    with pytest.raises(EffortConfigError, match="cannot be read"):
        load_effort_model(tmp_path / "absent.yaml")


def test_the_override_path_is_a_setting(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "costs.yaml"
    monkeypatch.setenv("SIGHTLINE_EFFORT_COSTS", str(target))
    assert Settings(_env_file=None).effort_costs == target  # type: ignore[call-arg]


def test_the_coverage_report_names_a_detector_the_table_does_not_price() -> None:
    pack = shipped_pack(REFERENCE)
    shipped = pack_coverage(pack)
    assert shipped.unpriced == ()
    assert "cost table" not in shipped.statement()

    partial = default_effort_model()
    without_alt = EffortModel(
        costs={key: value for key, value in partial.costs.items() if key != "D21"},
        default_hours=partial.default_hours,
    )
    report = pack_coverage(pack, effort=without_alt)
    assert report.unpriced == ("D21",)
    assert "no entry in the cost table" in report.statement()
    assert "D21" in report.detail()
    assert report.to_dict()["unpriced"] == ["D21"]
    assert report.ok


# ================================================================================================
# THE PROPERTY: Major is hard to reach on the shipped table
# ================================================================================================


def findings_to_reach(rating: str, detector: str, model: EffortModel) -> int:
    """The smallest number of this detector's findings that one row needs to band as ``rating``.

    Counted through the model's own ``total`` and the reference pack's own ``band_for``, so the
    answer is what the pipeline would do, not a division of two constants.
    """
    for count in range(1, 10_000):
        if band_of(model.total([finding(detector)] * count)) == rating:
            return count
    raise AssertionError(f"{detector} never reaches {rating}")


def test_major_revision_needs_an_implausible_number_of_findings_on_every_row() -> None:
    """Every row of the reference pack, fed by the costliest detector bound to it, and a detector
    nobody priced as well. A row fed by several detectors can only need *more* findings from the
    cheaper ones, so the costliest is the worst case."""
    model = default_effort_model()
    coverage = pack_coverage(shipped_pack(REFERENCE))
    rows = [row for row in coverage.rows if row.detectors]
    assert rows, "the reference pack binds nothing — this property would be vacuous"

    reachable: dict[str, int] = {}
    for row in rows:
        costliest = max(row.detectors, key=model.hours_for)
        reachable[row.code] = findings_to_reach("major", costliest, model)
    reachable["an unpriced detector"] = findings_to_reach("major", "D-unpriced", model)

    too_easy = {code: count for code, count in reachable.items() if count < MAJOR_FLOOR}
    assert too_easy == {}, f"Major reachable in fewer than {MAJOR_FLOOR} findings: {too_easy}"


def test_a_page_of_missing_alt_text_is_minor_revision_not_major() -> None:
    """The failure the item names: a handful of missing alt attributes must not read Major."""
    model = default_effort_model()
    ten = model.total([finding("D21")] * 10)
    assert band_of(ten) == "minor"


def test_one_finding_of_any_kind_on_its_own_reads_minor_revision() -> None:
    """The other side of the same shape: Minor is by far the commonest band humans give (15%, vs
    2.8% Moderate), so no single observation may jump a row past it."""
    model = default_effort_model()
    for detector in [*model.costs, "D-unpriced"]:
        assert band_of(model.total([finding(detector)])) == "minor", detector


def test_the_property_test_can_fail() -> None:
    """A property test that cannot fail is decoration. The item-04 placeholder (0.25 h per barrier)
    reaches Major in nine findings, and this test says so."""
    steep = EffortModel(costs={"D21": 0.25})
    assert findings_to_reach("major", "D21", steep) == 9
    assert findings_to_reach("major", "D21", steep) < MAJOR_FLOOR


# ================================================================================================
# Sum, then band — once, globally
# ================================================================================================


def test_a_row_sums_its_findings_and_bands_the_total_through_the_shipped_model() -> None:
    """``tests/test_rubric_project.py`` proves the fan-in across two detectors; this proves the
    shipped model's arithmetic is the sum and nothing else. Ten pages of two findings each at the
    probe's default price is 2.0 h: one band up from any single page."""
    model = default_effort_model()
    pack = pack_from(
        [{"code": "S 1", "section": "S", "text": "t", "tier": "A", "bindings": ["alt_text"]}]
    )
    one = only_row(project(pack, probe_report("Q01"), tags=probe_index("Q01"), effort=model))
    ten = only_row(
        project(pack, probe_report("Q01", pages=10), tags=probe_index("Q01"), effort=model)
    )
    assert one.hours == pytest.approx(2 * model.default_hours)
    assert ten.hours == pytest.approx(10 * one.hours)
    assert one.rating is Rating.MINOR
    assert ten.rating is Rating.MODERATE


# ================================================================================================
# The three states through the effort model
# ================================================================================================


def test_only_deterministic_coverage_can_come_out_sufficiently_present() -> None:
    """All three states fed through the projection and the shipped effort model at once.

    ``S 1`` is bound to a capability a detector implements and that detector ran clean. ``S 2`` is
    bound to a capability in the vocabulary that nothing here implements. ``S 3`` has no binding.
    Only ``S 1`` may read Sufficiently Present; the other two carry no hours, no band, and no
    near-boundary marker — the arithmetic never ran for them, and zero is not what it would say.
    """
    pack = pack_from(
        [
            {"code": "S 1", "section": "S", "text": "a", "tier": "A", "bindings": ["alt_text"]},
            {
                "code": "S 2",
                "section": "S",
                "text": "b",
                "tier": "A",
                "bindings": ["heading_structure"],
            },
            {"code": "S 3", "section": "S", "text": "c", "tier": "A"},
        ]
    )
    projection = project(
        pack, probe_report("Q02"), tags=probe_index("Q02"), effort=default_effort_model()
    )
    clean, pending, unbound = (only_row(projection, code) for code in ("S 1", "S 2", "S 3"))

    assert (clean.coverage, clean.rating) == (Coverage.DETERMINISTIC, Rating.SUFFICIENTLY_PRESENT)
    assert pending.coverage is Coverage.NOT_YET_IMPLEMENTED
    assert unbound.coverage is Coverage.NO_DETERMINISTIC_COVERAGE
    for entry in (pending, unbound):
        assert entry.rating is Rating.NO_RESPONSE
        assert entry.hours is None
        assert entry.near_boundary is None
        assert entry.clean is False
    assert clean.near_boundary is None  # no effort figure to measure a distance from


# ================================================================================================
# The near-boundary marker
# ================================================================================================

CEILINGS = [("minor", 0.5), ("moderate", 2.0), ("major", None)]


def test_the_lowest_margin_is_wider_than_the_rest_by_default() -> None:
    margins = default_effort_model().margins
    assert margins.lowest > margins.between_bands > 0


@pytest.mark.parametrize(
    "hours, expected",
    [
        (0.05, ("sufficiently_present", "minor", 0.0, 0.05)),
        (0.25, ("sufficiently_present", "minor", 0.0, 0.25)),
        (0.3, None),
        (0.45, ("minor", "moderate", 0.5, 0.05)),
        (0.55, ("minor", "moderate", 0.5, 0.05)),
        (1.0, None),
        (1.9, ("moderate", "major", 2.0, 0.1)),
        (2.1, ("moderate", "major", 2.0, 0.1)),
        (2.2, None),
    ],
)
def test_the_marker_is_the_distance_to_the_nearest_threshold_within_its_margin(
    hours: float, expected: tuple[str, str, float, float] | None
) -> None:
    model = EffortModel(margins=Margins(lowest=0.25, between_bands=0.1))
    marker = model.near_boundary(hours, CEILINGS)
    if expected is None:
        assert marker is None
        return
    assert marker is not None
    assert marker.below == expected[0]
    assert marker.above == expected[1]
    assert marker.threshold_hours == pytest.approx(expected[2])
    assert marker.distance_hours == pytest.approx(expected[3])
    assert marker.lowest is (expected[0] == "sufficiently_present")


def test_the_same_distance_is_near_at_the_lowest_boundary_and_not_between_bands() -> None:
    """The asymmetry the item exists for: 0.2 h from a threshold is near only where humans disagree
    most, at Sufficiently Present / Minor."""
    model = default_effort_model()
    assert model.near_boundary(0.2, CEILINGS) is not None
    assert model.near_boundary(0.5 - 0.2, CEILINGS) is None
    assert model.near_boundary(2.0 + 0.2, CEILINGS) is None


def test_the_marker_is_a_distance_and_says_nothing_else() -> None:
    """No percentage, no probability, no confidence — SPEC §01."""
    marker = default_effort_model().near_boundary(0.1, CEILINGS)
    assert marker is not None
    payload = marker.to_dict()
    assert set(payload) == {
        "below",
        "above",
        "threshold_hours",
        "hours",
        "distance_hours",
        "margin_hours",
        "lowest",
    }
    text = marker.statement({"sufficiently_present": "Sufficiently Present", "minor": "Minor"})
    assert "%" not in text
    for word in ("confiden", "likel", "probab", "certain", "sure"):
        assert word not in text.lower()
    assert "Sufficiently Present / Minor" in text


def test_the_marker_is_persisted_on_the_row_it_was_computed_for() -> None:
    """Computed once in the projection and carried in the row dictionary the review file stores, so
    every surface that renders the band reads the same marker rather than recomputing one."""
    pack = pack_from(
        [{"code": "S 1", "section": "S", "text": "t", "tier": "A", "bindings": ["alt_text"]}]
    )
    model = EffortModel(costs={"Q01": 0.05})
    entry = only_row(project(pack, probe_report("Q01"), tags=probe_index("Q01"), effort=model))
    assert entry.rating is Rating.MINOR
    assert entry.near_boundary is not None
    assert entry.near_boundary.lowest
    assert entry.to_dict()["near_boundary"] == entry.near_boundary.to_dict()

    far = EffortModel(costs={"Q01": 0.15})
    entry = only_row(project(pack, probe_report("Q01"), tags=probe_index("Q01"), effort=far))
    assert entry.hours == pytest.approx(0.3)
    assert entry.to_dict()["near_boundary"] is None


def test_tier_c_and_d_rows_carry_no_marker_whatever_their_findings() -> None:
    pack = pack_from(
        [
            {"code": "S 1", "section": "S", "text": "c", "tier": "C", "bindings": ["alt_text"]},
            {"code": "S 2", "section": "S", "text": "d", "tier": "D", "bindings": ["alt_text"]},
        ]
    )
    projection = project(
        pack, probe_report("Q01"), tags=probe_index("Q01"), effort=EffortModel(costs={"Q01": 0.05})
    )
    for code in ("S 1", "S 2"):
        assert only_row(projection, code).near_boundary is None


# ================================================================================================
# Override records
# ================================================================================================


@pytest.fixture
def store(paths: DataPaths) -> RunStore:
    store = RunStore.open(paths)
    yield store
    store.dispose()


def banded_projection() -> Projection:
    pack = pack_from(
        [
            {"code": "S 1", "section": "S", "text": "a", "tier": "A", "bindings": ["alt_text"]},
            {"code": "S 2", "section": "S", "text": "b", "tier": "C"},
        ]
    )
    return project(
        pack, probe_report("Q01"), tags=probe_index("Q01"), effort=EffortModel(costs={"Q01": 0.05})
    )


def test_a_run_with_no_overrides_writes_no_rows(store: RunStore) -> None:
    run = store.get_run(store.create_run(["101"], rubric_pack="probe"))
    assert run is not None
    assert store.list_overrides(run_id=run.id) == []


def test_an_override_keeps_the_machine_proposal_beside_the_human_rating(store: RunStore) -> None:
    projection = banded_projection()
    run = store.get_run(store.create_run(["101"], rubric_pack="probe"))
    assert run is not None
    course_id = run.courses[0].id
    proposal = MachineProposal.from_projection(projection, "S 1")

    record_id = record_override(
        store,
        course_id=course_id,
        proposal=proposal,
        human=Rating.SUFFICIENTLY_PRESENT,
        reviewer="designer@example.edu",
        note="Both images are decorative in context.",
    )

    [record] = store.list_overrides(course_id=course_id)
    assert record.id == record_id
    assert record.run_id == run.id
    assert record.criterion == "S 1"
    assert (record.pack_id, record.pack_version) == ("probe", "7")
    assert record.machine_rating == "minor"
    assert record.machine_hours == pytest.approx(0.1)
    assert record.machine_near_boundary == proposal.near_boundary
    assert record.machine_near_boundary is not None
    assert len(record.machine_findings) == 2
    assert {entry["detector"] for entry in record.machine_findings} == {"Q01"}
    assert record.effort_calibrated is False
    assert record.human_rating == "sufficiently_present"
    assert record.reviewer == "designer@example.edu"
    assert record.created_at.tzinfo is not None


def test_a_second_override_appends_and_never_overwrites(store: RunStore) -> None:
    projection = banded_projection()
    run = store.get_run(store.create_run(["101"], rubric_pack="probe"))
    assert run is not None
    course_id = run.courses[0].id
    proposal = MachineProposal.from_projection(projection, "S 1")
    for human in (Rating.SUFFICIENTLY_PRESENT, Rating.MODERATE):
        record_override(store, course_id=course_id, proposal=proposal, human=human, reviewer="a")

    records = store.list_overrides(course_id=course_id)
    assert [record.human_rating for record in records] == ["sufficiently_present", "moderate"]
    assert {record.machine_rating for record in records} == {"minor"}
    assert not hasattr(store, "update_override")
    assert not hasattr(store, "delete_override")


def test_an_unbanded_row_records_null_hours_not_zero(store: RunStore) -> None:
    """A Tier C row was never banded. Its proposal is No Response with no hours, and that is what
    the record says — zero hours would be a claim the effort model never made."""
    run = store.get_run(store.create_run(["101"], rubric_pack="probe"))
    assert run is not None
    proposal = MachineProposal.from_projection(banded_projection(), "S 2")
    record_override(
        store, course_id=run.courses[0].id, proposal=proposal, human=Rating.MINOR, reviewer="a"
    )
    [record] = store.list_overrides(run_id=run.id)
    assert record.machine_rating == "no_response"
    assert record.machine_hours is None
    assert record.machine_near_boundary is None


def test_the_persisted_review_and_the_live_projection_give_the_same_proposal() -> None:
    """A review screen has the review file in hand, not the projection object."""
    import json

    projection = banded_projection()
    persisted = json.loads(json.dumps(projection.to_dict(), default=str))
    for code in ("S 1", "S 2"):
        assert MachineProposal.from_review(persisted, code) == MachineProposal.from_projection(
            projection, code
        )
    with pytest.raises(KeyError):
        MachineProposal.from_review(persisted, "S 9")


def test_an_override_is_refused_without_a_reviewer_or_with_a_label_for_a_rating(
    store: RunStore,
) -> None:
    run = store.get_run(store.create_run(["101"], rubric_pack="probe"))
    assert run is not None
    course_id = run.courses[0].id
    proposal = MachineProposal.from_projection(banded_projection(), "S 1")
    with pytest.raises(ValueError, match="reviewer"):
        record_override(
            store, course_id=course_id, proposal=proposal, human=Rating.MINOR, reviewer=" "
        )
    with pytest.raises(ValueError, match="rating name"):
        store.record_override(
            course_id,
            criterion="S 1",
            pack_id="probe",
            pack_version="7",
            machine_rating="minor",
            machine_hours=0.1,
            machine_near_boundary=None,
            machine_findings=[],
            effort_calibrated=False,
            human_rating="Minor Revision",
            reviewer="a",
        )
    with pytest.raises(KeyError):
        record_override(
            store, course_id="nope", proposal=proposal, human=Rating.MINOR, reviewer="a"
        )
    assert store.list_overrides(run_id=run.id) == []


# ================================================================================================
# No aggregate — read the code for the arithmetic that would produce one
# ================================================================================================

#: Modules on the effort path: where hours are computed, rolled up, persisted and overridden.
EFFORT_PATH = [
    EFFORT_SOURCE,
    EFFORT_SOURCE.with_name("project.py"),
    EFFORT_SOURCE.with_name("overrides.py"),
    EFFORT_SOURCE.with_name("coverage.py"),
    EFFORT_SOURCE.parent.parent / "batch" / "pipeline.py",
]

#: Code (never prose — docstrings and comments are stripped first) that would build a course-level
#: score, index, percentage or total. Each pattern is the arithmetic, not a variable name.
AGGREGATE_ARITHMETIC = {
    "an average": re.compile(r"\b(mean|fmean|median|average|statistics)\b"),
    "a division by a count": re.compile(r"/\s*len\("),
    "a percentage": re.compile(r"\*\s*100\b|\b100\s*\*|:\.?\d*%|\bpercent"),
    "a score or index": re.compile(r"\b\w*(score|index_value|quality_index|grade)\w*\s*="),
    "hours summed across rows": re.compile(r"sum\([^()]*\brow\.hours"),
    "the instrument's VALUE column": re.compile(r"\btimeToFix\b|\bVALUE\b"),
}


def code_only(path: Path) -> str:
    """The module's source with every string and comment token removed."""
    kept: list[str] = []
    tokens = tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline)
    for token in tokens:
        if token.type in (tokenize.STRING, tokenize.COMMENT):
            continue
        if token.type in (tokenize.NAME, tokenize.OP, tokenize.NUMBER):
            kept.append(token.string)
        elif token.type in (tokenize.NEWLINE, tokenize.NL):
            kept.append("\n")
    return " ".join(kept).replace(" . ", ".").replace(" (", "(")


@pytest.mark.parametrize("path", EFFORT_PATH, ids=lambda path: path.name)
def test_no_aggregate_arithmetic_on_the_effort_path(path: Path) -> None:
    code = code_only(path)
    found = {name: pattern.findall(code) for name, pattern in AGGREGATE_ARITHMETIC.items()}
    assert {name: hits for name, hits in found.items() if hits} == {}


def test_the_aggregate_grep_fires_on_the_arithmetic_it_forbids(tmp_path: Path) -> None:
    """A guard nobody has fired is indistinguishable from one that cannot fire (item 21). This is
    the course-level total item 17 shipped and item 12a removed, plus an average of ratings."""
    offender = tmp_path / "offender.py"
    offender.write_text(
        "def rollup(rows):\n"
        "    hours = round(sum(row.hours or 0.0 for row in rows), 2)\n"
        "    quality_score = sum(values) / len(values)\n"
        "    return f'{hours:.0%}', statistics.mean(values)\n",
        encoding="utf-8",
    )
    code = code_only(offender)
    fired = {name for name, pattern in AGGREGATE_ARITHMETIC.items() if pattern.search(code)}
    assert fired >= {
        "hours summed across rows",
        "a division by a count",
        "a score or index",
        "an average",
    }


def test_a_data_root_from_before_item_12a_gains_the_override_table(paths: DataPaths) -> None:
    """A new table comes from ``create_all`` on open; a new *column* needs ``ADDED_COLUMNS``."""
    first = RunStore.open(paths)
    with first.engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE rating_overrides")
    first.dispose()

    reopened = RunStore.open(paths)
    try:
        run = reopened.get_run(reopened.create_run(["101"], rubric_pack="probe"))
        assert run is not None
        assert reopened.list_overrides(run_id=run.id) == []
    finally:
        reopened.dispose()
