"""Sign-in mode: a signed-in identity from a trusted auth proxy (roadmap item 44, SPEC §09).

**Sign-in is a mode, off by default** (item 40's reading). With ``SIGHTLINE_SIGNIN`` unset this
module installs nothing and Sightline is the single-user local tool it always was: the Reviewer
setting names the reviewer. With it on, sign-in happens *in front of* the app — an identity
provider federates the institution's SSO and an auth proxy forwards each request with the identity
it verified — and the app trusts that identity only from the proxy.

How the app knows a request came through the proxy: the proxy adds a **shared secret** header to
everything it forwards (``SIGHTLINE_SIGNIN_SECRET``, environment only — never under the data root,
never on the Settings screen). :class:`SignInMiddleware` runs before any route and refuses with
401, saying nothing about why, a request that

* does not carry the secret header, or carries a wrong one (compared in constant time),
* carries it more than once, or carries an identity header more than once — a proxy that appends
  rather than replaces would otherwise let a client's own header ride along,
* carries no subject, the signed-in person's stable id.

The one exception is the container health check: ``GET /healthz`` is answered without the proxy,
because the check runs inside the container. It is that path and that method only, and
:mod:`sightline.app` reduces what it says to a bare status when nobody signed in asked.

**Header names are configuration.** The defaults are the headers Authentik's proxy outpost sends
(``X-authentik-uid``, ``X-authentik-name``, ``X-authentik-groups``, groups separated by ``|``), plus
Sightline's own secret header, which the proxy is configured to add. Nothing here is
Authentik-specific: oauth2-proxy in front of Entra ID works by setting the names it uses
(``X-Forwarded-User``, ``X-Forwarded-Preferred-Username``, ``X-Forwarded-Groups``, separator ``,``).
Groups are read and kept on the identity; nothing in this item decides anything from them (the
admin group is item 45).

The proxy, not the app, holds the session: lifetime and sign-out are its configuration (item 47).
The secret and the identity headers are registered with :mod:`sightline.redaction` by
:class:`sightline.config.Settings`, so neither reaches a log, a run event or an error page.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .logging import get_logger
from .preferences import REVIEWER_MAX

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, MutableMapping

    from .config import Settings

    Scope = MutableMapping[str, Any]
    Message = MutableMapping[str, Any]
    Receive = Callable[[], Awaitable[Message]]
    Send = Callable[[Message], Awaitable[None]]
    ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

log = get_logger("sightline.signin")

#: The longest subject id accepted. Authentik's uid is 64 hex characters, an Entra object id 36;
#: the bound is the column's.
SUBJECT_MAX = 255

#: The one path answered without the proxy, and the one method: the container's own health check
#: (``Dockerfile`` HEALTHCHECK, the CI image job), which is a ``GET``.
HEALTH_PATH = "/healthz"
HEALTH_METHODS = frozenset({"GET"})

#: The whole of a refusal's body. It does not say which check failed, which header was expected or
#: what arrived: a request that did not come through the proxy is not owed a diagnosis, and the
#: log carries the reason for whoever runs the deployment.
REFUSAL = (
    b"Sign-in required. This request did not come through Sightline's sign-in proxy, so it was "
    b"not served.\n"
)


@dataclass(frozen=True)
class Identity:
    """The signed-in person, as the proxy forwarded them.

    ``subject`` is the stable id, which is what identifies the person; ``name`` is the display
    name shown beside it, falling back to the subject when the proxy sends none, so a renamed
    person keeps their history. ``groups`` are kept for item 45 and decide nothing here.
    """

    subject: str
    name: str
    groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class SignInConfig:
    """What the middleware checks, resolved once from :class:`sightline.config.Settings`."""

    secret: bytes
    secret_header: str
    subject_header: str
    name_header: str
    groups_header: str
    groups_separator: str

    @classmethod
    def from_settings(cls, settings: Settings) -> SignInConfig | None:
        """The configuration in force, or ``None`` in local mode."""
        if not settings.signin:
            return None
        # Settings refuses sign-in mode without a secret; this is the same rule, stated where the
        # secret is used rather than trusted from a distance.
        if settings.signin_secret is None:  # pragma: no cover - Settings validates it first
            raise ValueError("sign-in mode needs SIGHTLINE_SIGNIN_SECRET")
        return cls(
            secret=settings.signin_secret.get_secret_value().strip().encode("utf-8"),
            secret_header=settings.signin_secret_header.lower(),
            subject_header=settings.signin_subject_header.lower(),
            name_header=settings.signin_name_header.lower(),
            groups_header=settings.signin_groups_header.lower(),
            groups_separator=settings.signin_groups_separator,
        )


class SignInRefused(Exception):
    """Why a request was refused. Logged by reason, never shown, never carrying a header value."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def identify(config: SignInConfig, raw_headers: list[tuple[bytes, bytes]]) -> Identity:
    """The identity a request carries, or :class:`SignInRefused` saying which check failed."""
    wanted = {
        config.secret_header,
        config.subject_header,
        config.name_header,
        config.groups_header,
    }
    seen: dict[str, bytes] = {}
    for raw_name, raw_value in raw_headers:
        name = raw_name.decode("latin-1").lower()
        if name not in wanted:
            continue
        if name in seen:
            raise SignInRefused(f"duplicate {_role(config, name)} header")
        seen[name] = raw_value

    given = seen.get(config.secret_header)
    if given is None:
        raise SignInRefused("no secret header")
    if not hmac.compare_digest(given.strip(), config.secret):
        raise SignInRefused("wrong secret")

    subject = _text(seen.get(config.subject_header))
    if not subject:
        raise SignInRefused("no subject")
    if len(subject) > SUBJECT_MAX:
        raise SignInRefused("subject too long")

    name = " ".join(_text(seen.get(config.name_header)).split()) or subject
    groups_text = _text(seen.get(config.groups_header))
    groups = tuple(
        group.strip() for group in groups_text.split(config.groups_separator) if group.strip()
    )
    return Identity(subject=subject, name=name[:REVIEWER_MAX], groups=groups)


class SignInMiddleware:
    """Refuse, before any route, a request that did not come through the sign-in proxy.

    A pure ASGI middleware rather than a route dependency, so a route added later cannot forget
    it. On success the identity is put on the request's state (``request.state.identity``), which
    is where :func:`sightline.review.identity.current_reviewer` reads it.
    """

    def __init__(self, app: ASGIApp, config: SignInConfig) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        try:
            state["identity"] = identify(self.config, list(scope.get("headers") or []))
        except SignInRefused as refused:
            if _is_health_check(scope):
                state["identity"] = None
                await self.app(scope, receive, send)
                return
            log.warning("signin_refused", reason=refused.reason, method=scope.get("method"))
            await _refuse(scope, send)
            return
        await self.app(scope, receive, send)


def signed_in(scope_state: Any) -> Identity | None:
    """The identity :class:`SignInMiddleware` put on a request's state, if any."""
    identity = getattr(scope_state, "identity", None)
    return identity if isinstance(identity, Identity) else None


def _is_health_check(scope: Scope) -> bool:
    return (
        scope["type"] == "http"
        and scope.get("path") == HEALTH_PATH
        and scope.get("method") in HEALTH_METHODS
    )


async def _refuse(scope: Scope, send: Send) -> None:
    if scope["type"] == "websocket":  # pragma: no cover - the app serves no websockets
        await send({"type": "websocket.close", "code": 1008})
        return
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(REFUSAL)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": REFUSAL})


def _text(raw: bytes | None) -> str:
    """A header value as text. UTF-8 where it is (a name with an accent), Latin-1 otherwise."""
    if raw is None:
        return ""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return "".join(ch for ch in text if ch.isprintable()).strip()


def _role(config: SignInConfig, name: str) -> str:
    """Which configured header ``name`` is, for a log line that must not carry its value."""
    return {
        config.secret_header: "secret",
        config.subject_header: "subject",
        config.name_header: "name",
        config.groups_header: "groups",
    }[name]


__all__ = [
    "HEALTH_PATH",
    "REFUSAL",
    "Identity",
    "SignInConfig",
    "SignInMiddleware",
    "SignInRefused",
    "identify",
    "signed_in",
]
