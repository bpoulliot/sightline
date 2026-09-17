"""Preferences: the settings that are choices rather than credentials.

One so far. Whether Sightline asks Canvas to build a fresh export of the course it is reviewing is
a deployment's decision, not a detail of how it is wired, so it is a preference a person sets on
the settings screen rather than a constant somebody has to find in the source.

**Precedence is the credential store's, deliberately** (:mod:`sightline.credentials`): a value set
in the UI wins over the environment, and the environment wins over the built-in default. A
container can therefore be started with the request switched off and stay that way, and a designer
who then changes it on the screen is not silently overridden on the next restart.

**Off is a supported configuration, not a degraded one.** An institution with a no-writes policy
switches it off and keeps a working tool: acquisition falls back to reading an export the course
already has and reports what to do when there is none. What it loses is the checks that need a
document's own bytes, and those report what was not examined rather than reporting a clean course.

Nothing here is secret, so nothing here is encrypted — see :class:`sightline.models.AppSetting` for
why that is the opposite of the neighbouring table and why it is right.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .logging import get_logger
from .store import RunStore

log = get_logger("sightline.preferences")

#: Row name in ``app_settings``.
REQUEST_CONTENT_EXPORT = "request_content_export"

#: Stored spellings. Two words rather than ``1``/``0``, because the value is read by people
#: opening the database as often as by this module.
ON = "on"
OFF = "off"


class PreferenceSource(StrEnum):
    """Where the value in force came from. The answer to "why is this not what I set?"."""

    STORED = "stored"
    ENVIRONMENT = "environment"
    DEFAULT = "default"


@dataclass(frozen=True)
class ExportPreference:
    """Whether Sightline asks Canvas to build a fresh export, and where that was decided.

    The prose lives on the object rather than in the template because it is the same wording in
    three places — the screen, the README and the CLI's ``--help`` — and three copies of a claim
    about what a tool does to somebody's Canvas instance is two too many.
    """

    enabled: bool
    source: PreferenceSource
    updated_at: datetime | None = None

    #: The plain-language label. A toggle named after its field tells nobody what it does.
    title = "Ask Canvas to build a fresh export of the course"

    #: What happens with it on, stated as the thing Canvas will actually do.
    when_on = (
        "Sightline asks Canvas to package a copy of each course it reviews and waits for it, "
        "which takes from a few seconds to several minutes. The packaged copy appears in the "
        "course's export list under Settings, Export Course Content. No course content is "
        "modified and nothing a learner or an instructor sees changes."
    )

    #: What happens with it off, stated as a supported configuration rather than a degradation.
    when_off = (
        "Sightline reads an export the course already has, and reports the steps to create one "
        "when there is none. Every request it makes to Canvas is then a read. The checks that "
        "need a document's own bytes report that they had nothing to examine, rather than "
        "reporting the course as clean."
    )

    @property
    def state(self) -> str:
        return ON if self.enabled else OFF

    @property
    def consequence(self) -> str:
        return self.when_on if self.enabled else self.when_off


class PreferenceStore:
    """Reads and writes preferences, with an environment fallback."""

    def __init__(self, store: RunStore, *, env_request_content_export: bool | None = None) -> None:
        self._store = store
        self._env_request_content_export = env_request_content_export

    def request_content_export(self) -> ExportPreference:
        stored = self._store.get_setting(REQUEST_CONTENT_EXPORT)
        if stored is not None:
            return ExportPreference(enabled=stored == ON, source=PreferenceSource.STORED)
        if self._env_request_content_export is not None:
            return ExportPreference(
                enabled=self._env_request_content_export, source=PreferenceSource.ENVIRONMENT
            )
        # On by default. Without it every check that reads a document's own bytes is dead for any
        # course whose teacher has not exported it by hand, which is most of them.
        return ExportPreference(enabled=True, source=PreferenceSource.DEFAULT)

    def set_request_content_export(self, enabled: bool) -> ExportPreference:
        self._store.put_setting(REQUEST_CONTENT_EXPORT, ON if enabled else OFF)
        log.info("preference_saved", name=REQUEST_CONTENT_EXPORT, value=ON if enabled else OFF)
        return self.request_content_export()

    def clear_request_content_export(self) -> bool:
        """Forget the stored value so the environment, or the default, applies again."""
        removed = self._store.delete_setting(REQUEST_CONTENT_EXPORT)
        log.info("preference_cleared", name=REQUEST_CONTENT_EXPORT, had_stored_value=removed)
        return removed


__all__ = [
    "OFF",
    "ON",
    "REQUEST_CONTENT_EXPORT",
    "ExportPreference",
    "PreferenceSource",
    "PreferenceStore",
]
