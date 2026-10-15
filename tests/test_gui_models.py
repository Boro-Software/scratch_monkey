"""Tests for scratch_monkey.gui.models: entries, InstanceModel, AppModel state and async helpers.

Action tests live in tests/test_gui_controllers_*.py; shared helpers in tests/gui_helpers.py.
"""

from pathlib import Path

import pytest

atom = pytest.importorskip("atom")

from scratch_monkey.config import InstanceConfig  # noqa: E402
from scratch_monkey.gui.models import (  # noqa: E402
    AppModel,
    InstanceModel,
    SharedVolumeEntry,
    VolumeMountEntry,
)
from scratch_monkey.instance import InstanceInfo  # noqa: E402

from .gui_helpers import (  # noqa: E402
    create_sync,
    make_app,
    refresh_sync,
    sync_deferred_call,
    wait,
)


class TestVolumeMountEntry:
    def test_from_spec_host_container(self):
        e = VolumeMountEntry.from_spec("/host:/container")
        assert e.host_path == "/host"
        assert e.container_path == "/container"
        assert e.mode == "ro"

    def test_from_spec_with_mode(self):
        e = VolumeMountEntry.from_spec("/host:/container:ro")
        assert e.host_path == "/host"
        assert e.container_path == "/container"
        assert e.mode == "ro"

    def test_to_spec_always_includes_mode_ro(self):
        e = VolumeMountEntry(host_path="/a", container_path="/b", mode="ro")
        assert e.to_spec() == "/a:/b:ro"

    def test_to_spec_always_includes_mode_rw(self):
        e = VolumeMountEntry(host_path="/a", container_path="/b", mode="rw")
        assert e.to_spec() == "/a:/b:rw"

    def test_round_trip_explicit_rw(self):
        original = "/data:/mnt/data:rw"
        e = VolumeMountEntry.from_spec(original)
        assert e.to_spec() == original

    def test_round_trip_no_suffix_outputs_ro(self):
        e = VolumeMountEntry.from_spec("/data:/mnt/data")
        assert e.to_spec() == "/data:/mnt/data:ro"


class TestInstanceModelBaseImage:
    def test_from_info_populates_base_image(self):
        info = InstanceInfo(
            name="test",
            directory="/tmp/test",
            image_built=False,
            overlay_running=False,
            config=InstanceConfig(),
            base_image="scratch_monkey_fedora",
        )
        m = InstanceModel.from_info(info)
        assert m.base_image == "scratch_monkey_fedora"

    def test_from_info_base_image_none_becomes_empty(self):
        info = InstanceInfo(
            name="test",
            directory="/tmp/test",
            image_built=False,
            overlay_running=False,
            config=InstanceConfig(),
            base_image=None,
        )
        m = InstanceModel.from_info(info)
        assert m.base_image == ""


class TestInstanceModelGpuAndDevices:
    def _make_info(self, gpu=False, devices=None):
        cfg = InstanceConfig(
            gpu=gpu,
            devices=devices or [],
        )
        return InstanceInfo(
            name="test",
            directory="/tmp/test",
            image_built=False,
            overlay_running=False,
            config=cfg,
        )

    def test_from_info_populates_gpu_and_devices(self):
        info = self._make_info(gpu=True, devices=["/dev/dri", "/dev/video0"])
        m = InstanceModel.from_info(info)
        assert m.gpu is True
        assert m.devices == ["/dev/dri", "/dev/video0"]

    def test_from_info_defaults_gpu_false(self):
        info = self._make_info()
        m = InstanceModel.from_info(info)
        assert m.gpu is False
        assert m.devices == []

    def test_to_config_serializes_gpu_and_devices(self):
        m = InstanceModel()
        m.gpu = True
        m.devices = ["/dev/dri", "/dev/kfd"]
        cfg = m.to_config()
        assert cfg.gpu is True
        assert cfg.devices == ["/dev/dri", "/dev/kfd"]

    def test_to_config_gpu_false_by_default(self):
        m = InstanceModel()
        cfg = m.to_config()
        assert cfg.gpu is False
        assert cfg.devices == []


class TestInstanceModelVolumes:
    def _make_info(self, volumes=None, shared=None):
        cfg = InstanceConfig(
            volumes=volumes or [],
            shared=shared or [],
        )
        return InstanceInfo(
            name="test",
            directory="/tmp/test",
            image_built=False,
            overlay_running=False,
            config=cfg,
        )

    def test_from_info_populates_volume_entries(self):
        info = self._make_info(volumes=["/host:/container", "/a:/b:ro"])
        m = InstanceModel.from_info(info)
        assert len(m.volume_entries) == 2
        assert m.volume_entries[0].host_path == "/host"
        assert m.volume_entries[1].mode == "ro"

    def test_to_config_serializes_volume_entries(self):
        m = InstanceModel()
        m.volume_entries = [
            VolumeMountEntry(host_path="/a", container_path="/b", mode="rw"),
            VolumeMountEntry(host_path="/c", container_path="/d", mode="ro"),
        ]
        cfg = m.to_config()
        assert cfg.volumes == ["/a:/b:rw", "/c:/d:ro"]

    def test_to_config_filters_empty_volume_entries(self):
        """Blank volume entries (both paths empty) are silently dropped."""
        m = InstanceModel()
        m.volume_entries = [
            VolumeMountEntry(host_path="/a", container_path="/b", mode="ro"),
            VolumeMountEntry(),  # blank — should be filtered
            VolumeMountEntry(host_path="/c", container_path="/d", mode="rw"),
        ]
        cfg = m.to_config()
        assert cfg.volumes == ["/a:/b:ro", "/c:/d:rw"]

    def test_to_config_keeps_partially_filled_entry(self):
        """Entry with only host_path filled is kept (validation catches it later)."""
        m = InstanceModel()
        m.volume_entries = [
            VolumeMountEntry(host_path="/a", container_path="", mode="ro"),
        ]
        cfg = m.to_config()
        assert len(cfg.volumes) == 1

    def test_to_config_serializes_shared_entries(self):
        m = InstanceModel()
        m.shared_entries = [
            SharedVolumeEntry(name="comms", enabled=True, mode="rw"),
            SharedVolumeEntry(name="data", enabled=True, mode="ro"),
            SharedVolumeEntry(name="unused", enabled=False, mode="rw"),
        ]
        cfg = m.to_config()
        assert "comms" in cfg.shared
        assert "data:ro" in cfg.shared
        assert all("unused" not in s for s in cfg.shared)

    def test_add_volume_entry(self):
        m = InstanceModel()
        assert len(m.volume_entries) == 0
        m.add_volume_entry()
        assert len(m.volume_entries) == 1

    def test_remove_volume_entry(self):
        m = InstanceModel()
        m.volume_entries = [
            VolumeMountEntry(host_path="/a", container_path="/b"),
            VolumeMountEntry(host_path="/c", container_path="/d"),
        ]
        m.remove_volume_entry(0)
        assert len(m.volume_entries) == 1
        assert m.volume_entries[0].host_path == "/c"


class TestAppModelSharedEntries:
    def test_init_shared_entries_marks_enabled(self, tmp_path):
        """init_shared_entries should mark instance's shared volumes as enabled."""
        # Set up instances dir with a shared volume
        instances_dir = tmp_path / "scratch-monkey"
        instances_dir.mkdir()
        shared_dir = instances_dir / ".shared"
        shared_dir.mkdir()
        (shared_dir / "comms").mkdir()
        (shared_dir / "data").mkdir()

        # Create AppModel (will try to list instances, which is fine with empty dir)
        from unittest.mock import MagicMock
        runner = MagicMock()
        runner.container_exists.return_value = False
        runner.container_running.return_value = False
        runner.image_exists.return_value = False
        app = AppModel(instances_dir=instances_dir, runner=runner)
        refresh_sync(app)

        # Create a model with comms enabled
        m = InstanceModel()
        m.shared = ["comms"]
        app.init_shared_entries(m)

        assert len(m.shared_entries) == 2
        comms = next(e for e in m.shared_entries if e.name == "comms")
        data = next(e for e in m.shared_entries if e.name == "data")
        assert comms.enabled is True
        assert comms.mode == "rw"
        assert data.enabled is False

    def test_init_shared_entries_parses_mode(self, tmp_path):
        instances_dir = tmp_path / "scratch-monkey"
        instances_dir.mkdir()
        shared_dir = instances_dir / ".shared"
        shared_dir.mkdir()
        (shared_dir / "comms").mkdir()

        from unittest.mock import MagicMock
        runner = MagicMock()
        runner.container_exists.return_value = False
        runner.container_running.return_value = False
        runner.image_exists.return_value = False
        app = AppModel(instances_dir=instances_dir, runner=runner)
        refresh_sync(app)

        m = InstanceModel()
        m.shared = ["comms:ro"]
        app.init_shared_entries(m)

        comms = next(e for e in m.shared_entries if e.name == "comms")
        assert comms.enabled is True
        assert comms.mode == "ro"


class TestAppModelNewInstanceModel:
    def test_new_instance_model(self, tmp_path):
        app, instances_dir, project_dir = make_app(tmp_path)

        # Add a shared volume
        shared_dir = instances_dir / ".shared"
        shared_dir.mkdir()
        (shared_dir / "comms").mkdir()

        refresh_sync(app)
        m = app.new_instance_model()

        assert isinstance(m, InstanceModel)
        assert len(m.shared_entries) == 1
        assert m.shared_entries[0].name == "comms"


class TestControllerWiring:
    """AppModel owns the action controllers and exposes the controller API."""

    # Every action that moved off AppModel; none may linger as a shim.
    _MOVED = [
        "start_instance", "stop_instance", "build_instance", "enter_instance",
        "reset_overlay", "refresh_overlay", "delete_instance", "rename_instance",
        "clone_instance", "create_instance", "create_shared_volume",
        "export_command", "edit_file", "edit_config", "open_directory",
        "open_terminal", "_run_async", "_run_then_reload",
    ]

    def test_app_model_exposes_controllers(self, tmp_path):
        from scratch_monkey.gui.controllers import (
            InstanceController,
            OverlayController,
            SharedVolumeController,
            ToolsController,
        )

        app, _, _ = make_app(tmp_path)
        assert isinstance(app.instance_actions, InstanceController)
        assert isinstance(app.overlays, OverlayController)
        assert isinstance(app.shared, SharedVolumeController)
        assert isinstance(app.tools, ToolsController)
        # `instances` stays the observable list of InstanceModel, not a controller
        assert app.instances == []

    def test_controllers_are_per_app(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        app1, _, _ = make_app(tmp_path / "a")
        app2, _, _ = make_app(tmp_path / "b")
        assert app1.overlays is not app2.overlays
        assert app1.overlays._app is app1

    @pytest.mark.parametrize("name", _MOVED)
    def test_old_action_methods_are_gone(self, name):
        assert not hasattr(AppModel, name)

    def test_runner_property_returns_runner(self, tmp_path):
        app, _, _ = make_app(tmp_path)
        assert app.runner is app._runner

    def test_defer_goes_through_deferred_call(self, tmp_path):
        from unittest.mock import patch

        app, _, _ = make_app(tmp_path)
        calls = []
        with patch("scratch_monkey.gui.models.app.deferred_call", side_effect=calls.append):
            fn = object()
            app.defer(fn)
        assert calls == [fn]

    def test_reload_when_idle_starts_reload_immediately(self, tmp_path):
        from unittest.mock import patch

        app, _, _ = make_app(tmp_path)
        with patch.object(AppModel, "refresh_async") as refresh:
            app.reload_when_idle("Reloaded")
        refresh.assert_called_once_with("Reloading config...", done_status="Reloaded")
        assert app._reload_pending == ""

    def test_reload_when_idle_queues_while_busy(self, tmp_path):
        from unittest.mock import patch

        app, _, _ = make_app(tmp_path)
        app.busy = True
        with patch.object(AppModel, "refresh_async") as refresh:
            app.reload_when_idle("Reloaded")
        refresh.assert_not_called()
        assert app._reload_pending == "Reloaded"


class TestGUISmoke:
    """Smoke tests to verify the GUI import chain works."""

    def test_gui_main_module_imports(self):
        """Verify scratch_monkey.gui.main can be imported."""
        from scratch_monkey.gui import main  # noqa: F401

    def test_models_import_chain(self):
        """Verify all model classes are importable."""
        from scratch_monkey.gui.models import (  # noqa: F401
            AppModel,
            InstanceModel,
            SharedVolumeEntry,
            VolumeMountEntry,
        )

    def test_app_model_creates_with_empty_dir(self, tmp_path):
        """Verify AppModel can be created with an empty instances dir."""
        from unittest.mock import MagicMock

        from scratch_monkey.gui.models import AppModel

        runner = MagicMock()
        runner.container_exists.return_value = False
        runner.container_running.return_value = False
        runner.image_exists.return_value = False

        instances_dir = tmp_path / "scratch-monkey"
        instances_dir.mkdir()
        app = AppModel(instances_dir=instances_dir, runner=runner)
        assert app.instances == []
        refresh_sync(app)
        assert app.status_message.startswith("Loaded")

    def test_enaml_views_compile(self):
        """Verify all enaml view files compile without errors."""
        import enaml
        with enaml.imports():
            from scratch_monkey.gui.views import (
                config_editor,  # noqa: F401
                instance_detail,  # noqa: F401
                instance_list,  # noqa: F401
                main_window,  # noqa: F401
            )
            from scratch_monkey.gui.views.dialogs import (
                banner_details_dialog,  # noqa: F401
                clone_dialog,  # noqa: F401
                create_instance_dialog,  # noqa: F401
                create_shared_dialog,  # noqa: F401
                export_dialog,  # noqa: F401
                rename_dialog,  # noqa: F401
            )


class TestInstanceModelEnvVars:
    def test_add_env_var_default(self):
        m = InstanceModel()
        m.add_env_var()
        assert m.env_vars == [""]

    def test_add_env_var_with_value(self):
        m = InstanceModel()
        m.add_env_var("FOO=bar")
        assert m.env_vars == ["FOO=bar"]

    def test_remove_env_var(self):
        m = InstanceModel()
        m.env_vars = ["A=1", "B=2", "C=3"]
        m.remove_env_var(1)
        assert m.env_vars == ["A=1", "C=3"]

    def test_remove_env_var_out_of_range(self):
        m = InstanceModel()
        m.env_vars = ["A=1"]
        m.remove_env_var(5)
        assert m.env_vars == ["A=1"]

    def test_to_config_includes_env(self):
        m = InstanceModel()
        m.env_vars = ["FOO=bar", "BAZ=qux"]
        cfg = m.to_config()
        assert cfg.env == ["FOO=bar", "BAZ=qux"]


class TestInstanceModelDirty:
    """Tests for InstanceModel dirty tracking."""

    def _make_info(self, **kwargs):
        cfg = InstanceConfig(**kwargs)
        return InstanceInfo(
            name="test",
            directory="/tmp/test",
            image_built=False,
            overlay_running=False,
            config=cfg,
        )

    def test_not_dirty_after_from_info(self):
        info = self._make_info(cmd="/bin/bash")
        m = InstanceModel.from_info(info)
        assert m.dirty is False

    def test_dirty_after_scalar_change(self):
        info = self._make_info(cmd="/bin/bash")
        m = InstanceModel.from_info(info)
        m.cmd = "/bin/zsh"
        assert m.dirty is True

    def test_podman_round_trip(self):
        m = InstanceModel.from_info(self._make_info(podman=True))
        assert m.podman is True
        assert m.to_config().podman is True
        m2 = InstanceModel.from_info(self._make_info())
        assert m2.podman is False
        assert m2.to_config().podman is False

    def test_dirty_after_podman_change(self):
        m = InstanceModel.from_info(self._make_info())
        assert m.dirty is False
        m.podman = True
        assert m.dirty is True
        m.podman = False
        assert m.dirty is False

    def test_revert_restores_podman(self):
        m = InstanceModel.from_info(self._make_info(podman=True))
        m.podman = False
        m.revert()
        assert m.podman is True

    def test_dirty_after_bool_change(self):
        info = self._make_info()
        m = InstanceModel.from_info(info)
        m.wayland = True
        assert m.dirty is True

    def test_not_dirty_after_save(self, tmp_path):
        # Create a real instance dir so save() works
        inst_dir = tmp_path / "test"
        inst_dir.mkdir()
        info = InstanceInfo(
            name="test",
            directory=str(inst_dir),
            image_built=False,
            overlay_running=False,
            config=InstanceConfig(),
        )
        m = InstanceModel.from_info(info)
        m.cmd = "/bin/zsh"
        assert m.dirty is True
        m.save()
        assert m.dirty is False

    def test_revert_restores_saved_state(self):
        info = self._make_info(cmd="/bin/bash", wayland=False)
        m = InstanceModel.from_info(info)
        m.cmd = "/bin/zsh"
        m.wayland = True
        assert m.dirty is True
        m.revert()
        assert m.dirty is False
        assert m.cmd == "/bin/bash"
        assert m.wayland is False

    def test_check_dirty_detects_nested_change(self):
        info = self._make_info(volumes=["/a:/b"])
        m = InstanceModel.from_info(info)
        assert m.dirty is False
        # In-place mutation (won't trigger list observer)
        m.volume_entries[0].host_path = "/changed"
        # Must call check_dirty explicitly
        m.check_dirty()
        assert m.dirty is True

    def test_revert_restores_volumes(self):
        info = self._make_info(volumes=["/a:/b", "/c:/d:ro"])
        m = InstanceModel.from_info(info)
        m.volume_entries = []  # remove all
        assert m.dirty is True
        m.revert()
        assert m.dirty is False
        assert len(m.volume_entries) == 2
        assert m.volume_entries[0].host_path == "/a"

    def test_revert_restores_env_vars(self):
        info = self._make_info(env=["FOO=bar"])
        m = InstanceModel.from_info(info)
        # Note: from_info stores env in env_vars
        m.env_vars = ["CHANGED=1"]
        assert m.dirty is True
        m.revert()
        assert m.dirty is False
        assert m.env_vars == ["FOO=bar"]

    def test_has_unsaved_changes(self, tmp_path):
        from unittest.mock import MagicMock, patch

        instances_dir = tmp_path / "scratch-monkey"
        instances_dir.mkdir()

        runner = MagicMock()
        runner.container_exists.return_value = False
        runner.container_running.return_value = False
        runner.image_exists.return_value = False

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            app = AppModel(instances_dir=instances_dir, runner=runner)

        # No instances, no unsaved changes
        assert app.has_unsaved_changes() is False

        # Create an instance
        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        # Initially not dirty
        assert app.has_unsaved_changes() is False

        # Modify the instance
        inst_model = next(i for i in app.instances if i.name == "myinst")
        inst_model.cmd = "/bin/zsh"

        assert app.has_unsaved_changes() is True


class TestRunAsync:
    """Tests for AppModel.run_async background task helper."""

    def test_run_async_sets_busy_and_calls_on_success(self, tmp_path):
        """run_async sets busy=True, runs work, then calls on_success with result."""
        import threading
        from unittest.mock import patch

        app, _, project_dir = make_app(tmp_path)

        # Replace deferred_call with synchronous execution for testing
        with patch("scratch_monkey.gui.models.app.deferred_call", side_effect=lambda fn: fn()):
            results = []
            done = threading.Event()

            def work():
                return 42

            def on_success(result):
                results.append(result)
                done.set()

            app.run_async("Working...", work, on_success)
            wait(done)

        assert results == [42]
        assert app.busy is False

    def test_run_async_calls_on_error(self, tmp_path):
        """run_async calls on_error when work() raises."""
        import threading
        from unittest.mock import patch

        app, _, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.models.app.deferred_call", side_effect=lambda fn: fn()):
            errors = []
            done = threading.Event()

            def work():
                raise RuntimeError("boom")

            def on_error(exc):
                errors.append(str(exc))
                done.set()

            app.run_async("Working...", work, on_error=on_error)
            wait(done)

        assert errors == ["boom"]
        assert app.busy is False

    def test_run_async_default_error_sets_status(self, tmp_path):
        """Without on_error, run_async sets status_message to error string."""
        import threading

        app, _, project_dir = make_app(tmp_path)
        done = threading.Event()

        with sync_deferred_call(done):
            def work():
                raise RuntimeError("something broke")

            app.run_async("Working...", work)
            wait(done)

        assert "something broke" in app.status_message
        assert app.busy is False

    def test_run_async_reentrant_guard(self, tmp_path):
        """run_async returns immediately if already busy."""
        app, _, project_dir = make_app(tmp_path)
        app.busy = True

        called = []
        app.run_async("Working...", lambda: called.append(1))
        assert called == []

    def test_run_async_busy_sets_status_and_skips_work(self, tmp_path):
        """A second run_async call while busy sets a busy status message
        (naming the in-flight operation) and does NOT invoke the new work()."""
        import threading

        app, _, project_dir = make_app(tmp_path)
        gate = threading.Event()
        release = threading.Event()
        done = threading.Event()

        def first_work():
            gate.set()
            release.wait(timeout=2)
            return None

        with sync_deferred_call(done):
            app.run_async("Refreshing 'foo'...", first_work, on_success=lambda r: None)
            wait(gate, "first_work start")
            assert app.busy is True

            second_called = []
            app.run_async("Second op...", lambda: second_called.append(1))

            assert second_called == []
            assert "Busy" in app.status_message
            assert "Refreshing 'foo'..." in app.status_message

            release.set()
            wait(done)

        assert app.busy is False

    def test_run_async_clears_error_message_on_new_operation(self, tmp_path):
        """error_message from a prior failed operation is cleared when a new
        operation starts."""
        import threading

        app, _, project_dir = make_app(tmp_path)
        done = threading.Event()

        def failing_work():
            raise RuntimeError("first failure")

        with sync_deferred_call(done):
            app.run_async("First op...", failing_work)
            wait(done)

        assert "first failure" in app.error_message

        done2 = threading.Event()

        with sync_deferred_call(done2):
            app.run_async("Second op...", lambda: None, on_success=lambda r: None)
            wait(done2)

        assert app.error_message == ""

    def test_run_async_sets_error_message_on_failure(self, tmp_path):
        """run_async sets error_message (in addition to status_message) when
        work() raises, even when a tailored on_error callback is provided."""
        import threading

        app, _, project_dir = make_app(tmp_path)
        done = threading.Event()

        def failing_work():
            raise RuntimeError("disk on fire")

        def on_error(exc):
            app.status_message = f"Custom: {exc}"

        with sync_deferred_call(done):
            app.run_async("Working...", failing_work, on_error=on_error)
            wait(done)

        assert "disk on fire" in app.error_message
        assert app.status_message == "Custom: disk on fire"


    def test_refresh_async(self, tmp_path):
        """refresh_async uses run_async to reload instances."""
        import threading

        app, instances_dir, project_dir = make_app(tmp_path)

        done = threading.Event()

        with sync_deferred_call(done):
            app.refresh_async()
            wait(done)

        assert app.busy is False
        assert "Loaded" in app.status_message

    def test_run_async_sets_status_message_immediately(self, tmp_path):
        """run_async sets status_message to the status param before the thread runs."""
        import threading
        from unittest.mock import patch

        app, _, project_dir = make_app(tmp_path)
        observed_status = []
        done = threading.Event()

        def work():
            observed_status.append(app.status_message)
            return None

        with patch("scratch_monkey.gui.models.app.deferred_call", side_effect=lambda fn: fn()):
            app.run_async("Processing...", work, on_success=lambda r: done.set())
            wait(done)

        assert observed_status == ["Processing..."]

    def test_run_async_busy_true_during_work(self, tmp_path):
        """run_async keeps busy=True while work() is running."""
        import threading
        from unittest.mock import patch

        app, _, project_dir = make_app(tmp_path)
        observed_busy = []
        done = threading.Event()

        def work():
            observed_busy.append(app.busy)
            return None

        with patch("scratch_monkey.gui.models.app.deferred_call", side_effect=lambda fn: fn()):
            app.run_async("Working...", work, on_success=lambda r: done.set())
            wait(done)

        assert observed_busy == [True]
        assert app.busy is False


class TestPollStatus:
    """Tests for AppModel.poll_status lightweight status poller."""

    def test_poll_status_updates_overlay_running(self, tmp_path):
        """poll_status updates overlay_running in-place on existing instances."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)
        runner = app.runner

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        inst = next(i for i in app.instances if i.name == "myinst")
        assert inst.overlay_running is False
        instances_before = app.instances

        runner.container_running.return_value = True

        done = threading.Event()

        with sync_deferred_call(done):
            app.poll_status()
            wait(done)

        assert inst.overlay_running is True
        assert app.instances is instances_before
        assert app._polling is False

    def test_poll_status_updates_image_built(self, tmp_path):
        """poll_status updates image_built in-place."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)
        runner = app.runner

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        inst = next(i for i in app.instances if i.name == "myinst")
        assert inst.image_built is False

        runner.image_exists.return_value = True

        done = threading.Event()

        with sync_deferred_call(done):
            app.poll_status()
            wait(done)

        assert inst.image_built is True
        assert app._polling is False

    def test_poll_status_skips_when_busy(self, tmp_path):
        """poll_status returns immediately if busy is True."""
        app, _, _ = make_app(tmp_path)
        runner = app.runner
        app.busy = True

        app.poll_status()
        assert app._polling is False
        runner.container_running.assert_not_called()

    def test_poll_status_skips_when_already_polling(self, tmp_path):
        """poll_status returns immediately if _polling is True."""
        app, _, _ = make_app(tmp_path)
        runner = app.runner
        app._polling = True

        app.poll_status()
        # Should not have spawned a new poll (runner not called again)
        runner.container_running.assert_not_called()

    def test_poll_status_preserves_dirty_state(self, tmp_path):
        """poll_status does not affect dirty flag on instances."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)
        runner = app.runner

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        inst = next(i for i in app.instances if i.name == "myinst")
        inst.cmd = "changed"
        inst.check_dirty()
        assert inst.dirty is True

        runner.container_running.return_value = True

        done = threading.Event()

        with sync_deferred_call(done):
            app.poll_status()
            wait(done)

        assert inst.dirty is True
        assert inst.overlay_running is True

    def test_poll_status_handles_error(self, tmp_path):
        """poll_status clears _polling on exception without crashing."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)
        runner = app.runner

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        runner.container_running.side_effect = RuntimeError("podman down")

        done = threading.Event()

        with sync_deferred_call(done):
            app.poll_status()
            wait(done)

        assert app._polling is False


class TestSudoModel:
    def test_from_info_sudo_true(self):
        info = InstanceInfo(
            name="test", directory=Path("/tmp/test"),
            image_built=False, overlay_running=False,
            config=InstanceConfig(sudo=True), base_image="",
        )
        m = InstanceModel.from_info(info)
        assert m.sudo is True

    def test_from_info_sudo_false(self):
        info = InstanceInfo(
            name="test", directory=Path("/tmp/test"),
            image_built=False, overlay_running=False,
            config=InstanceConfig(sudo=False), base_image="",
        )
        m = InstanceModel.from_info(info)
        assert m.sudo is False

    def test_to_config_includes_sudo(self):
        m = InstanceModel()
        m.sudo = False
        cfg = m.to_config()
        assert cfg.sudo is False

    def test_sudo_change_triggers_dirty(self):
        info = InstanceInfo(
            name="test", directory=Path("/tmp/test"),
            image_built=False, overlay_running=False,
            config=InstanceConfig(sudo=True), base_image="",
        )
        m = InstanceModel.from_info(info)
        assert m.dirty is False
        m.sudo = False
        assert m.dirty is True

    def test_revert_restores_sudo(self):
        info = InstanceInfo(
            name="test", directory=Path("/tmp/test"),
            image_built=False, overlay_running=False,
            config=InstanceConfig(sudo=True), base_image="",
        )
        m = InstanceModel.from_info(info)
        m.sudo = False
        assert m.dirty is True
        m.revert()
        assert m.sudo is True
        assert m.dirty is False

    def test_is_fedora_true(self, tmp_path):
        inst_dir = tmp_path / "fedtest"
        inst_dir.mkdir()
        (inst_dir / "Dockerfile").write_text("FROM scratch_monkey_fedora\n")
        (inst_dir / "home").mkdir()
        (inst_dir / "scratch.toml").write_text("")
        (inst_dir / ".env").touch()
        info = InstanceInfo(
            name="fedtest", directory=inst_dir,
            image_built=False, overlay_running=False,
            config=InstanceConfig(), base_image="",
        )
        m = InstanceModel.from_info(info)
        assert m.is_fedora is True

    def test_is_fedora_false(self, tmp_path):
        inst_dir = tmp_path / "scrtest"
        inst_dir.mkdir()
        (inst_dir / "Dockerfile").write_text("FROM scratch_monkey\n")
        (inst_dir / "home").mkdir()
        (inst_dir / "scratch.toml").write_text("")
        (inst_dir / ".env").touch()
        info = InstanceInfo(
            name="scrtest", directory=inst_dir,
            image_built=False, overlay_running=False,
            config=InstanceConfig(), base_image="",
        )
        m = InstanceModel.from_info(info)
        assert m.is_fedora is False


class TestX11Model:
    def test_from_info_x11_true(self):
        info = InstanceInfo(
            name="test", directory=Path("/tmp/test"),
            image_built=False, overlay_running=False,
            config=InstanceConfig(x11=True), base_image="",
        )
        m = InstanceModel.from_info(info)
        assert m.x11 is True

    def test_from_info_x11_false(self):
        info = InstanceInfo(
            name="test", directory=Path("/tmp/test"),
            image_built=False, overlay_running=False,
            config=InstanceConfig(x11=False), base_image="",
        )
        m = InstanceModel.from_info(info)
        assert m.x11 is False

    def test_to_config_includes_x11(self):
        m = InstanceModel()
        m.x11 = True
        cfg = m.to_config()
        assert cfg.x11 is True

    def test_x11_change_triggers_dirty(self):
        info = InstanceInfo(
            name="test", directory=Path("/tmp/test"),
            image_built=False, overlay_running=False,
            config=InstanceConfig(x11=False), base_image="",
        )
        m = InstanceModel.from_info(info)
        assert m.dirty is False
        m.x11 = True
        assert m.dirty is True


class TestInitialLoad:
    """AppModel must not touch podman in __init__; the load is async."""

    def _make_dir(self, tmp_path, names=("alpha", "beta")):
        from scratch_monkey.config import save

        instances_dir = tmp_path / "scratch-monkey"
        instances_dir.mkdir()
        for n in names:
            d = instances_dir / n
            d.mkdir()
            (d / "Dockerfile").write_text("FROM scratch_monkey\n")
            save(d / "scratch.toml", InstanceConfig())
        return instances_dir

    def _sync_call(self, app_call):
        import threading

        done = threading.Event()

        with sync_deferred_call(done):
            app_call()
            wait(done)

    def test_init_makes_no_podman_calls(self, tmp_path):
        from unittest.mock import MagicMock

        instances_dir = self._make_dir(tmp_path)
        runner = MagicMock()
        app = AppModel(instances_dir=instances_dir, runner=runner)
        assert runner.method_calls == []
        assert app.instances == []

    def test_refresh_async_shows_loading_status_while_running(self, tmp_path):
        from unittest.mock import MagicMock, patch

        instances_dir = self._make_dir(tmp_path)
        app = AppModel(instances_dir=instances_dir, runner=MagicMock())
        with patch("scratch_monkey.gui.models.app.threading.Thread"):
            app.refresh_async("Loading instances...")
        assert app.status_message == "Loading instances..."
        assert app.busy is True

    def test_initial_async_load_populates_instances(self, tmp_path):
        from unittest.mock import MagicMock

        instances_dir = self._make_dir(tmp_path)
        runner = MagicMock()
        runner.image_exists.return_value = True
        runner.container_running.return_value = False
        app = AppModel(instances_dir=instances_dir, runner=runner)
        self._sync_call(lambda: app.refresh_async("Loading instances..."))
        assert [i.name for i in app.instances] == ["alpha", "beta"]
        assert app.status_message == "Loaded 2 instance(s)"
        assert app.error_message == ""
        assert app.busy is False

    def test_per_instance_errors_surface_in_error_message(self, tmp_path):
        from unittest.mock import MagicMock

        from scratch_monkey.container import PodmanError

        instances_dir = self._make_dir(tmp_path)
        runner = MagicMock()
        runner.image_exists.return_value = True

        def running(name):
            if name == "beta-overlay":
                raise PodmanError("podman 'inspect' timed out: stuck lock")
            return False

        runner.container_running.side_effect = running
        app = AppModel(instances_dir=instances_dir, runner=runner)
        self._sync_call(lambda: app.refresh_async("Loading instances..."))
        assert [i.name for i in app.instances] == ["alpha", "beta"]
        by_name = {i.name: i for i in app.instances}
        assert by_name["alpha"].status_error == ""
        assert "timed out" in by_name["beta"].status_error
        assert app.error_message.startswith("Could not query podman for: beta")
        assert "alpha" not in app.error_message
        assert "timed out" in app.error_message

    def test_whole_load_failure_sets_error_message(self, tmp_path):
        from unittest.mock import MagicMock, patch

        from scratch_monkey.container import PodmanError

        instances_dir = self._make_dir(tmp_path)
        app = AppModel(instances_dir=instances_dir, runner=MagicMock())
        with patch(
            "scratch_monkey.gui.models.app.list_all", side_effect=PodmanError("podman is broken")
        ):
            self._sync_call(lambda: app.refresh_async("Loading instances..."))
        assert "podman is broken" in app.error_message
        assert app.status_message.startswith("Error loading instances")
        assert app.busy is False

    def test_poll_failure_does_not_clobber_error_message(self, tmp_path):
        from unittest.mock import MagicMock

        instances_dir = self._make_dir(tmp_path, names=("alpha",))
        runner = MagicMock()
        runner.image_exists.return_value = False
        runner.container_running.return_value = False
        app = AppModel(instances_dir=instances_dir, runner=runner)
        self._sync_call(lambda: app.refresh_async("Loading instances..."))
        app.error_message = "Could not query podman for: alpha (x)"

        runner.container_running.side_effect = RuntimeError("boom")
        self._sync_call(app.poll_status)
        assert app.error_message == "Could not query podman for: alpha (x)"
        assert app._polling is False

    def test_poll_skips_instance_that_times_out(self, tmp_path):
        from unittest.mock import MagicMock

        from scratch_monkey.container import PodmanError

        instances_dir = self._make_dir(tmp_path)
        runner = MagicMock()
        runner.image_exists.return_value = False
        runner.container_running.return_value = False
        app = AppModel(instances_dir=instances_dir, runner=runner)
        self._sync_call(lambda: app.refresh_async("Loading instances..."))

        def running(name):
            if name == "alpha-overlay":
                raise PodmanError("timed out")
            return True

        runner.container_running.side_effect = running
        self._sync_call(app.poll_status)
        by_name = {i.name: i for i in app.instances}
        assert by_name["alpha"].overlay_running is False  # unchanged
        assert by_name["beta"].overlay_running is True


class TestPollStatusErrors:
    """poll_status sets/clears per-instance status_error and the load-error banner."""

    def _make_dir(self, tmp_path, names):
        from scratch_monkey.config import save

        instances_dir = tmp_path / "scratch-monkey"
        instances_dir.mkdir()
        for n in names:
            d = instances_dir / n
            d.mkdir()
            (d / "Dockerfile").write_text("FROM scratch_monkey\n")
            save(d / "scratch.toml", InstanceConfig())
        return instances_dir

    def _sync_call(self, app_call):
        import threading

        done = threading.Event()

        with sync_deferred_call(done):
            app_call()
            wait(done)

    def _loaded(self, tmp_path, names=("alpha", "beta", "gamma"), stuck=()):
        """An app whose initial load saw `stuck` instances' overlay queries time out."""
        from unittest.mock import MagicMock

        from scratch_monkey.container import PodmanError

        runner = MagicMock()
        runner.image_exists.return_value = True

        def running(name):
            if name.removesuffix("-overlay") in stuck:
                raise PodmanError(f"timed out on {name}")
            return False

        runner.container_running.side_effect = running
        app = AppModel(instances_dir=self._make_dir(tmp_path, names), runner=runner)
        self._sync_call(lambda: app.refresh_async("Loading instances..."))
        return app, runner

    def test_poll_failure_sets_status_error_on_right_instance(self, tmp_path):
        from unittest.mock import MagicMock

        from scratch_monkey.container import PodmanError

        app, runner = self._loaded(tmp_path)
        assert all(not i.status_error for i in app.instances)

        def running(name):
            if name == "beta-overlay":
                raise PodmanError("timed out on beta")
            return name == "gamma-overlay"

        runner.container_running = MagicMock(side_effect=running)
        self._sync_call(app.poll_status)
        by_name = {i.name: i for i in app.instances}
        assert by_name["alpha"].status_error == ""
        assert by_name["alpha"].overlay_running is False
        assert "timed out on beta" in by_name["beta"].status_error
        assert by_name["beta"].overlay_running is False  # last known value kept
        assert by_name["gamma"].status_error == ""
        assert by_name["gamma"].overlay_running is True

    def test_poll_failure_keeps_last_known_state(self, tmp_path):
        from unittest.mock import MagicMock

        from scratch_monkey.container import PodmanError

        app, runner = self._loaded(tmp_path, names=("alpha",))
        app.instances[0].overlay_running = True
        runner.container_running = MagicMock(side_effect=PodmanError("stuck"))
        self._sync_call(app.poll_status)
        assert app.instances[0].overlay_running is True
        assert app.instances[0].status_error == "stuck"

    def test_later_successful_poll_clears_status_error(self, tmp_path):
        from unittest.mock import MagicMock

        app, runner = self._loaded(tmp_path, stuck=("beta",))
        by_name = {i.name: i for i in app.instances}
        assert "timed out on beta-overlay" in by_name["beta"].status_error

        runner.container_running = MagicMock(return_value=True)
        self._sync_call(app.poll_status)
        assert all(i.status_error == "" for i in app.instances)
        assert all(i.overlay_running for i in app.instances)

    def test_banner_cleared_when_all_failing_instances_recover(self, tmp_path):
        from unittest.mock import MagicMock

        app, runner = self._loaded(tmp_path, stuck=("alpha", "beta"))
        assert app.error_message.startswith("Could not query podman for: ")

        runner.container_running = MagicMock(return_value=False)
        self._sync_call(app.poll_status)
        assert app.error_message == ""

    def test_banner_kept_while_any_instance_still_failing(self, tmp_path):
        from unittest.mock import MagicMock

        from scratch_monkey.container import PodmanError

        app, runner = self._loaded(tmp_path, stuck=("alpha", "beta"))
        banner = app.error_message

        def running(name):
            if name == "beta-overlay":
                raise PodmanError("still stuck")
            return False

        runner.container_running = MagicMock(side_effect=running)
        self._sync_call(app.poll_status)
        assert app.instances[0].status_error == ""
        assert "still stuck" in app.instances[1].status_error
        assert app.error_message == banner

    def test_unrelated_banner_not_cleared_by_poll(self, tmp_path):
        from unittest.mock import MagicMock

        app, runner = self._loaded(tmp_path, stuck=("alpha",))
        app.error_message = "Something else went wrong"

        runner.container_running = MagicMock(return_value=False)
        self._sync_call(app.poll_status)
        assert app.instances[0].status_error == ""
        assert app.error_message == "Something else went wrong"

    def test_poll_does_not_set_banner(self, tmp_path):
        from unittest.mock import MagicMock

        from scratch_monkey.container import PodmanError

        app, runner = self._loaded(tmp_path)
        runner.container_running = MagicMock(side_effect=PodmanError("stuck"))
        self._sync_call(app.poll_status)
        assert app.error_message == ""
        assert all(i.status_error == "stuck" for i in app.instances)


# ── dialog-facing actions: on_done callbacks, worker-thread execution ───────


# Per action: instances to create first, initial selection, how to invoke it, which
# function does the real work (patched to spy on the thread), a call that fails, and
# the expected end state.


class TestRunAsyncReturnValue:
    def test_returns_true_when_started_false_when_busy(self, tmp_path):
        import threading

        app, _, _ = make_app(tmp_path)
        done = threading.Event()

        with sync_deferred_call(done):
            assert app.run_async("go", lambda: None) is True
            wait(done)
        app.busy = True
        assert app.run_async("again", lambda: None) is False

    def test_blocking_refresh_is_gone(self):
        assert not hasattr(AppModel, "refresh")


class TestDialogHelpers:
    class FakeDialog:
        def __init__(self, destroyed=False, app_model=None):
            self.app_model = app_model
            self.working = False
            self._error_msg = ""
            self.is_destroyed = destroyed
            self.closed = 0

        def close(self):
            self.closed += 1

    def test_success_closes_and_clears_working(self):
        from scratch_monkey.gui.dialog_helpers import make_on_done, start_working

        dlg = self.FakeDialog()
        start_working(dlg)
        assert dlg.working is True
        make_on_done(dlg)("")
        assert dlg.working is False
        assert dlg.closed == 1

    def test_error_shown_and_dialog_stays_open(self):
        from scratch_monkey.gui.dialog_helpers import make_on_done, start_working

        dlg = self.FakeDialog()
        dlg._error_msg = "old"
        start_working(dlg)
        assert dlg._error_msg == ""
        make_on_done(dlg)("boom")
        assert dlg.working is False
        assert dlg._error_msg == "boom"
        assert dlg.closed == 0

    def test_late_callback_after_destroy_is_ignored(self):
        from scratch_monkey.gui.dialog_helpers import make_on_done

        dlg = self.FakeDialog(destroyed=True)
        make_on_done(dlg)("")
        make_on_done(dlg)("err")
        assert dlg.closed == 0
        assert dlg._error_msg == ""

    def test_late_error_after_destroy_reaches_banner(self):
        """Esc can destroy the dialog mid-operation; the failure must not be lost."""
        from scratch_monkey.gui.dialog_helpers import make_on_done

        app_model = AppModel(instances_dir=Path("/nonexistent"), runner=object())
        dlg = self.FakeDialog(destroyed=True, app_model=app_model)
        make_on_done(dlg)("boom")
        assert app_model.error_message == "boom"
        assert dlg.closed == 0

    def test_late_success_after_destroy_leaves_banner_alone(self):
        from scratch_monkey.gui.dialog_helpers import make_on_done

        app_model = AppModel(instances_dir=Path("/nonexistent"), runner=object())
        app_model.error_message = "earlier"
        dlg = self.FakeDialog(destroyed=True, app_model=app_model)
        make_on_done(dlg)("")
        assert app_model.error_message == "earlier"

    def test_app_model_captured_when_closure_built(self):
        """The model is read at build time, not from a possibly-dead dialog later."""
        from scratch_monkey.gui.dialog_helpers import make_on_done

        app_model = AppModel(instances_dir=Path("/nonexistent"), runner=object())
        dlg = self.FakeDialog(app_model=app_model)
        on_done = make_on_done(dlg)
        dlg.is_destroyed = True
        del dlg.app_model
        on_done("late failure")
        assert app_model.error_message == "late failure"

    def test_error_while_alive_does_not_touch_banner(self):
        from scratch_monkey.gui.dialog_helpers import make_on_done

        app_model = AppModel(instances_dir=Path("/nonexistent"), runner=object())
        dlg = self.FakeDialog(app_model=app_model)
        make_on_done(dlg)("boom")
        assert dlg._error_msg == "boom"
        assert app_model.error_message == ""
