"""The sign-in reference compose file keeps the app behind the proxy and holds no secret (item 47).

``docker-compose.signin.yml`` is the reference deployment for sign-in mode: the app and an
Authentik proxy outpost. Nothing here starts it. The file is read as YAML, uninterpolated, and
checked for the three properties a deployer cannot see by running it:

* **it parses**, as YAML here and, where the docker CLI has the compose plugin, as compose with
  obviously fake values (CI's test job has no docker CLI, so that half skips there and runs on a
  developer machine);
* **the app publishes no port** and shares a network with the outpost alone, and that network is
  ``internal``;
* **no secret is a literal**: every secret-shaped value is an interpolation with no default, or
  arrives through an ``env_file``.

Each check is also fired against a deliberately broken copy, so a check that cannot fail is caught.

The dev image carries neither the compose file nor the Dockerfile; there the test skips. It fails
if only the compose file is missing beside a Dockerfile.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SIGNIN_COMPOSE = REPO_ROOT / "docker-compose.signin.yml"
LOCAL_COMPOSE = REPO_ROOT / "docker-compose.yml"

APP = "sightline"
OUTPOST = "authentik-outpost"

#: An environment key that names a secret. Matched on the key, so a secret under any name with
#: one of these words in it is held to the rule.
SECRET_KEY = re.compile(r"SECRET|TOKEN|PASSWORD|PASSPHRASE|(^|_)KEY($|_)", re.IGNORECASE)

#: The one acceptable shape of a secret's value: an interpolation with no default. ``${X:?msg}``
#: and ``${X?msg}`` refuse to start without it; ``${X}`` and ``${X:-}`` are not allowed either,
#: because the second puts a literal (the empty string) in its place.
FROM_ENVIRONMENT = re.compile(r"^\$\{[A-Z_][A-Z0-9_]*(:?\?[^}]*)?\}$")

#: Obviously fake values for the compose half, in the shapes the runbook uses.
FAKE_ENV = {
    "SIGHTLINE_SIGNIN_SECRET": "fake-signin-secret-0123456789-abcdefghij",
    "AUTHENTIK_TAG": "2099.1.0",
}
FAKE_OUTPOST_ENV = "AUTHENTIK_HOST=https://auth.example.edu\nAUTHENTIK_TOKEN=fake-outpost-token\n"


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        if not (REPO_ROOT / "Dockerfile").is_file():
            pytest.skip("not a build context (the dev image carries no compose file)")
        pytest.fail(f"{path.name} is missing beside the Dockerfile")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return _load(SIGNIN_COMPOSE)


def _networks(service: dict[str, Any]) -> set[str]:
    networks = service.get("networks") or ["default"]
    return set(networks)


def app_exposure_problems(doc: dict[str, Any]) -> list[str]:
    """Every way the file lets something other than the outpost reach the app."""
    services = doc.get("services") or {}
    if APP not in services or OUTPOST not in services:
        return [f"expected services {APP!r} and {OUTPOST!r}, found {sorted(services)}"]
    app = services[APP]
    problems = []
    if app.get("ports"):
        problems.append(f"{APP} publishes ports {app['ports']}")
    if app.get("network_mode"):
        problems.append(f"{APP} sets network_mode {app['network_mode']!r}")
    shared_with_outpost = _networks(app) & _networks(services[OUTPOST])
    if not shared_with_outpost:
        problems.append(f"{APP} and {OUTPOST} share no network")
    declared = doc.get("networks") or {}
    for net in sorted(_networks(app)):
        others = sorted(
            name for name, svc in services.items() if name != APP and net in _networks(svc)
        )
        if others not in ([], [OUTPOST]):
            problems.append(f"network {net!r} joins {APP} to {others}")
        if others == [OUTPOST] and not (declared.get(net) or {}).get("internal"):
            problems.append(f"network {net!r}, shared with the outpost, is not internal")
    return problems


def _environment(service: dict[str, Any]) -> dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, list):
        pairs = (item.split("=", 1) if "=" in item else (item, "") for item in env)
        return {key: value for key, value in pairs}
    return {key: "" if value is None else str(value) for key, value in env.items()}


def secret_literal_problems(doc: dict[str, Any]) -> list[str]:
    """Every secret-shaped environment value that is not taken from the environment."""
    problems = []
    for name, service in sorted((doc.get("services") or {}).items()):
        for key, value in sorted(_environment(service).items()):
            if SECRET_KEY.search(key) and not FROM_ENVIRONMENT.match(value):
                problems.append(f"{name}: {key} is not an interpolation without a default")
    return problems


def test_the_signin_compose_file_parses(compose: dict[str, Any]) -> None:
    assert set(compose["services"]) == {APP, OUTPOST}


def test_the_app_publishes_no_port_and_is_reached_only_through_the_outpost(
    compose: dict[str, Any],
) -> None:
    assert app_exposure_problems(compose) == []
    # The outpost is the way in, so it is the one service that publishes.
    assert compose["services"][OUTPOST].get("ports")


def test_no_secret_is_a_literal(compose: dict[str, Any]) -> None:
    assert secret_literal_problems(compose) == []
    # The secret item 44 checks is required at config time, by name.
    app_env = _environment(compose["services"][APP])
    assert app_env["SIGHTLINE_SIGNIN"] == "true"
    assert app_env["SIGHTLINE_SIGNIN_SECRET"].startswith("${SIGHTLINE_SIGNIN_SECRET:?")
    # The outpost's URL and token come from its own env file, never from `environment:`, which
    # would override the file.
    outpost = compose["services"][OUTPOST]
    assert not _environment(outpost)
    assert [entry["path"] for entry in outpost["env_file"]] == [".env.outpost"]


def test_the_egress_subnet_stays_pinned_as_in_the_local_compose_file(
    compose: dict[str, Any],
) -> None:
    local = _load(LOCAL_COMPOSE)
    local_subnet = local["networks"]["default"]["ipam"]["config"][0]["subnet"]
    assert local_subnet.startswith("${SIGHTLINE_SUBNET:-")
    for net in _networks(compose["services"][APP]):
        subnets = [c["subnet"] for c in compose["networks"][net]["ipam"]["config"]]
        assert subnets and all(s.startswith("${SIGHTLINE_") for s in subnets), net
    egress = [
        net
        for net in _networks(compose["services"][APP])
        if not compose["networks"][net].get("internal")
    ]
    assert [compose["networks"][n]["ipam"]["config"][0]["subnet"] for n in egress] == [
        local_subnet
    ]


@pytest.mark.parametrize(
    "breakage",
    [
        "publish the app's port",
        "the outpost network is not internal",
        "a third service on the app's network",
        "host networking",
    ],
)
def test_the_exposure_check_fires_on_a_broken_file(
    compose: dict[str, Any], breakage: str
) -> None:
    broken = copy.deepcopy(compose)
    app = broken["services"][APP]
    if breakage == "publish the app's port":
        app["ports"] = ["8099:8000"]
    elif breakage == "the outpost network is not internal":
        for net in _networks(app) & _networks(broken["services"][OUTPOST]):
            broken["networks"][net]["internal"] = False
    elif breakage == "a third service on the app's network":
        broken["services"]["other"] = {"image": "example", "networks": sorted(_networks(app))}
    else:
        app["network_mode"] = "host"
    assert app_exposure_problems(broken)


@pytest.mark.parametrize(
    "value",
    [
        "fake-literal-secret",
        "${SIGHTLINE_SIGNIN_SECRET:-fake-default}",
        "${SIGHTLINE_SIGNIN_SECRET:-}",
        "prefix-${SIGHTLINE_SIGNIN_SECRET}",
    ],
)
def test_the_secret_check_fires_on_a_literal(compose: dict[str, Any], value: str) -> None:
    broken = copy.deepcopy(compose)
    broken["services"][APP]["environment"]["SIGHTLINE_SIGNIN_SECRET"] = value
    assert secret_literal_problems(broken)
    other = copy.deepcopy(compose)
    other["services"][OUTPOST]["environment"] = {"AUTHENTIK_TOKEN": value}
    assert secret_literal_problems(other)


def _compose_cli() -> list[str] | None:
    docker = shutil.which("docker")
    if docker is None:
        return None
    probe = subprocess.run(
        [docker, "compose", "version"], capture_output=True, text=True, check=False
    )
    return [docker, "compose"] if probe.returncode == 0 else None


def test_docker_compose_config_accepts_it_with_fake_values(tmp_path: Path) -> None:
    _load(SIGNIN_COMPOSE)
    cli = _compose_cli()
    if cli is None:
        pytest.skip("no docker CLI with the compose plugin here; the YAML checks above still ran")
    shutil.copy(SIGNIN_COMPOSE, tmp_path / SIGNIN_COMPOSE.name)
    (tmp_path / ".env.outpost").write_text(FAKE_OUTPOST_ENV, encoding="utf-8")
    command = [*cli, "-f", SIGNIN_COMPOSE.name, "config", "--format", "json"]
    # Only the fake values and what the CLI needs to run: never this machine's .env or shell.
    base = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)}
    ok = subprocess.run(
        command, cwd=tmp_path, env={**base, **FAKE_ENV}, capture_output=True, text=True
    )
    assert ok.returncode == 0, ok.stderr
    rendered = json.loads(ok.stdout)
    assert not rendered["services"][APP].get("ports")
    assert rendered["services"][OUTPOST]["ports"][0]["host_ip"] == "127.0.0.1"
    # And it refuses to render without the secret, naming it.
    missing = {k: v for k, v in FAKE_ENV.items() if k != "SIGHTLINE_SIGNIN_SECRET"}
    refused = subprocess.run(
        command, cwd=tmp_path, env={**base, **missing}, capture_output=True, text=True
    )
    assert refused.returncode != 0
    assert "SIGHTLINE_SIGNIN_SECRET" in refused.stderr
