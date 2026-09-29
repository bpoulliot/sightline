"""Sightline's voice: the one place a finding becomes English.

**The model does not write prose** (decided 2026-09-08, roadmap item 15). Every sentence in every
artifact — the workbook, the PDF, the review screen — is a variant from a committed phrase bank with
computed facts put into it: counts, locations, instrument labels, and item 10's verified quotes,
inserted verbatim. Nothing a language model composed reaches a report.

:mod:`~sightline.voice.bank`
    ``phrases.yaml``: the bank, its contract with the renderer, and deterministic rotation.
:mod:`~sightline.voice.lint`
    ``register.yaml``: the register lint — no hedge in Tier A, no verdict in Tier C, no grading
    vocabulary and no whole-course judgement anywhere.
:mod:`~sightline.voice.render`
    Rows to sentences, dispatched on tier and coverage state.
:mod:`~sightline.voice.narrative`
    The closing narrative's four slots, from computed aggregates.
:mod:`~sightline.voice.agreement`
    ``agreement.yaml``: how often independent human reviewers choose the same rating, stated with
    its limits — a fact about the task, never about Sightline (roadmap item 24).

``python -m sightline.voice --bundle examples/sample-course --pack uccs-oscqr-3-1-3`` prints a
plain-text rendering for reading and diffing.
"""

from .bank import PhraseBank, PhraseBankError, default_bank, lint_bank, load_bank
from .lint import Register, RegisterConfigError, Violation, default_register, load_register
from .narrative import Narrative, Slot, build_narrative
from .render import (
    RenderedReview,
    RenderedRow,
    Sentence,
    Voice,
    render_review,
    render_row,
)

__all__ = [
    "Narrative",
    "PhraseBank",
    "PhraseBankError",
    "Register",
    "RegisterConfigError",
    "RenderedReview",
    "RenderedRow",
    "Sentence",
    "Slot",
    "Violation",
    "Voice",
    "build_narrative",
    "default_bank",
    "default_register",
    "lint_bank",
    "load_bank",
    "load_register",
    "render_review",
    "render_row",
]
