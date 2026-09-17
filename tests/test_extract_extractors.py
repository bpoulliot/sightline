"""The ten extractors themselves: what they declare, and the register they are allowed to use.

Two guards here are worth more than the rest. The first reads SPEC §05's own table out of
``docs/SPEC.md`` and checks the registered extractors against it, so the inventory and the spec
cannot drift apart silently. The second is a register lint over every string that reaches a person
— an extraction question that asked a model to judge something, or a drafted question that answered
itself, would pass every other test in this suite.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sightline.detect.base import Tier
from sightline.extract.base import Extractor, registry, registry_snapshot
from sightline.extract.extractors import INVENTORY
from sightline.model import ArtifactKind

#: A criterion code on any instrument Sightline ships a pack for. An extractor naming one would be
#: the coupling SPEC §04 exists to prevent.
CRITERION_CODE = re.compile(r"\b(?:COAL|GSOV|OREP|ACD|ENIN|ASFE|MOB)\s*\d", re.I)

#: Words that turn an extraction question into a request for a judgement.
JUDGEMENT_WORDS = (
    r"\brate\b",
    r"\brating\b",
    r"\bscore\b",
    r"\bjudge\b",
    r"\bquality\b",
    r"\bbest\b",
    r"\bshould\b",
    r"how well",
    r"how effectively",
    r"do you think",
    r"your opinion",
)

#: Words that make a sentence a verdict on a course or on the person who built it.
VERDICT_WORDS = (
    r"\bgood\b",
    r"\bpoor\b",
    r"\bweak\b",
    r"\bexcellent\b",
    r"\binsufficient\b",
    r"\binadequate\b",
    r"\bfails?\b",
    r"\blacks?\b",
    r"\bclearly\b",
    r"appears to",
    r"we recommend",
)


def offending(text: str, patterns: tuple[str, ...]) -> list[str]:
    return [pattern for pattern in patterns if re.search(pattern, text, re.I)]


# -- the inventory -------------------------------------------------------------------------------


def test_the_ten_extractors_are_registered() -> None:
    assert sorted(registry()) == [f"E{index:02d}" for index in range(1, 11)]
    assert len(INVENTORY) == 10


def test_the_registered_tiers_match_the_spec_table() -> None:
    """Read out of docs/SPEC.md rather than restated here, so the two cannot drift apart."""
    spec = (Path(__file__).resolve().parents[1] / "docs" / "SPEC.md").read_text(encoding="utf-8")
    block = spec.split("### Model-backed extraction")[1].split("### Human-only")[0]
    rows = [line.strip("|").split("|") for line in block.splitlines() if line.startswith("| E")]
    stated = {row[0].strip(): row[3].strip() for row in rows if len(row) >= 4}
    assert {key: stated[key] for key in sorted(stated) if key <= "E10"}
    for extractor_id, cls in registry().items():
        assert stated[extractor_id] == cls.tier.value, extractor_id


def test_every_extractor_declares_what_it_reads() -> None:
    for extractor_id, cls in registry().items():
        assert cls.title, extractor_id
        assert cls.ask, extractor_id
        assert cls.kinds, extractor_id
        assert cls.collections, extractor_id
        assert cls.tier in (Tier.B, Tier.C), extractor_id


def test_collections_are_derived_from_the_kinds_rather_than_repeated() -> None:
    """A subclass that narrows its kinds cannot leave a stale collection behind."""
    with registry_snapshot():

        class Narrow(Extractor):
            id = "E99"
            title = "A test extractor"
            kinds = (ArtifactKind.SYLLABUS,)
            ask = "Find every sentence about anything at all."

        assert Narrow.collections == ("syllabus",)


def test_an_extractor_may_not_declare_tier_a() -> None:
    """Tier A is computed by a parser. An extractor claiming it would launder a model answer."""
    with registry_snapshot():
        with pytest.raises(TypeError) as caught:

            class ParserWork(Extractor):
                id = "E98"
                title = "A test extractor"
                tier = Tier.A
                kinds = (ArtifactKind.PAGE,)
                ask = "Find every sentence about anything at all."

        assert "tier A" in str(caught.value)


def test_an_extractor_may_not_declare_tier_d() -> None:
    """Tier D is not in the artifacts at all, so asking a model is how a fabrication gets in."""
    with registry_snapshot():
        with pytest.raises(TypeError) as caught:

            class NotInTheCourse(Extractor):
                id = "E98"
                title = "A test extractor"
                tier = Tier.D
                kinds = (ArtifactKind.PAGE,)
                ask = "Find every sentence about anything at all."

        assert "tier D" in str(caught.value)


def test_an_extractor_missing_a_declaration_fails_at_class_definition() -> None:
    with registry_snapshot():
        with pytest.raises(TypeError) as caught:

            class Undeclared(Extractor):
                id = "E97"
                kinds = (ArtifactKind.PAGE,)
                ask = "Find every sentence about anything at all."

        assert "title" in str(caught.value)


def test_two_extractors_cannot_claim_one_id() -> None:
    with registry_snapshot():

        class First(Extractor):
            id = "E96"
            title = "A test extractor"
            kinds = (ArtifactKind.PAGE,)
            ask = "Find every sentence about anything at all."

        with pytest.raises(TypeError):

            class Second(Extractor):
                id = "E96"
                title = "Another test extractor"
                kinds = (ArtifactKind.PAGE,)
                ask = "Find every sentence about anything at all."


# -- what an extractor is allowed to say -----------------------------------------------------------


def test_no_extractor_names_a_rubric_criterion() -> None:
    """Detectors and extractors emit facts about the course; a pack decides what they answer."""
    for extractor_id, cls in registry().items():
        for field in (cls.id, cls.title, cls.ask):
            assert not CRITERION_CODE.search(field), f"{extractor_id}: {field}"


def test_no_extractor_declares_a_capability_tag() -> None:
    """capabilities.yaml says why: extraction reaches a row through the routing contract."""
    for cls in registry().values():
        assert not hasattr(cls, "capability_tag")


def test_no_extraction_question_asks_for_a_judgement() -> None:
    for extractor_id, cls in registry().items():
        assert offending(cls.ask, JUDGEMENT_WORDS) == [], f"{extractor_id}: {cls.ask}"


def test_every_extraction_question_asks_for_a_passage() -> None:
    for extractor_id, cls in registry().items():
        assert re.search(r"\b(sentence|passage)s?\b", cls.ask, re.I), extractor_id


def test_a_drafted_question_is_a_question_and_carries_no_verdict() -> None:
    for extractor_id, cls in registry().items():
        extractor = cls()
        if extractor.tier is not Tier.C:
            assert extractor.question_for([], {}) is None, extractor_id
            continue
        summary = extractor.summarise([])
        question = extractor.question_for([], summary)
        assert question is not None and question.endswith("?"), extractor_id
        assert offending(question, VERDICT_WORDS) == [], f"{extractor_id}: {question}"
        assert "Nothing here" in question, extractor_id


def test_a_drafted_question_proposes_no_rating() -> None:
    for cls in registry().values():
        extractor = cls()
        if extractor.tier is not Tier.C:
            continue
        question = extractor.question_for([], extractor.summarise([]))
        assert question is not None
        lowered = question.lower()
        for phrase in ("sufficiently present", "minor", "moderate", "major", "not applicable"):
            assert phrase not in lowered


def test_an_evidence_shape_has_nowhere_to_put_a_verdict() -> None:
    from sightline.extract.base import Evidence, ExtractorResult

    for shape in (Evidence, ExtractorResult):
        fields = set(getattr(shape, "__dataclass_fields__", {}))
        assert not fields & {"rating", "score", "band", "confidence", "verdict"}, shape


# -- what an extractor computes from a verified quote, in code -------------------------------------


def test_e09_reads_a_stated_penalty_including_the_per_cent_sign() -> None:
    """`10%` is how a late penalty is almost always written, and it was the one shape missed.

    The first regex ended in `\\b` after an alternation whose first branch is `%`. `%` is not a
    word character, so the boundary never matched and "Late work loses 10% per day" reported no
    figure — a computed field that was quietly wrong on the commonest case there is.
    """
    from sightline.extract.extractors import E09Grading

    extractor = E09Grading()
    for quote, figure in (
        ("Late work loses 10% per day.", "10%"),
        ("Late work loses 10 percent per day.", "10 percent"),
        ("Late work loses 10 per cent per day.", "10 per cent"),
        ("A 5 pts penalty applies.", "5 pts"),
        ("Each late day costs 2 points.", "2 points"),
    ):
        context = extractor.annotate(quote, None)  # type: ignore[arg-type]
        assert context["states_a_figure"] is True, quote
        assert context["figure"] == figure, quote

    for quote in ("Work is due at 11:59 p.m. on Sunday.", "You lose half a letter grade."):
        assert extractor.annotate(quote, None)["states_a_figure"] is False, quote  # type: ignore[arg-type]


def test_e09_separates_late_work_from_grading_in_general() -> None:
    from sightline.extract.extractors import E09Grading

    extractor = E09Grading()
    assert extractor.annotate("No late work is accepted.", None)["mentions_late_work"] is True  # type: ignore[arg-type]
    assert (
        extractor.annotate("Grades are posted in the gradebook.", None)["mentions_late_work"]  # type: ignore[arg-type]
        is False
    )


def test_e03_reads_a_stated_period_and_does_not_invent_one() -> None:
    from sightline.extract.extractors import E03Interaction

    extractor = E03Interaction()
    for quote, period in (
        ("I reply to email within 24 hours on weekdays.", "within 24 hours"),
        ("Feedback is returned within one week.", "within one week"),
        ("Marks are posted in 2 business days.", "in 2 business days"),
    ):
        context = extractor.annotate(quote, None)  # type: ignore[arg-type]
        assert context["states_a_period"] is True, quote
        assert context["period"] == period, quote

    context = extractor.annotate("I reply to messages as soon as I can.", None)  # type: ignore[arg-type]
    assert context["states_a_period"] is False
    assert context["period"] is None


def test_e01_and_e06_read_the_task_verb_off_the_quote_rather_than_the_document() -> None:
    from sightline.extract.extractors import E01Objectives

    context = E01Objectives().annotate("Students will analyze two proposals.", None)  # type: ignore[arg-type]
    assert context["bloom_level"] == "analyze"
    assert E01Objectives().annotate("Read chapter three.", None)["task_verb"] is None  # type: ignore[arg-type]


def test_an_extractor_cannot_replace_its_own_verification() -> None:
    """`accept()` is the one line between a model and a fabricated quote (SPEC §11).

    Enforced at class-definition time rather than reviewed, for the same reason `Detector.run` is:
    a check a subclass can switch off is a check that will eventually be switched off by whoever
    is trying to get their extractor to return something.
    """
    with registry_snapshot():
        with pytest.raises(TypeError) as caught:

            class Lenient(Extractor):
                id = "E95"
                title = "A test extractor"
                kinds = (ArtifactKind.PAGE,)
                ask = "Find every sentence about anything at all."

                def accept(self, finding, passage):  # type: ignore[override]
                    return object()  # pragma: no cover - the raise happens at definition

        assert "final" in str(caught.value)


def test_no_shipped_extractor_overrides_it() -> None:
    for extractor_id, cls in registry().items():
        assert "accept" not in cls.__dict__, extractor_id
