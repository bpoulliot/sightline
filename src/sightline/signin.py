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
Groups are read and kept on the identity. Item 45 decides who is an admin from them
(:mod:`sightline.access`).

**Who may use the app at all** (roadmap item 51). With ``SIGHTLINE_SIGNIN_USER_GROUP`` set, an
identity that came through the proxy but is in neither that group nor the admin group is refused
here, after the proxy checks and before any route, with **403 and a page** naming the group to ask
for. The admin group alone admits. Group names are matched exactly, never as substrings. The page
links nowhere and renders no run, course or setting; the two stylesheets it loads are served, and
the health check answers such a person as it answers a request nobody signed in made. Unset, any
proxied identity is admitted, as item 44 built it, and startup says so in the log. Refusing
somebody deletes nothing: their runs and bindings stay in the store, visible to an admin.

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
    person keeps their history. ``groups`` decide who is an admin (item 45) and, with a users
    group configured, whether the person is admitted at all (item 51).
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
    #: ``SIGHTLINE_SIGNIN_USER_GROUP``; ``None`` admits any identity the proxy forwards.
    user_group: str | None = None
    #: ``SIGHTLINE_SIGNIN_ADMIN_GROUP``, which admits on its own.
    admin_group: str | None = None

    def admits(self, identity: Identity) -> bool:
        """Whether ``identity`` may use the app: always with no users group, else by membership."""
        if self.user_group is None:
            return True
        return any(
            group is not None and group in identity.groups
            for group in (self.user_group, self.admin_group)
        )

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
            user_group=settings.signin_user_group,
            admin_group=settings.signin_admin_group,
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
    is where :func:`sightline.review.identity.current_reviewer` reads it. An identity the
    configuration does not admit (item 51) is refused here too, with 403 and ``refused_page``
    rendered for the users group.
    """

    def __init__(
        self,
        app: ASGIApp,
        config: SignInConfig,
        refused_page: Callable[[str], str] | None = None,
    ) -> None:
        self.app = app
        self.config = config
        self.refused_page = refused_page or _plain_not_a_member

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
        if not self.config.admits(state["identity"]):
            if _is_health_check(scope):
                # Answered as a request nobody signed in made: the status alone.
                state["identity"] = None
                await self.app(scope, receive, send)
                return
            if _is_stylesheet(scope):
                # The refusal page's own two stylesheets, so it reads as a Sightline page.
                await self.app(scope, receive, send)
                return
            log.warning("signin_not_a_member", method=scope.get("method"))
            await _refuse_member(scope, send, self.refused_page(self.config.user_group or ""))
            return
        await self.app(scope, receive, send)


def signed_in(scope_state: Any) -> Identity | None:
    """The identity :class:`SignInMiddleware` put on a request's state, if any."""
    identity = getattr(scope_state, "identity", None)
    return identity if isinstance(identity, Identity) else None


#: What the refusal page loads, and the only paths besides the health check that a signed-in person
#: outside the users group is served. Neither carries a run, a course or a setting.
STYLESHEET_PATHS = frozenset({"/assets/tokens.css", "/static/app.css"})


def _is_stylesheet(scope: Scope) -> bool:
    return (
        scope["type"] == "http"
        and scope.get("path") in STYLESHEET_PATHS
        and scope.get("method") in {"GET", "HEAD"}
    )


def not_a_member_remedy(group: str) -> str:
    """What a person outside the users group is told: the group to ask for, by its name."""
    return (
        f"Ask whoever runs this deployment to add you to the sign-in group {group}. Until they "
        "do, Sightline shows you nothing. Anything you made here before is kept."
    )


#: The heading of the page a person outside the users group gets.
NOT_A_MEMBER_SUMMARY = "You are signed in, but you are not in the group that may use Sightline."


def _plain_not_a_member(group: str) -> str:  # pragma: no cover - the app passes a template
    return f"{NOT_A_MEMBER_SUMMARY}\n{not_a_member_remedy(group)}\n"


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


async def _refuse_member(scope: Scope, send: Send, page: str) -> None:
    if scope["type"] == "websocket":  # pragma: no cover - the app serves no websockets
        await send({"type": "websocket.close", "code": 1008})
        return
    body = page.encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"text/html; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


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
    "NOT_A_MEMBER_SUMMARY",
    "REFUSAL",
    "STYLESHEET_PATHS",
    "Identity",
    "SignInConfig",
    "SignInMiddleware",
    "SignInRefused",
    "identify",
    "not_a_member_remedy",
    "signed_in",
]
