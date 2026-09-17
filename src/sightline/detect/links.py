"""Resolving an external link, and the three answers there are.

One detector follows links off the Canvas instance: D13 asks whether the campus policy links a
syllabus points at still go anywhere. That is the only network traffic any detector generates, and
it is the one place in the deterministic half of Sightline where an answer depends on something
outside the course.

**There are three answers and the third is the important one.** A link resolves, a link is
definitely gone, or *we could not tell*. A DNS timeout, a proxy refusal, a TLS handshake that
fails on a corporate network — none of those is a broken link, and reporting a working policy link
as broken because a lookup timed out is the finding that costs the tool its credibility. So
:class:`LinkState` has an ``UNKNOWN`` member, the resolver returns it for every failure that is not
a definite answer from the far end, and the detector records it as something it could not read
rather than as something it found.

**Resolution is off unless somebody switches it on.** The default resolver is
:class:`NoLinkResolver`, which reaches nothing; the detector then reports the links it found and
makes no claim about where they go. That default is not timidity. The test suite and CI run with no
network, a course review runs in batches, and a check that silently becomes a few hundred outbound
requests is a check that gets switched off by an institution's network team rather than by us.

**Nothing here is unbounded.** :class:`HttpLinkResolver` caps the number of distinct URLs it will
ever fetch (:attr:`HttpLinkResolver.budget`), caches every answer including the failures, spaces
requests to a fixed minimum interval, and uses a short timeout. When the budget is spent every
further URL answers ``UNKNOWN`` with a reason saying so — which is a reported limit rather than a
silent truncation.

**A URL is not logged.** A link in a course body can carry a token in its query string, and this
module is the one that handles other people's URLs in bulk. The host is what reaches a log line;
the path and query never do.
"""

from __future__ import annotations

import enum
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from ..logging import get_logger

log = get_logger(__name__)

#: Seconds before a single request is abandoned. Short on purpose: a reviewer is waiting, and a
#: link that takes eight seconds to answer is not a link a learner will follow either.
DEFAULT_TIMEOUT_SECONDS = 4.0

#: Minimum seconds between two outbound requests. One request every quarter second is polite to a
#: campus web server being asked about the same policy page by every course in a batch.
DEFAULT_MIN_INTERVAL_SECONDS = 0.25

#: Distinct URLs one resolver will ever fetch. Beyond it every answer is ``UNKNOWN`` with the
#: reason stated, so an unusually link-heavy course cannot turn a review into a crawl.
DEFAULT_BUDGET = 40


class LinkState(enum.StrEnum):
    """What the far end said, or that it said nothing."""

    #: The server answered, and the answer was not "gone".
    RESOLVED = "resolved"
    #: The server answered and the answer was that this address holds nothing. 404 or 410 only.
    GONE = "gone"
    #: No answer that can be believed either way. The honest state, and the common one.
    UNKNOWN = "unknown"


@dataclass(frozen=True, kw_only=True, slots=True)
class LinkCheck:
    """One link, and what became of the attempt to reach it."""

    url: str
    state: LinkState
    #: HTTP status where there was one. ``None`` for every failure that never got a response.
    status: int | None = None
    #: Why, in a reviewer's words. Always set for ``UNKNOWN`` — that is the whole point of it.
    detail: str = ""

    @property
    def conclusive(self) -> bool:
        return self.state is not LinkState.UNKNOWN


@runtime_checkable
class LinkResolver(Protocol):
    """Anything that can be asked about a URL. Implemented by the two classes below and by tests."""

    def check(self, url: str) -> LinkCheck: ...


class NoLinkResolver:
    """The default: reaches nothing and says so.

    A named class rather than ``None`` threaded through the call graph, so "resolution is off" is
    a value a test can pass and a detector can read, and the off path is exercised by the suite the
    same way the on path is.
    """

    #: What every answer says. A detector reads this to tell "switched off" apart from "tried and
    #: failed", which are different things to report.
    REASON = (
        "Link resolution is switched off for this run, so nothing was requested from outside "
        "Canvas. The link is recorded as present; whether it resolves was not checked."
    )

    def check(self, url: str) -> LinkCheck:
        return LinkCheck(url=url, state=LinkState.UNKNOWN, detail=self.REASON)


class HttpLinkResolver:
    """Resolves URLs over HTTP, within a budget, a rate limit and a short timeout.

    ``fetch`` is injected rather than imported so the suite can drive every branch without a
    network: it takes a URL and returns an HTTP status, or raises. Production passes an httpx
    client's ``head``-then-``get``; see :meth:`from_httpx`.
    """

    def __init__(
        self,
        fetch: Callable[[str, float], int],
        *,
        budget: int = DEFAULT_BUDGET,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        min_interval: float = DEFAULT_MIN_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._fetch = fetch
        self.budget = budget
        self._timeout = timeout
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._cache: dict[str, LinkCheck] = {}
        self._spent = 0
        self._last = 0.0

    @property
    def spent(self) -> int:
        """Distinct URLs fetched. A test asserts on this; so does the run's own provenance."""
        return self._spent

    @property
    def cached(self) -> int:
        return len(self._cache)

    def check(self, url: str) -> LinkCheck:
        key = url.strip()
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        result = self._check_uncached(key)
        # Failures are cached too. Twenty pages linking one dead policy page should cost one
        # request and one wait, not twenty of each.
        self._cache[key] = result
        return result

    def _check_uncached(self, url: str) -> LinkCheck:
        scheme = urlsplit(url).scheme.lower()
        if scheme not in {"http", "https"}:
            return LinkCheck(
                url=url,
                state=LinkState.UNKNOWN,
                detail=f"{scheme or 'no'} is not a scheme this check follows.",
            )
        if self._spent >= self.budget:
            return LinkCheck(
                url=url,
                state=LinkState.UNKNOWN,
                detail=(
                    f"The link-checking budget for this run ({self.budget} addresses) was already "
                    "spent, so this address was not requested."
                ),
            )

        self._wait()
        self._spent += 1
        try:
            status = self._fetch(url, self._timeout)
        except Exception as exc:  # noqa: BLE001 - every failure is the same answer: UNKNOWN
            # The host, never the URL: a link in a course body can carry a token in its query.
            log.info(
                "link_check_failed",
                host=urlsplit(url).netloc or "unknown",
                error=type(exc).__name__,
            )
            return LinkCheck(
                url=url,
                state=LinkState.UNKNOWN,
                detail=(
                    f"The address could not be reached ({type(exc).__name__}). That is not "
                    "evidence the link is broken."
                ),
            )

        if status in {404, 410}:
            return LinkCheck(
                url=url,
                state=LinkState.GONE,
                status=status,
                detail=f"The server answered {status}, which means this address holds nothing.",
            )
        if status >= 400:
            return LinkCheck(
                url=url,
                state=LinkState.UNKNOWN,
                status=status,
                detail=(
                    f"The server answered {status}. That is not a statement that the page is "
                    "gone — a login wall and a rate limit both answer this way."
                ),
            )
        return LinkCheck(
            url=url,
            state=LinkState.RESOLVED,
            status=status,
            detail=f"The server answered {status}.",
        )

    def _wait(self) -> None:
        if self._spent == 0 or self._min_interval <= 0:
            self._last = self._clock()
            return
        elapsed = self._clock() - self._last
        if elapsed < self._min_interval:
            self._sleep(self._min_interval - elapsed)
        self._last = self._clock()

    @classmethod
    def from_httpx(cls, **kwargs: Any) -> HttpLinkResolver:
        """A resolver backed by httpx, following redirects, ``HEAD`` first then ``GET``.

        ``HEAD`` first because a policy page can be large and its status is all that is wanted;
        ``GET`` after, because a good many servers answer ``HEAD`` with 405 and the page itself
        with 200, and reporting that as a finding would be exactly the false positive this module
        is built to avoid.
        """
        import httpx

        def fetch(url: str, timeout: float) -> int:
            with httpx.Client(follow_redirects=True, timeout=timeout) as client:
                response = client.head(url)
                if response.status_code >= 400:
                    response = client.get(url)
                return response.status_code

        return cls(fetch, **kwargs)


__all__ = [
    "DEFAULT_BUDGET",
    "DEFAULT_MIN_INTERVAL_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "HttpLinkResolver",
    "LinkCheck",
    "LinkResolver",
    "LinkState",
    "NoLinkResolver",
]
