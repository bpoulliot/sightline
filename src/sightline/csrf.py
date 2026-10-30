"""A CSRF token on every form post, in both modes (roadmap item 44, SPEC §09).

Every page that renders a form puts a hidden ``csrf_token`` field in it (the ``csrf_field()``
template global), and :class:`CsrfMiddleware` refuses — before any route — a ``POST``, ``PUT``,
``PATCH`` or ``DELETE`` that does not carry the token for this browser, in that field or in an
``X-CSRF-Token`` header. A refused post did nothing, and the page that says so tells the person to
reload, because the ordinary cause is a page left open across a restart.

The token is not stored anywhere. A browser holds a random nonce in a cookie (``sightline_csrf``:
``HttpOnly``, ``SameSite=Lax``, ``Secure`` when the request arrived over HTTPS), and the token is an
HMAC of that nonce — and, in sign-in mode, of the signed-in subject — under a key this process
draws at start. Another site can make a browser post here, and the cookie goes with it, but it
cannot read the page and so cannot know the token; nor can it compute one without the key, even if
it could plant a cookie. Binding the subject means a page rendered for one person is not good for
another in the same browser. A restart draws a new key, so a form rendered before it is refused
once and works after a reload — the trade for keeping no key on disk.

The cookie is set only on a response that rendered a token, so the stylesheet requests a first
page load makes in parallel cannot replace the nonce the page's forms were rendered with.

This replaces what :mod:`sightline.views` used to say — that a single-user local tool needs no
CSRF token. A local tool listens on a port any web page in the same browser can post to, and
item 40 decided both modes carry one.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, Any

from jinja2 import pass_context
from markupsafe import Markup
from starlette.requests import Request

from .logging import get_logger
from .signin import signed_in

if TYPE_CHECKING:
    from collections.abc import Awaitable, MutableMapping

    Scope = MutableMapping[str, Any]
    Message = MutableMapping[str, Any]
    Receive = Callable[[], Awaitable[Message]]
    Send = Callable[[Message], Awaitable[None]]
    ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

log = get_logger("sightline.csrf")

COOKIE = "sightline_csrf"
FIELD = "csrf_token"
HEADER = "x-csrf-token"

#: Methods that change something, and so must carry the token.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Bodies the middleware reads the field from. Anything else must send the header.
FORM_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")

#: A nonce as :func:`secrets.token_urlsafe` makes one (32 bytes, 43 characters). A cookie of any
#: other shape is replaced rather than trusted.
_NONCE = re.compile(r"^[A-Za-z0-9_-]{43}$")

#: What the refusal page says. Nothing about the token or the cookie: the remedy is all a person
#: needs, and it is the same whatever was missing.
REFUSED_SUMMARY = "This form was out of date, so nothing was done."
REFUSED_REMEDY = (
    "Reload the page and submit the form again. A page left open while Sightline restarted, or "
    "a form submitted from somewhere other than Sightline's own page, is refused this way."
)


class CsrfTokens:
    """Makes and checks tokens under one key, drawn when the application is built."""

    def __init__(self, key: bytes | None = None) -> None:
        self._key = key or secrets.token_bytes(32)

    @staticmethod
    def new_nonce() -> str:
        return secrets.token_urlsafe(32)

    def token(self, nonce: str, subject: str = "") -> str:
        digest = hmac.new(self._key, f"{nonce}\n{subject}".encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def valid(self, nonce: str, subject: str, given: str) -> bool:
        return hmac.compare_digest(self.token(nonce, subject).encode(), given.encode())


@dataclass
class CsrfState:
    """One request's token, put on ``request.state.csrf`` by the middleware."""

    nonce: str
    fresh: bool
    token: str
    used: bool = False

    def field(self) -> Markup:
        self.used = True
        return Markup('<input type="hidden" name="{}" value="{}">').format(FIELD, self.token)


@pass_context
def csrf_field(context: Any) -> Markup:
    """The hidden field every ``<form method="post">`` carries. A template global."""
    request = context.get("request")
    state: CsrfState | None = getattr(getattr(request, "state", None), "csrf", None)
    if state is None:
        raise RuntimeError("a form was rendered outside CsrfMiddleware; it would be refused")
    return state.field()


class CsrfMiddleware:
    """Refuse an unsafe request without this browser's token; hand pages the token to render."""

    def __init__(self, app: ASGIApp, tokens: CsrfTokens, refused_page: Callable[[], str]) -> None:
        self.app = app
        self.tokens = tokens
        self.refused_page = refused_page

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        cookie = request.cookies.get(COOKIE, "")
        fresh = not _NONCE.match(cookie)
        nonce = self.tokens.new_nonce() if fresh else cookie
        identity = signed_in(request.state)
        subject = identity.subject if identity else ""
        state = CsrfState(nonce=nonce, fresh=fresh, token=self.tokens.token(nonce, subject))
        scope.setdefault("state", {})["csrf"] = state

        if scope.get("method") in UNSAFE_METHODS:
            body, replay = await _buffer(receive)
            given = request.headers.get(HEADER) or await _field(scope, body)
            if fresh or not given or not self.tokens.valid(nonce, subject, given):
                log.warning(
                    "csrf_refused",
                    method=scope.get("method"),
                    path=scope.get("path"),
                    reason="no cookie" if fresh else ("no token" if not given else "wrong token"),
                )
                await self._refuse(send)
                return
            receive = replay

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start" and state.fresh and state.used:
                headers = list(message.get("headers") or [])
                headers.append((b"set-cookie", _cookie(state.nonce, scope).encode("latin-1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_cookie)

    async def _refuse(self, send: Send) -> None:
        body = self.refused_page().encode("utf-8")
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


async def _buffer(receive: Receive) -> tuple[bytes, Receive]:
    """Read the whole body, and return it with a ``receive`` that replays it to the route."""
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    body = b"".join(chunks)
    replayed = False

    async def replay() -> Message:
        nonlocal replayed
        if not replayed:
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return body, replay


async def _field(scope: Scope, body: bytes) -> str:
    """The token field of a form body, or ``""``. Other bodies must use the header."""
    content_type = Request(scope).headers.get("content-type", "")
    if not content_type.lower().startswith(FORM_TYPES):
        return ""
    sent = False

    async def once() -> Message:
        nonlocal sent
        if sent:  # pragma: no cover - the parser stops at more_body False
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    try:
        form = await Request(scope, receive=once).form()
    except Exception:  # a malformed body carries no token; the route never sees it either
        return ""
    try:
        value = form.get(FIELD)
        return value if isinstance(value, str) else ""
    finally:
        await form.close()


def _cookie(nonce: str, scope: Scope) -> str:
    cookie: SimpleCookie = SimpleCookie()
    cookie[COOKIE] = nonce
    morsel = cookie[COOKIE]
    morsel["path"] = "/"
    morsel["httponly"] = True
    morsel["samesite"] = "Lax"
    if scope.get("scheme") == "https":
        morsel["secure"] = True
    return morsel.OutputString()


__all__ = [
    "COOKIE",
    "FIELD",
    "HEADER",
    "REFUSED_REMEDY",
    "REFUSED_SUMMARY",
    "CsrfMiddleware",
    "CsrfState",
    "CsrfTokens",
    "csrf_field",
]
