"""The ten extractors of SPEC §05, E01–E10. Each one asks a single near-objective question.

**What makes these questions safe to ask a small local model.** None of them is the criterion.
"Do activities develop higher-order thinking?" is a judgement with no attractor, and a model asked
it answers fluently and unfalsifiably. "Find every sentence that states what a learner must
produce" is a locate-and-copy task, checkable against the page in seconds, and wrong in ways
verification catches. SPEC §01 builds the first out of the second and a lookup table; E01 is that
worked example and every other extractor here follows its shape.

**None of these names a criterion, and none declares a capability tag.** The criteria in SPEC §05's
table are documentation of the reference pack, not a property of anything here — see
:mod:`sightline.extract.base` and the note at the end of ``src/sightline/capabilities.yaml``.

**Tier C extractors draft a question and never answer it.** They assemble the same verified quotes
plus the counts that describe them, and end in a question mark addressed to the reviewer's own
instrument. A Tier C extractor that returned a statement about the course would be issuing exactly
the verdict the tier exists to withhold.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..detect.base import Tier
from ..model import ArtifactKind
from . import bloom
from .base import BODY_KINDS, Evidence, Extractor
from .passages import Passage

#: Kinds that carry a graded task. E01 and E05 read these as well as pages, because the sentence
#: stating what a learner must produce is usually on the assignment rather than the page about it.
ACTIVITY_KINDS: tuple[ArtifactKind, ...] = (
    ArtifactKind.ASSIGNMENT,
    ArtifactKind.QUIZ,
    ArtifactKind.DISCUSSION,
)

#: A stated turnaround: "within 24 hours", "in 2 business days", "by the end of the week".
_TURNAROUND = re.compile(
    r"\b(?:within|in|after|no later than|up to)\s+"
    r"(?:\d{1,3}|one|two|three|four|five|six|seven|ten|twenty-four|forty-eight)\s*"
    r"(?:-|\s)?(?:business\s+|working\s+|calendar\s+)?"
    r"(hours?|days?|weeks?|hrs?)\b",
    re.I,
)

#: Words that mark a passage as being about work handed in late or not at all.
_LATE_WORDS = ("late", "overdue", "past due", "extension", "resubmit", "make-up", "makeup")

#: A stated penalty: "10%", "half a letter grade", "10 points".
_PENALTY = re.compile(r"\b\d{1,3}\s*(?:%|per\s?cent|percent|points?|pts?)\b", re.I)


class E01Objectives(Extractor):
    """The worked example of SPEC §01. Extract the sentence; the table takes the verb."""

    id = "E01"
    title = "Sentences stating what a learner will be able to do, or must produce"
    tier = Tier.B
    kinds = BODY_KINDS
    ask = (
        "Find every sentence that states what a learner will be able to do, or what a learner "
        "must produce, submit or complete. These are sentences like 'By the end of this module "
        "you will be able to …', 'Students will …', 'Write a …', 'Post your …'."
    )

    def annotate(self, quote: str, passage: Passage) -> Mapping[str, Any]:
        """The task verb, taken from the verified quote by :mod:`sightline.extract.bloom`.

        Computed from the quote in code, not asked of the model: the schema has no field for it,
        which is the point — the model located a sentence and a table classified it.
        """
        match = bloom.classify(quote)
        if match is None:
            return {"task_verb": None, "bloom_level": None}
        return {
            "task_verb": match.verb,
            "task_verb_as_written": match.written,
            "bloom_level": match.level,
        }

    def summarise(self, evidence: Sequence[Evidence]) -> Mapping[str, Any]:
        levels = [item.context.get("bloom_level") for item in evidence]
        return {
            "task_verb_distribution": bloom.distribution(list(levels)),
            "statements": len(evidence),
        }


class E02Alignment(Extractor):
    id = "E02"
    title = "Sentences tying content, an activity or an assessment to a stated objective"
    tier = Tier.B
    kinds = BODY_KINDS
    ask = (
        "Find every sentence that connects something in the course — a reading, a video, an "
        "activity, an assignment, a quiz — to a stated learning objective, outcome or goal. "
        "These are sentences like 'This reading supports objective 2' or 'This assignment "
        "addresses the second course outcome'."
    )


class E03Interaction(Extractor):
    id = "E03"
    title = "Sentences stating interaction expectations or a response time"
    tier = Tier.B
    kinds = (
        ArtifactKind.SYLLABUS,
        ArtifactKind.PAGE,
        ArtifactKind.ANNOUNCEMENT,
        ArtifactKind.DISCUSSION,
    )
    ask = (
        "Find every sentence stating how quickly the instructor will reply to a message or return "
        "feedback on submitted work, or stating how often learners are expected to take part. "
        "These are sentences like 'I reply to email within 24 hours' or 'Post twice a week'."
    )

    def annotate(self, quote: str, passage: Passage) -> Mapping[str, Any]:
        """Whether the quote states a period of time, and which one. A regex over the quote."""
        match = _TURNAROUND.search(quote)
        return {"states_a_period": match is not None, "period": match.group(0) if match else None}

    def summarise(self, evidence: Sequence[Evidence]) -> Mapping[str, Any]:
        return {
            "statements": len(evidence),
            "stating_a_period": sum(1 for item in evidence if item.context.get("states_a_period")),
        }


class E04Community(Extractor):
    id = "E04"
    title = "Sentences describing an activity in which learners interact with each other"
    tier = Tier.B
    kinds = BODY_KINDS
    ask = (
        "Find every sentence describing something learners do with or for each other — "
        "introductions, peer review, group work, replying to classmates, a shared document. "
        "These are sentences like 'Reply to two classmates' or 'Your group will present'."
    )


class E05Authentic(Extractor):
    id = "E05"
    title = "Sentences framing a task in a setting, audience or practice outside the course"
    tier = Tier.C
    kinds = (*ACTIVITY_KINDS, ArtifactKind.PAGE)
    ask = (
        "Find every sentence that describes a task in terms of something outside the course — a "
        "workplace, a client, a professional practice, a public audience, a real dataset, a case "
        "that actually happened. Quote the sentence describing the task, not the surrounding text."
    )

    def summarise(self, evidence: Sequence[Evidence]) -> Mapping[str, Any]:
        return {
            "statements": len(evidence),
            "artifacts": len({item.location.artifact_id for item in evidence}),
        }

    def question_for(self, evidence: Sequence[Evidence], summary: Mapping[str, Any]) -> str:
        return (
            f"{summary['statements']} passage(s) across {summary['artifacts']} artifact(s) name a "
            "setting, an audience or a practice outside the course, quoted above with their "
            "locations. Nothing here reads them. Against the criterion in front of you, do this "
            "course's tasks carry the real-world framing it asks for?"
        )


class E06Progression(Extractor):
    id = "E06"
    title = "Sentences stating what learners do, read module by module"
    tier = Tier.C
    kinds = (ArtifactKind.PAGE, *ACTIVITY_KINDS)
    ask = (
        "Find every sentence stating what a learner will do or produce in this part of the course. "
        "Quote the sentence itself, not the heading above it."
    )

    def annotate(self, quote: str, passage: Passage) -> Mapping[str, Any]:
        """The module the artifact sits in, and the task verb. Both read off, neither judged."""
        module = passage.artifact.module_path[0] if passage.artifact.module_path else None
        match = bloom.classify(quote)
        return {
            "module": module,
            "task_verb": match.verb if match else None,
            "bloom_level": match.level if match else None,
        }

    def summarise(self, evidence: Sequence[Evidence]) -> Mapping[str, Any]:
        """The verbs found, grouped by module, in the course's own order.

        In the course's order and not in the taxonomy's, because the question is whether the work
        changes *across the course*, and sorting by level would answer it before a human read it.
        """
        modules: dict[str, list[str | None]] = {}
        for item in evidence:
            module = item.context.get("module") or "not in a module"
            modules.setdefault(module, []).append(item.context.get("bloom_level"))
        return {
            "statements": len(evidence),
            "modules": len(modules),
            "by_module": {module: bloom.distribution(levels) for module, levels in modules.items()},
        }

    def question_for(self, evidence: Sequence[Evidence], summary: Mapping[str, Any]) -> str:
        return (
            f"{summary['statements']} passage(s) state what learners do, across "
            f"{summary['modules']} module(s), with the task verbs counted per module above in the "
            "course's own order. Nothing here reads the sequence. Against the criterion in front "
            "of you, does the work this course asks for develop across it?"
        )


class E07Sources(Extractor):
    id = "E07"
    title = "Passages naming the source, author or creator of a learning material"
    tier = Tier.C
    kinds = (ArtifactKind.PAGE, ArtifactKind.SYLLABUS, ArtifactKind.ASSIGNMENT)
    ask = (
        "Find every passage that names where a learning material came from — an author, a "
        "publisher, a journal, an organisation, a repository, a licence line, a citation. Quote "
        "the passage naming the source."
    )

    def summarise(self, evidence: Sequence[Evidence]) -> Mapping[str, Any]:
        return {
            "citations": len(evidence),
            "artifacts": len({item.location.artifact_id for item in evidence}),
        }

    def question_for(self, evidence: Sequence[Evidence], summary: Mapping[str, Any]) -> str:
        return (
            f"{summary['citations']} passage(s) across {summary['artifacts']} artifact(s) name a "
            "source, an author or a creator, quoted above with their locations. Nothing here reads "
            "the range of them. Against the criterion in front of you, do this course's materials "
            "draw on the range of sources and perspectives it asks for?"
        )


class E08Criteria(Extractor):
    id = "E08"
    title = "Sentences stating how submitted work will be evaluated, or showing an example of it"
    tier = Tier.B
    kinds = (*ACTIVITY_KINDS, ArtifactKind.PAGE, ArtifactKind.SYLLABUS)
    ask = (
        "Find every sentence that tells a learner what their work will be marked against, or "
        "points them at an example of acceptable work. These are sentences like 'You will be "
        "marked on …', 'The rubric for this assignment …', 'A sample paper is attached'."
    )


class E09Grading(Extractor):
    id = "E09"
    title = "Sentences stating a grading policy, or a policy on late or missed work"
    tier = Tier.B
    kinds = (ArtifactKind.SYLLABUS, ArtifactKind.PAGE, ArtifactKind.ASSIGNMENT)
    ask = (
        "Find every sentence stating a policy about grades or about work handed in late, missed "
        "or resubmitted. These are sentences like 'Late work loses 10% per day', 'Grades are "
        "posted within a week', 'I do not accept work after the final day of class'."
    )

    def annotate(self, quote: str, passage: Passage) -> Mapping[str, Any]:
        """Which of the two things the quote is about, and any figure in it. Read off the quote."""
        lowered = quote.lower()
        penalty = _PENALTY.search(quote)
        return {
            "mentions_late_work": any(word in lowered for word in _LATE_WORDS),
            "states_a_figure": penalty is not None,
            "figure": penalty.group(0) if penalty else None,
        }

    def summarise(self, evidence: Sequence[Evidence]) -> Mapping[str, Any]:
        return {
            "statements": len(evidence),
            "about_late_work": sum(
                1 for item in evidence if item.context.get("mentions_late_work")
            ),
            "stating_a_figure": sum(1 for item in evidence if item.context.get("states_a_figure")),
        }


class E10Scaffolding(Extractor):
    id = "E10"
    title = "Sentences telling a learner how to use a tool the course requires"
    tier = Tier.B
    kinds = (ArtifactKind.PAGE, ArtifactKind.SYLLABUS, *ACTIVITY_KINDS)
    ask = (
        "Find every sentence that tells a learner how to use a piece of technology the course "
        "requires, where to practise with it, or where to get help with it. These are sentences "
        "like 'Install the plug-in before week 2', 'A tutorial for the simulator is linked below', "
        "'Contact the help desk if the recorder does not start'."
    )


#: Every extractor of SPEC §05, in id order. Imported for the side effect of registration; listed
#: here as well so that a reader sees the inventory without walking the registry.
INVENTORY: tuple[type[Extractor], ...] = (
    E01Objectives,
    E02Alignment,
    E03Interaction,
    E04Community,
    E05Authentic,
    E06Progression,
    E07Sources,
    E08Criteria,
    E09Grading,
    E10Scaffolding,
)


__all__ = [
    "ACTIVITY_KINDS",
    "INVENTORY",
    "E01Objectives",
    "E02Alignment",
    "E03Interaction",
    "E04Community",
    "E05Authentic",
    "E06Progression",
    "E07Sources",
    "E08Criteria",
    "E09Grading",
    "E10Scaffolding",
]
