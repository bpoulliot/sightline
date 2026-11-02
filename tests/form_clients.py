"""A test client that posts the way Sightline's own pages do: with the CSRF token (item 44).

Every form Sightline renders carries the token (:mod:`sightline.csrf`), so a suite that posts
straight to a route is a browser that skipped the page. :class:`FormClient` supplies the token for
an unsafe request that did not bring one — as the ``X-CSRF-Token`` header, from a nonce cookie it
sets on itself and the application's own key — so the route tests keep testing their routes.
``tests/test_signin.py`` fires the check itself with the plain ``TestClient``, and shows the token
a rendered page carries is accepted when posted back as the form field.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from sightline.csrf import COOKIE, HEADER, CsrfTokens

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


class FormClient(TestClient):
    """``TestClient`` that adds the CSRF token to unsafe requests. ``subject`` binds it."""

    def __init__(self, *args: Any, subject: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.subject = subject

    def request(self, method: str, url: Any, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        if method.upper() in UNSAFE:
            headers = dict(kwargs.pop("headers", None) or {})
            if not any(name.lower() == HEADER for name in headers):
                headers[HEADER] = self.csrf_token()
            kwargs["headers"] = headers
        return super().request(method, url, *args, **kwargs)

    def csrf_token(self) -> str:
        nonce = self.cookies.get(COOKIE)
        if not nonce:
            nonce = CsrfTokens.new_nonce()
            self.cookies.set(COOKIE, nonce)
        tokens: CsrfTokens = self.app.state.csrf  # type: ignore[attr-defined]
        return tokens.token(nonce, self.subject)


__all__ = ["FormClient"]
