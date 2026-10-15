"""Contract shared by all dialog-facing controller actions (rename/clone/create instance, create shared volume):
on_done callbacks via AppModel.run_then_reload, worker-thread execution."""

import pytest

atom = pytest.importorskip("atom")

from .gui_helpers import (  # noqa: E402
    call_sync,
    create_sync,
    make_app,
    wait,
)


def _resolve(dotted):
    import importlib

    mod, attr = dotted.rsplit(".", 1)
    return getattr(importlib.import_module(mod), attr)


_DIALOG_CASES = {
    "rename": dict(
        setup=["old"],
        select="old",
        invoke=lambda app, cb: app.instance_actions.rename("old", "new", cb),
        patch_target="scratch_monkey.gui.controllers.instances.rename",
        fail=lambda app, cb: app.instance_actions.rename("nonexistent", "whatever", cb),
        selected_after="new",
        names_after={"new"},
        status="Renamed 'old' to 'new'",
    ),
    "clone": dict(
        setup=["src"],
        select="src",
        invoke=lambda app, cb: app.instance_actions.clone("src", "dest", cb),
        patch_target="scratch_monkey.gui.controllers.instances.clone",
        fail=lambda app, cb: app.instance_actions.clone("src", "src", cb),
        selected_after="dest",
        names_after={"src", "dest"},
        status="Cloned 'src' to 'dest'",
    ),
    "create": dict(
        setup=["existing"],
        select="existing",
        invoke=lambda app, cb: app.instance_actions.create("fresh", cb),
        patch_target="scratch_monkey.gui.controllers.instances.create",
        fail=lambda app, cb: app.instance_actions.create("existing", cb),
        selected_after="fresh",
        names_after={"existing", "fresh"},
        status="Created instance 'fresh'",
    ),
    "shared": dict(
        setup=["existing"],
        select="existing",
        invoke=lambda app, cb: app.shared.create("vol", cb),
        patch_target="scratch_monkey.shared.create_shared",
        fail=lambda app, cb: app.shared.create("bad name!", cb),
        selected_after="existing",
        names_after={"existing"},
        status="Created shared volume 'vol'",
    ),
}


@pytest.mark.parametrize("case_id", list(_DIALOG_CASES))
class TestDialogActions:
    """rename/clone/create/create_shared_volume: on_done callbacks via _run_async."""

    def _app(self, tmp_path, case):
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)
        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            for name in case["setup"]:
                assert create_sync(app, name) == ""
        app.selected_instance = case["select"]
        return app, instances_dir, project_dir

    def _run(self, app, invoke):
        # call_sync passes on_done as the last positional argument
        return call_sync(lambda on_done: invoke(app, on_done))

    def test_success(self, tmp_path, case_id):
        from unittest.mock import patch

        case = _DIALOG_CASES[case_id]
        app, instances_dir, project_dir = self._app(tmp_path, case)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            err = self._run(app, case["invoke"])

        assert err == ""
        assert app.busy is False
        assert {i.name for i in app.instances} == case["names_after"]
        assert app.selected_instance == case["selected_after"]
        assert app.status_message == case["status"]
        assert app.error_message == ""
        if case_id == "shared":
            assert "vol" in app.available_shared

    def test_error_reaches_callback_not_banner(self, tmp_path, case_id):
        from unittest.mock import patch

        case = _DIALOG_CASES[case_id]
        app, instances_dir, project_dir = self._app(tmp_path, case)
        before = {i.name for i in app.instances}

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            err = self._run(app, case["fail"])

        assert err != ""
        assert app.busy is False
        assert app.selected_instance == case["select"]  # unchanged
        assert {i.name for i in app.instances} == before
        # The dialog shows the error; the banner must not duplicate it.
        assert app.error_message == ""
        assert app.status_message == f"Error: {err}"

    def test_busy_rejection_still_calls_back(self, tmp_path, case_id):
        from unittest.mock import patch

        case = _DIALOG_CASES[case_id]
        app, instances_dir, project_dir = self._app(tmp_path, case)
        app.busy = True
        app._busy_status = "Starting 'x'..."
        calls = []

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            case["invoke"](app, calls.append)

        assert len(calls) == 1
        assert calls[0].startswith("Busy")
        assert app.busy is True  # the other operation is untouched
        assert {i.name for i in app.instances} == set(case["setup"])
        assert app.selected_instance == case["select"]

    def test_work_and_reload_run_off_gui_thread(self, tmp_path, case_id):
        """The action and the list reload run in the worker; the caller returns at once."""
        import threading
        from unittest.mock import patch

        import scratch_monkey.gui.models.app as models

        case = _DIALOG_CASES[case_id]
        app, instances_dir, project_dir = self._app(tmp_path, case)
        gui_thread = threading.current_thread()
        gate = threading.Event()
        threads = {}
        real_target = _resolve(case["patch_target"])
        real_list_all = models.list_all

        def spy_action(*a, **kw):
            threads["action"] = threading.current_thread()
            assert gate.wait(timeout=5)
            return real_target(*a, **kw)

        def spy_list_all(*a, **kw):
            threads["list_all"] = threading.current_thread()
            return real_list_all(*a, **kw)

        done = threading.Event()
        results = []

        def on_done(err):
            results.append(err)
            done.set()

        with (
            patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir),
            patch(case["patch_target"], side_effect=spy_action),
            patch("scratch_monkey.gui.models.app.list_all", side_effect=spy_list_all),
            patch("scratch_monkey.gui.models.app.deferred_call", side_effect=lambda fn: fn()),
        ):
            case["invoke"](app, on_done)
            # Returned while the action is still blocked: the GUI thread is not held.
            assert results == []
            assert app.busy is True
            gate.set()
            wait(done)

        assert results == [""]
        assert threads["action"] is not gui_thread
        assert threads["list_all"] is not gui_thread
