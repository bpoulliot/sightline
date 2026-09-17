"""Detectors: the deterministic half of a review.

A detector reads the normalized course document and emits typed findings — a computed count, a
verbatim quote with a location, or an explicit record that something could not be read. It never
emits a judgement, and it never names a rubric criterion: rubric packs project findings onto rows
(SPEC §04), so one ``link_text`` detector fires once and every pack renders it wherever its own
mapping says.

Importing this package registers every detector it ships. ``registry()`` is the list, and the
integrity guard in :mod:`sightline.detect.integrity` walks it: a registered detector with no fixture
test behind it fails the build by name.
"""

from .accessibility import (
    CaptionTrack,
    ColourAsSoleCarrier,
    ContrastRatio,
    DocumentAccessibility,
    HeadingStructure,
    ImageAltText,
    LinkText,
    TableSemantics,
    TextRenderedAsImage,
    TypeSizeAndFamily,
)
from .base import (
    Detector,
    DetectorResult,
    Finding,
    Location,
    NothingExamined,
    ResultState,
    Severity,
    Survey,
    Tier,
    Undetermined,
    detectors,
    registry,
    registry_snapshot,
)
from .runner import DetectionReport, run_detectors, tier_counts
from .sources import ExportFileSource, FileSource, MappingFileSource, NoFileSource

__all__ = [
    "CaptionTrack",
    "ColourAsSoleCarrier",
    "ContrastRatio",
    "DetectionReport",
    "Detector",
    "DetectorResult",
    "DocumentAccessibility",
    "ExportFileSource",
    "FileSource",
    "Finding",
    "HeadingStructure",
    "ImageAltText",
    "LinkText",
    "Location",
    "MappingFileSource",
    "NoFileSource",
    "NothingExamined",
    "ResultState",
    "Severity",
    "Survey",
    "TableSemantics",
    "TextRenderedAsImage",
    "Tier",
    "TypeSizeAndFamily",
    "Undetermined",
    "detectors",
    "registry",
    "registry_snapshot",
    "run_detectors",
    "tier_counts",
]
