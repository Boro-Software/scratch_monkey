"""Tests for scratch_monkey.gui.controllers.shared.SharedVolumeController."""

import pytest

atom = pytest.importorskip("atom")

from .gui_helpers import (  # noqa: E402
    call_sync,
    make_app,
)


class TestSharedVolumeCreate:
    """Tests for SharedVolumeController.create()."""

    def test_create_shared_volume_success(self, tmp_path):
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            result = call_sync(app.shared.create, "mydata")

        assert result == ""
        assert (instances_dir / ".shared" / "mydata").is_dir()
        assert "mydata" in app.available_shared

    def test_create_shared_volume_duplicate(self, tmp_path):
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            call_sync(app.shared.create, "dup")
            result = call_sync(app.shared.create, "dup")

        assert result != ""

    def test_create_shared_volume_invalid_name(self, tmp_path):
        from unittest.mock import patch

        app, instances_dir, project_dir = make_app(tmp_path)

        with patch("scratch_monkey.gui.controllers.instances.PROJECT_DIR", project_dir):
            result = call_sync(app.shared.create, "bad name!")

        assert result != ""


class TestSharedControllerWiring:
    def test_create_runs_through_run_then_reload(self, tmp_path):
        from unittest.mock import patch

        from scratch_monkey.gui.models import AppModel

        app, _, _ = make_app(tmp_path)
        cb = lambda err: None  # noqa: E731
        with patch.object(AppModel, "run_then_reload") as run:
            app.shared.create("vol", cb)
        run.assert_called_once()
        assert run.call_args.args[0] == "Creating shared volume 'vol'..."
        assert run.call_args.kwargs["on_done"] is cb
