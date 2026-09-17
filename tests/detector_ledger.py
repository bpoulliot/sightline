"""Observing which detectors the fixture suite actually runs.

This is the measuring instrument behind the coverage half of the integrity guard, and it measures
rather than reads. It calls the suite's detector tests with :meth:`Detector.run` instrumented and
records the ids that came through it. Nothing here searches a test file for a class name: a name in
an import line proves a detector was *mentioned*, and the failure this guard is modelled on — a
31-suite JS test set that gated nothing for months because the runner pointed at a directory,
printed one error and exited 0 — was a check that looked exactly like it was running.

**Why it calls the tests itself instead of watching the surrounding pytest session.** A ledger
collected from the session it runs inside gives a different answer depending on what was selected:
``pytest -k contrast`` would report nine uncovered detectors, and the reflex fix for that is a skip,
which is how a guard stops guarding. Calling the tests here makes the answer identical whether the
guard runs alone, inside the full suite, or under ``-k``. The documented one-line command depends on
that.

**A registry sweep is credited to nobody, and that rule came out of a drill rather than a design.**
The first version of this harness counted every observed run. Fired at it — an untested detector
added to the shipping registry on purpose — it reported the detector *covered*, because
``test_detect_base.py`` iterates the registry and runs everything in it, so any detector added
tomorrow arrives already credited with a test nobody wrote. A test that ran **every** registered
detector is therefore dropped and listed separately: it proves the registry can be iterated and
nothing about any detector in it. A test that names two detectors and runs both still counts for
both; only the whole-registry case is a sweep.

**What it cannot see, and says so.** A test function that asks for a pytest fixture cannot be called
outside pytest, so it is counted and skipped rather than guessed at. Every detector shipped today is
covered several times over by fixture-free tests; a future detector whose only test needs a fixture
reads as uncovered, which fails the build with a message rather than passing quietly. That is the
right direction for this to be wrong in.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from sightline.detect.base import Detector, registry, registry_snapshot
from sightline.detect.integrity import CoverageLedger

TESTS_DIR = Path(__file__).resolve().parent

#: The needle that marks a test module as one worth calling. Deliberately a substring of the
#: package path rather than a list of module names: a detector added with its tests in a module
#: nobody thought to list here would otherwise read as covered by a scan that never looked at it.
NEEDLE = "sightline.detect"

#: The guard's own module, which is not scanned. It defines sabotaged detectors on purpose and it
#: is what calls this harness, so calling it from in here would be circular. Nothing else is
#: excluded, and the guard asserts that.
EXCLUDED = frozenset({"test_detector_integrity.py"})


def detector_test_modules() -> tuple[Path, ...]:
    """Every test module that touches the detect package, in a stable order."""
    return tuple(
        sorted(
            path
            for path in TESTS_DIR.glob("test_*.py")
            if path.name not in EXCLUDED and NEEDLE in path.read_text(encoding="utf-8")
        )
    )


def _load(path: Path) -> object:
    """The module, reusing pytest's own import of it when the suite already has one."""
    existing = sys.modules.get(path.stem)
    if existing is not None and getattr(existing, "__file__", None) == str(path):
        return existing
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:  # pragma: no cover - a module that will not load
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def _watching(by_test: dict[str, set[str]], node: dict[str, str]) -> Iterator[None]:
    """Patch :meth:`Detector.run` so every call records the detector id against the live test.

    Patched on the base class, which is the whole of it: ``run`` is final, so no subclass has one
    of its own to patch.
    """
    original = Detector.run

    def watched(self: Detector, document: object, *, files: object = None) -> object:
        by_test.setdefault(node["current"], set()).add(self.id)
        return original(self, document, files=files)  # type: ignore[arg-type]

    Detector.run = watched  # type: ignore[method-assign]
    try:
        yield
    finally:
        Detector.run = original  # type: ignore[method-assign]


def _callables(module: object) -> Iterator[tuple[str, Callable[..., object]]]:
    for name, value in sorted(vars(module).items()):
        if name.startswith("test_") and callable(value) and not inspect.isclass(value):
            yield name, value


def _credit(
    by_test: Mapping[str, set[str]], registered: Collection[str]
) -> tuple[dict[str, tuple[str, ...]], tuple[str, ...]]:
    """Split the observation into per-detector credit and the sweeps credited to nobody.

    A test that ran every registered detector ran the registry. The ``len(registered) > 1`` clause
    is there so that a repository down to a single detector does not read every one of its tests as
    a sweep — with one detector, naming it and iterating the registry are the same act.
    """
    sweep = set(registered)
    swept = tuple(
        sorted(
            node
            for node, ran in by_test.items()
            if len(sweep) > 1 and sweep and sweep <= ran  # ran everything registered
        )
    )
    credited: dict[str, list[str]] = {}
    for node, ran in sorted(by_test.items()):
        if node in swept:
            continue
        for detector_id in sorted(ran):
            credited.setdefault(detector_id, []).append(node)
    return {key: tuple(value) for key, value in sorted(credited.items())}, swept


def build_ledger() -> CoverageLedger:
    """Call every fixture-free detector test and record what each one ran."""
    by_test: dict[str, set[str]] = {}
    node = {"current": "<none>"}
    modules = detector_test_modules()
    called = 0
    needing_fixtures = 0
    raised: list[str] = []

    with _watching(by_test, node):
        for path in modules:
            module = _load(path)
            for name, function in _callables(module):
                if inspect.signature(function).parameters:
                    needing_fixtures += 1
                    continue
                node["current"] = f"{path.name}::{name}"
                called += 1
                # Defining a detector registers it, and several of these tests define one. Under
                # pytest an autouse fixture puts the registry back; called directly there is no
                # fixture, so the snapshot is taken here instead. Without it a throwaway test
                # detector leaks into the registry the guard is about to check.
                with registry_snapshot():
                    try:
                        function()
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except BaseException as exc:  # noqa: BLE001 - recorded, never swallowed
                        raised.append(f"{node['current']}: {type(exc).__name__}")

    node["current"] = "<none>"
    credited, swept = _credit(by_test, set(registry()))
    return CoverageLedger(
        tests_by_detector=credited,
        modules_scanned=tuple(path.name for path in modules),
        sweeps=swept,
        tests_called=called,
        tests_needing_fixtures=needing_fixtures,
        tests_that_raised=tuple(raised),
    )


_CACHED: CoverageLedger | None = None


def ledger() -> CoverageLedger:
    """The ledger, built once per process. Several guards read it and it is not cheap."""
    global _CACHED
    if _CACHED is None:
        _CACHED = build_ledger()
    return _CACHED


__all__ = [
    "EXCLUDED",
    "NEEDLE",
    "TESTS_DIR",
    "build_ledger",
    "detector_test_modules",
    "ledger",
]
