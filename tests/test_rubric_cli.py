"""``python -m sightline.rubric``: the coverage report, the importer and the tag catalogue.

The documented commands are asserted to run verbatim, because a documented command nobody runs is
a command that stopped working three releases ago. What they print is asserted to be counts: the
governing rule of this project is that every machine-authored line is a computed number, a verbatim
quote with a location, or an explicit statement that nothing was found, and a terminal is the
easiest place for that to slip into an adjective.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sightline.rubric.__main__ import build_parser, main
from sightline.rubric.pack import loads

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rubric"
TINY = FIXTURES / "tiny-pack.yaml"
UNBOUND = FIXTURES / "unbound-pack.yaml"
RSI = REPO / "rubrics" / "source" / "UCCS OSCQR RSI Review.xlsm"


def test_the_coverage_command_prints_the_headline_sentence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["coverage", "--pack", str(TINY)]) == 0
    out = capsys.readouterr().out
    assert "4 of 10 criteria have deterministic coverage" in out
    assert "3 are evidence-only" in out


def test_the_coverage_command_on_an_unbound_pack_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A rubric with nothing mapped is not an error condition, so the command does not treat it as
    one. Exiting non-zero here would make an ordinary state look like a failure to any script."""
    assert main(["coverage", "--pack", str(UNBOUND)]) == 0
    out = capsys.readouterr().out
    assert "0 of 4 criteria have deterministic coverage" in out
    assert "4 are evidence-only" in out
    for word in ("degraded", "unsupported", "invalid", "error"):
        assert word not in out.lower()


def test_the_coverage_command_can_switch_an_optional_section_off(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["coverage", "--pack", str(TINY), "--without-section", "SML"]) == 0
    out = capsys.readouterr().out
    assert "2 more are omitted with a switched-off section" in out
    assert "SML:" not in out


def test_the_coverage_command_refuses_to_switch_off_a_mandatory_section(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["coverage", "--pack", str(TINY), "--without-section", "DES"]) == 2
    assert "is not optional" in capsys.readouterr().err


def test_the_coverage_command_reports_an_invalid_pack_by_path(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("schema_version: 1\nid: x\n", encoding="utf-8")
    assert main(["coverage", "--pack", str(broken)]) == 1
    error = capsys.readouterr().err
    assert "broken.yaml" in error


def test_the_coverage_command_can_print_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["coverage", "--pack", str(TINY), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["criteria"] == 10
    assert payload["ok"] is True


def test_the_import_command_writes_a_pack_that_loads_again(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The loop that matters: workbook in, pack out, pack back in. If the written file did not
    reload, the importer would be producing something only the importer understands."""
    assert (
        main(
            [
                "import",
                "--workbook",
                str(RSI),
                "--id",
                "cli-probe",
                "--name",
                "Probe",
                "--version",
                "1",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    pack = loads(captured.out, origin="cli")
    assert len(pack.criteria) == 18
    assert all(criterion.bindings == () for criterion in pack.criteria)
    # The report goes to stderr so the pack on stdout stays pipeable.
    assert "18 criterion row(s) read" in captured.err
    assert "bindings: none" in captured.err
    assert "0 of 18 criteria have deterministic coverage" in captured.err


def test_the_import_command_writes_no_file_of_its_own() -> None:
    """There is no ``--out``, and that is the data-root containment guard doing its job.

    ``sightline.paths`` is the only module in the package allowed to create a writable path, and
    ``tests/test_paths.py`` enforces it by scanning the source tree — it caught an earlier draft of
    this command. Shell redirection does the same job and leaves the choice of where the file lands
    with the person running it.
    """
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["import", "--workbook", str(RSI), "--out", "somewhere.yaml"])


def test_the_import_command_exits_non_zero_when_it_cannot_read_the_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A zero-row pack is a failed import, and a command that exits 0 over one is a command a
    script will treat as an imported rubric."""
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"not a spreadsheet")
    assert main(["import", "--workbook", str(broken)]) == 1
    assert "cannot be read as a spreadsheet" in capsys.readouterr().err


def test_the_tags_command_lists_the_vocabulary_and_what_implements_each_concept(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["tags"]) == 0
    out = capsys.readouterr().out
    assert "capability tag(s) in" in out
    assert "alt_text  [D21]" in out
    # A concept awaiting a detector is listed as such. Ordinary, and visible.
    assert "due_date_coverage  [no detector yet]" in out
    # Every shipped detector appears against the concept it declared, and no concept is described
    # by a rubric criterion — the catalogue is what a person authoring a pack reads.
    for detector_id in ("D21", "D22", "D23", "D30"):
        assert f"[{detector_id}]" in out
    for criterion in ("ACD 8", "GSOV 5", "OPEN SUNY"):
        assert criterion not in out


# ---------------------------------------------------------------------------------------------
# The mapping file and the shipped packs (roadmap item 05)
# ---------------------------------------------------------------------------------------------

MAPS = REPO / "rubrics" / "maps"
REFERENCE_WORKBOOK = REPO / "rubrics" / "source" / "UCCS OSCQR 3.1.3.xlsx"


def test_the_import_command_applies_a_mapping_file(capsys: pytest.CaptureFixture[str]) -> None:
    """The documented regeneration command, run verbatim. stdout is the pack and stderr is the
    report, so the command pipes and the report is still readable."""
    assert (
        main(
            [
                "import",
                "--workbook",
                str(REFERENCE_WORKBOOK),
                "--map",
                str(MAPS / "uccs-oscqr-3-1-3.yaml"),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    pack = loads(captured.out, origin="<imported>")
    assert len(pack.criteria) == 68
    assert pack.id == "uccs-oscqr-3-1-3"
    # The report says what the mapping reached, so an import can be checked rather than trusted.
    assert "mapping: uccs-oscqr-3-1-3.yaml reached 68 of 68 row(s)" in captured.err
    assert "Join key: column F, 68 distinct value(s)" in captured.err
    assert "39 are bound to a capability no detector implements yet" in captured.err


def test_an_unreadable_mapping_file_exits_two_without_writing_a_pack(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("critera: {}\n", encoding="utf-8")
    assert main(["import", "--workbook", str(RSI), "--map", str(broken)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "critera" in captured.err


def test_the_packs_command_reports_each_shipped_pack_separately(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Two peers, listed with their own counts. Neither is a default and neither is described in
    terms of the other."""
    assert main(["packs"]) == 0
    out = capsys.readouterr().out
    assert "2 rubric pack(s)" in out
    assert "uccs-oscqr-3-1-3" in out
    assert "uccs-oscqr-rsi" in out
    assert "10 of 68 criteria have deterministic coverage" in out
    assert "5 of 18 criteria have deterministic coverage" in out
    # The three states are printed apart, and the middle one is named for what it is.
    assert "are bound to a capability no detector implements yet" in out
    assert "are evidence-only" in out
    for word in ("degraded", "unsupported", "incomplete"):
        assert word not in out.casefold()
