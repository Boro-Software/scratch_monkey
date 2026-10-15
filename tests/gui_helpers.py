"""Shared helpers for the GUI tests: build an AppModel and drive background actions.

Importable without Qt/Atom installed; anything that needs ``AppModel`` imports it
lazily, so test modules guard themselves with ``pytest.importorskip("atom")``.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

DEFERRED_CALL = "scratch_monkey.gui.models.app.deferred_call"
PROJECT_DIR = "scratch_monkey.gui.controllers.instances.PROJECT_DIR"


def wait(event: threading.Event, what: str = "background action") -> None:
    assert event.wait(timeout=5), f"{what} did not complete"


def wait_for(cond, what: str = "condition", timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.01)


def make_app(tmp_path):
    """An AppModel on an empty temp instances dir with a mocked runner.

    Returns (app, instances_dir, project_dir). Tests that create instances patch
    ``PROJECT_DIR`` (see the constant above) with the returned project_dir.
    """
    from scratch_monkey.gui.models import AppModel

    instances_dir = tmp_path / "scratch-monkey"
    instances_dir.mkdir()

    runner = MagicMock()
    runner.container_exists.return_value = False
    runner.container_running.return_value = False
    runner.image_exists.return_value = False

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    app = AppModel(instances_dir=instances_dir, runner=runner)
    return app, instances_dir, project_dir


def sync_deferred_call(done: threading.Event | None = None):
    """Patch AppModel's deferred_call to run callbacks directly (on the worker thread).

    If ``done`` is given it is set after each deferred callback has run, so a test
    can ``wait(done)`` for the action's completion handler.
    """

    def run(fn):
        fn()
        if done is not None:
            done.set()

    return patch(DEFERRED_CALL, side_effect=run)


def call_sync(method, *args, **kwargs) -> str:
    """Call an on_done-style controller method, wait for it, return its err string.

    deferred_call is replaced with direct execution so the completion handlers
    run on the worker thread; the work itself still runs inside run_async.
    """
    done = threading.Event()
    out: list[str] = []

    def on_done(err):
        out.append(err)
        done.set()

    with sync_deferred_call():
        method(*args, on_done, **kwargs)
        wait(done)
    return out[0]


def create_sync(app, name, **kwargs) -> str:
    return call_sync(app.instance_actions.create, name, **kwargs)


def refresh_sync(app, **kwargs) -> None:
    """Run refresh_async to completion (replaces the removed blocking refresh())."""
    done = threading.Event()
    with sync_deferred_call(done):
        app.refresh_async(**kwargs)
        wait(done)
