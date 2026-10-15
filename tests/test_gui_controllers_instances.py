"""Tests for scratch_monkey.gui.controllers.instances.InstanceController."""

import pytest

atom = pytest.importorskip("atom")

from scratch_monkey.config import InstanceConfig  # noqa: E402
from scratch_monkey.gui.models import AppModel  # noqa: E402

from .gui_helpers import (  # noqa: E402
    call_sync,
    create_sync,
    make_app,
    sync_deferred_call,
    wait,
)


class TestInstanceCreate:
    """Tests for InstanceController.create()."""

    def test_create_success(self, tmp_path):
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)
        assert len(app.instances) == 0

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            result = create_sync(app, "myinst")

        assert result == ""
        assert (instances_dir / "myinst").is_dir()
        assert (instances_dir / "myinst" / "scratch.toml").exists()
        assert app.selected_instance == "myinst"
        assert any(i.name == "myinst" for i in app.instances)

    def test_create_invalid_name(self, tmp_path):
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            result = create_sync(app, "bad name!")

        assert result != ""
        assert len(app.instances) == 0

    def test_create_duplicate(self, tmp_path):
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "dup")
            result = create_sync(app, "dup")

        assert result != ""

    def test_create_fedora(self, tmp_path):
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            result = create_sync(app, "fed", fedora=True)

        assert result == ""
        dockerfile = (instances_dir / "fed" / "Dockerfile").read_text()
        assert "fedora" in dockerfile.lower()

    def test_create_selects_new_instance(self, tmp_path):
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "first")
            create_sync(app, "second")

        assert app.selected_instance == "second"

    def test_create_with_config_override(self, tmp_path):
        from unittest.mock import patch

        from scratch_monkey.config import load

        app, instances_dir, project_dir = make_app(tmp_path)

        custom_cfg = InstanceConfig(cmd="/bin/zsh", wayland=True, env=["EDITOR=vim"])

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            result = create_sync(app, "configured", config=custom_cfg)

        assert result == ""
        saved = load(instances_dir / "configured" / "scratch.toml")
        assert saved.cmd == "/bin/zsh"
        assert saved.wayland is True
        assert "EDITOR=vim" in saved.env

    def test_create_without_config_uses_default(self, tmp_path):
        from unittest.mock import patch

        from scratch_monkey.config import load

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            result = create_sync(app, "default-cfg")

        assert result == ""
        saved = load(instances_dir / "default-cfg" / "scratch.toml")
        assert saved.cmd == "/bin/bash"  # default


class TestInstanceRename:
    """Tests for InstanceController.rename()."""

    def test_rename_success(self, tmp_path):
        """Rename returns '' and updates state on success."""
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        # Create an instance first
        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "old")
        app.selected_instance = "old"

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            err = call_sync(app.instance_actions.rename, "old", "new")

        assert err == ""
        assert app.selected_instance == "new"
        assert (instances_dir / "new").is_dir()
        assert not (instances_dir / "old").is_dir()

    def test_rename_updates_selected(self, tmp_path):
        """When renaming the selected instance, selected_instance updates."""
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")
        app.selected_instance = "myinst"

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            err = call_sync(app.instance_actions.rename, "myinst", "renamed")

        assert err == ""
        assert app.selected_instance == "renamed"

    def test_rename_failure_returns_error(self, tmp_path):
        """On failure, returns the error string without changing state."""
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")
        app.selected_instance = "myinst"

        # Renaming nonexistent instance should raise an error
        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            err = call_sync(app.instance_actions.rename, "nonexistent", "whatever")

        assert err != ""
        assert app.selected_instance == "myinst"  # unchanged


class TestInstanceClone:
    """Tests for InstanceController.clone()."""

    def test_clone_success(self, tmp_path):
        """Clone returns '' and selects the clone on success."""
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "source")
            err = call_sync(app.instance_actions.clone, "source", "dest")

        assert err == ""
        assert app.selected_instance == "dest"
        assert (instances_dir / "dest").is_dir()

    def test_clone_selects_new_instance(self, tmp_path):
        """After cloning, selected_instance is set to the dest name."""
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "orig")
            err = call_sync(app.instance_actions.clone, "orig", "clone")

        assert err == ""
        assert app.selected_instance == "clone"
        assert any(i.name == "clone" for i in app.instances)

    def test_clone_failure_returns_error(self, tmp_path):
        """On failure, returns the error string without changing selected."""
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "source")
            create_sync(app, "existing")
        app.selected_instance = "source"

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            err = call_sync(app.instance_actions.clone, "source", "existing")

        assert err != ""
        assert app.selected_instance == "source"  # unchanged

    def test_clone_passes_runner_and_tags_image(self, tmp_path):
        """Clone passes runner to clone(); runner.tag() called when image exists."""
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "source")

        # Mark source image as existing so tag() gets called
        app._runner.image_exists.return_value = True

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            err = call_sync(app.instance_actions.clone, "source", "dest")

        assert err == ""
        app._runner.tag.assert_called_once_with("source", "dest")


class TestInstanceDelete:
    """Tests for InstanceController.delete()."""

    def test_delete_instance_async(self, tmp_path):
        """delete uses run_async (sets busy, calls delete, refreshes)."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "todelete")

        assert any(i.name == "todelete" for i in app.instances)
        app.selected_instance = "todelete"

        done = threading.Event()

        with sync_deferred_call(done):
            app.instance_actions.delete("todelete")
            wait(done)

        assert app.busy is False
        assert app.selected_instance == ""
        assert "Deleted" in app.status_message

    def test_delete_instance_error_preserves_message(self, tmp_path):
        """delete on_error shows the error message (not overwritten by refresh)."""
        import threading
        from unittest.mock import patch

        from scratch_monkey.instance import InstanceError

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        done = threading.Event()

        with (
            sync_deferred_call(done),
            patch("scratch_monkey.gui.controllers.instances.delete", side_effect=InstanceError("permission denied")),
        ):
            app.instance_actions.delete("myinst")
            wait(done)

        assert app.busy is False
        assert "permission denied" in app.status_message


class TestDeleteReload:
    """Delete failure reloads the list in the worker."""

    def test_delete_error_reloads_list_in_worker_and_keeps_banner(self, tmp_path):
        import threading
        from unittest.mock import patch

        import scratch_monkey.gui.models.app as models
        from scratch_monkey.instance import InstanceError

        app, instances_dir, project_dir = make_app(tmp_path)
        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "keep")
        app.selected_instance = "keep"
        gui_thread = threading.current_thread()
        threads = []
        real = models.list_all

        def spy(*a, **kw):
            threads.append(threading.current_thread())
            return real(*a, **kw)

        done = threading.Event()

        with (
            sync_deferred_call(done),
            patch("scratch_monkey.gui.controllers.instances.delete", side_effect=InstanceError("nope")),
            patch("scratch_monkey.gui.models.app.list_all", side_effect=spy),
        ):
            app.instance_actions.delete("keep")
            wait(done)

        assert threads and all(t is not gui_thread for t in threads)
        assert app.error_message == "nope"  # banner (no dialog for delete)
        assert app.status_message == "Error: nope"
        assert app.selected_instance == "keep"  # failed delete keeps the selection
        assert app.busy is False

    def test_delete_busy_does_not_raise(self, tmp_path):
        app, _, _ = make_app(tmp_path)
        app.busy = True
        app.instance_actions.delete("whatever")  # no on_done; just reports busy
        assert app.status_message.startswith("Busy")


class TestInstanceControllerWiring:
    """Actions go through AppModel's async helpers, not their own threads."""

    def test_delete_runs_through_run_then_reload(self, tmp_path):
        from unittest.mock import patch

        app, _, _ = make_app(tmp_path)
        with patch.object(AppModel, "run_then_reload") as run:
            app.instance_actions.delete("x")
        run.assert_called_once()
        assert run.call_args.args[0] == "Deleting 'x'..."
        assert run.call_args.kwargs["reload_on_error"] is True

    def test_create_runs_through_run_then_reload(self, tmp_path):
        from unittest.mock import patch

        app, _, _ = make_app(tmp_path)
        cb = lambda err: None  # noqa: E731
        with patch.object(AppModel, "run_then_reload") as run:
            app.instance_actions.create("x", cb)
        run.assert_called_once()
        assert run.call_args.args[0] == "Creating 'x'..."
        assert run.call_args.kwargs["on_done"] is cb
