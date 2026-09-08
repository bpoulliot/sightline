"""Configuration: nothing institution-specific is compiled in."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sightline.config import Settings, get_settings, reset_settings_cache

#: A hardcoded institution domain or LAN address anywhere in the package is a bug (SPEC §04).
FORBIDDEN_IN_SOURCE = (
    re.compile(r"\b[a-z0-9-]+\.(edu|ac\.uk)\b", re.IGNORECASE),
    re.compile(r"\b(?:10|127|172|192)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\binstructure\.com\b", re.IGNORECASE),
)

#: Placeholders that are allowed to look like the real thing, because they obviously are not.
#: ``example.edu`` is reserved by RFC 2606 and can never be an institution; the fixture sanitizer
#: names it as the domain it rewrites everything *to*, which is the opposite of compiling one in.
ALLOWED_PLACEHOLDERS = ("canvas.example.edu", "example.edu", "127.0.0.1")

# Assembled rather than written out, for the reason given in tests/test_redaction.py.
FAKE_CANVAS_TOKEN = "12345" + "~" + ("fake" * 8)


def test_defaults_point_at_nothing() -> None:
    settings = Settings(_env_file=None)
    assert settings.canvas_base_url is None
    assert settings.canvas_api_token is None
    assert settings.ollama_base_url is None
    assert settings.escalation_base_url is None
    assert settings.escalation_api_key is None
    assert settings.data_dir == Path("./data")
    assert settings.ollama_concurrency == 1


def test_environment_supplies_every_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANVAS_BASE_URL", "https://canvas.example.edu/")
    monkeypatch.setenv("CANVAS_API_TOKEN", FAKE_CANVAS_TOKEN)
    monkeypatch.setenv("SIGHTLINE_DATA_DIR", "/tmp/sightline-test-data")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://models.example.lan:11434")
    monkeypatch.setenv("OLLAMA_CONCURRENCY", "3")
    settings = Settings(_env_file=None)
    assert settings.canvas_base_url == "https://canvas.example.edu"  # trailing slash removed
    assert settings.canvas_api_token is not None
    assert settings.data_dir == Path("/tmp/sightline-test-data")
    assert settings.ollama_base_url == "http://models.example.lan:11434"
    assert settings.ollama_concurrency == 3
    assert settings.canvas_configured is True
    assert settings.ollama_configured is True
    assert settings.escalation_configured is False


def test_token_is_not_reprable() -> None:
    settings = Settings(_env_file=None, canvas_api_token=FAKE_CANVAS_TOKEN)
    assert "fakefakefake" not in repr(settings)
    assert "fakefakefake" not in str(settings.canvas_api_token)


def test_status_rows_never_carry_values() -> None:
    settings = Settings(
        _env_file=None,
        canvas_base_url="https://canvas.example.edu",
        canvas_api_token=FAKE_CANVAS_TOKEN,
        ollama_base_url="http://models.example.lan:11434",
    )
    rendered = " ".join(
        f"{row['name']} {row['env']} {row['state']} {row['note']}" for row in settings.status_rows()
    )
    assert "canvas.example.edu" not in rendered
    assert "models.example.lan" not in rendered
    assert "fakefakefake" not in rendered
    assert "configured" in rendered


def test_settings_cache_can_be_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_CONCURRENCY", "2")
    reset_settings_cache()
    assert get_settings().ollama_concurrency == 2
    monkeypatch.setenv("OLLAMA_CONCURRENCY", "4")
    assert get_settings().ollama_concurrency == 2  # cached
    reset_settings_cache()
    assert get_settings().ollama_concurrency == 4


def test_no_institution_or_host_is_compiled_into_the_package(repo_root: Path) -> None:
    package = repo_root / "src" / "sightline"
    offenders: list[str] = []
    scanned = 0
    for source in sorted(package.rglob("*")):
        if source.suffix not in {".py", ".html", ".css"} or not source.is_file():
            continue
        scanned += 1
        for lineno, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
            cleaned = line
            for placeholder in ALLOWED_PLACEHOLDERS:
                cleaned = cleaned.replace(placeholder, "")
            for pattern in FORBIDDEN_IN_SOURCE:
                if pattern.search(cleaned):
                    offenders.append(f"{source.relative_to(repo_root)}:{lineno}: {line.strip()}")
    assert scanned > 0, "the source scan examined zero files"
    assert not offenders, f"deployment-specific values are compiled in: {offenders}"
