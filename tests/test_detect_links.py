"""The link resolver: the one place a detector reaches outside Canvas.

Three things are asserted here and each of them is a way this could cost the tool its credibility.

**A failure is never a broken link.** Every outcome that is not a definite answer from the far end
is ``UNKNOWN``, including a 403 and a 500, because a login wall and a rate limit both answer that
way. Reporting a working campus policy page as broken because a DNS lookup timed out is the false
finding a reviewer remembers.

**Nothing is unbounded.** A budget, a cache that holds failures as well as successes, and a
minimum interval between requests — all three driven here without a socket, because a test that
needed the network is a test that gets skipped and then deleted.

**Off is the default.** :class:`NoLinkResolver` reaches nothing and says which of the two silences
it is: not checked, rather than checked and unanswered.
"""

from __future__ import annotations

import pytest

from sightline.detect.links import (
    DEFAULT_BUDGET,
    HttpLinkResolver,
    LinkResolver,
    LinkState,
    NoLinkResolver,
)


class Clock:
    """A clock that only moves when something sleeps on it."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def resolver(
    answers: dict[str, object] | None = None, **kwargs: object
) -> tuple[HttpLinkResolver, list[str], Clock]:
    """A resolver over a table of statuses or exceptions, plus the log of what it fetched."""
    table = answers or {}
    asked: list[str] = []
    clock = Clock()

    def fetch(url: str, timeout: float) -> int:
        asked.append(url)
        answer = table.get(url, 200)
        if isinstance(answer, Exception):
            raise answer
        return int(answer)  # type: ignore[arg-type]

    return (
        HttpLinkResolver(fetch, clock=clock, sleep=clock.sleep, **kwargs),  # type: ignore[arg-type]
        asked,
        clock,
    )


# -- the three answers ---------------------------------------------------------------------------


def test_a_two_hundred_resolves() -> None:
    check, asked, _ = resolver()
    result = check.check("https://policies.example.edu/x")
    assert result.state is LinkState.RESOLVED
    assert result.status == 200
    assert asked == ["https://policies.example.edu/x"]


@pytest.mark.parametrize("status", [404, 410])
def test_only_a_four_oh_four_or_a_four_ten_means_gone(status: int) -> None:
    check, _, _ = resolver({"https://policies.example.edu/x": status})
    result = check.check("https://policies.example.edu/x")
    assert result.state is LinkState.GONE
    assert result.status == status


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_every_other_error_status_is_unknown_and_says_why(status: int) -> None:
    """A login wall, a rate limit and a server fault are not statements that a page is gone."""
    check, _, _ = resolver({"https://policies.example.edu/x": status})
    result = check.check("https://policies.example.edu/x")
    assert result.state is LinkState.UNKNOWN
    assert result.status == status
    assert "not a statement that the page is gone" in result.detail


def test_a_transport_failure_is_unknown_and_not_a_broken_link() -> None:
    boom = OSError("name resolution failed")
    check, _, _ = resolver({"https://policies.example.edu/x": boom})
    result = check.check("https://policies.example.edu/x")
    assert result.state is LinkState.UNKNOWN
    assert result.status is None
    assert "not evidence the link is broken" in result.detail


def test_a_scheme_this_check_does_not_follow_is_unknown_and_costs_nothing() -> None:
    check, asked, _ = resolver()
    result = check.check("mailto:nobody@example.edu")
    assert result.state is LinkState.UNKNOWN
    assert asked == []
    assert check.spent == 0


# -- the bounds ------------------------------------------------------------------------------------


def test_the_same_address_is_fetched_once_however_often_it_is_asked_about() -> None:
    check, asked, _ = resolver()
    for _ in range(5):
        check.check("https://policies.example.edu/x")
    assert asked == ["https://policies.example.edu/x"]
    assert check.spent == 1


def test_a_failure_is_cached_too() -> None:
    """Twenty pages linking one dead policy page cost one request, not twenty."""
    check, asked, _ = resolver({"https://policies.example.edu/x": OSError("timeout")})
    for _ in range(4):
        assert check.check("https://policies.example.edu/x").state is LinkState.UNKNOWN
    assert len(asked) == 1


def test_the_budget_stops_the_fetching_and_says_so_rather_than_going_quiet() -> None:
    check, asked, _ = resolver(budget=3)
    results = [check.check(f"https://policies.example.edu/{index}") for index in range(6)]
    assert len(asked) == 3
    assert [result.state for result in results[:3]] == [LinkState.RESOLVED] * 3
    for result in results[3:]:
        assert result.state is LinkState.UNKNOWN
        assert "budget for this run (3 addresses) was already spent" in result.detail


def test_the_default_budget_is_a_number_rather_than_no_limit() -> None:
    assert 0 < DEFAULT_BUDGET < 1000


def test_requests_are_spaced_by_the_minimum_interval() -> None:
    check, asked, clock = resolver(min_interval=0.25)
    for index in range(4):
        check.check(f"https://policies.example.edu/{index}")
    assert len(asked) == 4
    # The first costs nothing; each of the other three waits the interval out.
    assert clock.slept == [0.25, 0.25, 0.25]


def test_the_timeout_reaches_the_fetch() -> None:
    seen: list[float] = []

    def fetch(url: str, timeout: float) -> int:
        seen.append(timeout)
        return 200

    HttpLinkResolver(fetch, timeout=1.5).check("https://policies.example.edu/x")
    assert seen == [1.5]


# -- off, which is the default --------------------------------------------------------------------


def test_the_no_op_resolver_reaches_nothing_and_says_which_silence_it_is() -> None:
    result = NoLinkResolver().check("https://policies.example.edu/x")
    assert result.state is LinkState.UNKNOWN
    assert "switched off" in result.detail
    assert result.conclusive is False


def test_both_resolvers_satisfy_the_protocol() -> None:
    assert isinstance(NoLinkResolver(), LinkResolver)
    assert isinstance(resolver()[0], LinkResolver)
