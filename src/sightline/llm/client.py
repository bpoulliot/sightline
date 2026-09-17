"""The Ollama client: discovery, health, chat and install.

The endpoint is configuration and is never hardcoded (SPEC §06). Everything here was built
against an AGX Orin 64 GB running Ollama 0.20.6, and three of its behaviours were measured rather
than assumed. Each one changes what a correct client does, so each is stated here and encoded
below.

**A request must pin its context window.** Ollama 0.20.6 sizes the allocation from the model's
*declared* context length when the request does not say otherwise. On the target host that means
``llama3.2:3b`` — a 2 GB model — is refused outright with *"model requires more system memory
(52.2 GiB) than is available (48.7 GiB)"*, because it declares a 131 072-token window. The same
call with ``num_ctx: 8192`` answers in 3.7 s. So :data:`DEFAULT_CONTEXT_TOKENS` is pinned on every
call, and the refusal is mapped to an error whose remedy names the context window rather than
telling somebody to buy more memory.

**Thinking is pinned per call, and the pin is capability-aware.** ``think: true`` against a model
with no ``thinking`` capability is an HTTP 400 — Ollama rejects it rather than ignoring it. So the
field is only sent when the model reports the capability, and asking for it on a model that does
not is an error raised *before* the request.

**Thinking off does not mean no thinking.** Measured on ``gemma4:e4b``, twice each way at
``temperature: 0``: with ``think: true`` the reasoning arrives in ``message.thinking`` and
``message.content`` holds the answer alone. With ``think: false`` the reasoning is not suppressed —
it arrives *inside* ``message.content``, ahead of the answer, in one run trailed by a ``<channel|>``
control token. An extractor reading ``content`` from a thinking model with the pin off therefore
gets prose reasoning where it expected an answer, which is precisely the determinism SPEC §06 is
protecting. :class:`ChatResult` keeps the two fields apart and reports which way the call was
pinned, so item 10 can choose with the evidence in front of it.

**Concurrency defaults to 1.** An earlier local-AI pipeline on this network died from 48-way
concurrency against capacity that could not sustain it, and it died *silently*. One semaphore
guards every call this client makes, discovery included.

``/api/delete`` is deliberately not wired up (SPEC §06). There is no method here that removes a
model, and ``tests/test_llm_client.py`` asserts the string does not appear in this package.
"""

from __future__ import annotations

import asyncio
import enum
import json
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Literal, Self
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..logging import get_logger, model_call
from .errors import (
    ModelHostError,
    ModelHostNotConfiguredError,
    ModelHostNotOllamaError,
    ModelHostOutOfMemoryError,
    ModelHostServerError,
    ModelHostUnreachableError,
    ModelNotFoundError,
    ModelNotUsableError,
    ModelResponseError,
    ModelThinkingUnsupportedError,
)
from .selection import ModelCard, Selection, select

log = get_logger("sightline.llm")

#: Pinned on every call. Large enough for a Canvas page and its surrounding context, small enough
#: that the target host can serve it — see this module's docstring for the measurement. It is a
#: *pin*, not a maximum: a model whose declared window is smaller gets its own.
DEFAULT_CONTEXT_TOKENS = 8192

#: Generation is measured in tens of seconds on this class of host, and a cold model load is
#: measured in minutes. A timeout tuned for an API is a timeout that reports a working host as
#: broken.
DEFAULT_TIMEOUT = 300.0

#: Discovery and health are cheap. They get their own ceiling so an unreachable host fails the
#: settings screen in seconds rather than in five minutes.
DISCOVERY_TIMEOUT = 15.0

#: A pull moves gigabytes and has no useful upper bound; the read timeout is what matters, and a
#: stalled connection still trips it.
PULL_TIMEOUT = httpx.Timeout(60.0, connect=15.0, read=300.0)

CheckStatus = Literal["ok", "failed", "skipped"]

#: What a host may be made of. Deliberately narrow: a value with a space in it is a sentence
#: somebody pasted, and letting it through produces a DNS failure that names the wrong problem.
_HOSTNAME = re.compile(r"[A-Za-z0-9._\-]+|\[[0-9A-Fa-f:.]+\]")


class Thinking(enum.StrEnum):
    """How a call pins the ``think`` field. There is no "leave it to the model" member."""

    ON = "on"
    OFF = "off"


# ---------------------------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatResult:
    """One completed generation, with everything the log line also recorded.

    ``content`` and ``thinking`` are separate fields because the host separates them — and
    because, with thinking pinned off, a thinking model puts its reasoning in ``content``. A
    caller that wants the answer wants ``content``; a caller that wants to know whether it is
    reading reasoning wants ``thinking_pinned``.
    """

    model: str
    content: str
    thinking: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_duration_ms: float | None = None
    load_duration_ms: float | None = None
    done_reason: str | None = None
    #: How this call pinned thinking, and whether the field was sent at all. ``None`` means the
    #: model has no ``thinking`` capability, so the field was omitted rather than sent as false.
    thinking_pinned: bool | None = None
    context_tokens: int = DEFAULT_CONTEXT_TOKENS

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None and self.completion_tokens is None:
            return None
        return (self.prompt_tokens or 0) + (self.completion_tokens or 0)


@dataclass(frozen=True)
class PullProgress:
    """One line of an ``/api/pull`` stream.

    Ollama emits ``{"status": "pulling manifest"}``, then a line per layer carrying ``digest``,
    ``total`` and ``completed``, then ``{"status": "success"}``. Layer lines are the only ones
    with numbers in them, which is why ``percent`` is ``None`` rather than 0 on the others: a
    progress bar that reads "0%" while a manifest is being fetched is telling the reader something
    false.
    """

    status: str
    digest: str | None = None
    completed: int | None = None
    total: int | None = None

    @property
    def percent(self) -> float | None:
        if self.total and self.completed is not None and self.total > 0:
            return min(100.0, round(self.completed * 100 / self.total, 1))
        return None

    @property
    def done(self) -> bool:
        return self.status == "success"


@dataclass(frozen=True)
class HostCheck:
    """One step of the health check, with what it examined and what to change."""

    name: str
    status: CheckStatus
    detail: str
    remedy: str | None = None
    elapsed_ms: float | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class HostReport:
    """The health check: which host answered, its version, and what it can do.

    Concrete on success and on failure. When nothing answers, this still renders — with the host
    that was tried, the reason, and the remedy — because "the model host is down" is a fact a
    settings screen should state, not a blank section.
    """

    endpoint: str | None
    host: str | None
    version: str | None = None
    checks: tuple[HostCheck, ...] = ()
    selection: Selection | None = None
    concurrency: int = 1
    context_tokens: int = DEFAULT_CONTEXT_TOKENS

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(check.status == "ok" for check in self.checks)

    @property
    def remedy(self) -> str | None:
        for check in self.checks:
            if check.status == "failed" and check.remedy:
                return check.remedy
        return None

    @property
    def usable(self) -> tuple[ModelCard, ...]:
        return self.selection.usable if self.selection else ()

    @property
    def rejected(self) -> tuple[ModelCard, ...]:
        return self.selection.rejected if self.selection else ()

    def summary(self) -> str:
        """One line of counts and facts, in the declarative register of SPEC §08 tier A."""
        where = self.host or self.endpoint or "no endpoint"
        if not self.ok:
            passed = sum(1 for check in self.checks if check.status == "ok")
            return f"{passed} of {len(self.checks)} checks passed against {where}."
        version = f"Ollama {self.version}" if self.version else "an unnamed Ollama version"
        models = self.selection.summary() if self.selection else "No models were listed."
        return f"{where} answered, running {version}. {models}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "endpoint": self.endpoint,
            "host": self.host,
            "version": self.version,
            "summary": self.summary(),
            "remedy": self.remedy,
            "concurrency": self.concurrency,
            "context_tokens": self.context_tokens,
            "checks": [
                {
                    "name": check.name,
                    "status": check.status,
                    "detail": check.detail,
                    "remedy": check.remedy,
                    "elapsed_ms": check.elapsed_ms,
                }
                for check in self.checks
            ],
            "models": self.selection.as_dict() if self.selection else None,
        }


# ---------------------------------------------------------------------------------------------
# Endpoint handling
# ---------------------------------------------------------------------------------------------


def normalize_endpoint(url: str | None) -> str:
    """Scheme, host and port, with nothing after them.

    A person pasting an endpoint pastes what their browser showed them, which may have a path on
    it and may have no scheme at all. ``http`` is assumed when none is given: Ollama serves plain
    HTTP and an assumed ``https`` would fail with a TLS error that names the wrong problem.
    """
    if not url or not url.strip():
        raise ModelHostNotConfiguredError("No model host endpoint is configured.")
    text = url.strip()
    if "//" not in text:
        text = f"http://{text}"
    parsed = urlsplit(text)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or not _HOSTNAME.fullmatch(parsed.hostname)
    ):
        raise ModelHostNotConfiguredError(
            "That is not a usable model host endpoint.",
            detail=f"Could not read a host out of {url!r}.",
            remedy=(
                "Give the root of the Ollama server as scheme, host and port — for example "
                "http://model-host.example.lan:11434."
            ),
        )
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def host_of(url: str | None) -> str | None:
    """Host and port, for display. Not redacted: the model host is not a secret (SPEC §06)."""
    if not url:
        return None
    parsed = urlsplit(url if "//" in url else f"//{url}")
    if not parsed.hostname:
        return None
    return f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname


# ---------------------------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------------------------


class OllamaClient:
    """An async Ollama client. One instance per endpoint.

    ``transport`` exists so the suite runs against recorded payloads rather than a live host —
    a test suite that needs a GPU is a test suite that does not run in CI.
    """

    def __init__(
        self,
        base_url: str | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        concurrency: int = 1,
        context_tokens: int = DEFAULT_CONTEXT_TOKENS,
        user_agent: str = "sightline",
    ) -> None:
        self.base_url = normalize_endpoint(base_url)
        self.host = host_of(self.base_url)
        self.concurrency = max(1, concurrency)
        self.context_tokens = context_tokens
        self._gate = asyncio.Semaphore(self.concurrency)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            transport=transport,
            timeout=timeout,
            headers={"Accept": "application/json", "User-Agent": user_agent},
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def __repr__(self) -> str:
        return f"<OllamaClient host={self.host!r} concurrency={self.concurrency}>"

    # -- transport ---------------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """One request, with every failure mapped to an error that carries its remedy."""
        endpoint = f"{method} {path}"
        async with self._gate:
            try:
                response = await self._client.request(
                    method,
                    path,
                    json=dict(payload) if payload is not None else None,
                    timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
                )
            except httpx.TimeoutException as exc:
                raise ModelHostUnreachableError(
                    f"{self.host} did not answer in time.",
                    detail=f"{endpoint} timed out.",
                    endpoint=endpoint,
                ) from exc
            except httpx.HTTPError as exc:
                raise ModelHostUnreachableError(
                    f"Could not reach {self.host}.",
                    detail=f"{endpoint}: {type(exc).__name__}.",
                    endpoint=endpoint,
                ) from exc
        _raise_for_status(response, endpoint=endpoint, host=self.host)
        return _json_body(response, endpoint=endpoint, host=self.host)

    # -- discovery ---------------------------------------------------------------------------

    async def version(self) -> str:
        """The Ollama version string, from ``/api/version``."""
        body = await self._request("GET", "/api/version", timeout=DISCOVERY_TIMEOUT)
        version = body.get("version") if isinstance(body, Mapping) else None
        if not isinstance(version, str) or not version.strip():
            raise ModelHostNotOllamaError(
                f"{self.host} answered, but not with an Ollama version.",
                detail="GET /api/version returned JSON with no 'version' string in it.",
                endpoint="GET /api/version",
            )
        return version.strip()

    async def card(self, name: str, *, size_bytes: int | None = None) -> ModelCard:
        """One model's capabilities, from ``/api/show``. Live and authoritative."""
        body = await self._request(
            "POST", "/api/show", payload={"model": name}, timeout=DISCOVERY_TIMEOUT
        )
        if not isinstance(body, Mapping):
            raise ModelResponseError(
                f"{self.host} returned an unexpected shape for {name}.",
                detail="POST /api/show did not return a JSON object.",
                endpoint="POST /api/show",
            )
        return ModelCard.from_show(name, body, size_bytes=size_bytes)

    async def installed(self) -> Selection:
        """Every installed model, split by the capability rule.

        ``/api/tags`` for the names and sizes, then ``/api/show`` per model for its capabilities:
        ``/api/tags`` does not report them, so there is no shortcut here. Read fresh every time
        and never cached across restarts (SPEC §06) — a model installed while the app was running
        appears on the next look, and a capability list held over a host upgrade would be a
        confident wrong answer.
        """
        body = await self._request("GET", "/api/tags", timeout=DISCOVERY_TIMEOUT)
        entries = body.get("models") if isinstance(body, Mapping) else None
        if not isinstance(entries, Sequence):
            raise ModelHostNotOllamaError(
                f"{self.host} answered, but not with an Ollama model list.",
                detail="GET /api/tags returned JSON with no 'models' array in it.",
                endpoint="GET /api/tags",
            )
        named = [
            (entry["name"], entry.get("size"))
            for entry in entries
            if isinstance(entry, Mapping) and isinstance(entry.get("name"), str)
        ]
        cards = await asyncio.gather(
            *(
                self.card(name, size_bytes=size if isinstance(size, int) else None)
                for name, size in named
            )
        )
        return select(sorted(cards, key=lambda card: card.name))

    async def health(self) -> HostReport:
        """Which host answered, its version, and the usable models after filtering.

        Never raises. A health check that throws is a health check whose failure has to be caught
        by every caller, and the one thing this call exists to report is failure.
        """
        checks: list[HostCheck] = []
        version: str | None = None
        selection: Selection | None = None

        try:
            version = await self.version()
        except ModelHostError as exc:
            checks.append(
                HostCheck(
                    name="Host answered",
                    status="failed",
                    detail=" ".join(filter(None, (exc.summary, exc.detail))),
                    remedy=exc.remedy,
                )
            )
            return HostReport(
                endpoint=self.base_url,
                host=self.host,
                checks=tuple(checks),
                concurrency=self.concurrency,
                context_tokens=self.context_tokens,
            )

        checks.append(
            HostCheck(
                name="Host answered",
                status="ok",
                detail=f"{self.host} is running Ollama {version}.",
            )
        )

        try:
            selection = await self.installed()
        except ModelHostError as exc:
            checks.append(
                HostCheck(
                    name="Models listed",
                    status="failed",
                    detail=" ".join(filter(None, (exc.summary, exc.detail))),
                    remedy=exc.remedy,
                )
            )
            return HostReport(
                endpoint=self.base_url,
                host=self.host,
                version=version,
                checks=tuple(checks),
                concurrency=self.concurrency,
                context_tokens=self.context_tokens,
            )

        checks.append(
            HostCheck(
                name="Models listed",
                status="ok",
                detail=(
                    f"{len(selection.all)} installed, read from /api/tags and /api/show. "
                    "Capabilities are read live, never cached."
                ),
            )
        )
        usable_names = ", ".join(card.name for card in selection.usable)
        checks.append(
            HostCheck(
                name="Usable models",
                status="ok" if selection.usable else "failed",
                detail=(
                    f"{selection.summary()} {usable_names}"
                    if selection.usable
                    else f"{selection.summary()} Nothing installed here can do review work."
                ),
                remedy=(
                    None
                    if selection.usable
                    else (
                        "Install a model that reports 'completion' and 'tools'. The recommended "
                        "models on this screen are sized for a host of this class."
                    )
                ),
            )
        )
        return HostReport(
            endpoint=self.base_url,
            host=self.host,
            version=version,
            checks=tuple(checks),
            selection=selection,
            concurrency=self.concurrency,
            context_tokens=self.context_tokens,
        )

    # -- generation --------------------------------------------------------------------------

    async def chat(
        self,
        model: ModelCard | str,
        messages: Sequence[Mapping[str, Any]],
        *,
        thinking: Thinking,
        operation: str,
        context_tokens: int | None = None,
        temperature: float = 0.0,
        options: Mapping[str, Any] | None = None,
        response_format: Mapping[str, Any] | str | None = None,
        require_usable: bool = True,
    ) -> ChatResult:
        """One non-streaming chat completion, logged with its latency and token counts.

        ``thinking`` has no default. Item 10's extractors depend on output determinism and the
        pin changes it, so every call site states which way it wants it (SPEC §06) and a call site
        that has not thought about it does not compile.

        ``model`` may be a :class:`ModelCard` or a name. A name costs one extra ``/api/show``,
        because the thinking pin cannot be resolved without the model's capabilities and guessing
        it produces an HTTP 400 from the host.

        ``response_format`` goes into Ollama's top-level ``format`` field — ``"json"``, or a JSON
        schema the host constrains generation to. It is a *top-level* field and not one of
        ``options``; put it in ``options`` and the host accepts the request and ignores it, which
        is the shape of bug that is found six weeks later. Measured working on the target host with
        the extraction schema of :mod:`sightline.extract.schema`. **It does not remove the need to
        validate the response**: a constraint the host promises to apply is not a constraint this
        client has checked.
        """
        card = model if isinstance(model, ModelCard) else await self.card(model)
        if require_usable and not card.usable:
            raise ModelNotUsableError(
                f"{card.name} is not selected for review work.",
                detail=card.verdict,
                endpoint="POST /api/chat",
            )
        think = _resolve_think(card, thinking)
        window = context_tokens or self.context_tokens

        payload: dict[str, Any] = {
            "model": card.name,
            "messages": [dict(message) for message in messages],
            "stream": False,
            # num_ctx is pinned on every call. Without it Ollama 0.20.6 sizes the allocation from
            # the model's declared window and refuses models that would otherwise fit.
            "options": {"temperature": temperature, "num_ctx": window, **(options or {})},
        }
        if think is not None:
            payload["think"] = think
        if response_format is not None:
            payload["format"] = (
                response_format if isinstance(response_format, str) else dict(response_format)
            )

        with model_call(
            operation,
            model=card.name,
            endpoint=self.base_url,
            thinking=thinking.value,
            think_sent=think,
            context_tokens=window,
            format_constrained=response_format is not None,
        ) as record:
            body = await self._request("POST", "/api/chat", payload=payload)
            if not isinstance(body, Mapping):
                raise ModelResponseError(
                    f"{self.host} returned an unexpected shape for a chat completion.",
                    detail="POST /api/chat did not return a JSON object.",
                    endpoint="POST /api/chat",
                )
            message = body.get("message")
            message = message if isinstance(message, Mapping) else {}
            prompt_tokens = _int(body.get("prompt_eval_count"))
            completion_tokens = _int(body.get("eval_count"))
            record.tokens(prompt=prompt_tokens, completion=completion_tokens)
            record.extra["done_reason"] = body.get("done_reason")
            return ChatResult(
                model=str(body.get("model") or card.name),
                content=str(message.get("content") or ""),
                thinking=_text_or_none(message.get("thinking")),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_duration_ms=_ns_to_ms(body.get("total_duration")),
                load_duration_ms=_ns_to_ms(body.get("load_duration")),
                done_reason=_text_or_none(body.get("done_reason")),
                thinking_pinned=think,
                context_tokens=window,
            )

    # -- install -----------------------------------------------------------------------------

    async def pull(self, name: str) -> AsyncIterator[PullProgress]:
        """Install a model, yielding each progress line the host emits.

        There is no matching ``delete``. ``/api/delete`` exists and is deliberately not wired up
        (SPEC §06): a review tool that can remove somebody's models is a review tool that removes
        somebody's models.
        """
        endpoint = "POST /api/pull"
        async with self._gate:
            try:
                async with self._client.stream(
                    "POST",
                    "/api/pull",
                    json={"model": name, "stream": True},
                    timeout=PULL_TIMEOUT,
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        _raise_for_status(response, endpoint=endpoint, host=self.host)
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise ModelHostNotOllamaError(
                                f"{self.host} sent a pull line that is not JSON.",
                                detail=f"{endpoint} emitted a line this client cannot read.",
                                endpoint=endpoint,
                            ) from exc
                        if not isinstance(payload, Mapping):
                            continue
                        error = payload.get("error")
                        if isinstance(error, str) and error:
                            # A pull reports failure *inside* a 200 stream. Left unmapped it would
                            # end the generator normally and look like a completed install.
                            raise _error_for(error, status=200, endpoint=endpoint, host=self.host)
                        yield PullProgress(
                            status=str(payload.get("status") or ""),
                            digest=_text_or_none(payload.get("digest")),
                            completed=_int(payload.get("completed")),
                            total=_int(payload.get("total")),
                        )
            except httpx.TimeoutException as exc:
                raise ModelHostUnreachableError(
                    f"{self.host} stopped sending while installing {name}.",
                    detail=f"{endpoint} timed out mid-stream.",
                    endpoint=endpoint,
                ) from exc
            except httpx.HTTPError as exc:
                raise ModelHostUnreachableError(
                    f"Could not reach {self.host} to install {name}.",
                    detail=f"{endpoint}: {type(exc).__name__}.",
                    endpoint=endpoint,
                ) from exc


# ---------------------------------------------------------------------------------------------
# Mapping failures
# ---------------------------------------------------------------------------------------------


def _resolve_think(card: ModelCard, thinking: Thinking) -> bool | None:
    """What to put in the ``think`` field, or ``None`` to omit it.

    Omission is not the same as ``false``. A model with no ``thinking`` capability rejects
    ``true`` with a 400 and accepts ``false`` as a no-op; sending the no-op would put a field in
    the request that claims a decision the model cannot act on.
    """
    if not card.supports_thinking:
        if thinking is Thinking.ON:
            raise ModelThinkingUnsupportedError(
                f"{card.name} does not support thinking.",
                detail=(
                    f"It reports {', '.join(card.capabilities) or 'no capabilities'}, which does "
                    "not include 'thinking'."
                ),
                endpoint="POST /api/chat",
            )
        return None
    return thinking is Thinking.ON


def _json_body(response: httpx.Response, *, endpoint: str, host: str | None) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise ModelHostNotOllamaError(
            f"{host} answered {endpoint} with something that is not JSON.",
            detail=f"HTTP {response.status_code}, content-type "
            f"{response.headers.get('content-type', 'unknown')}.",
            status_code=response.status_code,
            endpoint=endpoint,
        ) from exc


def _raise_for_status(response: httpx.Response, *, endpoint: str, host: str | None) -> None:
    if response.status_code < 400:
        return
    message = _error_message(response)
    raise _error_for(message, status=response.status_code, endpoint=endpoint, host=host)


def _error_message(response: httpx.Response) -> str:
    """Ollama's own error text, which is where the cause actually is."""
    try:
        body = response.json()
    except ValueError:
        return response.text.strip()[:400]
    if isinstance(body, Mapping):
        error = body.get("error")
        if isinstance(error, str):
            return error.strip()
    return ""


def _error_for(message: str, *, status: int, endpoint: str, host: str | None) -> ModelHostError:
    """Map Ollama's status and error text onto the failure that carries the right remedy.

    The text is matched rather than the status alone because Ollama says something useful in it
    and the statuses collide: a 404 is both "no such model" and "no such route", and a 500 is both
    "out of memory" and everything else.
    """
    lowered = message.lower()
    detail = f"{endpoint} -> HTTP {status}" + (f": {message}" if message else ".")

    if "does not support thinking" in lowered:
        return ModelThinkingUnsupportedError(
            "That model does not support thinking.",
            detail=detail,
            status_code=status,
            endpoint=endpoint,
        )
    if "system memory" in lowered or "out of memory" in lowered:
        return ModelHostOutOfMemoryError(
            f"{host} could not fit that model in memory.",
            detail=detail,
            status_code=status,
            endpoint=endpoint,
        )
    if "not found" in lowered and "model" in lowered:
        # Matched on the text rather than the status: a pull reports exactly this failure inside
        # a 200 stream, and mapping it only on 404 would let that one through as a generic error.
        return ModelNotFoundError(
            "That model is not installed on this host.",
            detail=detail,
            status_code=status,
            endpoint=endpoint,
        )
    if status == 404:
        return ModelHostNotOllamaError(
            f"{host} has no such endpoint.", detail=detail, status_code=status, endpoint=endpoint
        )
    if status >= 500:
        return ModelHostServerError(
            f"{host} returned a server error.",
            detail=detail,
            status_code=status,
            endpoint=endpoint,
        )
    return ModelHostError(
        f"{host} refused the request.",
        detail=detail,
        status_code=status,
        endpoint=endpoint,
        remedy=(
            "The host rejected the request rather than failing to answer it. Its own message is "
            "in the detail above, and the Ollama server's log on that machine has the rest."
        ),
    )


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _text_or_none(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _ns_to_ms(value: Any) -> float | None:
    """Ollama reports durations in nanoseconds; everything else here is milliseconds."""
    number = _int(value)
    return round(number / 1_000_000, 2) if number is not None else None


__all__ = [
    "DEFAULT_CONTEXT_TOKENS",
    "DEFAULT_TIMEOUT",
    "DISCOVERY_TIMEOUT",
    "ChatResult",
    "HostCheck",
    "HostReport",
    "OllamaClient",
    "PullProgress",
    "Thinking",
    "host_of",
    "normalize_endpoint",
]
