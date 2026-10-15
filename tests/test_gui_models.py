"""Tests for scratch_monkey.gui.models volume support."""

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
from scratch_monkey.run_args import DEFAULT_BASE_IMAGE  # noqa: E402

# ── helpers: drive background AppModel actions to completion ────────────────

def _wait(event, what="background action"):
    assert event.wait(timeout=5), f"{what} did not complete"


def call_sync(method, *args, **kwargs) -> str:
    """Call an on_done-style AppModel method, wait for it, return its err string.

    deferred_call is replaced with direct execution so the completion handlers
    run on the worker thread; the work itself still runs inside _run_async.
    """
    import threading
    from unittest.mock import patch

    done = threading.Event()
    out: list[str] = []

    def on_done(err):
        out.append(err)
        done.set()

    with patch("scratch_monkey.gui.models.deferred_call", side_effect=lambda fn: fn()):
        method(*args, on_done, **kwargs)
        _wait(done)
    return out[0]


def create_sync(app, name, **kwargs) -> str:
    return call_sync(app.create_instance, name, **kwargs)


def refresh_sync(app, **kwargs) -> None:
    """Run refresh_async to completion (replaces the removed blocking refresh())."""
    import threading
    from unittest.mock import patch

    done = threading.Event()

    def sync_deferred_call(fn):
        fn()
        done.set()

    with patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call):
        app.refresh_async(**kwargs)
        _wait(done)


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
                create_dialog,  # noqa: F401
                instance_detail,  # noqa: F401
                instance_list,  # noqa: F401
                main_window,  # noqa: F401
            )


class TestAppModelCreateInstance:
    """Tests for AppModel.create_instance()."""

    def _make_app(self, tmp_path):
        from unittest.mock import MagicMock, patch

        instances_dir = tmp_path / "scratch-monkey"
        instances_dir.mkdir()

        runner = MagicMock()
        runner.container_exists.return_value = False
        runner.container_running.return_value = False
        runner.image_exists.return_value = False

        # Patch PROJECT_DIR to use a temp dir with scratch.toml.default
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            app = AppModel(instances_dir=instances_dir, runner=runner)

        return app, instances_dir, project_dir

    def test_create_success(self, tmp_path):
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)
        assert len(app.instances) == 0

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            result = create_sync(app, "myinst")

        assert result == ""
        assert (instances_dir / "myinst").is_dir()
        assert (instances_dir / "myinst" / "scratch.toml").exists()
        assert app.selected_instance == "myinst"
        assert any(i.name == "myinst" for i in app.instances)

    def test_create_invalid_name(self, tmp_path):
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            result = create_sync(app, "bad name!")

        assert result != ""
        assert len(app.instances) == 0

    def test_create_duplicate(self, tmp_path):
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "dup")
            result = create_sync(app, "dup")

        assert result != ""

    def test_create_fedora(self, tmp_path):
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            result = create_sync(app, "fed", fedora=True)

        assert result == ""
        dockerfile = (instances_dir / "fed" / "Dockerfile").read_text()
        assert "fedora" in dockerfile.lower()

    def test_create_selects_new_instance(self, tmp_path):
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "first")
            create_sync(app, "second")

        assert app.selected_instance == "second"

    def test_create_with_config_override(self, tmp_path):
        from unittest.mock import patch

        from scratch_monkey.config import InstanceConfig, load

        app, instances_dir, project_dir = self._make_app(tmp_path)

        custom_cfg = InstanceConfig(cmd="/bin/zsh", wayland=True, env=["EDITOR=vim"])

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            result = create_sync(app, "configured", config=custom_cfg)

        assert result == ""
        saved = load(instances_dir / "configured" / "scratch.toml")
        assert saved.cmd == "/bin/zsh"
        assert saved.wayland is True
        assert "EDITOR=vim" in saved.env

    def test_create_without_config_uses_default(self, tmp_path):
        from unittest.mock import patch

        from scratch_monkey.config import load

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            result = create_sync(app, "default-cfg")

        assert result == ""
        saved = load(instances_dir / "default-cfg" / "scratch.toml")
        assert saved.cmd == "/bin/bash"  # default

    def test_new_instance_model(self, tmp_path):
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        # Add a shared volume
        shared_dir = instances_dir / ".shared"
        shared_dir.mkdir()
        (shared_dir / "comms").mkdir()

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            refresh_sync(app)
            m = app.new_instance_model()

        assert isinstance(m, InstanceModel)
        assert len(m.shared_entries) == 1
        assert m.shared_entries[0].name == "comms"


class TestAppModelCreateSharedVolume:
    """Tests for AppModel.create_shared_volume()."""

    def _make_app(self, tmp_path):
        from unittest.mock import MagicMock, patch

        instances_dir = tmp_path / "scratch-monkey"
        instances_dir.mkdir()

        runner = MagicMock()
        runner.container_exists.return_value = False
        runner.container_running.return_value = False
        runner.image_exists.return_value = False

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            app = AppModel(instances_dir=instances_dir, runner=runner)

        return app, instances_dir, project_dir

    def test_create_shared_volume_success(self, tmp_path):
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            result = call_sync(app.create_shared_volume, "mydata")

        assert result == ""
        assert (instances_dir / ".shared" / "mydata").is_dir()
        assert "mydata" in app.available_shared

    def test_create_shared_volume_duplicate(self, tmp_path):
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            call_sync(app.create_shared_volume, "dup")
            result = call_sync(app.create_shared_volume, "dup")

        assert result != ""

    def test_create_shared_volume_invalid_name(self, tmp_path):
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            result = call_sync(app.create_shared_volume, "bad name!")

        assert result != ""


class TestAppModelRenameInstance:
    """Tests for AppModel.rename_instance()."""

    def _make_app(self, tmp_path):
        from unittest.mock import MagicMock, patch

        instances_dir = tmp_path / "scratch-monkey"
        instances_dir.mkdir()

        runner = MagicMock()
        runner.container_exists.return_value = False
        runner.container_running.return_value = False
        runner.image_exists.return_value = False

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            app = AppModel(instances_dir=instances_dir, runner=runner)

        return app, instances_dir, project_dir

    def test_rename_success(self, tmp_path):
        """Rename returns '' and updates state on success."""
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        # Create an instance first
        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "old")
        app.selected_instance = "old"

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            err = call_sync(app.rename_instance, "old", "new")

        assert err == ""
        assert app.selected_instance == "new"
        assert (instances_dir / "new").is_dir()
        assert not (instances_dir / "old").is_dir()

    def test_rename_updates_selected(self, tmp_path):
        """When renaming the selected instance, selected_instance updates."""
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")
        app.selected_instance = "myinst"

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            err = call_sync(app.rename_instance, "myinst", "renamed")

        assert err == ""
        assert app.selected_instance == "renamed"

    def test_rename_failure_returns_error(self, tmp_path):
        """On failure, returns the error string without changing state."""
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")
        app.selected_instance = "myinst"

        # Renaming nonexistent instance should raise an error
        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            err = call_sync(app.rename_instance, "nonexistent", "whatever")

        assert err != ""
        assert app.selected_instance == "myinst"  # unchanged


class TestAppModelCloneInstance:
    """Tests for AppModel.clone_instance()."""

    def _make_app(self, tmp_path):
        from unittest.mock import MagicMock, patch

        instances_dir = tmp_path / "scratch-monkey"
        instances_dir.mkdir()

        runner = MagicMock()
        runner.container_exists.return_value = False
        runner.container_running.return_value = False
        runner.image_exists.return_value = False

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            app = AppModel(instances_dir=instances_dir, runner=runner)

        return app, instances_dir, project_dir

    def test_clone_success(self, tmp_path):
        """Clone returns '' and selects the clone on success."""
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "source")
            err = call_sync(app.clone_instance, "source", "dest")

        assert err == ""
        assert app.selected_instance == "dest"
        assert (instances_dir / "dest").is_dir()

    def test_clone_selects_new_instance(self, tmp_path):
        """After cloning, selected_instance is set to the dest name."""
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "orig")
            err = call_sync(app.clone_instance, "orig", "clone")

        assert err == ""
        assert app.selected_instance == "clone"
        assert any(i.name == "clone" for i in app.instances)

    def test_clone_failure_returns_error(self, tmp_path):
        """On failure, returns the error string without changing selected."""
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "source")
            create_sync(app, "existing")
        app.selected_instance = "source"

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            err = call_sync(app.clone_instance, "source", "existing")

        assert err != ""
        assert app.selected_instance == "source"  # unchanged

    def test_clone_passes_runner_and_tags_image(self, tmp_path):
        """Clone passes runner to clone(); runner.tag() called when image exists."""
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "source")

        # Mark source image as existing so tag() gets called
        app._runner.image_exists.return_value = True

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            err = call_sync(app.clone_instance, "source", "dest")

        assert err == ""
        app._runner.tag.assert_called_once_with("source", "dest")


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

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            app = AppModel(instances_dir=instances_dir, runner=runner)

        # No instances, no unsaved changes
        assert app.has_unsaved_changes() is False

        # Create an instance
        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        # Initially not dirty
        assert app.has_unsaved_changes() is False

        # Modify the instance
        inst_model = next(i for i in app.instances if i.name == "myinst")
        inst_model.cmd = "/bin/zsh"

        assert app.has_unsaved_changes() is True


class TestAppModelExportCommand:
    """Tests for AppModel.export_command()."""

    def _make_app(self, tmp_path):
        from unittest.mock import MagicMock, patch

        instances_dir = tmp_path / "scratch-monkey"
        instances_dir.mkdir()

        runner = MagicMock()
        runner.container_exists.return_value = False
        runner.container_running.return_value = False
        runner.image_exists.return_value = False

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            app = AppModel(instances_dir=instances_dir, runner=runner)

        return app, instances_dir, project_dir

    def test_export_command_success(self, tmp_path):
        """export_command returns '' and sets status on success."""
        from pathlib import Path as P
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        mock_path = P("/home/user/.local/bin/rg")
        with patch("scratch_monkey.gui.models.export_command_fn", return_value=mock_path) as mock_export:
            err = app.export_command("myinst", "/usr/bin/rg")

        assert err == ""
        assert "rg" in app.status_message
        assert "myinst" in app.status_message
        mock_export.assert_called_once()
        # Verify the Instance and cmd were passed correctly
        call_args = mock_export.call_args
        assert call_args[0][1] == "/usr/bin/rg"
        assert call_args[1]["bin_name"] == ""

    def test_export_command_not_found(self, tmp_path):
        """export_command returns error for nonexistent instance."""
        app, instances_dir, project_dir = self._make_app(tmp_path)

        err = app.export_command("nonexistent", "/usr/bin/rg")
        assert err != ""
        assert "nonexistent" in err

    def test_export_command_with_bin_name(self, tmp_path):
        """export_command passes bin_name to export_command_fn."""
        from pathlib import Path as P
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        mock_path = P("/home/user/.local/bin/ripgrep")
        with patch("scratch_monkey.gui.models.export_command_fn", return_value=mock_path) as mock_export:
            err = app.export_command("myinst", "/usr/bin/rg", "ripgrep")

        assert err == ""
        call_args = mock_export.call_args
        assert call_args[1]["bin_name"] == "ripgrep"

    def test_export_command_exception(self, tmp_path):
        """export_command returns error string when export_command_fn raises."""
        from unittest.mock import patch

        from scratch_monkey.export import ExportError

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        with patch("scratch_monkey.gui.models.export_command_fn", side_effect=ExportError("disk full")):
            err = app.export_command("myinst", "/usr/bin/rg")

        assert err == "disk full"


class TestAppModelEditFile:
    """Tests for AppModel.edit_file()."""

    def _make_app(self, tmp_path):
        from unittest.mock import MagicMock, patch

        instances_dir = tmp_path / "scratch-monkey"
        instances_dir.mkdir()

        runner = MagicMock()
        runner.container_exists.return_value = False
        runner.container_running.return_value = False
        runner.image_exists.return_value = False

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            app = AppModel(instances_dir=instances_dir, runner=runner)

        return app, instances_dir, project_dir

    def test_edit_dockerfile_success(self, tmp_path):
        """edit_file opens Dockerfile in editor and sets status."""
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        with patch("scratch_monkey.gui.models._open_in_editor", return_value="") as mock_edit:
            app.edit_file("myinst", "dockerfile")

        mock_edit.assert_called_once()
        called_path = mock_edit.call_args[0][0]
        assert called_path == instances_dir / "myinst" / "Dockerfile"
        assert "Editing dockerfile" in app.status_message

    def test_edit_env_success(self, tmp_path):
        """edit_file opens .env in editor and sets status."""
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        with patch("scratch_monkey.gui.models._open_in_editor", return_value="") as mock_edit:
            app.edit_file("myinst", "env")

        mock_edit.assert_called_once()
        called_path = mock_edit.call_args[0][0]
        assert called_path == instances_dir / "myinst" / ".env"
        assert "Editing env" in app.status_message

    def test_edit_file_not_found(self, tmp_path):
        """edit_file sets error status for nonexistent instance."""
        app, instances_dir, project_dir = self._make_app(tmp_path)

        app.edit_file("nonexistent", "dockerfile")
        assert "not found" in app.status_message

    def test_edit_file_editor_error(self, tmp_path):
        """edit_file propagates editor error to status_message."""
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        with patch("scratch_monkey.gui.models._open_in_editor", return_value="No terminal emulator found."):
            app.edit_file("myinst", "dockerfile")

        assert "No terminal emulator found." in app.status_message


class TestRunAsync:
    """Tests for AppModel._run_async background task helper."""

    def _make_app(self, tmp_path):
        from unittest.mock import MagicMock, patch

        instances_dir = tmp_path / "scratch-monkey"
        instances_dir.mkdir()

        runner = MagicMock()
        runner.container_exists.return_value = False
        runner.container_running.return_value = False
        runner.image_exists.return_value = False

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            app = AppModel(instances_dir=instances_dir, runner=runner)

        return app, instances_dir, project_dir

    def test_run_async_sets_busy_and_calls_on_success(self, tmp_path):
        """_run_async sets busy=True, runs work, then calls on_success with result."""
        import threading
        from unittest.mock import patch

        app, _, project_dir = self._make_app(tmp_path)

        # Replace deferred_call with synchronous execution for testing
        with patch("scratch_monkey.gui.models.deferred_call", side_effect=lambda fn: fn()):
            results = []
            done = threading.Event()

            def work():
                return 42

            def on_success(result):
                results.append(result)
                done.set()

            app._run_async("Working...", work, on_success)
            _wait(done)

        assert results == [42]
        assert app.busy is False

    def test_run_async_calls_on_error(self, tmp_path):
        """_run_async calls on_error when work() raises."""
        import threading
        from unittest.mock import patch

        app, _, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.deferred_call", side_effect=lambda fn: fn()):
            errors = []
            done = threading.Event()

            def work():
                raise RuntimeError("boom")

            def on_error(exc):
                errors.append(str(exc))
                done.set()

            app._run_async("Working...", work, on_error=on_error)
            _wait(done)

        assert errors == ["boom"]
        assert app.busy is False

    def test_run_async_default_error_sets_status(self, tmp_path):
        """Without on_error, _run_async sets status_message to error string."""
        import threading
        from unittest.mock import patch

        app, _, project_dir = self._make_app(tmp_path)
        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call):
            def work():
                raise RuntimeError("something broke")

            app._run_async("Working...", work)
            _wait(done)

        assert "something broke" in app.status_message
        assert app.busy is False

    def test_run_async_reentrant_guard(self, tmp_path):
        """_run_async returns immediately if already busy."""
        app, _, project_dir = self._make_app(tmp_path)
        app.busy = True

        called = []
        app._run_async("Working...", lambda: called.append(1))
        assert called == []

    def test_run_async_busy_sets_status_and_skips_work(self, tmp_path):
        """A second _run_async call while busy sets a busy status message
        (naming the in-flight operation) and does NOT invoke the new work()."""
        import threading
        from unittest.mock import patch

        app, _, project_dir = self._make_app(tmp_path)
        gate = threading.Event()
        release = threading.Event()
        done = threading.Event()

        def first_work():
            gate.set()
            release.wait(timeout=2)
            return None

        def sync_deferred_call(fn):
            fn()
            done.set()

        with patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call):
            app._run_async("Refreshing 'foo'...", first_work, on_success=lambda r: None)
            _wait(gate, "first_work start")
            assert app.busy is True

            second_called = []
            app._run_async("Second op...", lambda: second_called.append(1))

            assert second_called == []
            assert "Busy" in app.status_message
            assert "Refreshing 'foo'..." in app.status_message

            release.set()
            _wait(done)

        assert app.busy is False

    def test_run_async_clears_error_message_on_new_operation(self, tmp_path):
        """error_message from a prior failed operation is cleared when a new
        operation starts."""
        import threading
        from unittest.mock import patch

        app, _, project_dir = self._make_app(tmp_path)
        done = threading.Event()

        def failing_work():
            raise RuntimeError("first failure")

        def sync_deferred_call(fn):
            fn()
            done.set()

        with patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call):
            app._run_async("First op...", failing_work)
            _wait(done)

        assert "first failure" in app.error_message

        done2 = threading.Event()

        def sync_deferred_call2(fn):
            fn()
            done2.set()

        with patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call2):
            app._run_async("Second op...", lambda: None, on_success=lambda r: None)
            _wait(done2)

        assert app.error_message == ""

    def test_run_async_sets_error_message_on_failure(self, tmp_path):
        """_run_async sets error_message (in addition to status_message) when
        work() raises, even when a tailored on_error callback is provided."""
        import threading
        from unittest.mock import patch

        app, _, project_dir = self._make_app(tmp_path)
        done = threading.Event()

        def failing_work():
            raise RuntimeError("disk on fire")

        def sync_deferred_call(fn):
            fn()
            done.set()

        def on_error(exc):
            app.status_message = f"Custom: {exc}"

        with patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call):
            app._run_async("Working...", failing_work, on_error=on_error)
            _wait(done)

        assert "disk on fire" in app.error_message
        assert app.status_message == "Custom: disk on fire"

    def test_delete_instance_async(self, tmp_path):
        """delete_instance uses _run_async (sets busy, calls delete, refreshes)."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "todelete")

        assert any(i.name == "todelete" for i in app.instances)
        app.selected_instance = "todelete"

        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call):
            app.delete_instance("todelete")
            _wait(done)

        assert app.busy is False
        assert app.selected_instance == ""
        assert "Deleted" in app.status_message

    def test_reset_overlay_async(self, tmp_path):
        """reset_overlay updates overlay_running in-place (no full refresh)."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        # Pre-set overlay_running to True so we can verify it gets cleared
        inst = next(i for i in app.instances if i.name == "myinst")
        inst.overlay_running = True
        instances_before = app.instances

        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with (
            patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call),
            patch("scratch_monkey.gui.models.overlay_reset", return_value=True),
        ):
            app.reset_overlay("myinst")
            _wait(done)

        assert app.busy is False
        assert "removed" in app.status_message
        # Verify in-place update: same list object, overlay_running cleared
        assert app.instances is instances_before
        assert inst.overlay_running is False

    def test_stop_instance(self, tmp_path):
        """stop_instance stops the overlay container and clears overlay_running."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        inst = next(i for i in app.instances if i.name == "myinst")
        inst.overlay_running = True
        instances_before = app.instances

        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call):
            app.stop_instance("myinst")
            _wait(done)

        assert app.busy is False
        assert "Stopped" in app.status_message
        assert app.instances is instances_before
        assert inst.overlay_running is False

    def test_stop_instance_not_found(self, tmp_path):
        """stop_instance sets error status for nonexistent instance."""
        app, _, _ = self._make_app(tmp_path)

        app.stop_instance("nonexistent")
        assert app.busy is False
        assert "not found" in app.status_message

    def test_start_instance(self, tmp_path):
        """start_instance calls ensure_running and sets overlay_running."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        inst = next(i for i in app.instances if i.name == "myinst")
        assert inst.overlay_running is False
        instances_before = app.instances

        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with (
            patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call),
            patch("scratch_monkey.gui.models.overlay_ensure_running") as mock_ensure,
        ):
            app.start_instance("myinst")
            _wait(done)

        mock_ensure.assert_called_once()
        assert app.busy is False
        assert "Started" in app.status_message
        assert app.instances is instances_before
        assert inst.overlay_running is True

    @pytest.mark.parametrize(
        "method,target,expected",
        [
            ("start_instance", "overlay_ensure_running", "Error starting 'myinst': podman broke"),
            ("stop_instance", None, "Error stopping 'myinst': podman broke"),
        ],
    )
    def test_start_stop_error_sets_status_before_polling(
        self, tmp_path, method, target, expected
    ):
        """on_error sets status_message first, then polls (poll thread must not race it)."""
        import contextlib
        import threading
        from unittest.mock import patch

        from scratch_monkey.container import PodmanError

        app, instances_dir, project_dir = self._make_app(tmp_path)
        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        done = threading.Event()
        seen_at_poll: list[str] = []

        def sync_deferred_call(fn):
            fn()
            done.set()

        with (
            patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call),
            patch.object(
                AppModel, "poll_status", lambda self: seen_at_poll.append(self.status_message)
            ),
        ):
            if target:
                ctx = patch(
                    f"scratch_monkey.gui.models.{target}", side_effect=PodmanError("podman broke")
                )
            else:
                app._runner.stop.side_effect = PodmanError("podman broke")
                ctx = contextlib.nullcontext()
            with ctx:
                getattr(app, method)("myinst")
                _wait(done)

        assert app.busy is False
        assert app.status_message == expected
        assert seen_at_poll == [expected]

    def test_start_instance_not_found(self, tmp_path):
        """start_instance sets error status for nonexistent instance."""
        app, _, _ = self._make_app(tmp_path)

        app.start_instance("nonexistent")
        assert app.busy is False
        assert "not found" in app.status_message

    def test_refresh_async(self, tmp_path):
        """refresh_async uses _run_async to reload instances."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call):
            app.refresh_async()
            _wait(done)

        assert app.busy is False
        assert "Loaded" in app.status_message

    def test_run_async_sets_status_message_immediately(self, tmp_path):
        """_run_async sets status_message to the status param before the thread runs."""
        import threading
        from unittest.mock import patch

        app, _, project_dir = self._make_app(tmp_path)
        observed_status = []
        done = threading.Event()

        def work():
            observed_status.append(app.status_message)
            return None

        with patch("scratch_monkey.gui.models.deferred_call", side_effect=lambda fn: fn()):
            app._run_async("Processing...", work, on_success=lambda r: done.set())
            _wait(done)

        assert observed_status == ["Processing..."]

    def test_run_async_busy_true_during_work(self, tmp_path):
        """_run_async keeps busy=True while work() is running."""
        import threading
        from unittest.mock import patch

        app, _, project_dir = self._make_app(tmp_path)
        observed_busy = []
        done = threading.Event()

        def work():
            observed_busy.append(app.busy)
            return None

        with patch("scratch_monkey.gui.models.deferred_call", side_effect=lambda fn: fn()):
            app._run_async("Working...", work, on_success=lambda r: done.set())
            _wait(done)

        assert observed_busy == [True]
        assert app.busy is False

    def test_reset_overlay_not_found(self, tmp_path):
        """reset_overlay returns early without setting busy for nonexistent instance."""
        app, _, project_dir = self._make_app(tmp_path)

        app.reset_overlay("nonexistent")
        assert app.busy is False
        assert "not found" in app.status_message

    def test_reset_overlay_no_container(self, tmp_path):
        """reset_overlay reports when no overlay container exists."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with (
            patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call),
            patch("scratch_monkey.gui.models.overlay_reset", return_value=False),
        ):
            app.reset_overlay("myinst")
            _wait(done)

        assert app.busy is False
        assert "No overlay container found" in app.status_message

    def test_delete_instance_error_preserves_message(self, tmp_path):
        """delete_instance on_error shows the error message (not overwritten by refresh)."""
        import threading
        from unittest.mock import patch

        from scratch_monkey.instance import InstanceError

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with (
            patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call),
            patch("scratch_monkey.gui.models.delete", side_effect=InstanceError("permission denied")),
        ):
            app.delete_instance("myinst")
            _wait(done)

        assert app.busy is False
        assert "permission denied" in app.status_message

    def test_reset_overlay_error_preserves_message(self, tmp_path):
        """reset_overlay on_error calls poll_status (not refresh) and preserves error message."""
        import threading
        from unittest.mock import patch

        from scratch_monkey.container import PodmanError

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        instances_before = app.instances

        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with (
            patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call),
            patch("scratch_monkey.gui.models.overlay_reset", side_effect=PodmanError("podman broke")),
            # Don't start a real poll thread (it would outlive the test).
            patch.object(AppModel, "poll_status") as poll,
        ):
            app.reset_overlay("myinst")
            _wait(done)

        assert app.busy is False
        assert "podman broke" in app.status_message
        poll.assert_called_once()
        # Verify instances list was not replaced (poll_status, not refresh)
        assert app.instances is instances_before

    def test_refresh_overlay_success_uses_instance_image(self, tmp_path):
        """refresh_overlay calls overlay.refresh with the instance tag when it exists."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        inst = next(i for i in app.instances if i.name == "myinst")
        inst.overlay_running = False
        instances_before = app.instances

        app._runner.image_exists.return_value = True

        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with (
            patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call),
            patch("scratch_monkey.gui.models.overlay_refresh") as mock_refresh,
        ):
            app.refresh_overlay("myinst")
            _wait(done)

        mock_refresh.assert_called_once()
        call_args = mock_refresh.call_args
        # overlay.refresh(inst, runner, image) — image should be the instance tag
        assert call_args[0][2] == "myinst"
        # Preserve mode only: no discard kwarg passed as True
        assert call_args.kwargs.get("discard", False) is False

        assert app.busy is False
        assert "Refreshed" in app.status_message
        assert "myinst" in app.status_message
        assert app.instances is instances_before
        assert inst.overlay_running is True

    def test_refresh_overlay_uses_base_image_when_not_built(self, tmp_path):
        """refresh_overlay falls back to the detected/default base image when no instance tag exists."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        app._runner.image_exists.return_value = False

        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with (
            patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call),
            patch("scratch_monkey.gui.models.overlay_refresh") as mock_refresh,
            patch("scratch_monkey.gui.models.detect_base_image", return_value=None),
        ):
            app.refresh_overlay("myinst")
            _wait(done)

        mock_refresh.assert_called_once()
        call_args = mock_refresh.call_args
        assert call_args[0][2] == DEFAULT_BASE_IMAGE

    def test_refresh_overlay_not_found(self, tmp_path):
        """refresh_overlay sets an error status for a nonexistent instance without going busy."""
        app, _, _ = self._make_app(tmp_path)

        app.refresh_overlay("nonexistent")
        assert app.busy is False
        assert "not found" in app.status_message

    def test_refresh_overlay_error_preserves_message(self, tmp_path):
        """refresh_overlay on_error calls poll_status (not refresh) and preserves the error."""
        import threading
        from unittest.mock import patch

        from scratch_monkey.container import PodmanError

        app, instances_dir, project_dir = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        instances_before = app.instances

        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with (
            patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call),
            patch(
                "scratch_monkey.gui.models.overlay_refresh",
                side_effect=PodmanError("podman broke"),
            ),
            patch.object(AppModel, "poll_status") as poll,
        ):
            app.refresh_overlay("myinst")
            _wait(done)

        assert app.busy is False
        assert "podman broke" in app.status_message
        poll.assert_called_once()
        # Verify instances list was not replaced (poll_status, not refresh)
        assert app.instances is instances_before


class TestDirectoryHelpers:
    """Tests for _open_file_manager, _open_terminal_at, and AppModel methods."""

    def _make_app(self, tmp_path):
        from unittest.mock import MagicMock, patch

        instances_dir = tmp_path / "scratch-monkey"
        instances_dir.mkdir()

        runner = MagicMock()
        runner.container_exists.return_value = False
        runner.container_running.return_value = False
        runner.image_exists.return_value = False

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            app = AppModel(instances_dir=instances_dir, runner=runner)

        return app

    def test_open_directory(self, tmp_path):
        """open_directory calls xdg-open with the path."""
        from unittest.mock import patch

        app = self._make_app(tmp_path)

        with (
            patch("scratch_monkey.gui.models.shutil.which", return_value="/usr/bin/xdg-open"),
            patch("scratch_monkey.gui.models.subprocess.Popen") as mock_popen,
        ):
            app.open_directory("/some/path")

        mock_popen.assert_called_once_with(["xdg-open", "/some/path"])
        assert "Opened file manager" in app.status_message

    def test_open_directory_no_xdg_open(self, tmp_path):
        """open_directory sets error status when xdg-open is not found."""
        from unittest.mock import patch

        app = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.shutil.which", return_value=None):
            app.open_directory("/some/path")

        assert "xdg-open not found" in app.status_message

    def test_open_terminal(self, tmp_path):
        """open_terminal launches terminal with correct cwd."""
        from unittest.mock import patch

        app = self._make_app(tmp_path)

        with (
            patch("scratch_monkey.gui.models._find_terminal", return_value=["xdg-terminal-exec"]),
            patch("scratch_monkey.gui.models.subprocess.Popen") as mock_popen,
        ):
            app.open_terminal("/some/path")

        mock_popen.assert_called_once_with(["xdg-terminal-exec"], cwd="/some/path")
        assert "Opened terminal" in app.status_message

    def test_open_terminal_no_terminal(self, tmp_path):
        """open_terminal sets error status when no terminal is found."""
        from unittest.mock import patch

        app = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models._find_terminal", return_value=[]):
            app.open_terminal("/some/path")

        assert "No terminal emulator found" in app.status_message


class TestPollStatus:
    """Tests for AppModel.poll_status lightweight status poller."""

    def _make_app(self, tmp_path):
        from unittest.mock import MagicMock, patch

        instances_dir = tmp_path / "scratch-monkey"
        instances_dir.mkdir()

        runner = MagicMock()
        runner.container_exists.return_value = False
        runner.container_running.return_value = False
        runner.image_exists.return_value = False

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            app = AppModel(instances_dir=instances_dir, runner=runner)

        return app, instances_dir, project_dir, runner

    def test_poll_status_updates_overlay_running(self, tmp_path):
        """poll_status updates overlay_running in-place on existing instances."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir, runner = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        inst = next(i for i in app.instances if i.name == "myinst")
        assert inst.overlay_running is False
        instances_before = app.instances

        runner.container_running.return_value = True

        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call):
            app.poll_status()
            _wait(done)

        assert inst.overlay_running is True
        assert app.instances is instances_before
        assert app._polling is False

    def test_poll_status_updates_image_built(self, tmp_path):
        """poll_status updates image_built in-place."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir, runner = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        inst = next(i for i in app.instances if i.name == "myinst")
        assert inst.image_built is False

        runner.image_exists.return_value = True

        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call):
            app.poll_status()
            _wait(done)

        assert inst.image_built is True
        assert app._polling is False

    def test_poll_status_skips_when_busy(self, tmp_path):
        """poll_status returns immediately if busy is True."""
        app, _, _, runner = self._make_app(tmp_path)
        app.busy = True

        app.poll_status()
        assert app._polling is False
        runner.container_running.assert_not_called()

    def test_poll_status_skips_when_already_polling(self, tmp_path):
        """poll_status returns immediately if _polling is True."""
        app, _, _, runner = self._make_app(tmp_path)
        app._polling = True

        app.poll_status()
        # Should not have spawned a new poll (runner not called again)
        runner.container_running.assert_not_called()

    def test_poll_status_preserves_dirty_state(self, tmp_path):
        """poll_status does not affect dirty flag on instances."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir, runner = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        inst = next(i for i in app.instances if i.name == "myinst")
        inst.cmd = "changed"
        inst.check_dirty()
        assert inst.dirty is True

        runner.container_running.return_value = True

        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call):
            app.poll_status()
            _wait(done)

        assert inst.dirty is True
        assert inst.overlay_running is True

    def test_poll_status_handles_error(self, tmp_path):
        """poll_status clears _polling on exception without crashing."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir, runner = self._make_app(tmp_path)

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        runner.container_running.side_effect = RuntimeError("podman down")

        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call):
            app.poll_status()
            _wait(done)

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
        from unittest.mock import patch

        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call):
            app_call()
            _wait(done)

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
        with patch("scratch_monkey.gui.models.threading.Thread"):
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
            "scratch_monkey.gui.models.list_all", side_effect=PodmanError("podman is broken")
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
        from unittest.mock import patch

        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call):
            app_call()
            _wait(done)

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

def _wait_for(cond, what="condition", timeout=5.0):
    import time

    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.01)


def _make_dialog_app(tmp_path):
    from unittest.mock import MagicMock, patch

    instances_dir = tmp_path / "scratch-monkey"
    instances_dir.mkdir()
    runner = MagicMock()
    runner.container_exists.return_value = False
    runner.container_running.return_value = False
    runner.image_exists.return_value = False
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
        app = AppModel(instances_dir=instances_dir, runner=runner)
    return app, instances_dir, project_dir


def _resolve(dotted):
    import importlib

    mod, attr = dotted.rsplit(".", 1)
    return getattr(importlib.import_module(mod), attr)


# Per action: instances to create first, initial selection, how to invoke it, which
# function does the real work (patched to spy on the thread), a call that fails, and
# the expected end state.
_DIALOG_CASES = {
    "rename": dict(
        setup=["old"],
        select="old",
        invoke=lambda app, cb: app.rename_instance("old", "new", cb),
        patch_target="scratch_monkey.gui.models.rename",
        fail=lambda app, cb: app.rename_instance("nonexistent", "whatever", cb),
        selected_after="new",
        names_after={"new"},
        status="Renamed 'old' to 'new'",
    ),
    "clone": dict(
        setup=["src"],
        select="src",
        invoke=lambda app, cb: app.clone_instance("src", "dest", cb),
        patch_target="scratch_monkey.gui.models.clone",
        fail=lambda app, cb: app.clone_instance("src", "src", cb),
        selected_after="dest",
        names_after={"src", "dest"},
        status="Cloned 'src' to 'dest'",
    ),
    "create": dict(
        setup=["existing"],
        select="existing",
        invoke=lambda app, cb: app.create_instance("fresh", cb),
        patch_target="scratch_monkey.gui.models.create",
        fail=lambda app, cb: app.create_instance("existing", cb),
        selected_after="fresh",
        names_after={"existing", "fresh"},
        status="Created instance 'fresh'",
    ),
    "shared": dict(
        setup=["existing"],
        select="existing",
        invoke=lambda app, cb: app.create_shared_volume("vol", cb),
        patch_target="scratch_monkey.shared.create_shared",
        fail=lambda app, cb: app.create_shared_volume("bad name!", cb),
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

        app, instances_dir, project_dir = _make_dialog_app(tmp_path)
        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
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

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
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

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
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

        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
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

        import scratch_monkey.gui.models as models

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
            patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir),
            patch(case["patch_target"], side_effect=spy_action),
            patch("scratch_monkey.gui.models.list_all", side_effect=spy_list_all),
            patch("scratch_monkey.gui.models.deferred_call", side_effect=lambda fn: fn()),
        ):
            case["invoke"](app, on_done)
            # Returned while the action is still blocked: the GUI thread is not held.
            assert results == []
            assert app.busy is True
            gate.set()
            _wait(done)

        assert results == [""]
        assert threads["action"] is not gui_thread
        assert threads["list_all"] is not gui_thread


class TestDeleteReload:
    def test_delete_error_reloads_list_in_worker_and_keeps_banner(self, tmp_path):
        import threading
        from unittest.mock import patch

        import scratch_monkey.gui.models as models
        from scratch_monkey.instance import InstanceError

        app, instances_dir, project_dir = _make_dialog_app(tmp_path)
        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "keep")
        app.selected_instance = "keep"
        gui_thread = threading.current_thread()
        threads = []
        real = models.list_all

        def spy(*a, **kw):
            threads.append(threading.current_thread())
            return real(*a, **kw)

        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with (
            patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call),
            patch("scratch_monkey.gui.models.delete", side_effect=InstanceError("nope")),
            patch("scratch_monkey.gui.models.list_all", side_effect=spy),
        ):
            app.delete_instance("keep")
            _wait(done)

        assert threads and all(t is not gui_thread for t in threads)
        assert app.error_message == "nope"  # banner (no dialog for delete)
        assert app.status_message == "Error: nope"
        assert app.selected_instance == "keep"  # failed delete keeps the selection
        assert app.busy is False

    def test_delete_busy_does_not_raise(self, tmp_path):
        app, _, _ = _make_dialog_app(tmp_path)
        app.busy = True
        app.delete_instance("whatever")  # no on_done; just reports busy
        assert app.status_message.startswith("Busy")


class TestRunAsyncReturnValue:
    def test_returns_true_when_started_false_when_busy(self, tmp_path):
        import threading
        from unittest.mock import patch

        app, _, _ = _make_dialog_app(tmp_path)
        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call):
            assert app._run_async("go", lambda: None) is True
            _wait(done)
        app.busy = True
        assert app._run_async("again", lambda: None) is False

    def test_blocking_refresh_is_gone(self):
        assert not hasattr(AppModel, "refresh")


class TestEditConfigReload:
    def _edit(self, app, name):
        from unittest.mock import MagicMock, patch

        with (
            patch("scratch_monkey.gui.models._find_terminal", return_value=["term"]),
            patch("scratch_monkey.gui.models.subprocess.Popen", return_value=MagicMock()),
            patch("scratch_monkey.gui.models.deferred_call", side_effect=lambda fn: fn()),
        ):
            app.edit_config(name)

    def test_reload_after_editor_runs_in_background(self, tmp_path):
        import threading
        from unittest.mock import patch

        import scratch_monkey.gui.models as models

        app, instances_dir, project_dir = _make_dialog_app(tmp_path)
        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")
        app.selected_instance = "myinst"
        gui_thread = threading.current_thread()
        threads = []
        real = models.list_all

        def spy(*a, **kw):
            threads.append(threading.current_thread())
            return real(*a, **kw)

        with (
            patch("scratch_monkey.gui.models.list_all", side_effect=spy),
            patch("scratch_monkey.gui.models.deferred_call", side_effect=lambda fn: fn()),
        ):
            self._edit(app, "myinst")
            _wait_for(lambda: app.status_message == "Reloaded config for 'myinst'", "reload")

        assert threads and all(t is not gui_thread for t in threads)
        assert app.busy is False

    def test_reload_deferred_while_busy(self, tmp_path):
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = _make_dialog_app(tmp_path)
        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")
        gate = threading.Event()

        with patch("scratch_monkey.gui.models.deferred_call", side_effect=lambda fn: fn()):
            assert app._run_async("Other op", lambda: gate.wait(timeout=5))
            self._edit(app, "myinst")
            _wait_for(lambda: app._reload_pending, "reload to be queued")
            gate.set()
            _wait_for(lambda: app.status_message == "Reloaded config for 'myinst'", "deferred reload")

        assert app._reload_pending == ""
        assert app.busy is False


class TestImageLookupOffGuiThread:
    @pytest.mark.parametrize("method", ["start_instance", "refresh_overlay"])
    def test_image_exists_runs_in_worker(self, tmp_path, method):
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = _make_dialog_app(tmp_path)
        with patch("scratch_monkey.gui.models.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")
        gui_thread = threading.current_thread()
        threads = []

        def image_exists(name):
            threads.append(threading.current_thread())
            return False

        app._runner.image_exists.side_effect = image_exists
        done = threading.Event()

        def sync_deferred_call(fn):
            fn()
            done.set()

        with (
            patch("scratch_monkey.gui.models.deferred_call", side_effect=sync_deferred_call),
            patch("scratch_monkey.gui.models.overlay_ensure_running"),
            patch("scratch_monkey.gui.models.overlay_refresh"),
        ):
            getattr(app, method)("myinst")
            _wait(done)

        assert threads and all(t is not gui_thread for t in threads)


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
