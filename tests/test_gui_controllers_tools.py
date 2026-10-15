"""Tests for scratch_monkey.gui.controllers.tools.ToolsController."""

import pytest

atom = pytest.importorskip("atom")

from scratch_monkey.gui.models import AppModel  # noqa: E402

from .gui_helpers import (  # noqa: E402
    create_sync,
    make_app,
    wait,
    wait_for,
)


class TestToolsExportCommand:
    """Tests for ToolsController.export_command()."""

    def test_export_command_success(self, tmp_path):
        """export_command returns '' and sets status on success."""
        from pathlib import Path as P
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        mock_path = P("/home/user/.local/bin/rg")
        with patch("scratch_monkey.gui.controllers.tools.export_command_fn", return_value=mock_path) as mock_export:
            err = app.tools.export_command("myinst", "/usr/bin/rg")

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
        app, instances_dir, project_dir = make_app(tmp_path)

        err = app.tools.export_command("nonexistent", "/usr/bin/rg")
        assert err != ""
        assert "nonexistent" in err

    def test_export_command_with_bin_name(self, tmp_path):
        """export_command passes bin_name to export_command_fn."""
        from pathlib import Path as P
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        mock_path = P("/home/user/.local/bin/ripgrep")
        with patch("scratch_monkey.gui.controllers.tools.export_command_fn", return_value=mock_path) as mock_export:
            err = app.tools.export_command("myinst", "/usr/bin/rg", "ripgrep")

        assert err == ""
        call_args = mock_export.call_args
        assert call_args[1]["bin_name"] == "ripgrep"

    def test_export_command_exception(self, tmp_path):
        """export_command returns error string when export_command_fn raises."""
        from unittest.mock import patch

        from scratch_monkey.export import ExportError

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        with patch("scratch_monkey.gui.controllers.tools.export_command_fn", side_effect=ExportError("disk full")):
            err = app.tools.export_command("myinst", "/usr/bin/rg")

        assert err == "disk full"


class TestToolsEditFile:
    """Tests for ToolsController.edit_file()."""

    def test_edit_dockerfile_success(self, tmp_path):
        """edit_file opens Dockerfile in editor and sets status."""
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        with patch("scratch_monkey.gui.controllers.tools._open_in_editor", return_value="") as mock_edit:
            app.tools.edit_file("myinst", "dockerfile")

        mock_edit.assert_called_once()
        called_path = mock_edit.call_args[0][0]
        assert called_path == instances_dir / "myinst" / "Dockerfile"
        assert "Editing dockerfile" in app.status_message

    def test_edit_env_success(self, tmp_path):
        """edit_file opens .env in editor and sets status."""
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        with patch("scratch_monkey.gui.controllers.tools._open_in_editor", return_value="") as mock_edit:
            app.tools.edit_file("myinst", "env")

        mock_edit.assert_called_once()
        called_path = mock_edit.call_args[0][0]
        assert called_path == instances_dir / "myinst" / ".env"
        assert "Editing env" in app.status_message

    def test_edit_file_not_found(self, tmp_path):
        """edit_file sets error status for nonexistent instance."""
        app, instances_dir, project_dir = make_app(tmp_path)

        app.tools.edit_file("nonexistent", "dockerfile")
        assert "not found" in app.status_message

    def test_edit_file_editor_error(self, tmp_path):
        """edit_file propagates editor error to status_message."""
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        with patch("scratch_monkey.gui.controllers.tools._open_in_editor", return_value="No terminal emulator found."):
            app.tools.edit_file("myinst", "dockerfile")

        assert "No terminal emulator found." in app.status_message


class TestToolsDirectoryHelpers:
    """Tests for ToolsController.open_directory/open_terminal."""

    def test_open_directory(self, tmp_path):
        """open_directory calls xdg-open with the path."""
        from unittest.mock import patch

        app, _, _ = make_app(tmp_path)

        with (
            patch("scratch_monkey.gui.desktop.shutil.which", return_value="/usr/bin/xdg-open"),
            patch("scratch_monkey.gui.desktop.subprocess.Popen") as mock_popen,
        ):
            app.tools.open_directory("/some/path")

        mock_popen.assert_called_once_with(["xdg-open", "/some/path"])
        assert "Opened file manager" in app.status_message

    def test_open_directory_no_xdg_open(self, tmp_path):
        """open_directory sets error status when xdg-open is not found."""
        from unittest.mock import patch

        app, _, _ = make_app(tmp_path)

        with patch("scratch_monkey.gui.desktop.shutil.which", return_value=None):
            app.tools.open_directory("/some/path")

        assert "xdg-open not found" in app.status_message

    def test_open_terminal(self, tmp_path):
        """open_terminal launches terminal with correct cwd."""
        from unittest.mock import patch

        app, _, _ = make_app(tmp_path)

        with (
            patch("scratch_monkey.gui.desktop._find_terminal", return_value=["xdg-terminal-exec"]),
            patch("scratch_monkey.gui.desktop.subprocess.Popen") as mock_popen,
        ):
            app.tools.open_terminal("/some/path")

        mock_popen.assert_called_once_with(["xdg-terminal-exec"], cwd="/some/path")
        assert "Opened terminal" in app.status_message

    def test_open_terminal_no_terminal(self, tmp_path):
        """open_terminal sets error status when no terminal is found."""
        from unittest.mock import patch

        app, _, _ = make_app(tmp_path)

        with patch("scratch_monkey.gui.desktop._find_terminal", return_value=[]):
            app.tools.open_terminal("/some/path")

        assert "No terminal emulator found" in app.status_message


class TestToolsEditConfig:
    def _edit(self, app, name):
        from unittest.mock import MagicMock, patch

        with (
            patch("scratch_monkey.gui.controllers.tools._find_terminal", return_value=["term"]),
            patch("scratch_monkey.gui.controllers.tools.subprocess.Popen", return_value=MagicMock()),
            patch("scratch_monkey.gui.models.app.deferred_call", side_effect=lambda fn: fn()),
        ):
            app.tools.edit_config(name)

    def test_reload_after_editor_runs_in_background(self, tmp_path):
        import threading
        from unittest.mock import patch

        import scratch_monkey.gui.models.app as models

        app, instances_dir, project_dir = make_app(tmp_path)
        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")
        app.selected_instance = "myinst"
        gui_thread = threading.current_thread()
        threads = []
        real = models.list_all

        def spy(*a, **kw):
            threads.append(threading.current_thread())
            return real(*a, **kw)

        with (
            patch("scratch_monkey.gui.models.app.list_all", side_effect=spy),
            patch("scratch_monkey.gui.models.app.deferred_call", side_effect=lambda fn: fn()),
        ):
            self._edit(app, "myinst")
            wait_for(lambda: app.status_message == "Reloaded config for 'myinst'", "reload")

        assert threads and all(t is not gui_thread for t in threads)
        assert app.busy is False

    def test_reload_deferred_while_busy(self, tmp_path):
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)
        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")
        gate = threading.Event()

        with patch("scratch_monkey.gui.models.app.deferred_call", side_effect=lambda fn: fn()):
            assert app.run_async("Other op", lambda: gate.wait(timeout=5))
            self._edit(app, "myinst")
            wait_for(lambda: app._reload_pending, "reload to be queued")
            gate.set()
            wait_for(lambda: app.status_message == "Reloaded config for 'myinst'", "deferred reload")

        assert app._reload_pending == ""
        assert app.busy is False


class TestToolsControllerWiring:
    def test_edit_config_defers_through_app(self, tmp_path):
        """The editor-wait thread hands the reload back via AppModel.defer."""
        import threading
        from unittest.mock import MagicMock, patch

        app, instances_dir, project_dir = make_app(tmp_path)
        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        deferred = []
        got = threading.Event()

        def record(self, fn):
            deferred.append(fn)
            got.set()

        with (
            patch("scratch_monkey.gui.controllers.tools._find_terminal", return_value=["term"]),
            patch("scratch_monkey.gui.controllers.tools.subprocess.Popen", return_value=MagicMock()),
            patch.object(AppModel, "defer", record),
            patch.object(AppModel, "reload_when_idle") as reload_,
        ):
            app.tools.edit_config("myinst")
            wait(got)
            assert len(deferred) == 1
            deferred[0]()

        reload_.assert_called_once_with("Reloaded config for 'myinst'")
