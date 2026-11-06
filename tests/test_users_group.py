"""Sign-in admits only members of a users group, or the admin group (roadmap item 51, SPEC §09).

**Sign-in mode with ``SIGHTLINE_SIGNIN_USER_GROUP`` set.** A person in the users group is admitted;
a person in the admin group alone is admitted as an admin; a person in neither is refused with 403
and a page naming the group to ask for, on every kind of route, before any route runs, and the page
carries no run, course or setting. ``GET /healthz`` still answers. Membership is exact: a group
whose name merely contains the users group's is refused. A request that did not come through the
proxy is refused with 401 before membership is looked at. Refusing somebody deletes nothing.

**Unset**, any proxied identity is admitted, as before, and startup logs one line saying so.
**Local mode** ignores the setting.

No identity provider and no proxy is involved: the proxy is these headers, written by hand, with
obviously fake values.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from form_clients import FormClient
from sightline.app import create_app
from sightline.config import Settings
from sightline.csrf import REFUSED_SUMMARY
from sightline.signin import NOT_A_MEMBER_SUMMARY, REFUSAL
from sightline.store import RunStore
from test_per_user import ADMIN_GROUP, ALICE, SECRET, reviewed_run

REPO = Path(__file__).resolve().parents[1]

USERS_GROUP = "sightline-users"
#: One person in each of the three positions item 51 distinguishes.
MEMBER = "fake-subject-member-0101"
ADMIN_ONLY = "fake-subject-admin-only-0102"
OUTSIDER = "fake-subject-outsider-0103"


def settings_for(data_root: Path, **extra: Any) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=data_root,
        tokens_css_path=REPO / "docs" / "tokens.css",
        log_format="json",
        signin=True,
        signin_secret=SECRET,
        **{"signin_admin_group": ADMIN_GROUP, "signin_user_group": USERS_GROUP, **extra},
    )


def headers_for(subject: str, groups: str | None, *, secret: str = SECRET) -> dict[str, str]:
    sent = {
        "X-Sightline-Proxy-Secret": secret,
        "X-authentik-uid": subject,
        "X-authentik-name": f"Person {subject[-4:]}",
    }
    if groups is not None:
        sent["X-authentik-groups"] = groups
    return sent


GROUPS = {
    MEMBER: f"fake-staff|{USERS_GROUP}",
    ADMIN_ONLY: ADMIN_GROUP,
    OUTSIDER: "fake-staff|fake-students",
}


class Client(FormClient):
    def as_(self, subject: str, method: str, url: str, **kwargs: Any) -> Any:
        self.subject = subject
        return self.request(method, url, headers=headers_for(subject, GROUPS[subject]), **kwargs)


def text_of(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split()).replace("&#39;", "'")


@pytest.fixture
def run_and_course(data_root: Path, paths: Any) -> tuple[str, str]:
    """A reviewed run of Alice's, who is in no group at all once the users group is set."""
    return reviewed_run(paths, starter=ALICE)


@pytest.fixture
def gated(data_root: Path, run_and_course: tuple[str, str]) -> Iterator[Client]:
    with Client(create_app(settings_for(data_root))) as client:
        yield client


def surfaces(run_id: str, course_id: str) -> list[tuple[str, str, Any]]:
    """``/``, ``/runs``, a run, a review and a form post, plus the other sections and the API."""
    return [
        ("GET", "/", None),
        ("GET", "/runs", None),
        ("GET", f"/runs/{run_id}", None),
        ("GET", f"/api/runs/{run_id}", None),
        ("GET", f"/review/{course_id}", None),
        ("GET", f"/review/{course_id}/category/MOB", None),
        ("POST", f"/review/{course_id}/override", {"code": "MOB 2", "rating": "major"}),
        ("POST", "/settings/model-host", {"endpoint": "http://192.0.2.10:11434"}),
        ("GET", "/settings", None),
        ("GET", "/courses", None),
        ("GET", "/rubrics", None),
        ("GET", "/docs", None),
        ("GET", "/static/run-progress.js", None),
    ]


# -- in neither group: refused, with a page ------------------------------------------------------


def test_a_person_in_neither_group_is_refused_everywhere_with_a_page_naming_the_group(
    gated: Client, run_and_course: tuple[str, str]
) -> None:
    run_id, course_id = run_and_course
    store: RunStore = gated.app.state.store  # type: ignore[attr-defined]
    before = store.get_run(run_id)
    assert before is not None
    for method, path, data in surfaces(run_id, course_id):
        kwargs: dict[str, Any] = {"follow_redirects": False}
        if data is not None:
            kwargs["data"] = data
        response = gated.as_(OUTSIDER, method, path, **kwargs)
        assert response.status_code == 403, (method, path, response.text[:300])
        assert response.headers["content-type"].startswith("text/html")
        page = text_of(response.text)
        assert NOT_A_MEMBER_SUMMARY in page, path
        assert USERS_GROUP in page, path
        # No run, no course, no setting, and nowhere to go.
        assert run_id not in response.text and course_id not in response.text, path
        assert ADMIN_GROUP not in response.text, path
        assert 'aria-label="Sections"' not in response.text, path
        assert re.findall(r'href="([^"]+)"', response.text) == [
            "/assets/tokens.css",
            "/static/app.css",
            "#main",
        ], path
        assert "<form" not in response.text, path
    # The post changed nothing and nothing was opened.
    assert store.list_overrides(course_id=course_id) == []
    course = store.get_course(course_id)
    assert course is not None and course.review_opened_at is None
    after = store.get_run(run_id)
    assert after is not None
    assert (after.status, after.starter_subject) == (before.status, before.starter_subject)


def test_the_refusal_page_loads_its_own_stylesheets(gated: Client) -> None:
    for path in ("/assets/tokens.css", "/static/app.css"):
        response = gated.as_(OUTSIDER, "GET", path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("text/css"), path


def test_the_health_check_answers_a_person_in_neither_group_with_its_status_alone(
    gated: Client,
) -> None:
    response = gated.as_(OUTSIDER, "GET", "/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert gated.get("/healthz").json() == {"status": "ok"}
    # A member gets the full report, as before.
    full = gated.as_(MEMBER, "GET", "/healthz").json()
    assert "run_store" in full


@pytest.mark.parametrize(
    "groups",
    [
        f"not-{USERS_GROUP}",
        f"{USERS_GROUP}-old",
        USERS_GROUP.upper(),
        f"fake-staff,{USERS_GROUP}",  # the wrong separator: one group with a comma in it
        f"{ADMIN_GROUP}s",
        "",
    ],
    ids=["prefixed", "suffixed", "case", "wrong-separator", "admin-suffixed", "no-groups"],
)
def test_membership_is_exact_never_a_substring(gated: Client, groups: str) -> None:
    response = gated.get("/", headers=headers_for(OUTSIDER, groups))
    assert response.status_code == 403
    assert USERS_GROUP in text_of(response.text)


def test_a_request_that_did_not_come_through_the_proxy_is_refused_before_membership(
    gated: Client,
) -> None:
    for groups in (GROUPS[OUTSIDER], GROUPS[MEMBER]):
        wrong = headers_for(OUTSIDER, groups, secret="fake-wrong-secret-" + "9" * 20)
        response = gated.get("/", headers=wrong)
        assert response.status_code == 401
        assert response.content == REFUSAL
        assert USERS_GROUP not in response.text


def test_a_refused_post_is_the_group_page_not_the_csrf_page(gated: Client) -> None:
    """The group check sits with the identity, before CSRF; neither says anything of the other."""
    response = gated.post(
        "/settings/model-host", headers=headers_for(OUTSIDER, GROUPS[OUTSIDER]), data={}
    )
    assert response.status_code == 403
    assert NOT_A_MEMBER_SUMMARY in text_of(response.text)
    assert REFUSED_SUMMARY not in text_of(response.text)


# -- in a group: admitted ------------------------------------------------------------------------


def test_a_member_of_the_users_group_is_admitted_and_is_not_an_admin(
    gated: Client, run_and_course: tuple[str, str]
) -> None:
    run_id, _ = run_and_course
    for path in ("/", "/runs", "/settings", "/courses", "/rubrics"):
        assert gated.as_(MEMBER, "GET", path).status_code == 200, path
    # Admitted is not admin: Alice's run is not theirs, and the instance settings refuse them.
    assert gated.as_(MEMBER, "GET", f"/runs/{run_id}").status_code == 404
    assert run_id not in gated.as_(MEMBER, "GET", "/runs").text
    posted = gated.as_(MEMBER, "POST", "/settings/model-host", data={}, follow_redirects=False)
    assert posted.status_code == 403
    assert NOT_A_MEMBER_SUMMARY not in text_of(posted.text)


def test_the_admin_group_alone_admits_as_an_admin(
    gated: Client, run_and_course: tuple[str, str]
) -> None:
    run_id, course_id = run_and_course
    for path in ("/", "/runs", f"/runs/{run_id}", f"/review/{course_id}", "/settings"):
        response = gated.as_(ADMIN_ONLY, "GET", path)
        assert response.status_code == 200, path
    assert run_id in gated.as_(ADMIN_ONLY, "GET", "/runs").text


def test_refusing_somebody_deletes_nothing_and_an_admin_still_sees_their_run(
    data_root: Path, run_and_course: tuple[str, str]
) -> None:
    run_id, course_id = run_and_course
    alice = headers_for(ALICE, f"fake-staff|{USERS_GROUP}")
    with Client(create_app(settings_for(data_root))) as client:
        assert client.get(f"/runs/{run_id}", headers=alice).status_code == 200
    # Alice is taken out of the group.
    removed = headers_for(ALICE, "fake-staff")
    with Client(create_app(settings_for(data_root))) as client:
        assert client.get(f"/runs/{run_id}", headers=removed).status_code == 403
        store: RunStore = client.app.state.store  # type: ignore[attr-defined]
        run = store.get_run(run_id)
        assert run is not None and run.starter_subject == ALICE
        assert client.as_(ADMIN_ONLY, "GET", f"/runs/{run_id}").status_code == 200
        assert client.as_(ADMIN_ONLY, "GET", f"/review/{course_id}").status_code == 200


# -- unset, and local mode -----------------------------------------------------------------------


def test_unset_admits_any_proxied_identity_and_startup_says_so(
    data_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = settings_for(data_root, signin_user_group=None)
    with Client(create_app(settings)) as client:
        assert client.get("/", headers=headers_for(OUTSIDER, None)).status_code == 200
        assert client.get("/", headers=headers_for(OUTSIDER, "fake-staff")).status_code == 200
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    said = [line for line in lines if line.get("event") == "signin_any_identity_admitted"]
    assert len(said) == 1, lines
    assert "SIGHTLINE_SIGNIN_USER_GROUP" in said[0]["note"]


def test_set_says_nothing_at_startup(data_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with Client(create_app(settings_for(data_root))):
        pass
    out = capsys.readouterr().out
    assert '"event": "startup"' in out, "the probe captured nothing"
    assert "signin_any_identity_admitted" not in out


def test_local_mode_ignores_the_users_group(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    local = settings.model_copy(update={"signin_user_group": USERS_GROUP})
    with Client(create_app(local)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/runs").status_code == 200
    assert "signin_any_identity_admitted" not in capsys.readouterr().out


def test_the_setting_comes_from_the_environment_and_blank_is_unset(
    monkeypatch: pytest.MonkeyPatch, data_root: Path
) -> None:
    monkeypatch.setenv("SIGHTLINE_SIGNIN_USER_GROUP", f"  {USERS_GROUP} ")
    assert Settings(_env_file=None, data_dir=data_root).signin_user_group == USERS_GROUP
    monkeypatch.setenv("SIGHTLINE_SIGNIN_USER_GROUP", "  ")
    assert Settings(_env_file=None, data_dir=data_root).signin_user_group is None
    # Not required in sign-in mode.
    monkeypatch.delenv("SIGHTLINE_SIGNIN_USER_GROUP")
    plain = Settings(_env_file=None, data_dir=data_root, signin=True, signin_secret=SECRET)
    assert plain.signin and plain.signin_user_group is None
