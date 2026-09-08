"""Canvas credentials: entered in the UI, encrypted at rest, and never rendered back.

The roadmap item says the base URL and token are entered in the UI with an environment fallback,
stored encrypted, and redacted everywhere. Each of those is a separate mechanism and they are all
here.

**Where the credential lives.** Ciphertext goes in the run store's ``app_secrets`` table; the key
goes in ``<data root>/secrets/credentials.key``, owner-only, created on first use by
:meth:`sightline.paths.DataPaths.read_or_create_key`. Set ``SIGHTLINE_SECRET_KEY`` to supply the
key from the environment instead, which is how to keep it out of the data volume entirely.

**What that protects against, stated precisely, because overstating it is worse than not doing
it.** The run-store database is the artifact that travels: it is copied for a bug report, it is
backed up, it is mounted into another tool, it gets opened in a SQLite browser while somebody is
screen-sharing. Encrypting the credential means none of those exposes the token. It does *not*
protect against someone who can read the whole data root, because by default the key is in it. If
that is the threat, put the key in ``SIGHTLINE_SECRET_KEY`` and keep it somewhere the volume is
not.

**Precedence.** A credential entered in the UI wins over the environment. The environment is the
fallback, so a container started with ``CANVAS_BASE_URL`` set works with no setup, and a designer
who then types a different value on the settings screen is not silently overridden on restart.

**Redaction.** Every value that passes through here is registered with
:mod:`sightline.redaction`, base URL and derived hostname included, so it is scrubbed out of log
events, stored run events and error text without any call site opting in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .canvas.refs import CourseRefError, host_of, normalize_base_url
from .logging import get_logger
from .paths import DataPaths
from .redaction import register_secret
from .store import RunStore

log = get_logger("sightline.credentials")

#: Row name in ``app_secrets``. One row holds the whole Canvas credential, because base URL and
#: token are only meaningful together.
CANVAS_SECRET_NAME = "canvas"


class CredentialSource(StrEnum):
    """Where a value came from. Shown on the settings screen; it is the answer to "why is this
    not what I set?"."""

    STORED = "stored"
    ENVIRONMENT = "environment"
    UNSET = "unset"


class CredentialError(RuntimeError):
    """A credential could not be read or written. Carries a remedy, like the Canvas errors do."""

    def __init__(self, summary: str, *, remedy: str) -> None:
        super().__init__(summary)
        self.summary = summary
        self.remedy = remedy


@dataclass(frozen=True)
class CanvasCredentials:
    """The merged view of stored and environment values."""

    base_url: str | None
    token: str | None
    base_url_source: CredentialSource
    token_source: CredentialSource
    stored_at: datetime | None = None

    @property
    def complete(self) -> bool:
        return bool(self.base_url and self.token)

    @property
    def host(self) -> str | None:
        return host_of(self.base_url)

    def presence(self) -> list[dict[str, str]]:
        """Rows for the settings screen: presence and provenance, never a value.

        The token is never rendered, not even partially. A last-four is a habit borrowed from
        payment forms, where the number is printed on the card the user is holding; a Canvas
        token is not printed anywhere, so the four characters are a leak that buys nothing.
        """
        return [
            {
                "name": "Canvas base URL",
                "state": "set" if self.base_url else "not set",
                "source": self.base_url_source.value,
            },
            {
                "name": "Canvas API token",
                "state": "stored" if self.token else "not stored",
                "source": self.token_source.value,
            },
        ]


class CredentialStore:
    """Reads and writes the Canvas credential, encrypted, with an environment fallback."""

    def __init__(
        self,
        store: RunStore,
        paths: DataPaths,
        *,
        env_base_url: str | None = None,
        env_token: str | None = None,
        key: bytes | str | None = None,
    ) -> None:
        self._store = store
        self._paths = paths
        self._env_base_url = env_base_url or None
        self._env_token = env_token or None
        self._explicit_key = key.encode() if isinstance(key, str) else key
        self._fernet: Fernet | None = None
        register_secret(self._env_base_url)
        register_secret(host_of(self._env_base_url))
        register_secret(self._env_token)

    # -- key ---------------------------------------------------------------------------------

    def _cipher(self) -> Fernet:
        if self._fernet is None:
            material = self._explicit_key or self._paths.read_or_create_key(Fernet.generate_key)
            try:
                self._fernet = Fernet(material)
            except (ValueError, TypeError) as exc:
                raise CredentialError(
                    "The credential encryption key is not a valid key.",
                    remedy=(
                        "SIGHTLINE_SECRET_KEY must be a 32-byte urlsafe-base64 key. Generate one "
                        'with: python -c "from cryptography.fernet import Fernet; '
                        'print(Fernet.generate_key().decode())". Leave it unset to have '
                        "Sightline manage a key file under the data root instead."
                    ),
                ) from exc
        return self._fernet

    # -- read --------------------------------------------------------------------------------

    def canvas(self) -> CanvasCredentials:
        """The credential Sightline will actually use. Stored values win over the environment."""
        stored = self._read_stored()
        base_url = stored.get("base_url") or self._env_base_url
        token = stored.get("token") or self._env_token

        register_secret(base_url)
        register_secret(host_of(base_url))
        register_secret(token)

        return CanvasCredentials(
            base_url=base_url,
            token=token,
            base_url_source=_source(bool(stored.get("base_url")), bool(self._env_base_url)),
            token_source=_source(bool(stored.get("token")), bool(self._env_token)),
            stored_at=self._store.secret_updated_at(CANVAS_SECRET_NAME),
        )

    def _read_stored(self) -> dict[str, Any]:
        blob = self._store.get_secret_blob(CANVAS_SECRET_NAME)
        if blob is None:
            return {}
        try:
            payload = json.loads(self._cipher().decrypt(blob))
        except InvalidToken:
            # A key that no longer matches its ciphertext is the failure mode of moving a data
            # volume without its key, or of rotating SIGHTLINE_SECRET_KEY. Silently falling back
            # to the environment here would leave the designer changing a value that is never
            # read, so it fails loudly and says which of the two happened.
            raise CredentialError(
                "The stored Canvas credential cannot be decrypted with the current key.",
                remedy=(
                    "The key does not match the stored value — either SIGHTLINE_SECRET_KEY "
                    "changed, or the data volume was moved without "
                    f"{self._paths.credentials_key_file.name}. Restore the original key, or "
                    "clear the stored credential on the Settings screen and enter it again."
                ),
            ) from None
        return payload if isinstance(payload, dict) else {}

    # -- write -------------------------------------------------------------------------------

    def save_canvas(self, *, base_url: str | None = None, token: str | None = None) -> str | None:
        """Store what was supplied, leaving anything not supplied as it was.

        ``base_url`` is normalized to a scheme and host: pasting the URL of a course page as the
        base URL is the commonest configuration mistake there is, and it produces a 404 on every
        call with nothing pointing at the cause. The normalized value is returned so the caller
        can tell the user what was actually stored.

        Passing an empty string clears that field; passing ``None`` leaves it alone. A form that
        submits a blank token field must therefore send ``None``, not ``""``, or it would wipe a
        working token every time somebody changed the URL.
        """
        payload = dict(self._read_stored())
        normalized: str | None = None

        if base_url is not None:
            if base_url.strip():
                try:
                    normalized = normalize_base_url(base_url)
                except CourseRefError as exc:
                    raise CredentialError(exc.summary, remedy=exc.remedy) from exc
                payload["base_url"] = normalized
                register_secret(normalized)
                register_secret(host_of(normalized))
            else:
                payload.pop("base_url", None)

        if token is not None:
            cleaned = token.strip()
            if cleaned:
                payload["token"] = cleaned
                register_secret(cleaned)
            else:
                payload.pop("token", None)

        if payload:
            self._store.put_secret_blob(
                CANVAS_SECRET_NAME, self._cipher().encrypt(json.dumps(payload).encode())
            )
        else:
            self._store.delete_secret_blob(CANVAS_SECRET_NAME)

        # Fields, never values. "base_url" is a field name; the base URL is a secret.
        log.info(
            "canvas_credential_saved",
            fields=sorted(payload),
            base_url_normalized=bool(normalized),
        )
        return normalized

    def clear_canvas(self) -> bool:
        """Forget the stored credential entirely. The environment fallback still applies."""
        removed = self._store.delete_secret_blob(CANVAS_SECRET_NAME)
        log.info("canvas_credential_cleared", had_stored_value=removed)
        return removed


def _source(stored: bool, from_env: bool) -> CredentialSource:
    if stored:
        return CredentialSource.STORED
    if from_env:
        return CredentialSource.ENVIRONMENT
    return CredentialSource.UNSET
