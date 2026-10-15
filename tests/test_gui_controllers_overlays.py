"""Tests for scratch_monkey.gui.controllers.overlays.OverlayController."""

import pytest

atom = pytest.importorskip("atom")

from scratch_monkey.gui.models import AppModel  # noqa: E402
from scratch_monkey.run_args import DEFAULT_BASE_IMAGE  # noqa: E402

from .gui_helpers import (  # noqa: E402
    create_sync,
    make_app,
    sync_deferred_call,
    wait,
)


class TestOverlayActions:
    """Tests for OverlayController start/stop/reset/refresh."""

    def test_reset_async(self, tmp_path):
        """reset updates overlay_running in-place (no full refresh)."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        # Pre-set overlay_running to True so we can verify it gets cleared
        inst = next(i for i in app.instances if i.name == "myinst")
        inst.overlay_running = True
        instances_before = app.instances

        done = threading.Event()

        with (
            sync_deferred_call(done),
            patch("scratch_monkey.gui.controllers.overlays.overlay_reset", return_value=True),
        ):
            app.overlays.reset("myinst")
            wait(done)

        assert app.busy is False
        assert "removed" in app.status_message
        # Verify in-place update: same list object, overlay_running cleared
        assert app.instances is instances_before
        assert inst.overlay_running is False

    def test_stop(self, tmp_path):
        """stop stops the overlay container and clears overlay_running."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        inst = next(i for i in app.instances if i.name == "myinst")
        inst.overlay_running = True
        instances_before = app.instances

        done = threading.Event()

        with sync_deferred_call(done):
            app.overlays.stop("myinst")
            wait(done)

        assert app.busy is False
        assert "Stopped" in app.status_message
        assert app.instances is instances_before
        assert inst.overlay_running is False

    def test_stop_not_found(self, tmp_path):
        """stop sets error status for nonexistent instance."""
        app, _, _ = make_app(tmp_path)

        app.overlays.stop("nonexistent")
        assert app.busy is False
        assert "not found" in app.status_message

    def test_start(self, tmp_path):
        """start calls ensure_running and sets overlay_running."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        inst = next(i for i in app.instances if i.name == "myinst")
        assert inst.overlay_running is False
        instances_before = app.instances

        done = threading.Event()

        with (
            sync_deferred_call(done),
            patch("scratch_monkey.gui.controllers.overlays.overlay_ensure_running") as mock_ensure,
        ):
            app.overlays.start("myinst")
            wait(done)

        mock_ensure.assert_called_once()
        assert app.busy is False
        assert "Started" in app.status_message
        assert app.instances is instances_before
        assert inst.overlay_running is True

    @pytest.mark.parametrize(
        "method,target,expected",
        [
            ("start", "overlay_ensure_running", "Error starting 'myinst': podman broke"),
            ("stop", None, "Error stopping 'myinst': podman broke"),
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

        app, instances_dir, project_dir = make_app(tmp_path)
        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        done = threading.Event()
        seen_at_poll: list[str] = []

        with (
            sync_deferred_call(done),
            patch.object(
                AppModel, "poll_status", lambda self: seen_at_poll.append(self.status_message)
            ),
        ):
            if target:
                ctx = patch(
                    f"scratch_monkey.gui.controllers.overlays.{target}", side_effect=PodmanError("podman broke")
                )
            else:
                app._runner.stop.side_effect = PodmanError("podman broke")
                ctx = contextlib.nullcontext()
            with ctx:
                getattr(app.overlays, method)("myinst")
                wait(done)

        assert app.busy is False
        assert app.status_message == expected
        assert seen_at_poll == [expected]

    def test_start_not_found(self, tmp_path):
        """start sets error status for nonexistent instance."""
        app, _, _ = make_app(tmp_path)

        app.overlays.start("nonexistent")
        assert app.busy is False
        assert "not found" in app.status_message

    def test_reset_not_found(self, tmp_path):
        """reset returns early without setting busy for nonexistent instance."""
        app, _, project_dir = make_app(tmp_path)

        app.overlays.reset("nonexistent")
        assert app.busy is False
        assert "not found" in app.status_message

    def test_reset_no_container(self, tmp_path):
        """reset reports when no overlay container exists."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        done = threading.Event()

        with (
            sync_deferred_call(done),
            patch("scratch_monkey.gui.controllers.overlays.overlay_reset", return_value=False),
        ):
            app.overlays.reset("myinst")
            wait(done)

        assert app.busy is False
        assert "No overlay container found" in app.status_message

    def test_reset_error_preserves_message(self, tmp_path):
        """reset on_error calls poll_status (not refresh) and preserves error message."""
        import threading
        from unittest.mock import patch

        from scratch_monkey.container import PodmanError

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        instances_before = app.instances

        done = threading.Event()

        with (
            sync_deferred_call(done),
            patch("scratch_monkey.gui.controllers.overlays.overlay_reset", side_effect=PodmanError("podman broke")),
            # Don't start a real poll thread (it would outlive the test).
            patch.object(AppModel, "poll_status") as poll,
        ):
            app.overlays.reset("myinst")
            wait(done)

        assert app.busy is False
        assert "podman broke" in app.status_message
        poll.assert_called_once()
        # Verify instances list was not replaced (poll_status, not refresh)
        assert app.instances is instances_before

    def test_refresh_success_uses_instance_image(self, tmp_path):
        """refresh calls overlay.refresh with the instance tag when it exists."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        inst = next(i for i in app.instances if i.name == "myinst")
        inst.overlay_running = False
        instances_before = app.instances

        app._runner.image_exists.return_value = True

        done = threading.Event()

        with (
            sync_deferred_call(done),
            patch("scratch_monkey.gui.controllers.overlays.overlay_refresh") as mock_refresh,
        ):
            app.overlays.refresh("myinst")
            wait(done)

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

    def test_refresh_uses_base_image_when_not_built(self, tmp_path):
        """refresh falls back to the detected/default base image when no instance tag exists."""
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        app._runner.image_exists.return_value = False

        done = threading.Event()

        with (
            sync_deferred_call(done),
            patch("scratch_monkey.gui.controllers.overlays.overlay_refresh") as mock_refresh,
            patch("scratch_monkey.gui.controllers.overlays.detect_base_image", return_value=None),
        ):
            app.overlays.refresh("myinst")
            wait(done)

        mock_refresh.assert_called_once()
        call_args = mock_refresh.call_args
        assert call_args[0][2] == DEFAULT_BASE_IMAGE

    def test_refresh_not_found(self, tmp_path):
        """refresh sets an error status for a nonexistent instance without going busy."""
        app, _, _ = make_app(tmp_path)

        app.overlays.refresh("nonexistent")
        assert app.busy is False
        assert "not found" in app.status_message

    def test_refresh_error_preserves_message(self, tmp_path):
        """refresh on_error calls poll_status (not refresh) and preserves the error."""
        import threading
        from unittest.mock import patch

        from scratch_monkey.container import PodmanError

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        instances_before = app.instances

        done = threading.Event()

        with (
            sync_deferred_call(done),
            patch(
                "scratch_monkey.gui.controllers.overlays.overlay_refresh",
                side_effect=PodmanError("podman broke"),
            ),
            patch.object(AppModel, "poll_status") as poll,
        ):
            app.overlays.refresh("myinst")
            wait(done)

        assert app.busy is False
        assert "podman broke" in app.status_message
        poll.assert_called_once()
        # Verify instances list was not replaced (poll_status, not refresh)
        assert app.instances is instances_before


class TestImageLookupOffGuiThread:
    @pytest.mark.parametrize("method", ["start", "refresh"])
    def test_image_exists_runs_in_worker(self, tmp_path, method):
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)
        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")
        gui_thread = threading.current_thread()
        threads = []

        def image_exists(name):
            threads.append(threading.current_thread())
            return False

        app._runner.image_exists.side_effect = image_exists
        done = threading.Event()

        with (
            sync_deferred_call(done),
            patch("scratch_monkey.gui.controllers.overlays.overlay_ensure_running"),
            patch("scratch_monkey.gui.controllers.overlays.overlay_refresh"),
        ):
            getattr(app.overlays, method)("myinst")
            wait(done)

        assert threads and all(t is not gui_thread for t in threads)


class TestOverlayControllerWiring:
    """Actions go through AppModel.run_async, not their own threads."""

    def test_start_runs_through_run_async(self, tmp_path):
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)
        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        with patch.object(AppModel, "run_async") as run:
            app.overlays.start("myinst")
        run.assert_called_once()
        assert run.call_args.args[0] == "Starting 'myinst'..."
        assert app.busy is False  # nothing ran: run_async was the only gate

    def test_stop_reset_refresh_run_through_run_async(self, tmp_path):
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)
        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")

        with patch.object(AppModel, "run_async") as run:
            app.overlays.stop("myinst")
            app.overlays.reset("myinst")
            app.overlays.refresh("myinst")
        assert [c.args[0] for c in run.call_args_list] == [
            "Stopping 'myinst'...",
            "Resetting overlay for 'myinst'...",
            "Refreshing 'myinst'...",
        ]


class TestResetOverlayData:
    """OverlayController.reset_overlay_data discards overlay-mode volume changes."""

    def test_not_found(self, tmp_path):
        app, _, _ = make_app(tmp_path)
        app.overlays.reset_overlay_data("nonexistent")
        assert app.busy is False
        assert "not found" in app.status_message

    def test_nothing_to_reset(self, tmp_path):
        import threading
        from unittest.mock import patch

        app, _, project_dir = make_app(tmp_path)
        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")
        done = threading.Event()
        with (
            sync_deferred_call(done),
            patch("scratch_monkey.gui.controllers.overlays.reset_overlays") as reset,
        ):
            app.overlays.reset_overlay_data("myinst")
            wait(done)
        reset.assert_not_called()
        assert "No overlay data" in app.status_message

    def test_resets_and_marks_stopped(self, tmp_path):
        import threading
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)
        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            create_sync(app, "myinst")
        (instances_dir / "myinst" / ".overlays" / "x-1").mkdir(parents=True)
        inst = next(i for i in app.instances if i.name == "myinst")
        inst.overlay_running = True
        done = threading.Event()
        with (
            sync_deferred_call(done),
            patch(
                "scratch_monkey.gui.controllers.overlays.reset_overlays", return_value=True
            ) as reset,
        ):
            app.overlays.reset_overlay_data("myinst")
            wait(done)
        reset.assert_called_once()
        assert app.busy is False
        assert "Reset 1 overlay mount(s)" in app.status_message
        assert inst.overlay_running is False
