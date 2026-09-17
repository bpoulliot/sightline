"""The extraction layer: model-located, schema-bound, string-verified quotes.

Ten extractors (SPEC §05, E01–E10), each asking one near-objective question of one document at a
time, returning a fixed JSON shape carrying quotes and nothing else, every quote checked against
the source before it is accepted.

Three modules carry the item's weight and are worth reading in this order:

:mod:`sightline.extract.verify`
    The string match that stands between a model and a fabricated quote, and the list of
    normalizations that are folds of the same characters rather than a loosening of the check.
:mod:`sightline.extract.schema`
    The one shape a response may take. Violations are rejected, never coerced, and an empty list
    is a complete answer rather than a failure.
:mod:`sightline.extract.base`
    What an extractor may produce — a quote, a location computed by the harness, and counts — and
    the four outcomes that must never be blurred into each other.
"""

from __future__ import annotations

from .base import (
    BODY_KINDS,
    CallRecord,
    Corpus,
    Discarded,
    Evidence,
    Extractor,
    ExtractorResult,
    Failure,
    FailureKind,
    extractors,
    prompt_for,
    registry,
    registry_snapshot,
)
from .extractors import INVENTORY
from .passages import WORD_BUDGET, Block, Passage, passages_for
from .runner import (
    ExtractionReport,
    RowExtraction,
    RowExtractor,
    answer_request,
    answer_requests,
    run_extractor,
    run_extractors,
    thinking_for,
)
from .schema import RESPONSE_SCHEMA, RawFinding, SchemaViolation, parse_response
from .verify import Match, Normalized, normalize, verify

__all__ = [
    "BODY_KINDS",
    "INVENTORY",
    "RESPONSE_SCHEMA",
    "WORD_BUDGET",
    "Block",
    "CallRecord",
    "Corpus",
    "Discarded",
    "Evidence",
    "ExtractionReport",
    "Extractor",
    "ExtractorResult",
    "Failure",
    "FailureKind",
    "Match",
    "Normalized",
    "Passage",
    "RawFinding",
    "RowExtraction",
    "RowExtractor",
    "SchemaViolation",
    "answer_request",
    "answer_requests",
    "extractors",
    "normalize",
    "parse_response",
    "passages_for",
    "prompt_for",
    "registry",
    "registry_snapshot",
    "run_extractor",
    "run_extractors",
    "thinking_for",
    "verify",
]
