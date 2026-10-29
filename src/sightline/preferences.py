"""Preferences: the settings that are choices rather than credentials.

Four of them. Whether Sightline asks Canvas to build a fresh export of the course it is reviewing
is a deployment's decision, not a detail of how it is wired, so it is a preference a person sets
on the settings screen rather than a constant somebody has to find in the source. The model host
endpoint and the model chosen to do review work are the same kind of thing. So is the reviewer's
name every recorded rating carries (roadmap item 16): it is typed once, here, rather than on every
override.

**The model host endpoint is a preference and not a credential, and that is a decision.** The
Canvas base URL is held encrypted and redacted out of every log because it identifies the
institution (SPEC §11). A model host is a machine on the operator's own network, it carries no
identity, and :func:`sightline.logging.model_call` writes its host into every model-call log line
on purpose — a model layer that cannot say which host served a call is the silent failure mode
SPEC §06 is written against. So it is stored in the clear, next to the export toggle, and
:mod:`sightline.config` registers the Canvas base URL with the redaction registry and not this
one.

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

from .llm.client import host_of, normalize_endpoint
from .llm.errors import ModelHostNotConfiguredError
from .logging import get_logger
from .store import RunStore

log = get_logger("sightline.preferences")

#: Row names in ``app_settings``.
REQUEST_CONTENT_EXPORT = "request_content_export"
MODEL_HOST_ENDPOINT = "model_host_endpoint"
REVIEW_MODEL = "review_model"
REVIEWER = "reviewer"

#: The longest reviewer name stored. The override record's column is this wide
#: (:class:`sightline.models.RatingOverride`), so a longer one is refused here rather than cut there.
REVIEWER_MAX = 128

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


@dataclass(frozen=True)
class ModelHostPreference:
    """Where the local model host is, and where that was decided."""

    endpoint: str | None
    source: PreferenceSource

    title = "Local model host"

    #: What this endpoint is and is not, stated where somebody is about to type one in.
    note = (
        "The root of an Ollama server — scheme, host and port. Sightline reads the models "
        "installed there and their capabilities live, every time it looks. It is stored in the "
        "clear, unlike the Canvas credentials: a model host is a machine on your own network and "
        "every model call logs which host served it."
    )

    @property
    def configured(self) -> bool:
        return bool(self.endpoint)

    @property
    def host(self) -> str | None:
        """Host and port for display, without the scheme."""
        return host_of(self.endpoint) if self.endpoint else None


@dataclass(frozen=True)
class ReviewModelPreference:
    """Which installed model does review work.

    Held as a *name* rather than as a card, because a card is a live reading of the host and this
    is a stored choice. What that separation buys is the honest case: a model chosen last week and
    uninstalled since is reported as chosen-and-missing, rather than silently falling back to
    something else.
    """

    name: str | None
    source: PreferenceSource

    title = "Model for review work"

    @property
    def chosen(self) -> bool:
        return bool(self.name)


@dataclass(frozen=True)
class ReviewerPreference:
    """Who a rating recorded on the review screen is recorded as (roadmap item 16, Q1).

    Free text — a name, initials, an email address; whatever the person wants the record to say.
    Sightline has no login (:mod:`sightline.views`), so this is a statement, not an identity, and
    it is never used to decide what anybody may do. Read it through
    :func:`sightline.review.identity.current_reviewer`, the one lookup the override path calls,
    so that sign-in (roadmap item 40) can replace the source without touching the form or the
    record.
    """

    name: str | None
    source: PreferenceSource

    title = "Reviewer"

    note = (
        "The name every rating you record on the review screen is recorded with, beside the "
        "machine proposal it replaces. It is stored with the other preferences, in the clear, and "
        "it is not written into an exported report or workbook."
    )

    @property
    def set(self) -> bool:
        return bool(self.name)


class ReviewerNameError(ValueError):
    """A reviewer name that cannot be stored. Says why and what to do instead."""

    def __init__(self, summary: str, *, remedy: str) -> None:
        super().__init__(summary)
        self.summary = summary
        self.remedy = remedy


class PreferenceStore:
    """Reads and writes preferences, with an environment fallback."""

    def __init__(
        self,
        store: RunStore,
        *,
        env_request_content_export: bool | None = None,
        env_model_host_endpoint: str | None = None,
    ) -> None:
        self._store = store
        self._env_request_content_export = env_request_content_export
        self._env_model_host_endpoint = env_model_host_endpoint

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

    # -- the model host ------------------------------------------------------------------------

    def model_host(self) -> ModelHostPreference:
        stored = self._store.get_setting(MODEL_HOST_ENDPOINT)
        if stored:
            return ModelHostPreference(endpoint=stored, source=PreferenceSource.STORED)
        if self._env_model_host_endpoint:
            return ModelHostPreference(
                endpoint=self._env_model_host_endpoint, source=PreferenceSource.ENVIRONMENT
            )
        return ModelHostPreference(endpoint=None, source=PreferenceSource.DEFAULT)

    def set_model_host(self, endpoint: str) -> ModelHostPreference:
        """Store an endpoint, normalized to scheme, host and port.

        Normalizing on the way in rather than on the way out means the value in the database is
        the value that gets called, so a person reading the row and a person reading a log line
        are looking at the same string. A malformed endpoint raises rather than being stored:
        :class:`sightline.llm.errors.ModelHostNotConfiguredError` already carries the remedy.
        """
        normalized = normalize_endpoint(endpoint)
        self._store.put_setting(MODEL_HOST_ENDPOINT, normalized)
        log.info("preference_saved", name=MODEL_HOST_ENDPOINT, host=host_of(normalized))
        return self.model_host()

    def clear_model_host(self) -> bool:
        """Forget the stored endpoint so the environment, or nothing, applies again."""
        removed = self._store.delete_setting(MODEL_HOST_ENDPOINT)
        log.info("preference_cleared", name=MODEL_HOST_ENDPOINT, had_stored_value=removed)
        return removed

    # -- the model that does the work ----------------------------------------------------------

    def review_model(self) -> ReviewModelPreference:
        stored = self._store.get_setting(REVIEW_MODEL)
        if stored:
            return ReviewModelPreference(name=stored, source=PreferenceSource.STORED)
        return ReviewModelPreference(name=None, source=PreferenceSource.DEFAULT)

    def set_review_model(self, name: str) -> ReviewModelPreference:
        """Store the chosen model by name.

        No capability check happens here, on purpose. Capabilities are a live reading of the host
        (SPEC §06) and this store cannot make one; the screen that offers the choice offers only
        models that passed the filter, and the client checks again at call time. A check here
        would be a third answer, taken at the wrong moment, from data that had to be stale to
        exist.
        """
        chosen = name.strip()
        if not chosen:
            raise ModelHostNotConfiguredError(
                "No model was chosen.",
                remedy="Pick one of the usable models listed on the Settings screen.",
            )
        self._store.put_setting(REVIEW_MODEL, chosen)
        log.info("preference_saved", name=REVIEW_MODEL, value=chosen)
        return self.review_model()

    def clear_review_model(self) -> bool:
        removed = self._store.delete_setting(REVIEW_MODEL)
        log.info("preference_cleared", name=REVIEW_MODEL, had_stored_value=removed)
        return removed

    # -- who records a rating ------------------------------------------------------------------

    def reviewer(self) -> ReviewerPreference:
        """The stored reviewer name. No environment fallback: a name is a person's, not a host's."""
        stored = self._store.get_setting(REVIEWER)
        if stored:
            return ReviewerPreference(name=stored, source=PreferenceSource.STORED)
        return ReviewerPreference(name=None, source=PreferenceSource.DEFAULT)

    def set_reviewer(self, name: str) -> ReviewerPreference:
        """Store the name, with surrounding and repeated whitespace collapsed.

        Not logged: the log records that the name changed, never what it is.
        """
        cleaned = " ".join(name.split())
        if not cleaned:
            raise ReviewerNameError(
                "No name was entered, so nothing was saved.",
                remedy="Type the name a recorded rating should carry, then save.",
            )
        if len(cleaned) > REVIEWER_MAX:
            raise ReviewerNameError(
                f"That name is {len(cleaned)} characters; a recorded rating holds at most "
                f"{REVIEWER_MAX}.",
                remedy="Use a shorter form of the name, such as initials.",
            )
        self._store.put_setting(REVIEWER, cleaned)
        log.info("preference_saved", name=REVIEWER)
        return self.reviewer()

    def clear_reviewer(self) -> bool:
        removed = self._store.delete_setting(REVIEWER)
        log.info("preference_cleared", name=REVIEWER, had_stored_value=removed)
        return removed


__all__ = [
    "MODEL_HOST_ENDPOINT",
    "OFF",
    "ON",
    "REQUEST_CONTENT_EXPORT",
    "REVIEWER",
    "REVIEWER_MAX",
    "REVIEW_MODEL",
    "ExportPreference",
    "ModelHostPreference",
    "PreferenceSource",
    "PreferenceStore",
    "ReviewModelPreference",
    "ReviewerNameError",
    "ReviewerPreference",
]
