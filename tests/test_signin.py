"""Sign-in behind a trusted proxy, and a CSRF token on every form post (roadmap item 44).

Two modes, both fired. **Local mode** (the default) serves a request with no proxy headers, as it
always has. **Sign-in mode** refuses with 401, before any route, a request without the proxy's
shared secret, with a wrong one, or without a subject — with the header names Authentik's proxy
outpost sends by default, and with oauth2-proxy's (in front of Entra ID) set by configuration
alone. The CSRF half runs in both modes: a post without its token is refused with a page that says
to reload, and a post carrying the token its own page rendered is accepted.

No identity provider and no proxy is involved: the proxy is these headers, written by hand, with
obviously fake values.
"""

from __future__ import annotations

import asyncio
import io
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import ValidationError

from form_clients import FormClient
from sightline.app import TEMPLATE_DIR, create_app
from sightline.batch import (
    BatchRunner,
    CourseWork,
    Stage,
    StageOutcome,
    create_batch_run,
    pipeline_factory,
)
from sightline.config import Settings
from sightline.csrf import COOKIE, FIELD, HEADER, REFUSED_SUMMARY, CsrfTokens
from sightline.logging import configure_logging
from sightline.paths import DataPaths
from sightline.redaction import REDACTED, scrub
from sightline.signin import REFUSAL
from sightline.store import RunStore

REPO = Path(__file__).resolve().parents[1]
SAMPLE = REPO / "examples" / "sample-report" / "review.json"
REFERENCE = "uccs-oscqr-3-1-3"

#: Obviously fake, and long enough for the 32-character floor.
SECRET = "fake-proxy-secret-0000000000000000000000"
#: Sign-in mode's master key (item 50), generated per process: no key literal in the tree.
MASTER_KEY = Fernet.generate_key().decode()
SUBJECT = "fake-subject-7f3a9c"
NAME = "Pat Example"
ADMIN_GROUP = "fake-sightline-admins"

AUTHENTIK = {
    "secret": "X-Sightline-Proxy-Secret",
    "subject": "X-authentik-uid",
    "name": "X-authentik-name",
    "groups": "X-authentik-groups",
    "separator": "|",
}
#: oauth2-proxy with ``--pass-user-headers`` in front of Entra ID: the user is the token's
#: subject, the display name the preferred username, the groups Entra's object ids, comma-joined.
#: The secret header is whatever the proxy (or the server in front of it) is told to add.
OAUTH2_PROXY = {
    "secret": "X-Proxy-Shared-Secret",
    "subject": "X-Forwarded-User",
    "name": "X-Forwarded-Preferred-Username",
    "groups": "X-Forwarded-Groups",
    "separator": ",",
}


def signin_settings(data_root: Path, headers: dict[str, str] = AUTHENTIK, **extra: Any) -> Settings:
    names: dict[str, Any] = {}
    if headers is not AUTHENTIK:
        names = {
            "signin_secret_header": headers["secret"],
            "signin_subject_header": headers["subject"],
            "signin_name_header": headers["name"],
            "signin_groups_header": headers["groups"],
            "signin_groups_separator": headers["separator"],
        }
    return Settings(
        _env_file=None,
        data_dir=data_root,
        tokens_css_path=REPO / "docs" / "tokens.css",
        log_format="json",
        signin=True,
        signin_secret=SECRET,
        secret_key=MASTER_KEY,
        **names,
        **extra,
    )


def proxied(
    headers: dict[str, str] = AUTHENTIK,
    *,
    secret: str | None = SECRET,
    subject: str | None = SUBJECT,
    name: str | None = NAME,
    groups: str | None = None,
) -> dict[str, str]:
    """What the proxy adds to a request it forwards."""
    sent: dict[str, str] = {}
    if secret is not None:
        sent[headers["secret"]] = secret
    if subject is not None:
        sent[headers["subject"]] = subject
    if name is not None:
        sent[headers["name"]] = name
    if groups is not None:
        sent[headers["groups"]] = groups
    return sent


@pytest.fixture
def local(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def signin(data_root: Path) -> Iterator[TestClient]:
    with TestClient(create_app(signin_settings(data_root))) as client:
        yield client


#: A route of every kind: pages, a JSON endpoint, static files, a post. All behind the check.
ROUTES = [
    ("GET", "/"),
    ("GET", "/settings"),
    ("GET", "/courses"),
    ("GET", "/runs"),
    ("GET", "/rubrics"),
    ("GET", "/api/runs/nope"),
    ("GET", "/static/app.css"),
    ("GET", "/assets/tokens.css"),
    ("GET", "/docs"),
    ("POST", "/settings/reviewer"),
    ("POST", "/runs"),
]


# -- the proxy check ------------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_local_mode_serves_a_request_with_no_proxy_headers(local, method, path) -> None:
    response = local.request(method, path, follow_redirects=False)
    assert response.status_code != 401


@pytest.mark.parametrize(("method", "path"), ROUTES)
@pytest.mark.parametrize(
    "case",
    [
        {"secret": None},
        {"secret": "fake-proxy-secret-1111111111111111111111"},
        {"secret": SECRET[:-1]},
        {"secret": ""},
        {"subject": None},
        {"subject": "   "},
    ],
    ids=[
        "no-secret",
        "wrong-secret",
        "secret-prefix",
        "empty-secret",
        "no-subject",
        "blank-subject",
    ],
)
def test_sign_in_mode_refuses_before_any_route(signin, method, path, case) -> None:
    response = signin.request(method, path, headers=proxied(**case), follow_redirects=False)
    assert response.status_code == 401
    assert response.content == REFUSAL
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize(("method", "path"), [route for route in ROUTES if route[0] == "GET"])
def test_sign_in_mode_serves_what_came_through_the_proxy(signin, method, path) -> None:
    response = signin.request(method, path, headers=proxied())
    assert response.status_code != 401
    assert response.status_code < 500


def test_a_header_sent_twice_is_refused(signin) -> None:
    """A proxy that appends instead of replacing would let a client's own header ride along."""
    base = list(proxied().items())
    for doubled in (AUTHENTIK["secret"], AUTHENTIK["subject"], AUTHENTIK["name"]):
        value = "fake-subject-forged" if doubled != AUTHENTIK["secret"] else SECRET
        response = signin.get("/settings", headers=[*base, (doubled, value)])
        assert response.status_code == 401, doubled


def test_the_secret_header_name_is_matched_whatever_its_case(signin) -> None:
    headers = {name.upper(): value for name, value in proxied().items()}
    assert signin.get("/settings", headers=headers).status_code == 200


def test_oauth2_proxy_in_front_of_entra_works_by_configuration_alone(data_root: Path) -> None:
    with FormClient(
        create_app(signin_settings(data_root, OAUTH2_PROXY)), subject=SUBJECT
    ) as client:
        served = client.get("/settings", headers=proxied(OAUTH2_PROXY))
        assert served.status_code == 200
        assert NAME in served.text
        # Authentik's names mean nothing to this deployment.
        assert client.get("/settings", headers=proxied(AUTHENTIK)).status_code == 401
        assert client.get("/settings", headers=proxied(OAUTH2_PROXY, secret=None)).status_code == (
            401
        )
        assert client.get("/settings", headers=proxied(OAUTH2_PROXY, subject=None)).status_code == (
            401
        )


def test_a_name_in_utf8_reads_as_written_and_a_missing_name_falls_back_to_the_subject(
    signin,
) -> None:
    headers: dict[str, Any] = proxied(name=None)
    headers[AUTHENTIK["name"]] = "Zoë Example".encode()
    assert "Zoë Example" in signin.get("/settings", headers=headers).text
    assert SUBJECT in signin.get("/settings", headers=proxied(name=None)).text


def test_groups_are_read_with_the_configured_separator() -> None:
    from sightline.signin import SignInConfig, identify

    def config(separator: str) -> SignInConfig:
        return SignInConfig(
            secret=SECRET.encode(),
            secret_header="s",
            subject_header="u",
            name_header="n",
            groups_header="g",
            groups_separator=separator,
        )

    raw = [(b"s", SECRET.encode()), (b"u", SUBJECT.encode())]
    authentik = identify(config("|"), [*raw, (b"g", b"sightline admins| reviewers |")])
    assert authentik.groups == ("sightline admins", "reviewers")
    entra = identify(
        config(","),
        [
            *raw,
            (b"g", b"00000000-0000-0000-0000-000000000001,00000000-0000-0000-0000-000000000002"),
        ],
    )
    assert len(entra.groups) == 2
    assert identify(config("|"), raw).groups == ()
    assert identify(config("|"), raw).name == SUBJECT


# -- the health check -----------------------------------------------------------------------------


def test_the_health_check_answers_without_the_proxy_and_says_only_its_status(signin) -> None:
    bare = signin.get("/healthz")
    assert bare.status_code == 200
    assert bare.json() == {"status": "ok"}
    # Through the proxy it is the full report, as in local mode.
    full = signin.get("/healthz", headers=proxied()).json()
    assert full["status"] == "ok" and "run_store" in full and "data_root" in full


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/healthz"),
        ("HEAD", "/healthz"),
        ("GET", "/healthz/"),
        ("GET", "/healthzz"),
        ("GET", "/healthz/../"),
    ],
)
def test_nothing_else_rides_on_the_health_check(signin, method, path) -> None:
    assert signin.request(method, path, follow_redirects=False).status_code == 401


def test_local_mode_health_check_is_unchanged(local) -> None:
    body = local.get("/healthz").json()
    assert body["status"] == "ok" and "run_store" in body


# -- configuration --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"signin_secret": None},
        {"signin_secret": "short-fake-secret"},
        {"signin_secret": " " * 40},
        {"signin_name_header": AUTHENTIK["subject"].upper()},
        {"signin_subject_header": "X Bad Header"},
    ],
    ids=["no-secret", "short-secret", "blank-secret", "duplicate-names", "bad-name"],
)
def test_sign_in_mode_refuses_to_start_on_a_configuration_that_would_trust_anybody(
    data_root: Path, extra: dict[str, Any]
) -> None:
    base: dict[str, Any] = {
        "signin": True,
        "signin_secret": SECRET,
        "secret_key": MASTER_KEY,
        **extra,
    }
    with pytest.raises(ValidationError):
        Settings(_env_file=None, data_dir=data_root, **base)


def test_the_mode_and_its_header_names_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, data_root: Path
) -> None:
    monkeypatch.setenv("SIGHTLINE_SIGNIN", "true")
    monkeypatch.setenv("SIGHTLINE_SIGNIN_SECRET", SECRET)
    monkeypatch.setenv("SIGHTLINE_SECRET_KEY", MASTER_KEY)
    monkeypatch.setenv("SIGHTLINE_SIGNIN_SUBJECT_HEADER", OAUTH2_PROXY["subject"])
    settings = Settings(_env_file=None, data_dir=data_root)
    assert settings.signin and settings.signin_subject_header == OAUTH2_PROXY["subject"]
    assert settings.signin_name_header == AUTHENTIK["name"]
    monkeypatch.delenv("SIGHTLINE_SIGNIN")
    assert Settings(_env_file=None, data_dir=data_root).signin is False


# -- the reviewer ---------------------------------------------------------------------------------


class SavedReviewPipeline:
    def __init__(self, review: dict[str, Any]) -> None:
        self.review_payload = review

    async def run(self, stage: Stage, work: CourseWork) -> StageOutcome:
        return work.record(
            StageOutcome(stage=stage, examined=1, statement=f"{stage.value}: examined 1.")
        )

    def review(self, work: CourseWork) -> dict[str, Any]:
        return self.review_payload


@pytest.fixture
def course(paths: DataPaths) -> str:
    import json

    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    store = RunStore.open(paths)
    try:
        # Started by the person who reviews it: a run is its starter's (roadmap item 45).
        run_id = create_batch_run(
            store, ["examples/sample-course"], rubric_pack=REFERENCE, starter_subject=SUBJECT
        )
        pipeline = SavedReviewPipeline(payload)
        asyncio.run(BatchRunner(store, paths, pipeline_factory(pipeline)).execute(run_id))
        run = store.get_run(run_id)
        assert run is not None
        return run.courses[0].id
    finally:
        store.dispose()


def test_an_override_records_the_subject_beside_the_display_name(
    data_root: Path, course: str
) -> None:
    with FormClient(create_app(signin_settings(data_root)), subject=SUBJECT) as client:
        headers = proxied()
        page = client.get(f"/review/{course}/category/MOB", headers=headers)
        assert page.status_code == 200
        assert NAME in page.text
        response = client.post(
            f"/review/{course}/override",
            data={"code": "MOB 2", "rating": "major"},
            headers=headers,
            follow_redirects=False,
        )
        assert response.status_code == 303, response.text
        store: RunStore = client.app.state.store  # type: ignore[attr-defined]
        (record,) = store.list_overrides(course_id=course)
        assert (record.reviewer, record.reviewer_subject) == (NAME, SUBJECT)
        # Neither reaches the run's events.
        run = store.get_course(course)
        assert run is not None
        events = " ".join(repr(event.data) for event in store.events_in_order(run.run_id))
        assert SUBJECT not in events and NAME not in events


def test_local_mode_records_the_reviewer_setting_and_no_subject(settings, course: str) -> None:
    with FormClient(create_app(settings)) as client:
        assert client.post("/settings/reviewer", data={"reviewer": NAME}).status_code == 200
        client.get(f"/review/{course}/category/MOB")
        response = client.post(
            f"/review/{course}/override",
            data={"code": "MOB 2", "rating": "major"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        store: RunStore = client.app.state.store  # type: ignore[attr-defined]
        (record,) = store.list_overrides(course_id=course)
        assert (record.reviewer, record.reviewer_subject) == (NAME, None)


def test_a_record_made_before_the_column_keeps_its_name_and_has_no_subject(
    paths: DataPaths, course: str
) -> None:
    from sightline.rubric import Rating
    from sightline.rubric.overrides import MachineProposal, record_override

    store = RunStore.open(paths)
    try:
        proposal = MachineProposal(
            criterion="MOB 2",
            pack_id=REFERENCE,
            pack_version="3.1.3",
            rating=Rating("minor"),
            hours=None,
            near_boundary=None,
            findings=(),
            calibrated=False,
        )
        record_override(
            store, course_id=course, proposal=proposal, human=Rating("major"), reviewer=NAME
        )
        with store.engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE rating_overrides DROP COLUMN reviewer_subject")
    finally:
        store.dispose()
    reopened = RunStore.open(paths)
    try:
        (record,) = reopened.list_overrides(course_id=course)
        assert (record.reviewer, record.reviewer_subject) == (NAME, None)
        assert reopened.apply_added_columns() == []
    finally:
        reopened.dispose()


def test_settings_hides_the_reviewer_field_in_sign_in_mode(signin, local) -> None:
    signed = signin.get("/settings", headers=proxied()).text
    assert 'name="reviewer"' not in signed
    assert "/settings/reviewer" not in signed
    assert f"You are signed in as <strong>{NAME}</strong>" in signed
    assert SUBJECT not in signed
    unsigned = local.get("/settings").text
    assert 'name="reviewer"' in unsigned
    assert "You are signed in" not in unsigned


def test_the_reviewer_setting_cannot_be_posted_in_sign_in_mode(data_root: Path) -> None:
    with FormClient(create_app(signin_settings(data_root)), subject=SUBJECT) as client:
        for path, data in (
            ("/settings/reviewer", {"reviewer": "Somebody Else"}),
            ("/settings/reviewer/forget", {}),
        ):
            response = client.post(path, data=data, headers=proxied(), follow_redirects=False)
            assert response.status_code == 409
        preferences = client.app.state.preferences  # type: ignore[attr-defined]
        assert preferences.reviewer().name is None


# -- nothing leaks --------------------------------------------------------------------------------


def test_the_secret_and_the_identity_never_reach_a_log_or_a_refusal(
    data_root: Path, course: str
) -> None:
    stream = io.StringIO()
    with FormClient(create_app(signin_settings(data_root)), subject=SUBJECT) as client:
        configure_logging(level="DEBUG", fmt="json", stream=stream)
        try:
            refused = [
                client.get("/settings", headers=proxied(secret="fake-wrong-secret-" + "9" * 20)),
                client.get("/settings", headers=proxied(subject=None)),
                client.post(
                    "/settings/model-host", headers={**proxied(), HEADER: "fake-wrong"}, data={}
                ),
            ]
            client.get(f"/review/{course}/category/MOB", headers=proxied())
            client.post(
                f"/review/{course}/override",
                data={"code": "MOB 2", "rating": "major"},
                headers=proxied(),
            )
            # Something that does log a header mapping, as a careless debug line would.
            from sightline.logging import get_logger

            get_logger("sightline.test").debug("headers", headers=proxied())
        finally:
            configure_logging(level="INFO", fmt="json")
    logged = stream.getvalue()
    assert "signin_refused" in logged, "the probe captured nothing"
    assert [response.status_code for response in refused] == [401, 401, 403]
    for value in (SECRET, SUBJECT, NAME):
        assert value not in logged
        for response in refused:
            assert value not in response.text
    assert REDACTED in logged


def test_redaction_keeps_a_header_name_and_loses_its_value(data_root: Path) -> None:
    signin_settings(data_root)  # registers the secret and the header names
    lines = [
        f"{AUTHENTIK['secret']}: {SECRET}",
        f"x-authentik-uid: {SUBJECT}",
        f"{{'x-authentik-name': '{NAME}', 'host': 'testserver'}}",
        f"[(b'x-authentik-uid', b'{SUBJECT}'), (b'accept', b'*/*')]",
    ]
    for line in lines:
        cleaned = scrub(line)
        assert SECRET not in cleaned and SUBJECT not in cleaned and NAME not in cleaned, cleaned
        assert REDACTED in cleaned
    assert "testserver" in scrub(lines[2]) and "*/*" in scrub(lines[3])
    # A probe that redacted everything would pass the loop above: ordinary text survives.
    assert scrub("the x-authentik-uid header names the person") == (
        "the x-authentik-uid header names the person"
    )


# -- CSRF, in both modes --------------------------------------------------------------------------


def hidden_token(html: str) -> str:
    tokens = set(re.findall(rf'name="{FIELD}" value="([^"]+)"', html))
    assert len(tokens) == 1, tokens
    return tokens.pop()


@pytest.fixture(params=["local", "signin"])
def mode(request, settings: Settings, data_root: Path) -> Iterator[tuple[TestClient, dict]]:
    if request.param == "local":
        with TestClient(create_app(settings)) as client:
            yield client, {}
    else:
        # An admin: the forms these tests post include the instance settings (roadmap item 45).
        admin = signin_settings(data_root, signin_admin_group=ADMIN_GROUP)
        with TestClient(create_app(admin)) as client:
            yield client, proxied(groups=ADMIN_GROUP)


def test_a_form_post_without_its_token_is_refused_with_a_page_that_says_to_reload(mode) -> None:
    client, headers = mode
    client.get("/settings", headers=headers)  # the page that sets the cookie
    response = client.post(
        "/settings/model-host",
        data={"model_host_endpoint": "http://models.example.lan:11434"},
        headers=headers,
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert REFUSED_SUMMARY in response.text
    assert "Reload the page" in response.text
    preferences = client.app.state.preferences  # type: ignore[attr-defined]
    assert preferences.model_host().source.value != "stored"


def test_a_wrong_token_and_a_token_without_its_cookie_are_refused(mode) -> None:
    client, headers = mode
    token = hidden_token(client.get("/settings", headers=headers).text)
    wrong = client.post(
        "/settings/export", data={FIELD: token[:-2] + "AA"}, headers=headers, follow_redirects=False
    )
    assert wrong.status_code == 403
    client.cookies.clear()
    stray = client.post(
        "/settings/export", data={FIELD: token}, headers=headers, follow_redirects=False
    )
    assert stray.status_code == 403


def test_the_token_a_page_renders_is_accepted_as_the_form_field_and_as_the_header(mode) -> None:
    client, headers = mode
    token = hidden_token(client.get("/settings", headers=headers).text)
    by_field = client.post(
        "/settings/model-host",
        data={FIELD: token, "model_host_endpoint": "http://models.example.lan:11434"},
        headers=headers,
        follow_redirects=False,
    )
    assert by_field.status_code == 303, by_field.text
    preferences = client.app.state.preferences  # type: ignore[attr-defined]
    # The route read the body the middleware had already read: it reached the store.
    assert preferences.model_host().endpoint == "http://models.example.lan:11434"
    by_header = client.post(
        "/settings/model-host/forget", headers={**headers, HEADER: token}, follow_redirects=False
    )
    assert by_header.status_code == 303
    assert preferences.model_host().source.value != "stored"


def test_a_multipart_form_carries_its_token_too(mode) -> None:
    client, headers = mode
    token = hidden_token(client.get("/courses", headers=headers).text)
    files = {"unrelated": ("note.txt", b"fake", "text/plain")}
    accepted = client.post(
        "/courses", data={FIELD: token, "references": ""}, files=files, headers=headers
    )
    assert accepted.status_code == 200
    assert "Nothing to resolve" in accepted.text
    refused = client.post("/courses", data={"references": ""}, files=files, headers=headers)
    assert refused.status_code == 403


def test_every_form_on_settings_posts_back_with_the_token_it_rendered(mode) -> None:
    """Every existing form still works: each is posted as a browser would, field for field."""
    client, headers = mode
    html = client.get("/settings", headers=headers).text
    forms = re.findall(r'<form method="post" action="([^"]+)"[^>]*>(.*?)</form>', html, re.S)
    assert len(forms) >= 6
    for action, body in forms:
        data = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', body))
        assert FIELD in data, action
        response = client.post(action, data=data, headers=headers, follow_redirects=False)
        assert response.status_code != 403, action


def test_a_token_rendered_for_one_signed_in_person_is_refused_for_another(signin) -> None:
    token = hidden_token(signin.get("/settings", headers=proxied()).text)
    other = proxied(subject="fake-subject-someone-else")
    response = signin.post(
        "/settings/export", data={FIELD: token}, headers=other, follow_redirects=False
    )
    assert response.status_code == 403


def test_the_cookie_is_set_only_where_a_form_was_rendered(local) -> None:
    for path in ("/static/app.css", "/assets/tokens.css", "/healthz", "/api/runs/nope"):
        assert "set-cookie" not in local.get(path).headers, path
    cookie = local.get("/settings").headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE}=")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Path=/" in cookie
    # Once the browser holds one it is kept, not replaced.
    assert "set-cookie" not in local.get("/settings").headers


def test_every_post_form_in_every_template_carries_the_field() -> None:
    for template in sorted(TEMPLATE_DIR.glob("*.html")):
        text = template.read_text(encoding="utf-8")
        for match in re.finditer(r'<form method="post"[^>]*>', text):
            following = text[match.end() : match.end() + 80]
            assert following.lstrip().startswith(
                "{{ csrf_field() }}"
            ), f"{template.name}: {match.group(0)}"
    # And the scan has something to find.
    assert len(list(TEMPLATE_DIR.glob("*.html"))) > 5


def test_tokens_are_bound_to_the_nonce_and_the_subject() -> None:
    tokens = CsrfTokens(key=b"k" * 32)
    nonce = CsrfTokens.new_nonce()
    token = tokens.token(nonce, SUBJECT)
    assert tokens.valid(nonce, SUBJECT, token)
    assert not tokens.valid(nonce, "", token)
    assert not tokens.valid(CsrfTokens.new_nonce(), SUBJECT, token)
    assert not CsrfTokens(key=b"j" * 32).valid(nonce, SUBJECT, token)
