"""Roadmap item 22, part two: bindings proposed, confirmed and reverted on a screen.

Against the recorded model host only (``tests/ollama_recordings.py``), never a live one, and with
no Canvas contact. The acceptance criteria, one or more tests each:

    every box starts unchecked                      -> test_every_box_starts_unchecked
    check all checks every non-conflict, no conflict -> test_check_all_checks_every_proposal...
    conflicts listed apart, each its own box        -> test_conflicts_are_listed_apart...
    what, why and how for each proposal             -> test_each_proposal_shows_what_why_and_how
    the delta of the checked set before accepting   -> test_the_change_is_shown_before..., ...
    Tier D reads not asked; a failure is a failure  -> test_tier_d_reads_not_asked..., ...
    accept records reviewer, time, numbers, delta   -> test_accepting_records...
    revert restores the state before; history kept  -> test_revert_restores...
    a set made over another state is refused        -> test_a_set_made_over_another_state...
    the screen proposes, in the background          -> test_proposing_from_the_screen...
    proposing and a run refuse each other           -> test_proposing_is_refused_while..., ...
    the picker says proposed, not confirmed         -> test_the_picker_says...
    ONE resolver; a run records its digest           -> test_a_run_started_from_the_screen..., ...
    THE TRAP: an old run renders with its bindings  -> test_an_old_runs_report_renders_with...
    no confidence, ranking or model order           -> test_no_confidence_shaped_word...
    the voice lint covers every new sentence        -> test_the_register_lint_passes...
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from collections.abc import Iterator, Sequence
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx
import pytest

import ollama_recordings as recordings
from canvas_recordings import EXAMPLE_BASE_URL, FAKE_TOKEN
from form_clients import FormClient as TestClient
from run_owners import OTHER_HOST, set_owner
from sightline import rubric_screens
from sightline.app import TEMPLATE_DIR, create_app
from sightline.batch import DEFAULT_RUBRIC_PACK
from sightline.batch import service as batch_service
from sightline.batch.__main__ import main as batch_main
from sightline.batch.pipeline import BundleSource, CourseWork, ReviewPipeline
from sightline.config import Settings, reset_settings_cache
from sightline.detect.base import Tier
from sightline.llm.client import OllamaClient
from sightline.models import RunStatus
from sightline.paths import DataPaths
from sightline.proposing import ProposingState
from sightline.report.loader import ReviewFileError, load_review, read_review
from sightline.rubric.accepted import (
    BindingsError,
    BindingState,
    BindingStore,
    base_mapping,
    resolve_pack,
    standing,
    state_of,
)
from sightline.rubric.pack import RubricPack
from sightline.rubric.proposals import Acceptance, ProposalSet, accept, fingerprint, propose
from sightline.rubric.shipped import PACKS_DIR_ENV, packs_dir, shipped_pack
from sightline.store import RunStore
from sightline.voice.lint import default_register, mask

REPO = Path(__file__).resolve().parents[1]
SAMPLE_REVIEW = REPO / "examples" / "sample-report" / "review.json"
PACK = DEFAULT_RUBRIC_PACK  # uccs-oscqr-3-1-3: unbound rows, bound rows and a Tier D row
MODEL = "llama3.1:8b"
MODEL_HOST = "http://192.0.2.10:11434"
CLOCK = "2026-10-30T09:00:00+00:00"
WAIT_SECONDS = 60.0

#: Part one's vocabulary scan (tests/test_rubric_proposals.py), extended to the screens.
CONFIDENCE_WORDS = (
    "confidence",
    "confident",
    "score",
    "scores",
    "probability",
    "probable",
    "likely",
    "likelihood",
    "rank",
    "ranked",
    "ranking",
    "strength",
    "strong",
    "weak",
    "certain",
    "certainty",
    "weight",
)
CONFIDENCE = re.compile(r"\b(" + "|".join(CONFIDENCE_WORDS) + r")\b", re.IGNORECASE)


# -- building a proposal set --------------------------------------------------------------------


def answer(*pairs: tuple[str, str]) -> str:
    return json.dumps({"proposals": [{"tag": tag, "span": span} for tag, span in pairs]})


def opening_words(pack: RubricPack, code: str, words: int = 3) -> str:
    criterion = pack.criterion(code)
    assert criterion is not None
    return " ".join(criterion.text.split()[:words])


def planned(pack: RubricPack) -> dict[str, str]:
    """The answers the recorded host gives, by row. Every other asked row gets an empty list.

    COAL 3 and COAL 4 are bound to nothing; ACD 5 is bound to ``link_text`` already, so a proposal
    there is a conflict; ENIN 2's answer does not fit the schema, so it is a row that could not be
    asked; MOB 5 is Tier D and is never sent.
    """
    return {
        "COAL 3": answer(("link_text", opening_words(pack, "COAL 3")), ("alt_text", "")),
        "COAL 4": answer(("course_schedule", "words that are not in the criterion at all")),
        "ACD 5": answer(("alt_text", "")),
        "ENIN 2": '{"proposals": "not a list"}',
    }


def bodies_for(pack: RubricPack, plan: dict[str, str]) -> list[str]:
    return [plan.get(c.code, answer()) for c in pack.criteria if c.tier is not Tier.D]


def host_for(pack: RubricPack, plan: dict[str, str]) -> recordings.FakeHost:
    host = recordings.only([MODEL])
    host.chat_responses = [recordings.chat_response(MODEL, b) for b in bodies_for(pack, plan)]
    return host


def make_set(pack: RubricPack, plan: dict[str, str] | None = None) -> ProposalSet:
    host = host_for(pack, planned(pack) if plan is None else plan)

    async def go() -> ProposalSet:
        async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=host.transport()) as c:
            return await propose(pack, c, MODEL, clock=lambda: CLOCK)

    return asyncio.run(go())


def accept_on(proposals: ProposalSet, pack: RubricPack, **kwargs: Any) -> Acceptance:
    """``accept`` with the base mapping the screen passes: both shipped packs declare a join key."""
    return accept(proposals, pack, base=base_mapping(pack, shipped=shipped_pack(pack.id)), **kwargs)


def by_tag(proposals: ProposalSet, code: str, tag: str) -> int:
    (found,) = [p.id for p in proposals.proposals if p.code == code and p.tag == tag]
    return found


# -- the application ------------------------------------------------------------------------------


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def paths_of(client: TestClient) -> DataPaths:
    return client.app.state.paths  # type: ignore[attr-defined]


def store_of(client: TestClient) -> RunStore:
    return client.app.state.store  # type: ignore[attr-defined]


def set_reviewer(client: TestClient, name: str = "Avery Example") -> None:
    client.app.state.preferences.set_reviewer(name)  # type: ignore[attr-defined]


def configure_model(client: TestClient) -> None:
    preferences = client.app.state.preferences  # type: ignore[attr-defined]
    preferences.set_model_host(MODEL_HOST)
    preferences.set_review_model(MODEL)


def configure_canvas(client: TestClient) -> None:
    response = client.post(
        "/settings",
        data={"canvas_base_url": EXAMPLE_BASE_URL, "canvas_token": FAKE_TOKEN},
        follow_redirects=False,
    )
    assert response.status_code == 303


def kept_set(client: TestClient, plan: dict[str, str] | None = None) -> tuple[str, ProposalSet]:
    """A proposal set over the pack as runs use it now, kept as the screen keeps one."""
    pack = resolve_pack(PACK, paths_of(client)).pack
    proposals = make_set(pack, plan)
    set_id = BindingStore(paths_of(client)).save_proposals(proposals)
    return set_id, proposals


def set_href(set_id: str) -> str:
    return f"/rubrics/{PACK}/proposals/{set_id}"


def post(client: TestClient, set_id: str, action: str, **fields: Any) -> Any:
    data: dict[str, Any] = {"action": action, "base": fields.pop("base", "")}
    data.update(fields)
    return client.post(set_href(set_id), data=data, follow_redirects=False)


def shown_token(html: str) -> str | None:
    match = re.search(r'name="shown" value="([^"]+)"', html)
    return match.group(1) if match else None


def accept_through_screen(
    client: TestClient, set_id: str, names: Sequence[int], conflicts: Sequence[int] = ()
) -> Any:
    base = resolve_pack(PACK, paths_of(client)).digest or ""
    shown = post(
        client, set_id, "preview", proposal=list(names), conflict=list(conflicts), base=base
    )
    if shown.status_code != 200:
        return shown
    return post(
        client,
        set_id,
        "accept",
        proposal=list(names),
        conflict=list(conflicts),
        base=base,
        shown=shown_token(shown.text),
    )


# -- reading pages --------------------------------------------------------------------------------


class Node:
    def __init__(self, tag: str, attrs: dict[str, str | None], parent: Node | None) -> None:
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list[Node | str] = []

    def text(self) -> str:
        parts = [c if isinstance(c, str) else c.text() for c in self.children]
        return " ".join(" ".join(parts).split())

    def walk(self) -> Iterator[Node]:
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()


class _Tree(HTMLParser):
    VOID = {"input", "br", "meta", "link", "img", "hr"}

    def __init__(self) -> None:
        super().__init__()
        self.root = Node("root", {}, None)
        self.at = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, dict(attrs), self.at)
        self.at.children.append(node)
        if tag not in self.VOID:
            self.at = node

    def handle_endtag(self, tag: str) -> None:
        node: Node | None = self.at
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.at = node.parent

    def handle_data(self, data: str) -> None:
        if self.at.tag not in {"script", "style"}:
            self.at.children.append(data)


def main_of(html: str) -> Node:
    tree = _Tree()
    tree.feed(html)
    (main,) = [n for n in tree.root.walk() if n.tag == "main"]
    return main


def by_id(html: str, element_id: str) -> Node:
    (found,) = [n for n in main_of(html).walk() if n.attrs.get("id") == element_id]
    return found


def boxes(node: Node) -> list[dict[str, str | None]]:
    return [n.attrs for n in node.walk() if n.tag == "input" and n.attrs.get("type") == "checkbox"]


def text_of(html: str) -> str:
    return main_of(html).text()


# -- the confirmation screen: the 2026-09-08 decisions --------------------------------------------


def test_every_box_starts_unchecked(client: TestClient) -> None:
    set_id, proposals = kept_set(client)
    html = client.get(set_href(set_id)).text
    found = boxes(main_of(html))
    assert len(found) == len(proposals.proposals) == 4
    assert all("checked" not in attrs for attrs in found)


def test_check_all_checks_every_proposal_that_is_not_a_conflict_and_no_conflict(
    client: TestClient,
) -> None:
    set_id, proposals = kept_set(client)
    html = post(client, set_id, "check_all").text
    plain = boxes(by_id(html, "proposals"))
    crossed = boxes(by_id(html, "conflicts"))
    assert {a["value"] for a in plain} == {str(p.id) for p in proposals.proposals if not p.conflict}
    assert all("checked" in a for a in plain)
    assert crossed and all("checked" not in a for a in crossed)
    # Check all shows the change of what it checked, which is the delta accept() computes.
    expected = accept_on(
        proposals,
        resolve_pack(PACK, paths_of(client)).pack,
        names=[p.id for p in proposals.proposals if not p.conflict],
    ).delta.statement()
    assert expected in by_id(html, "change").text()


def test_conflicts_are_listed_apart_and_each_needs_its_own_box(client: TestClient) -> None:
    set_id, proposals = kept_set(client)
    conflict = by_tag(proposals, "ACD 5", "alt_text")
    html = client.get(set_href(set_id)).text
    crossed = boxes(by_id(html, "conflicts"))
    assert [(a["name"], a["value"]) for a in crossed] == [("conflict", str(conflict))]
    assert str(conflict) not in {a["value"] for a in boxes(by_id(html, "proposals"))}
    assert "bound to link_text now" in by_id(html, "conflicts").text()


def test_each_proposal_shows_what_why_and_how(client: TestClient) -> None:
    set_id, proposals = kept_set(client)
    html = client.get(set_href(set_id)).text
    for proposal in proposals.proposals:
        card = by_id(html, f"proposal-{proposal.id}")
        text = card.text()
        assert proposal.tag in text and proposal.summary in text  # what
        assert proposal.changes in text  # how: part one's computed consequence
        (why,) = [n for n in card.walk() if "why" in (n.attrs.get("class") or "").split()]
        assert why.attrs["data-span-state"] == proposal.span_state
    verified = proposals.get(by_tag(proposals, "COAL 3", "link_text"))
    assert verified is not None and verified.span_state == "verified"
    assert verified.span in by_id(html, f"proposal-{verified.id}").text()
    discarded = by_tag(proposals, "COAL 4", "course_schedule")
    card = by_id(html, f"proposal-{discarded}").text()
    assert "No supporting span" in card and "not in the criterion at all" not in card
    absent = by_tag(proposals, "COAL 3", "alt_text")
    assert "The model named no words" in by_id(html, f"proposal-{absent}").text()
    # The detectors behind a tag, by id, each with what it reports.
    assert re.search(r"D\d+ reports", by_id(html, f"proposal-{verified.id}").text())


def test_tier_d_reads_not_asked_and_a_failure_reads_as_a_failure(client: TestClient) -> None:
    set_id, _ = kept_set(client)
    html = client.get(set_href(set_id)).text
    others = by_id(html, "others")
    rows = {n.attrs["data-code"]: n.text() for n in others.walk() if n.attrs.get("data-code")}
    assert "Not asked: Tier D" in rows["MOB 5"]
    assert "Could not be asked" in rows["ENIN 2"]
    assert "None proposed" not in rows["ENIN 2"]
    assert "None proposed" in rows["COAL 5"]


def test_proposals_keep_part_ones_order_never_the_models(client: TestClient) -> None:
    """The host returned COAL 3's tags as link_text then alt_text; the page lists them by name."""
    set_id, proposals = kept_set(client)
    html = client.get(set_href(set_id)).text
    shown = [
        int(n.attrs["data-proposal"] or 0)
        for n in main_of(html).walk()
        if n.attrs.get("data-proposal")
    ]
    plain = [p.id for p in proposals.proposals if not p.conflict]
    assert shown[: len(plain)] == plain == sorted(plain)
    coal3 = [proposals.get(i).tag for i in plain if proposals.get(i).code == "COAL 3"]  # type: ignore[union-attr]
    assert coal3 == ["alt_text", "link_text"]


# -- the change before the act, and the act -------------------------------------------------------


def test_the_change_is_shown_before_accept_is_offered(client: TestClient) -> None:
    set_id, proposals = kept_set(client)
    set_reviewer(client)
    page = client.get(set_href(set_id)).text
    (accept_button,) = [
        n for n in main_of(page).walk() if n.tag == "button" and n.attrs.get("value") == "accept"
    ]
    assert "disabled" in accept_button.attrs
    name = by_tag(proposals, "COAL 3", "link_text")
    shown = post(client, set_id, "preview", proposal=[name]).text
    expected = accept_on(proposals, resolve_pack(PACK, paths_of(client)).pack, names=[name])
    assert expected.delta.statement() in by_id(shown, "change").text()
    assert "COAL 3" in by_id(shown, "change").text()
    (accept_button,) = [
        n for n in main_of(shown).walk() if n.tag == "button" and n.attrs.get("value") == "accept"
    ]
    assert "disabled" not in accept_button.attrs


def test_accept_without_the_change_shown_writes_nothing(client: TestClient) -> None:
    set_id, proposals = kept_set(client)
    set_reviewer(client)
    name = by_tag(proposals, "COAL 3", "link_text")
    refused = post(client, set_id, "accept", proposal=[name])
    assert refused.status_code == 409
    assert "changed after their change was shown" in text_of(refused.text)
    assert BindingStore(paths_of(client)).history(PACK) == ()


def test_accept_after_the_checked_set_changed_writes_nothing(client: TestClient) -> None:
    set_id, proposals = kept_set(client)
    set_reviewer(client)
    one, two = by_tag(proposals, "COAL 3", "link_text"), by_tag(proposals, "COAL 3", "alt_text")
    shown = shown_token(post(client, set_id, "preview", proposal=[one]).text)
    refused = post(client, set_id, "accept", proposal=[one, two], shown=shown)
    assert refused.status_code == 409
    assert BindingStore(paths_of(client)).history(PACK) == ()
    # The page shows the change of what is checked now, so a second Accept can record it.
    assert shown_token(refused.text) != shown


def test_nothing_checked_accepts_nothing(client: TestClient) -> None:
    set_id, _ = kept_set(client)
    set_reviewer(client)
    page = post(client, set_id, "preview").text
    assert "Nothing is checked" in by_id(page, "change").text()
    assert post(client, set_id, "accept", shown="p:;c:").status_code == 409
    assert BindingStore(paths_of(client)).history(PACK) == ()


def test_accepting_is_refused_without_a_reviewer(client: TestClient) -> None:
    set_id, proposals = kept_set(client)
    response = accept_through_screen(client, set_id, [by_tag(proposals, "COAL 3", "link_text")])
    assert response.status_code == 409
    assert "No reviewer is set" in text_of(response.text)
    assert BindingStore(paths_of(client)).history(PACK) == ()


def test_a_conflict_named_as_a_plain_proposal_is_refused(client: TestClient) -> None:
    set_id, proposals = kept_set(client)
    set_reviewer(client)
    conflict = by_tag(proposals, "ACD 5", "alt_text")
    response = accept_through_screen(client, set_id, [conflict])
    assert response.status_code == 409
    assert "is a conflict" in text_of(response.text)


def test_accepting_records_who_when_what_and_the_delta(client: TestClient) -> None:
    set_id, proposals = kept_set(client)
    set_reviewer(client)
    paths = paths_of(client)
    name = by_tag(proposals, "COAL 3", "link_text")
    conflict = by_tag(proposals, "ACD 5", "alt_text")
    shipped = shipped_pack(PACK)
    expected = accept_on(proposals, shipped, names=[name], conflicts=[conflict])

    response = accept_through_screen(client, set_id, [name], [conflict])
    assert response.status_code == 303, response.text[:2000]
    (entry,) = BindingStore(paths).history(PACK)
    assert entry.kind == "accepted" and entry.reviewer == "Avery Example"
    assert entry.at and entry.proposal_set == set_id
    assert entry.accepted == (name, conflict) and entry.conflicts == (conflict,)
    assert entry.added == (("COAL 3", "link_text"), ("ACD 5", "alt_text"))
    assert entry.before is None
    assert entry.delta.statement == expected.delta.statement()

    resolved = resolve_pack(PACK, paths)
    assert resolved.digest == entry.after
    assert resolved.pack.criterion("COAL 3").bindings == ("link_text",)  # type: ignore[union-attr]
    assert resolved.pack.criterion("ACD 5").bindings == ("link_text", "alt_text")  # type: ignore[union-attr]
    # The shipped file is never written.
    assert shipped_pack(PACK).criterion("COAL 3").bindings == ()  # type: ignore[union-attr]
    # The state is kept immutably under its own digest.
    state = BindingStore(paths).state(PACK, entry.after)  # type: ignore[arg-type]
    assert state.digest == entry.after
    # The rubric's page shows the delta after accepting, as the artifact.
    page = client.get(response.headers["location"]).text
    assert expected.delta.statement() in text_of(page)
    # The set is accepted once, as a unit: it no longer accepts anything.
    again = accept_through_screen(client, set_id, [by_tag(proposals, "COAL 4", "course_schedule")])
    assert again.status_code == 409
    assert len(BindingStore(paths).history(PACK)) == 1


def test_an_edited_state_file_is_refused(client: TestClient) -> None:
    set_id, proposals = kept_set(client)
    set_reviewer(client)
    accept_through_screen(client, set_id, [by_tag(proposals, "COAL 3", "link_text")])
    paths = paths_of(client)
    digest = resolve_pack(PACK, paths).digest
    assert digest is not None
    path = paths.resolve("rubrics", PACK, "states", digest.removeprefix("sha256:") + ".json")
    edited = path.read_text(encoding="utf-8").replace('"link_text"', '"alt_text"', 1)
    paths.save_text("rubrics", PACK, "states", path.name, text=edited)
    with pytest.raises(BindingsError, match="edited"):
        resolve_pack(PACK, paths)


# -- revert ---------------------------------------------------------------------------------------


def test_revert_restores_the_state_before_the_latest_acceptance_and_keeps_history(
    client: TestClient,
) -> None:
    set_reviewer(client)
    paths = paths_of(client)
    first_id, first = kept_set(client)
    accept_through_screen(client, first_id, [by_tag(first, "COAL 3", "link_text")])
    after_first = resolve_pack(PACK, paths).digest
    second_id, second = kept_set(client, {"COAL 4": answer(("course_schedule", ""))})
    accept_through_screen(client, second_id, [by_tag(second, "COAL 4", "course_schedule")])
    store = BindingStore(paths)
    two = store.history(PACK)[-1]
    assert resolve_pack(PACK, paths).digest == two.after != after_first

    # The page offers the latest standing acceptance, with what reverting it changes.
    page = client.get(f"/rubrics/{PACK}").text
    assert f"Revert acceptance {two.number}" in by_id(page, "revert").text()
    response = client.post(
        f"/rubrics/{PACK}/revert", data={"acceptance": two.number}, follow_redirects=False
    )
    assert response.status_code == 303
    history = store.history(PACK)
    assert [e.kind for e in history] == ["accepted", "accepted", "reverted"]
    assert history[-1].reverts == two.number and history[-1].reviewer == "Avery Example"
    assert resolve_pack(PACK, paths).digest == after_first
    # The second set is open again: the state it was made over is back in force.
    assert (
        rubric_screens.set_state(
            store.proposal_set(PACK, second_id),  # type: ignore[arg-type]
            fingerprint(resolve_pack(PACK, paths).pack),
            history,
        )[0]
        == "open"
    )
    # A stale revert (the one just undone) is refused, and nothing changes.
    stale = client.post(f"/rubrics/{PACK}/revert", data={"acceptance": two.number})
    assert stale.status_code == 409
    assert len(store.history(PACK)) == 3
    # Reverting the first goes back to the shipped bindings.
    client.post(f"/rubrics/{PACK}/revert", data={"acceptance": history[0].number})
    assert resolve_pack(PACK, paths).digest is None
    assert standing(store.history(PACK)) == ()
    page = client.get(f"/rubrics/{PACK}").text
    assert "Reverted by entry" in by_id(page, "history").text()


def test_revert_is_refused_without_a_reviewer(client: TestClient) -> None:
    set_reviewer(client)
    set_id, proposals = kept_set(client)
    accept_through_screen(client, set_id, [by_tag(proposals, "COAL 3", "link_text")])
    client.app.state.preferences.clear_reviewer()  # type: ignore[attr-defined]
    response = client.post(f"/rubrics/{PACK}/revert", data={"acceptance": 1})
    assert response.status_code == 409
    assert len(BindingStore(paths_of(client)).history(PACK)) == 1


# -- the trap: a set made over another state ------------------------------------------------------


def test_a_set_made_over_another_state_is_refused_and_says_to_propose_again(
    client: TestClient,
) -> None:
    set_reviewer(client)
    old_id, old = kept_set(client)
    new_id, new = kept_set(client, {"COAL 4": answer(("course_schedule", ""))})
    accept_through_screen(client, new_id, [by_tag(new, "COAL 4", "course_schedule")])
    page = client.get(set_href(old_id)).text
    assert "Propose again" in text_of(page)
    assert boxes(main_of(page)) == []
    response = accept_through_screen(client, old_id, [by_tag(old, "COAL 3", "link_text")])
    assert response.status_code == 409
    assert len(BindingStore(paths_of(client)).history(PACK)) == 1


# -- proposing from the screen --------------------------------------------------------------------


@pytest.fixture
def recorded_host(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, recordings.FakeHost]]:
    """Every OllamaClient the rubric screens build talks to a recorded host."""
    pack = shipped_pack(PACK)
    state = {"host": host_for(pack, planned(pack))}
    built: list[dict[str, Any]] = []
    original = rubric_screens.OllamaClient

    def build(endpoint, **kwargs):  # type: ignore[no-untyped-def]
        built.append({"endpoint": endpoint, **kwargs})
        kwargs.setdefault("transport", state["host"].transport())
        return original(endpoint, **kwargs)

    monkeypatch.setattr(rubric_screens, "OllamaClient", build)
    state["built"] = built  # type: ignore[assignment]
    yield state


def wait_until_proposed(client: TestClient) -> dict[str, Any]:
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        payload = client.get(f"/api/rubrics/{PACK}/proposing").json()
        if not payload["running"]:
            return payload
        if time.monotonic() > deadline:
            raise AssertionError(f"proposing did not finish: {payload}")
        time.sleep(0.05)


def test_proposing_from_the_screen_runs_in_the_background_and_keeps_a_set(
    client: TestClient, recorded_host: dict[str, Any]
) -> None:
    configure_model(client)
    response = client.post(f"/rubrics/{PACK}/propose", follow_redirects=False)
    assert response.status_code == 303
    payload = wait_until_proposed(client)
    assert payload["error"] is None and payload["set"]
    asked = sum(1 for c in shipped_pack(PACK).criteria if c.tier is not Tier.D)
    assert payload["asked"] == asked and payload["could_not"] == 1
    # With the review model chosen in Settings, through item 09's client at concurrency 1.
    (built,) = recorded_host["built"]
    assert built["endpoint"] == MODEL_HOST and built["concurrency"] == 1
    saved = BindingStore(paths_of(client)).proposal_set(PACK, payload["set"])
    assert saved is not None and saved.proposals.model == MODEL
    assert len(saved.proposals.proposals) == 4
    page = client.get(f"/rubrics/{PACK}").text
    progress = by_id(page, "propose").text()
    assert f"Rows to ask {asked}" in progress and "Could not be asked 1" in progress
    assert not re.search(r"\d\s*%", progress)
    assert "Open the proposals" in progress


def test_proposing_that_stops_keeps_nothing_and_says_why(
    client: TestClient, recorded_host: dict[str, Any]
) -> None:
    configure_model(client)
    stopped = recordings.only([MODEL])
    stopped.raises = httpx.ConnectError("connection refused")
    recorded_host["host"] = stopped
    client.post(f"/rubrics/{PACK}/propose")
    payload = wait_until_proposed(client)
    assert payload["error"] and payload["set"] is None
    assert BindingStore(paths_of(client)).proposal_sets(PACK) == ()
    assert "nothing was kept" in by_id(client.get(f"/rubrics/{PACK}").text, "propose").text()


def test_proposing_is_refused_without_a_model(client: TestClient) -> None:
    response = client.post(f"/rubrics/{PACK}/propose")
    assert response.status_code == 409
    assert "No model host is set" in by_id(response.text, "propose").text()


def test_proposing_is_refused_while_a_run_is_active_and_names_it(client: TestClient) -> None:
    configure_model(client)
    store = store_of(client)
    run_id = store.create_run(["4411"], rubric_pack=PACK)
    # Another process's run, as item 43 records one: a live owner on another host.
    set_owner(store, run_id, host=OTHER_HOST, pid=7)
    response = client.post(f"/rubrics/{PACK}/propose")
    assert response.status_code == 409
    text = by_id(response.text, "propose").text()
    assert f"Run {run_id[:8]} is running" in text
    assert f'href="/runs/{run_id}"' in response.text
    assert BindingStore(paths_of(client)).proposal_sets(PACK) == ()


def test_a_run_is_refused_while_proposing_and_names_the_rubric(client: TestClient) -> None:
    configure_canvas(client)
    configure_model(client)
    registry = client.app.state.proposing  # type: ignore[attr-defined]
    pack = shipped_pack(PACK)
    registry._states[PACK] = ProposingState(
        pack_id=PACK, pack_name=pack.name, model=MODEL, over=None, to_ask=67
    )
    response = client.post(
        "/runs",
        data={"course": ["4411"], "rubric": PACK, "label-4411": "Sample Course"},
        follow_redirects=False,
    )
    assert response.status_code == 409
    text = text_of(response.text)
    assert f"Bindings are being proposed for {pack.name}" in text
    assert f'href="/rubrics/{PACK}"' in response.text
    assert store_of(client).list_runs() == []
    # And a second proposer is refused while one is going, naming it.
    refused = client.post(f"/rubrics/{PACK}/propose")
    assert refused.status_code == 409
    assert "already being proposed" in by_id(refused.text, "propose").text()


# -- the picker -----------------------------------------------------------------------------------


def test_the_picker_says_proposed_not_confirmed_and_start_is_not_blocked(
    client: TestClient,
) -> None:
    from sightline.new_run import rubric_choices

    paths = paths_of(client)
    before = {c.id: c for c in rubric_choices(paths=paths)}
    assert not before[PACK].unconfirmed
    kept_set(client)
    after = {c.id: c for c in rubric_choices(paths=paths)}
    assert after[PACK].unconfirmed and after[PACK].href == f"/rubrics/{PACK}"
    assert not after["uccs-oscqr-rsi"].unconfirmed
    # A refused Start re-renders the picker: the note and its link are beside the choice, and the
    # only refusals are the ones that were there anyway.
    response = client.post("/runs", data={"course": ["4411"], "rubric": PACK})
    picker = by_id(response.text, "rubric")
    assert "Bindings proposed, not confirmed" in picker.text()
    assert f'href="/rubrics/{PACK}#sets"' in response.text


def test_a_run_started_from_the_screen_records_the_confirmed_bindings_only(
    client: TestClient,
) -> None:
    configure_canvas(client)
    configure_model(client)
    set_reviewer(client)
    set_id, proposals = kept_set(client)
    accept_through_screen(client, set_id, [by_tag(proposals, "COAL 3", "link_text")])
    kept_set(client)  # proposals left unconfirmed: the run starts anyway
    client.app.state.runner.handler = _never  # type: ignore[attr-defined]
    response = client.post(
        "/runs",
        data={"course": ["4411"], "rubric": PACK, "label-4411": "Sample Course"},
        follow_redirects=False,
    )
    assert response.status_code == 303, text_of(response.text)
    (run,) = store_of(client).list_runs()
    assert run.bindings_digest == resolve_pack(PACK, paths_of(client)).digest is not None


async def _never(run_id: str) -> None:
    return None


# -- ONE resolver: the app, the command and the service --------------------------------------------


def accepted_state(paths: DataPaths, pack_id: str, code: str, tag: str) -> str:
    """Record an acceptance of ``tag`` on ``code`` through the store. Returns its digest."""
    shipped = shipped_pack(pack_id)
    current = resolve_pack(pack_id, paths)
    plan = {code: answer((tag, ""))}
    proposals = make_set(current.pack, plan)
    store = BindingStore(paths)
    saved = store.proposal_set(pack_id, store.save_proposals(proposals))
    assert saved is not None
    result = accept_on(proposals, current.pack, names=[proposals.proposals[0].id])
    entry = store.record_acceptance(
        saved, result, shipped=shipped, before=current.digest, reviewer="Avery Example"
    )
    assert entry.after is not None
    return entry.after


def test_the_service_resolves_a_run_by_the_digest_it_recorded(paths: DataPaths) -> None:
    paths.ensure()
    store = RunStore.open(paths)
    try:
        first = accepted_state(paths, PACK, "COAL 3", "link_text")
        run_id = store.create_run(["4411"], rubric_pack=PACK, bindings_digest=first)
        accepted_state(paths, PACK, "COAL 4", "course_schedule")
        run = store.get_run(run_id)
        assert run is not None
        resolved = batch_service.resolved_pack(run, paths)
        assert resolved.digest == first
        assert resolved.pack.criterion("COAL 4").bindings == ()  # type: ignore[union-attr]
        old = store.create_run(["4411"], rubric_pack=PACK)
        assert batch_service.resolved_pack(store.get_run(old), paths).pack == shipped_pack(PACK)  # type: ignore[arg-type]
    finally:
        store.dispose()


def test_the_review_file_names_the_bindings_it_was_made_with(paths: DataPaths) -> None:
    pack = shipped_pack(PACK)
    work = CourseWork(reference="4411", course_id="c1", ordinal=0)
    with_bindings = ReviewPipeline(source=BundleSource(), pack=pack, bindings="sha256:" + "a" * 64)
    assert with_bindings.review(work)["pack"]["bindings"] == "sha256:" + "a" * 64
    # Without accepted bindings the file names none, exactly as before this item.
    assert "bindings" not in ReviewPipeline(source=BundleSource(), pack=pack).review(work)["pack"]


@pytest.fixture
def cli_root(monkeypatch: pytest.MonkeyPatch, data_root: Path, repo_root: Path) -> Iterator[Path]:
    monkeypatch.setenv("SIGHTLINE_DATA_DIR", str(data_root))
    monkeypatch.chdir(repo_root)
    reset_settings_cache()
    yield data_root
    reset_settings_cache()


def test_the_command_records_the_bindings_accepted_on_the_screen(
    cli_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = DataPaths.from_setting(cli_root).ensure()
    digest = accepted_state(paths, "uccs-oscqr-rsi", "2", "link_text")
    # It stops at the extract stage for want of a model host, after the run is recorded.
    code = batch_main(["run", "--bundle", "examples/sample-course", "--pack", "uccs-oscqr-rsi"])
    assert code in (0, 1)
    assert digest in capsys.readouterr().out
    store = RunStore.open(paths)
    try:
        (run,) = store.list_runs()
        assert run.bindings_digest == digest
    finally:
        store.dispose()


# -- THE TRAP: an old run renders with the bindings it ran with -----------------------------------


def test_an_old_runs_report_renders_with_the_bindings_it_ran_with(paths: DataPaths) -> None:
    """After a later acceptance, and after a revert, the old run still reads its own bindings."""
    paths.ensure()
    ran_with = accepted_state(paths, PACK, "COAL 3", "link_text")
    payload = json.loads(SAMPLE_REVIEW.read_text(encoding="utf-8"))
    payload["pack"]["bindings"] = ran_with

    def bindings_of(loaded_pack: RubricPack) -> dict[str, tuple[str, ...]]:
        return {c.code: tuple(c.bindings) for c in loaded_pack.criteria}

    expected = dict(BindingStore(paths).state(PACK, ran_with).bindings)
    loaded = load_review(payload, paths=paths)
    assert bindings_of(loaded.pack) == expected
    assert loaded.render() is not None

    later = accepted_state(paths, PACK, "COAL 4", "course_schedule")
    assert resolve_pack(PACK, paths).digest == later
    after_acceptance = load_review(payload, paths=paths)
    assert bindings_of(after_acceptance.pack) == expected
    after_acceptance.render()

    store = BindingStore(paths)
    for entry in reversed(standing(store.history(PACK))):
        store.revert_latest(shipped=shipped_pack(PACK), expected=entry.number, reviewer="A")
    assert resolve_pack(PACK, paths).digest is None
    after_revert = load_review(payload, paths=paths)
    assert bindings_of(after_revert.pack) == expected
    after_revert.render()

    # A review file with no digest reads the shipped pack, which is what such a run ran with.
    del payload["pack"]["bindings"]
    assert load_review(payload, paths=paths).pack == shipped_pack(PACK)


def test_a_review_whose_bindings_cannot_be_read_is_refused_not_read_with_others(
    paths: DataPaths,
) -> None:
    payload = json.loads(SAMPLE_REVIEW.read_text(encoding="utf-8"))
    payload["pack"]["bindings"] = "sha256:" + "0" * 64
    with pytest.raises(ReviewFileError, match="cannot be read back"):
        load_review(payload, paths=paths.ensure())
    with pytest.raises(ReviewFileError, match="cannot be read back"):
        load_review(payload)


def test_the_review_screen_and_export_read_the_run_bindings(client: TestClient) -> None:
    """End to end through the run store: the review screen opens a course whose file names an
    accepted state, after that state has been reverted, and its export reads the same."""
    paths = paths_of(client)
    ran_with = accepted_state(paths, PACK, "COAL 3", "link_text")
    store = store_of(client)
    run_id = store.create_run(["4411"], rubric_pack=PACK, bindings_digest=ran_with)
    (course,) = store.get_run(run_id).courses  # type: ignore[union-attr]
    payload = json.loads(SAMPLE_REVIEW.read_text(encoding="utf-8"))
    payload["pack"]["bindings"] = ran_with
    paths.save_text("runs", run_id, "courses", course.id, "review.json", text=json.dumps(payload))
    store.set_course_status(course.id, RunStatus.SUCCEEDED)
    BindingStore(paths).revert_latest(shipped=shipped_pack(PACK), expected=1, reviewer="A")
    assert client.get(f"/review/{course.id}").status_code == 200
    loaded = read_review(paths, store, course.id)
    assert loaded.pack.criterion("COAL 3").bindings == ("link_text",)  # type: ignore[union-attr]


# -- the store on its own -------------------------------------------------------------------------


def test_a_state_is_named_by_its_digest_and_the_same_bindings_get_the_same_name(
    paths: DataPaths,
) -> None:
    pack = shipped_pack(PACK)
    state = state_of(pack, shipped=pack)
    store = BindingStore(paths.ensure())
    assert store.save_state(state) == store.save_state(state) == state.digest
    assert store.state(PACK, state.digest) == state
    other = BindingState(
        pack_id=state.pack_id,
        pack_version=state.pack_version,
        base=state.base,
        bindings={**state.bindings, "COAL 3": ("link_text",)},
    )
    assert other.digest != state.digest


def test_a_state_over_a_pack_this_build_no_longer_ships_is_not_applied(
    paths: DataPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths.ensure()
    ran_with = accepted_state(paths, PACK, "COAL 3", "link_text")
    changed = tmp_path / "packs"
    shutil.copytree(packs_dir(), changed)
    target = changed / f"{PACK}.yaml"
    text = target.read_text(encoding="utf-8")
    assert "outcomes_defined" in text
    target.write_text(text.replace("- outcomes_defined", "- syllabus_presence", 1))
    monkeypatch.setenv(PACKS_DIR_ENV, str(changed))
    resolved = resolve_pack(PACK, paths)
    assert resolved.digest is None and resolved.stale == ran_with
    assert resolved.pack == shipped_pack(PACK)
    # A run that recorded the state still reads it.
    assert resolve_pack(PACK, paths, digest=ran_with).pack.criterion("COAL 3").bindings == (  # type: ignore[union-attr]
        "link_text",
    )


# -- vocabulary and voice -------------------------------------------------------------------------


def all_pages(client: TestClient) -> list[str]:
    """Every new page in every state: open, previewed, accepted, reverted, proposing, refused."""
    set_reviewer(client)
    configure_model(client)
    set_id, proposals = kept_set(client)
    pages = [
        client.get("/rubrics").text,
        client.get(f"/rubrics/{PACK}").text,
        client.get(set_href(set_id)).text,
        post(client, set_id, "check_all").text,
        post(client, set_id, "preview").text,
    ]
    accept_through_screen(
        client,
        set_id,
        [p.id for p in proposals.proposals if not p.conflict],
        [by_tag(proposals, "ACD 5", "alt_text")],
    )
    pages += [
        client.get(f"/rubrics/{PACK}?done=1").text,
        client.get(set_href(set_id)).text,
        client.get("/rubrics").text,
    ]
    client.post(f"/rubrics/{PACK}/revert", data={"acceptance": 1})
    registry = client.app.state.proposing  # type: ignore[attr-defined]
    registry._states[PACK] = ProposingState(
        pack_id=PACK, pack_name="x", model=MODEL, over=None, to_ask=67, asked=3, could_not=1
    )
    pages += [
        client.get(f"/rubrics/{PACK}?done=2").text,
        picker_only(client.post("/runs", data={"course": ["4411"], "rubric": PACK}).text),
        client.post(f"/rubrics/{PACK}/propose").text,
        client.get("/rubrics/no-such-pack").text,
    ]
    return pages


def picker_only(html: str) -> str:
    """The New run screen's rubric picker, which is all of that page this item wrote."""
    match = re.search(r'<fieldset class="field picker" id="rubric">.*?</fieldset>', html, re.S)
    assert match
    return f"<main>{match.group(0)}</main>"


def test_no_confidence_shaped_word_is_on_any_page_or_in_any_template(client: TestClient) -> None:
    for html in all_pages(client):
        assert CONFIDENCE.findall(text_of(html)) == []
    for name in ("rubric.html", "rubrics.html", "proposals.html", "rubric_missing.html"):
        source = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
        visible = re.sub(r"{[{%#].*?[}%#]}", " ", re.sub(r"<[^>]+>", " ", source), flags=re.S)
        assert CONFIDENCE.findall(visible) == [], name


def test_the_confidence_scan_fires_on_a_planted_word(client: TestClient) -> None:
    html = client.get("/rubrics").text.replace(
        "</main>", "<p>Proposals ranked by confidence.</p></main>"
    )
    assert sorted(w.lower() for w in CONFIDENCE.findall(text_of(html))) == [
        "confidence",
        "ranked",
    ]


_TEXT_TAGS = {"p", "h1", "h2", "h3", "dt", "dd", "li", "a", "span", "button", "label", "q"} | {
    "summary"
}


def verbatim(client: TestClient) -> list[str]:
    """What the pages print that Sightline did not write: the instrument's own words, the tags and
    detector ids, model names, the reviewer's name, and the host's own error text."""
    values: list[str] = []
    for pack_id in ("uccs-oscqr-3-1-3", "uccs-oscqr-rsi"):
        pack = shipped_pack(pack_id)
        values += [c.text for c in pack.criteria] + [pack.name]
    values += [MODEL, "Avery Example"]
    for pairs in rubric_screens.detectors_behind().values():
        values += [description for _, description in pairs]
    return values


def test_the_register_lint_passes_over_every_new_page(client: TestClient) -> None:
    """Item 15's lint, in the register every tier shares, over each element's visible text with
    the verbatim values masked — and a planted aggregate fires, so the pass reads something."""
    rules = default_register()
    pages = all_pages(client)
    values = verbatim(client)
    read = 0
    for html in pages:
        for node in main_of(html).walk():
            if node.tag not in _TEXT_TAGS:
                continue
            flat = node.text()
            if not flat:
                continue
            spans = _spans(flat, values)
            violations = [
                v
                for v in rules.lint(flat, "B", where=node.tag, sentence=False, spans=spans)
                if v.rule in {"grading", "aggregate", "self_assessment"}
            ]
            assert violations == [], [v.line() for v in violations]
            own = mask(flat, spans)
            assert not re.search(r"\d\s*%|\bper ?cent", own), flat
            read += 1
    assert read > 100
    planted = rules.lint(
        "12 of 67 rows asked; the overall score is 80%.", "B", where="x", sentence=False
    )
    assert {v.rule for v in planted} >= {"grading", "aggregate"}


def _spans(flat: str, values: list[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for value in sorted(set(values), key=len, reverse=True):
        needle = " ".join(value.split())
        if len(needle) >= 2:
            spans.extend((m.start(), m.end()) for m in re.finditer(re.escape(needle), flat))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def test_the_rubric_pages_never_evaluate_or_grade(client: TestClient) -> None:
    values = verbatim(client)
    for html in all_pages(client):
        flat = text_of(html)
        body = mask(flat, _spans(flat, values)).lower()
        for forbidden in (" grade", "grading", "evaluation", "score", "pass/fail"):
            assert forbidden not in body, forbidden
