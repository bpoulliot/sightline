"""Sightline — an automated first pass over online Canvas courses against a review rubric.

Sightline gathers evidence, computes remediation effort, and hands an instructional designer a
pre-populated action plan. It does not grade courses and it does not grade faculty.

The governing design rule, which every module here is subject to: the model produces **checkable
artifacts, never verdicts**. Every machine-authored line of output is a computed count, a verbatim
quote with a location, or an explicit statement that no evidence was found.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
