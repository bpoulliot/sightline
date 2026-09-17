"""The rubric pack format: its schema, its loader, and the things a schema cannot say.

The fixtures are **synthetic**. ``tiny-pack.yaml`` is a ten-row instrument invented for this suite
and ``unbound-pack.yaml`` is a four-row one with nothing mapped; neither is an OSCQR derivative,
neither carries an OPEN SUNY number and neither is crosswalked to anything. That is deliberate:
if the format needed any of that, a rubric Sightline does not ship a mapping for could not be
loaded at all, and those are the users third-party rubric support exists for.

The two load-bearing assertions in here are easy to miss among the validation cases:

* **A pack with zero bindings is valid.** Not a stub, not a skeleton, not degraded. The projection
  half of that claim is in ``tests/test_rubric_project.py``.
* **Every lookup keys on the row code.** The reference instrument skips an index inside one of its
  categories, so a positional lookup misaligns criteria silently — the quietest way to report a
  finding against the wrong row.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sightline.detect.base import Tier
from sightline.rubric.pack import (
    RubricPack,
    RubricPackError,
    default_rating_scale,
    load,
    loads,
    schema,
    validate,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rubric"
TINY = FIXTURES / "tiny-pack.yaml"
UNBOUND = FIXTURES / "unbound-pack.yaml"


def tiny() -> RubricPack:
    return load(TINY)


def unbound() -> RubricPack:
    return load(UNBOUND)


def minimal(**overrides: object) -> dict[str, object]:
    """The smallest valid pack, as data, for tests that break one thing in it."""
    payload: dict[str, object] = {
        "schema_version": 1,
        "id": "probe",
        "name": "Probe",
        "version": "1",
        "sections": [{"code": "S", "title": "A section"}],
        "criteria": [{"code": "S 1", "section": "S", "text": "A criterion.", "tier": "A"}],
    }
    payload.update(overrides)
    return payload


def as_yaml(payload: object) -> str:
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


# -- the committed schema ------------------------------------------------------------------------


def test_the_schema_is_committed_and_is_a_json_schema() -> None:
    """Validated against rather than reimplemented. A schema nothing checks drifts from the
    loader within a release."""
    document = schema()
    assert document["$schema"].startswith("https://json-schema.org/draft/2020-12")
    assert set(document["required"]) == {
        "schema_version",
        "id",
        "name",
        "version",
        "sections",
        "criteria",
    }


def test_the_schema_forbids_an_instrument_with_no_rows() -> None:
    """An import that yielded zero rows is a failed import, not an empty rubric. The importer
    raises on it too; this is the same rule stated in the file a third party authors by hand."""
    assert schema()["properties"]["criteria"]["minItems"] == 1


def test_both_committed_fixtures_validate() -> None:
    for path in (TINY, UNBOUND):
        validate(yaml.safe_load(path.read_text(encoding="utf-8")), origin=path.name)


# -- loading -------------------------------------------------------------------------------------


def test_the_tiny_pack_loads_with_everything_it_declares() -> None:
    pack = tiny()
    assert pack.id == "tiny-synthetic"
    assert len(pack.criteria) == 10
    assert [section.code for section in pack.sections] == ["DES", "REA", "SML"]
    assert pack.priority_order == ("Critical", "Worth doing")
    assert pack.rating_scale.sufficiently_present == "Present"
    assert [band.rating for band in pack.rating_scale.bands] == ["minor", "moderate", "major"]


def test_a_pack_with_zero_bindings_is_valid_and_not_a_stub() -> None:
    """The state every third-party instrument starts in, asserted to be ordinary.

    Binding is an optimization, not a prerequisite. Requiring forty hand-authored bindings before
    somebody's own rubric works is a roadblock this project does not put in front of them — and
    since Sightline can never ship a pack for a proprietary instrument, an unbound import is the
    only way those users exist.
    """
    pack = unbound()
    assert len(pack.criteria) == 4
    assert all(criterion.bindings == () for criterion in pack.criteria)
    assert all(criterion.bound is False for criterion in pack.criteria)
    # And it says so in counts rather than in an adjective.
    assert "0 of 4 rendered row(s) carry a binding" in pack.statement()


def test_a_binding_to_an_unrecognised_tag_does_not_stop_a_pack_loading() -> None:
    """The pack side of the asymmetry. A detector with an unknown tag fails the build; a pack with
    one loads, and the row degrades with a warning at projection time."""
    pack = tiny()
    row = pack.criterion("REA 5")
    assert row is not None
    assert row.bindings == ("format_alternatives_nobody_declared",)


def test_the_bindings_are_kept_exactly_as_authored() -> None:
    """Including a tag this build does not recognise.

    Dropping it on load would make loading a pack on one build and saving it on another lose data,
    and the degradation an unknown tag causes is a fact about a *run* rather than about the file.
    """
    text = TINY.read_text(encoding="utf-8")
    assert "format_alternatives_nobody_declared" in tiny().to_yaml()
    assert "format_alternatives_nobody_declared" in text


def test_a_missing_file_is_reported_with_its_name() -> None:
    with pytest.raises(RubricPackError, match="no-such-pack.yaml"):
        load(FIXTURES / "no-such-pack.yaml")


def test_an_empty_file_is_an_error_rather_than_an_empty_pack() -> None:
    with pytest.raises(RubricPackError, match="the file is empty"):
        loads("", origin="probe")


def test_malformed_yaml_is_reported_as_malformed_yaml() -> None:
    with pytest.raises(RubricPackError, match="not valid YAML"):
        loads("criteria: [\n", origin="probe")


# -- validation names the offending path ---------------------------------------------------------


def test_a_bad_tier_is_rejected_naming_the_row_that_carries_it() -> None:
    """A schema error reading only "invalid" costs an afternoon on a sixty-eight-row file."""
    payload = minimal()
    payload["criteria"] = [
        {"code": "S 1", "section": "S", "text": "Fine.", "tier": "A"},
        {"code": "S 2", "section": "S", "text": "Not fine.", "tier": "E"},
    ]
    with pytest.raises(RubricPackError) as caught:
        loads(as_yaml(payload), origin="probe.yaml")
    assert caught.value.path == "$.criteria[1].tier"
    assert "probe.yaml" in str(caught.value)


def test_an_unexpected_key_is_rejected_naming_it() -> None:
    """``additionalProperties: false`` throughout, so a misspelled ``bindigs`` is a loud error
    rather than forty rows that silently lost their bindings."""
    payload = minimal()
    payload["criteria"] = [
        {"code": "S 1", "section": "S", "text": "Fine.", "tier": "A", "bindigs": ["alt_text"]}
    ]
    with pytest.raises(RubricPackError) as caught:
        loads(as_yaml(payload), origin="probe.yaml")
    assert "bindigs" in str(caught.value)


def test_several_problems_are_counted_rather_than_hidden_behind_the_first() -> None:
    payload = minimal()
    payload["criteria"] = [
        {"code": "S 1", "section": "S", "text": "One.", "tier": "Z"},
        {"code": "S 2", "section": "S", "text": "Two.", "tier": "Y"},
    ]
    with pytest.raises(RubricPackError, match=r"1 further problem\(s\)"):
        loads(as_yaml(payload), origin="probe.yaml")


def test_a_duplicate_row_code_is_rejected_because_lookups_key_on_it() -> None:
    payload = minimal()
    payload["criteria"] = [
        {"code": "S 1", "section": "S", "text": "One.", "tier": "A"},
        {"code": "S 1", "section": "S", "text": "The same code again.", "tier": "A"},
    ]
    with pytest.raises(RubricPackError, match="used twice") as caught:
        loads(as_yaml(payload), origin="probe.yaml")
    assert caught.value.path == "$.criteria[1]"


def test_a_row_in_an_undeclared_section_is_rejected_listing_the_declared_ones() -> None:
    payload = minimal()
    payload["criteria"] = [{"code": "S 1", "section": "ELSEWHERE", "text": "One.", "tier": "A"}]
    with pytest.raises(RubricPackError) as caught:
        loads(as_yaml(payload), origin="probe.yaml")
    assert caught.value.path == "$.criteria[0].section"
    assert "Declared: S" in str(caught.value)


def test_a_duplicate_section_code_is_rejected() -> None:
    payload = minimal()
    payload["sections"] = [
        {"code": "S", "title": "One"},
        {"code": "S", "title": "The same code again"},
    ]
    with pytest.raises(RubricPackError, match="declared twice"):
        loads(as_yaml(payload), origin="probe.yaml")


def test_bands_must_ascend() -> None:
    payload = minimal(
        rating_scale={
            "bands": [
                {"rating": "minor", "label": "Small", "up_to_hours": 2.0},
                {"rating": "moderate", "label": "Medium", "up_to_hours": 0.5},
                {"rating": "major", "label": "Large", "up_to_hours": None},
            ]
        }
    )
    with pytest.raises(RubricPackError, match="must ascend") as caught:
        loads(as_yaml(payload), origin="probe.yaml")
    assert caught.value.path == "$.rating_scale.bands[1].up_to_hours"


def test_the_top_band_must_be_open_ended() -> None:
    """A closed top band leaves a rolled-up cost above it with no rating at all, which is a row
    that silently reads as the instrument's default because the arithmetic fell off the end."""
    payload = minimal(
        rating_scale={
            "bands": [
                {"rating": "minor", "label": "Small", "up_to_hours": 0.5},
                {"rating": "major", "label": "Large", "up_to_hours": 8.0},
            ]
        }
    )
    with pytest.raises(RubricPackError, match="must be open-ended"):
        loads(as_yaml(payload), origin="probe.yaml")


def test_a_repeated_band_rating_is_rejected() -> None:
    payload = minimal(
        rating_scale={
            "bands": [
                {"rating": "minor", "label": "Small", "up_to_hours": 0.5},
                {"rating": "minor", "label": "Small again", "up_to_hours": None},
            ]
        }
    )
    with pytest.raises(RubricPackError, match="band rating is repeated"):
        loads(as_yaml(payload), origin="probe.yaml")


def test_a_bad_import_date_is_rejected_naming_the_field() -> None:
    payload = minimal(source={"imported_at": "the fourteenth"})
    with pytest.raises(RubricPackError) as caught:
        loads(as_yaml(payload), origin="probe.yaml")
    assert "imported_at" in caught.value.path


# -- lookups key on the code ---------------------------------------------------------------------


def test_every_lookup_keys_on_the_row_code() -> None:
    """Never on position. The reference instrument's assessment block runs 1, 2, 3, 4, 5, 7, so
    index-walking would report a finding against the wrong criterion and say nothing about it."""
    pack = tiny()
    assert pack.criterion("REA 3") is not None
    assert pack.criterion("REA 3").tier is Tier.A
    assert pack.criterion("REA 6") is None
    assert pack.codes[:3] == ("DES 1", "DES 2", "DES 3")


def test_a_null_priority_survives_loading_as_null() -> None:
    """Null, not "Important". The reference instrument's mobile block carries no flag at all and
    inventing one would put nine unprioritised rows in the middle of an action plan."""
    pack = tiny()
    assert pack.criterion("DES 3").priority is None
    assert pack.criterion("DES 1").priority == "Critical"


def test_a_null_priority_sorts_after_every_labelled_one() -> None:
    pack = tiny()
    assert pack.priority_rank("Critical") == 0
    assert pack.priority_rank("Worth doing") == 1
    assert pack.priority_rank("Something nobody declared") == 2
    assert pack.priority_rank(None) == 3


# -- sections, and the optional-render flag ------------------------------------------------------


def test_an_optional_section_renders_by_default() -> None:
    pack = tiny()
    assert pack.section("SML").optional is True
    assert pack.section("SML").enabled is True
    assert len(pack.rendered_criteria()) == 10
    assert pack.omitted_criteria() == ()


def test_a_pack_with_its_optional_section_flagged_off_omits_those_rows() -> None:
    """The flag roadmap item 08 needs: ``sections[].optional``.

    That item declares its mobile block optional and needs no more than that. Switching a section
    off drops its rows from the review; they are *reported* as omitted rather than vanishing,
    because a block nobody reviewed is information and not an absence.
    """
    pack = tiny().with_section_enabled("SML", False)
    assert [section.code for section in pack.rendered_sections()] == ["DES", "REA"]
    assert [section.code for section in pack.omitted_sections()] == ["SML"]
    rendered = {criterion.code for criterion in pack.rendered_criteria()}
    assert rendered == {
        "DES 1",
        "DES 2",
        "DES 3",
        "REA 1",
        "REA 2",
        "REA 3",
        "REA 4",
        "REA 5",
    }
    assert [criterion.code for criterion in pack.omitted_criteria()] == ["SML 1", "SML 2"]
    # The pack still declares all ten rows; only the rendering changed.
    assert len(pack.criteria) == 10
    assert "2 omitted with the SML section(s) switched off" in pack.statement()


def test_a_section_flagged_off_in_the_file_loads_that_way() -> None:
    payload = minimal()
    payload["sections"] = [
        {"code": "S", "title": "Kept"},
        {"code": "OPT", "title": "Dropped", "optional": True, "enabled": False},
    ]
    payload["criteria"] = [
        {"code": "S 1", "section": "S", "text": "Kept.", "tier": "A"},
        {"code": "OPT 1", "section": "OPT", "text": "Dropped.", "tier": "A"},
    ]
    pack = loads(as_yaml(payload), origin="probe.yaml")
    assert [criterion.code for criterion in pack.rendered_criteria()] == ["S 1"]


def test_a_mandatory_section_cannot_be_switched_off_in_the_file() -> None:
    """``enabled: false`` without ``optional: true`` is refused, so a block a course is actually
    reviewed against cannot be dropped by a typo in a YAML file."""
    payload = minimal()
    payload["sections"] = [{"code": "S", "title": "A section", "enabled": False}]
    with pytest.raises(RubricPackError, match="without 'optional: true'") as caught:
        loads(as_yaml(payload), origin="probe.yaml")
    assert caught.value.path == "$.sections[0]"


def test_a_mandatory_section_cannot_be_switched_off_at_runtime_either() -> None:
    with pytest.raises(RubricPackError, match="is not optional"):
        tiny().with_section_enabled("DES", False)


def test_switching_off_a_section_nobody_declared_is_an_error() -> None:
    with pytest.raises(KeyError):
        tiny().with_section_enabled("NOPE", False)


def test_a_section_switched_off_can_be_switched_back_on() -> None:
    pack = tiny().with_section_enabled("SML", False).with_section_enabled("SML", True)
    assert len(pack.rendered_criteria()) == 10


# -- round trip ----------------------------------------------------------------------------------


@pytest.mark.parametrize("path", [TINY, UNBOUND])
def test_a_valid_pack_round_trips_yaml_to_model_to_yaml_without_loss(path: Path) -> None:
    """YAML -> model -> YAML -> model, and the two models are equal.

    Comments are not preserved and are not meant to be: a hand-authored pack is edited by hand,
    and the dump exists for the importer's output and for diffing a re-import. What must survive
    is every value, including a null priority, an unrecognised binding and the source instrument's
    own identifiers.
    """
    first = load(path)
    again = loads(first.to_yaml(), origin=f"{path.name} (round trip)")
    assert again == first
    assert again.to_dict() == first.to_dict()
    # And it is stable: dumping twice gives the same bytes, so a re-import diffs cleanly.
    assert again.to_yaml() == first.to_yaml()


def test_the_round_trip_keeps_a_null_priority_null() -> None:
    """A round trip that quietly dropped a null and let the default fill it in would be the
    silent renumbering SPEC §11 is about, one field over."""
    again = loads(tiny().to_yaml(), origin="round trip")
    assert again.criterion("DES 3").priority is None
    assert again.criterion("REA 3").priority is None


def test_the_round_trip_keeps_the_source_identifiers() -> None:
    again = loads(tiny().to_yaml(), origin="round trip")
    assert again.criterion("REA 4").source_ids == {"printed_as": "7"}
    assert again.criterion("REA 4").note is not None


def test_the_dumped_yaml_is_block_style_and_reloadable_by_a_plain_parser() -> None:
    text = tiny().to_yaml()
    assert "criteria:" in text
    assert yaml.safe_load(text)["id"] == "tiny-synthetic"


# -- the default band table ----------------------------------------------------------------------


def test_the_default_bands_are_the_ones_printed_on_the_reference_instrument() -> None:
    """Not a guess and not a calibration. ``Instructional Designer`` row 3 carries the rating
    names and row 4 the ranges directly beneath them, and the RSI instrument prints the same
    three on its rows 6-7. Sufficiently Present has no band, because it is "no qualifying defects
    found" rather than zero hours."""
    scale = default_rating_scale()
    assert [(band.rating, band.up_to_hours) for band in scale.bands] == [
        ("minor", 0.5),
        ("moderate", 2.0),
        ("major", None),
    ]
    assert [band.range_label for band in scale.bands] == [
        "1/2 hour or less",
        "1/2-2 hours",
        "2+ hours",
    ]


@pytest.mark.parametrize(
    ("hours", "rating"),
    [
        (0.0, "minor"),
        (0.25, "minor"),
        (0.5, "minor"),
        (0.51, "moderate"),
        (2.0, "moderate"),
        (2.01, "major"),
        (40.0, "major"),
    ],
)
def test_a_band_boundary_is_inclusive_of_its_own_ceiling(hours: float, rating: str) -> None:
    """ "1/2 hour or less" includes a half hour. Asserted at the boundary rather than near it,
    because an off-by-one here moves a rating on a real review."""
    assert default_rating_scale().band_for(hours).rating == rating
