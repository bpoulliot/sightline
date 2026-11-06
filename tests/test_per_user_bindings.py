"""Rubric bindings belong to the person who accepts them (roadmap item 48, SPEC §09).

Sign-in mode, with the proxy written by hand as headers and the model host a recording; no
identity provider, no Canvas instance and no live model host. The acceptance, one or more tests
each:

    two people accept different bindings; each run uses
    its starter's digest; neither sees the other's       -> test_two_people_accept_different...
    an admin reads both histories and changes neither    -> test_an_admin_reads_every_history...
    a run recorded before the change keeps its bindings  -> test_a_run_recorded_before_item_48...
    the subject id is the key, never the display name    -> test_the_subject_id_is_the_key...
    proposing is kept for its proposer, one at a time    -> test_a_proposal_set_is_kept...,
                                                            test_somebody_elses_proposing...
    the command line reads the shipped pack by default   -> tests/test_rubric_screens.py

Local mode's own tests are tests/test_rubric_screens.py, unchanged but for the command-line one,
whose behaviour reading (d) changes.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import ollama_recordings as recordings
from canvas_recordings import EXAMPLE_BASE_URL
from form_clients import FormClient
from sightline import rubric_screens
from sightline.app import create_app
from sightline.batch import DEFAULT_RUBRIC_PACK
from sightline.batch import service as batch_service
from sightline.config import Settings
from sightline.credentials import CredentialStore
from sightline.detect.base import Tier
from sightline.llm.client import OllamaClient
from sightline.paths import DataPaths
from sightline.preferences import PreferenceStore
from sightline.proposing import ProposingState
from sightline.report.loader import load_review
from sightline.rubric.accepted import (
    PEOPLE,
    BindingsError,
    BindingStore,
    base_mapping,
    owner_key,
    owners,
    resolve_pack,
)
from sightline.rubric.pack import RubricPack
from sightline.rubric.proposals import ProposalSet, accept, propose
from sightline.rubric.shipped import shipped_pack
from sightline.store import RunStore

REPO = Path(__file__).resolve().parents[1]
SAMPLE_REVIEW = REPO / "examples" / "sample-report" / "review.json"
PACK = DEFAULT_RUBRIC_PACK
MODEL = "llama3.1:8b"
#: A documentation address (RFC 5737). Never reached: every client is given a recording.
MODEL_HOST = "http://192.0.2.10:11434"
CLOCK = "2026-11-05T09:00:00+00:00"
WAIT_SECONDS = 60.0

#: Obviously fake, and long enough for the 32-character floor.
SECRET = "fake-proxy-secret-0000000000000000000000"
ADMIN_GROUP = "fake-sightline-admins"

ALICE = "fake-subject-alice-0001"
BOB = "fake-subject-bob-0002"
ADMIN = "fake-subject-admin-0003"


def _token(tag: str) -> str:
    """A Canvas-shaped fake token, assembled so no token-shaped literal is in the tree."""
    return "7213" + "~" + "SightlineFixture" + tag + "NotReal" + ("0" * 12)


def name_of(subject: str) -> str:
    return f"Person {subject[-4:]}"


def headers(subject: str, *, name: str | None = None, admin: bool = False) -> dict[str, str]:
    """What the proxy (Authentik's outpost header names, the defaults) adds for ``subject``."""
    sent = {
        "X-Sightline-Proxy-Secret": SECRET,
        "X-authentik-uid": subject,
        "X-authentik-name": name or name_of(subject),
    }
    if admin:
        sent["X-authentik-groups"] = f"fake-staff|{ADMIN_GROUP}"
    return sent


class Client(FormClient):
    """A browser per request: each call names who is signed in, and its CSRF token follows."""

    def as_(self, subject: str, method: str, url: str, **kwargs: Any) -> Any:
        self.subject = subject
        sent = headers(subject, name=kwargs.pop("name", None), admin=subject == ADMIN)
        return self.request(method, url, headers=sent, **kwargs)


@pytest.fixture
def signin(data_root: Path) -> Iterator[Client]:
    settings = Settings(
        _env_file=None,
        data_dir=data_root,
        tokens_css_path=REPO / "docs" / "tokens.css",
        log_format="json",
        signin=True,
        signin_secret=SECRET,
        signin_admin_group=ADMIN_GROUP,
    )
    with Client(create_app(settings)) as client:
        yield client


def paths_of(client: Client) -> DataPaths:
    return client.app.state.paths  # type: ignore[attr-defined]


def store_of(client: Client) -> RunStore:
    return client.app.state.store  # type: ignore[attr-defined]


def text_of(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split()).replace("&#39;", "'")


# -- proposal sets, as the screen keeps them -----------------------------------------------------


def answer(*pairs: tuple[str, str]) -> str:
    return json.dumps({"proposals": [{"tag": tag, "span": span} for tag, span in pairs]})


def host_for(pack: RubricPack, plan: dict[str, str]) -> recordings.FakeHost:
    host = recordings.only([MODEL])
    bodies = [plan.get(c.code, answer()) for c in pack.criteria if c.tier is not Tier.D]
    host.chat_responses = [recordings.chat_response(MODEL, body) for body in bodies]
    return host


def make_set(pack: RubricPack, plan: dict[str, str]) -> ProposalSet:
    host = host_for(pack, plan)

    async def go() -> ProposalSet:
        async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=host.transport()) as c:
            return await propose(pack, c, MODEL, clock=lambda: CLOCK)

    return asyncio.run(go())


#: What each person proposes and accepts: one row each, never the same.
PLANS = {
    ALICE: ("COAL 3", "link_text"),
    BOB: ("COAL 4", "course_schedule"),
    ADMIN: ("COAL 5", "alt_text"),
}


def kept_set(client: Client, subject: str) -> str:
    """A proposal set over ``subject``'s bindings in force, kept in their own record."""
    paths = paths_of(client)
    code, tag = PLANS[subject]
    pack = resolve_pack(PACK, paths, starter=subject).pack
    store = BindingStore(paths, subject, name=name_of(subject))
    return store.save_proposals(make_set(pack, {code: answer((tag, ""))}))


def accept_through_screen(client: Client, subject: str, set_id: str) -> Any:
    """Show the change of the set's one proposal, then accept it, as ``subject``."""
    base = resolve_pack(PACK, paths_of(client), starter=subject).digest or ""
    href = f"/rubrics/{PACK}/proposals/{set_id}"
    fields = {"proposal": ["1"], "base": base}
    shown = client.as_(subject, "POST", href, data={"action": "preview", **fields})
    assert shown.status_code == 200, text_of(shown.text)[:1500]
    token = re.search(r'name="shown" value="([^"]+)"', shown.text)
    assert token is not None
    return client.as_(
        subject,
        "POST",
        href,
        data={"action": "accept", "shown": token.group(1), **fields},
        follow_redirects=False,
    )


def accepted_by(client: Client, subject: str) -> str:
    set_id = kept_set(client, subject)
    response = accept_through_screen(client, subject, set_id)
    assert response.status_code == 303, text_of(response.text)[:1500]
    digest = resolve_pack(PACK, paths_of(client), starter=subject).digest
    assert digest is not None
    return digest


async def _never(run_id: str) -> None:
    return None


def configure_instance(client: Client, *people: str) -> None:
    credentials: CredentialStore = client.app.state.credentials  # type: ignore[attr-defined]
    credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=_token("Instance"))
    for person in people:
        credentials.save_user_token(person, _token(person[-4:]))
    preferences: PreferenceStore = client.app.state.preferences  # type: ignore[attr-defined]
    preferences.set_model_host(MODEL_HOST)
    preferences.set_review_model(MODEL)
    preferences.set_request_content_export(False)


def start(client: Client, subject: str) -> str:
    response = client.as_(
        subject,
        "POST",
        "/runs",
        data={"course": ["4411"], "rubric": PACK, "label-4411": "Sample Course"},
        follow_redirects=False,
    )
    assert response.status_code == 303, text_of(response.text)[:1500]
    return response.headers["location"].removeprefix("/runs/")


# -- two people, one pack ------------------------------------------------------------------------


def test_two_people_accept_different_bindings_and_each_run_uses_their_own(
    signin: Client,
) -> None:
    alices = accepted_by(signin, ALICE)
    bobs = accepted_by(signin, BOB)
    assert alices != bobs

    paths = paths_of(signin)
    alice_pack = resolve_pack(PACK, paths, starter=ALICE).pack
    bob_pack = resolve_pack(PACK, paths, starter=BOB).pack
    assert alice_pack.criterion("COAL 3").bindings == ("link_text",)  # type: ignore[union-attr]
    assert alice_pack.criterion("COAL 4").bindings == ()  # type: ignore[union-attr]
    assert bob_pack.criterion("COAL 4").bindings == ("course_schedule",)  # type: ignore[union-attr]
    assert bob_pack.criterion("COAL 3").bindings == ()  # type: ignore[union-attr]
    # Nobody else's bindings moved: local mode's user and the admin still read the shipped pack.
    assert resolve_pack(PACK, paths).digest is None
    assert resolve_pack(PACK, paths, starter=ADMIN).digest is None

    # Each run started from the screen records its starter's digest.
    configure_instance(signin, ALICE, BOB)
    signin.app.state.runner.handler = _never  # type: ignore[attr-defined]
    alice_run = store_of(signin).get_run(start(signin, ALICE))
    bob_run = store_of(signin).get_run(start(signin, BOB))
    assert alice_run is not None and alice_run.bindings_digest == alices
    assert bob_run is not None and bob_run.bindings_digest == bobs

    # The New run picker says whose runs use what.
    from sightline.access import Viewer
    from sightline.new_run import rubric_choices

    def picker(subject: str) -> str | None:
        who = Viewer(subject=subject, name=name_of(subject), admin=False, signed_in=True)
        return {c.id: c for c in rubric_choices(paths=paths, viewer=who)}[PACK].accepted

    for subject in (ALICE, BOB):
        said = picker(subject) or ""
        assert said.startswith("Your runs use the bindings accepted on "), said
        assert said.endswith(f" by {name_of(subject)}."), said
    assert picker(ADMIN) is None


def test_neither_person_sees_the_others_proposals_or_accepted_state(signin: Client) -> None:
    alices = accepted_by(signin, ALICE)
    alice_open = kept_set(signin, ALICE)  # proposals Alice has not accepted
    bobs = accepted_by(signin, BOB)

    for subject, own, other, other_set in (
        (BOB, bobs, alices, alice_open),
        (ALICE, alices, bobs, None),
    ):
        for path in ("/rubrics", f"/rubrics/{PACK}"):
            page = signin.as_(subject, "GET", path).text
            other_name = name_of(ALICE if subject == BOB else BOB)
            assert other not in page, (subject, path)
            assert other_name not in page, (subject, path)
            assert "Your runs" in page, (subject, path)
        assert own in signin.as_(subject, "GET", f"/rubrics/{PACK}").text
        if other_set is not None:
            assert other_set not in signin.as_(subject, "GET", f"/rubrics/{PACK}").text
            # Another person's set answers exactly as a set id that names nothing.
            theirs = signin.as_(subject, "GET", f"/rubrics/{PACK}/proposals/{other_set}")
            fake = "20991231T000000Z-000000"
            nothing = signin.as_(subject, "GET", f"/rubrics/{PACK}/proposals/{fake}")
            assert theirs.status_code == nothing.status_code == 404
            assert theirs.text.replace(other_set, fake) == nothing.text
            posted = signin.as_(
                subject,
                "POST",
                f"/rubrics/{PACK}/proposals/{other_set}",
                data={"action": "accept", "proposal": ["1"]},
            )
            assert posted.status_code == 404
    # People pages are an admin's: anybody else gets 404, as for a key that names nothing.
    key = owner_key(ALICE)
    theirs = signin.as_(BOB, "GET", f"/rubrics/{PACK}/people/{key}")
    nothing = signin.as_(BOB, "GET", f"/rubrics/{PACK}/people/{'0' * 48}")
    assert theirs.status_code == nothing.status_code == 404
    assert theirs.text.replace(key, "0" * 48) == nothing.text
    assert "#people" not in signin.as_(BOB, "GET", "/rubrics").text


def test_an_admin_reads_every_history_and_changes_none_of_it(signin: Client) -> None:
    alices = accepted_by(signin, ALICE)
    bobs = accepted_by(signin, BOB)
    alice_open = kept_set(signin, ALICE)
    paths = paths_of(signin)
    before = {s: BindingStore(paths, s).history(PACK) for s in (ALICE, BOB)}

    listing = text_of(signin.as_(ADMIN, "GET", "/rubrics").text)
    assert "2 other person(s) keep bindings or proposals for this rubric" in listing
    page = signin.as_(ADMIN, "GET", f"/rubrics/{PACK}")
    people = page.text[page.text.index('id="people"') :]
    for subject, digest in ((ALICE, alices), (BOB, bobs)):
        assert name_of(subject) in people
        href = f"/rubrics/{PACK}/people/{owner_key(subject)}"
        assert f'href="{href}"' in people
        theirs = signin.as_(ADMIN, "GET", href)
        assert theirs.status_code == 200
        words = text_of(theirs.text)
        assert digest in words
        assert "Acceptance 1, 2" in words and f"by {name_of(subject)}" in words
        # Read-only: no form on the page acts on their bindings.
        assert "/revert" not in theirs.text and "/propose" not in theirs.text
        assert 'name="action"' not in theirs.text
    # The admin's own page is the admin's own bindings: none accepted.
    assert alices not in page.text[: page.text.index('id="people"')]

    # No route acts on another person's behalf: their sets are not the admin's, and a revert on
    # the admin's own page reverts only the admin's own history, which is empty.
    assert signin.as_(ADMIN, "GET", f"/rubrics/{PACK}/proposals/{alice_open}").status_code == 404
    accepted = signin.as_(
        ADMIN,
        "POST",
        f"/rubrics/{PACK}/proposals/{alice_open}",
        data={"action": "accept", "proposal": ["1"], "base": alices},
    )
    assert accepted.status_code == 404
    reverted = signin.as_(ADMIN, "POST", f"/rubrics/{PACK}/revert", data={"acceptance": "1"})
    assert reverted.status_code == 409
    after = {s: BindingStore(paths, s).history(PACK) for s in (ALICE, BOB)}
    assert after == before
    assert BindingStore(paths, ADMIN).history(PACK) == ()
    assert resolve_pack(PACK, paths, starter=ALICE).digest == alices
    assert resolve_pack(PACK, paths, starter=BOB).digest == bobs


# -- what was there before -----------------------------------------------------------------------


def legacy_acceptance(paths: DataPaths) -> str:
    """An acceptance as item 22 kept one, at the pack's own level: local mode's user's record."""
    shipped = shipped_pack(PACK)
    proposals = make_set(shipped, {"COAL 3": answer(("link_text", ""))})
    store = BindingStore(paths)
    saved = store.proposal_set(PACK, store.save_proposals(proposals))
    assert saved is not None
    result = accept(proposals, shipped, names=[1], base=base_mapping(shipped, shipped=shipped))
    entry = store.record_acceptance(
        saved, result, shipped=shipped, before=None, reviewer="Avery Example"
    )
    assert entry.after is not None
    return entry.after


def test_a_run_recorded_before_item_48_keeps_its_bindings(signin: Client) -> None:
    paths = paths_of(signin)
    ran_with = legacy_acceptance(paths)
    # Item 22's layout: the history and the sets at the pack's level, nothing under people/.
    root = paths.resolve("rubrics", PACK)
    assert (root / "history.json").is_file() and (root / "proposals").is_dir()
    assert (root / "states" / f"{ran_with.removeprefix('sha256:')}.json").is_file()
    store = store_of(signin)
    old = store.get_run(store.create_run(["4411"], rubric_pack=PACK, bindings_digest=ran_with))
    assert old is not None

    # Later, signed-in people accept their own; one of them also over the same row.
    accepted_by(signin, ALICE)
    accepted_by(signin, BOB)

    # Local mode's one user still owns what was accepted before, so a local install reads as now.
    assert resolve_pack(PACK, paths).digest == ran_with
    # The run resolves by its recorded digest, whoever's history it came from.
    resolved = batch_service.resolved_pack(old, paths)
    assert resolved.digest == ran_with
    assert resolved.pack.criterion("COAL 3").bindings == ("link_text",)  # type: ignore[union-attr]
    assert resolved.pack.criterion("COAL 4").bindings == ()  # type: ignore[union-attr]
    # And its review file re-exports with the same bindings.
    payload = json.loads(SAMPLE_REVIEW.read_text(encoding="utf-8"))
    payload["pack"]["bindings"] = ran_with
    loaded = load_review(payload, paths=paths)
    assert loaded.pack.criterion("COAL 3").bindings == ("link_text",)  # type: ignore[union-attr]
    # An admin sees the local-mode user's history beside everybody else's.
    people = owners(paths, PACK)
    assert [owner.subject for owner in people][0] is None
    assert {owner.subject for owner in people} == {None, ALICE, BOB}
    page = signin.as_(ADMIN, "GET", f"/rubrics/{PACK}/people/local")
    assert page.status_code == 200 and ran_with in page.text


# -- whose is named by the subject ---------------------------------------------------------------


def test_the_subject_id_is_the_key_never_the_display_name(signin: Client) -> None:
    paths = paths_of(signin)
    # Two people with one display name keep two records.
    one = BindingStore(paths, ALICE, name="Same Name")
    other = BindingStore(paths, BOB, name="Same Name")
    one.save_proposals(make_set(shipped_pack(PACK), {"COAL 3": answer(("link_text", ""))}))
    assert len(one.proposal_sets(PACK)) == 1
    assert other.proposal_sets(PACK) == ()
    # A renamed person keeps their history: the screen finds it by the subject.
    alices = accepted_by(signin, ALICE)
    renamed = signin.as_(ALICE, "GET", f"/rubrics/{PACK}", name="Alice Renamed")
    assert alices in renamed.text
    # The subject is never a path: the directory is a digest of it, and the owner file says whose.
    people_dir = paths.resolve("rubrics", PACK, PEOPLE)
    names = [str(p.relative_to(people_dir)) for p in people_dir.rglob("*")]
    assert names and not any(ALICE in name or BOB in name for name in names)
    owner_file = people_dir / owner_key(ALICE) / "owner.json"
    assert json.loads(owner_file.read_text(encoding="utf-8"))["subject"] == ALICE
    # An owner file edited to name another subject is refused, not read as theirs.
    owner_file.write_text(
        json.dumps({"owner_file": 1, "subject": BOB, "name": "x"}), encoding="utf-8"
    )
    with pytest.raises(BindingsError, match="has been edited"):
        owners(paths, PACK)
    with pytest.raises(BindingsError, match="has been edited"):
        BindingStore(paths, ALICE).save_proposals(
            make_set(shipped_pack(PACK), {"COAL 3": answer(("link_text", ""))})
        )


# -- proposing -----------------------------------------------------------------------------------


@pytest.fixture
def recorded_host(monkeypatch: pytest.MonkeyPatch) -> None:
    pack = shipped_pack(PACK)
    host = host_for(pack, {"COAL 3": answer(("link_text", ""))})
    original = rubric_screens.OllamaClient

    def build(endpoint, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", host.transport())
        return original(endpoint, **kwargs)

    monkeypatch.setattr(rubric_screens, "OllamaClient", build)


def test_a_proposal_set_is_kept_for_the_person_who_proposed(
    signin: Client, recorded_host: None
) -> None:
    preferences: PreferenceStore = signin.app.state.preferences  # type: ignore[attr-defined]
    preferences.set_model_host(MODEL_HOST)
    preferences.set_review_model(MODEL)
    started = signin.as_(ALICE, "POST", f"/rubrics/{PACK}/propose", follow_redirects=False)
    assert started.status_code == 303, text_of(started.text)[:1500]
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        payload = signin.as_(ALICE, "GET", f"/api/rubrics/{PACK}/proposing").json()
        if not payload["running"]:
            break
        assert time.monotonic() < deadline, payload
        time.sleep(0.05)
    assert payload["error"] is None and payload["set"]
    paths = paths_of(signin)
    assert BindingStore(paths, ALICE).proposal_set(PACK, payload["set"]) is not None
    assert BindingStore(paths, BOB).proposal_sets(PACK) == ()
    assert BindingStore(paths).proposal_sets(PACK) == ()
    # Bob is not shown Alice's progress, and the set is not his to open.
    assert signin.as_(BOB, "GET", f"/api/rubrics/{PACK}/proposing").json()["running"] is False
    assert payload["set"] not in signin.as_(BOB, "GET", f"/rubrics/{PACK}").text
    assert payload["set"] in signin.as_(ALICE, "GET", f"/rubrics/{PACK}").text


def test_somebody_elses_proposing_still_refuses_a_second_proposer_and_a_run(
    signin: Client,
) -> None:
    """One model host: proposing stays serialized against proposing and runs, whoever proposes."""
    configure_instance(signin, ALICE, BOB)
    registry = signin.app.state.proposing  # type: ignore[attr-defined]
    pack = shipped_pack(PACK)
    registry._states[(ALICE, PACK)] = ProposingState(
        pack_id=PACK,
        pack_name=pack.name,
        model=MODEL,
        over=None,
        to_ask=67,
        owner=ALICE,
        owner_name=name_of(ALICE),
    )
    refused = signin.as_(BOB, "POST", f"/rubrics/{PACK}/propose")
    assert refused.status_code == 409
    words = text_of(refused.text)
    assert f"Another person is proposing bindings for {pack.name}" in words
    assert name_of(ALICE) not in words
    run = signin.as_(
        BOB, "POST", "/runs", data={"course": ["4411"], "rubric": PACK}, follow_redirects=False
    )
    assert run.status_code == 409
    assert f"Another person is proposing bindings for {pack.name}" in text_of(run.text)
    assert store_of(signin).list_runs() == []
    # Alice herself is told it is hers, with the link to her page.
    hers = signin.as_(ALICE, "POST", f"/rubrics/{PACK}/propose")
    assert hers.status_code == 409
    assert f"Bindings are already being proposed for {pack.name}" in text_of(hers.text)
    listing = text_of(signin.as_(BOB, "GET", "/rubrics").text)
    assert f"Another person is proposing bindings for {pack.name}" in listing
