"""Tests for scratch-monkey CLI commands (create, delete, enter, build-instance)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from scratch_monkey.cli.main import cli
from scratch_monkey.container import PodmanRunner


def _make_instance_dir(tmp_path: Path, name: str, toml_content: str = "") -> Path:
    inst_dir = tmp_path / name
    inst_dir.mkdir()
    (inst_dir / "home").mkdir()
    (inst_dir / "Dockerfile").write_text("FROM scratch_monkey\n")
    (inst_dir / "scratch.toml").write_text(toml_content)
    (inst_dir / ".env").touch()
    return inst_dir


class TestCreateCommand:
    def test_create_success(self, tmp_path):
        d = tmp_path / "instances"
        d.mkdir()
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=MagicMock(spec=PodmanRunner)):
            result = r.invoke(cli, ["--instances-dir", str(d), "create", "testinst"])
        assert result.exit_code == 0
        assert "Created instance" in result.output
        assert (d / "testinst" / "scratch.toml").exists()

    def test_create_duplicate_fails(self, tmp_path):
        d = tmp_path / "instances"
        d.mkdir()
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=MagicMock(spec=PodmanRunner)):
            r.invoke(cli, ["--instances-dir", str(d), "create", "testinst"])
            result = r.invoke(cli, ["--instances-dir", str(d), "create", "testinst"])
        assert result.exit_code != 0

    def test_create_invalid_name(self, tmp_path):
        d = tmp_path / "instances"
        d.mkdir()
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=MagicMock(spec=PodmanRunner)):
            result = r.invoke(cli, ["--instances-dir", str(d), "create", "-invalid"])
        assert result.exit_code != 0

    def test_create_fedora(self, tmp_path):
        d = tmp_path / "instances"
        d.mkdir()
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=MagicMock(spec=PodmanRunner)):
            result = r.invoke(cli, ["--instances-dir", str(d), "create", "--fedora", "fed"])
        assert result.exit_code == 0
        assert "fedora" in (d / "fed" / "Dockerfile").read_text().lower()


class TestDeleteCommand:
    def test_delete_success(self, tmp_path):
        d = tmp_path / "instances"
        d.mkdir()
        _make_instance_dir(d, "testinst")
        mock = MagicMock(spec=PodmanRunner)
        mock.image_exists.return_value = False
        mock.container_exists.return_value = False
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=mock):
            result = r.invoke(cli, ["--instances-dir", str(d), "delete", "-y", "testinst"])
        assert result.exit_code == 0
        assert "Deleted" in result.output

    def test_delete_nonexistent(self, tmp_path):
        d = tmp_path / "instances"
        d.mkdir()
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=MagicMock(spec=PodmanRunner)):
            result = r.invoke(cli, ["--instances-dir", str(d), "delete", "-y", "ghost"])
        assert result.exit_code != 0


class TestEnterCommand:
    def test_enter_calls_run(self, tmp_path):
        _make_instance_dir(tmp_path, "testinst")
        mock = MagicMock(spec=PodmanRunner)
        mock.image_exists.return_value = True
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=mock):
            r.invoke(cli, ["--instances-dir", str(tmp_path), "enter", "testinst"])
        assert mock.run.called

    def test_enter_nonexistent(self, tmp_path):
        d = tmp_path / "instances"
        d.mkdir()
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=MagicMock(spec=PodmanRunner)):
            result = r.invoke(cli, ["--instances-dir", str(d), "enter", "ghost"])
        assert result.exit_code != 0


class TestStartCommand:
    def test_start_creates_overlay(self, tmp_path):
        _make_instance_dir(tmp_path, "testinst", 'overlay = true\noverlay_id = "sm-test123"\n')
        mock = MagicMock(spec=PodmanRunner)
        mock.image_exists.return_value = True
        mock.container_exists.return_value = False
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=mock):
            result = r.invoke(cli, ["--instances-dir", str(tmp_path), "start", "testinst"])
        assert result.exit_code == 0
        assert "Started" in result.output
        assert mock.run_daemon.called

    def test_start_enables_overlay_if_not_set(self, tmp_path):
        _make_instance_dir(tmp_path, "testinst")
        mock = MagicMock(spec=PodmanRunner)
        mock.image_exists.return_value = True
        mock.container_exists.return_value = False
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=mock):
            result = r.invoke(cli, ["--instances-dir", str(tmp_path), "start", "testinst"])
        assert result.exit_code == 0
        assert "Enabled overlay mode" in result.output
        # Config should now have overlay = true
        toml_text = (tmp_path / "testinst" / "scratch.toml").read_text()
        assert "overlay = true" in toml_text

    def test_start_nonexistent(self, tmp_path):
        d = tmp_path / "instances"
        d.mkdir()
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=MagicMock(spec=PodmanRunner)):
            result = r.invoke(cli, ["--instances-dir", str(d), "start", "ghost"])
        assert result.exit_code != 0


class TestStopCommand:
    def test_stop_running_container(self, tmp_path):
        _make_instance_dir(tmp_path, "testinst", 'overlay_id = "sm-test123"\n')
        mock = MagicMock(spec=PodmanRunner)
        mock.container_exists.return_value = True
        mock.container_running.return_value = True
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=mock):
            result = r.invoke(cli, ["--instances-dir", str(tmp_path), "stop", "testinst"])
        assert result.exit_code == 0
        assert "Stopped" in result.output
        mock.stop.assert_called_once_with("sm-test123")

    def test_stop_already_stopped(self, tmp_path):
        _make_instance_dir(tmp_path, "testinst", 'overlay_id = "sm-test123"\n')
        mock = MagicMock(spec=PodmanRunner)
        mock.container_exists.return_value = True
        mock.container_running.return_value = False
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=mock):
            result = r.invoke(cli, ["--instances-dir", str(tmp_path), "stop", "testinst"])
        assert result.exit_code == 0
        assert "already stopped" in result.output

    def test_stop_no_overlay(self, tmp_path):
        _make_instance_dir(tmp_path, "testinst")
        mock = MagicMock(spec=PodmanRunner)
        mock.container_exists.return_value = False
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=mock):
            result = r.invoke(cli, ["--instances-dir", str(tmp_path), "stop", "testinst"])
        assert result.exit_code == 0
        assert "No overlay container found" in result.output

    def test_stop_nonexistent(self, tmp_path):
        d = tmp_path / "instances"
        d.mkdir()
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=MagicMock(spec=PodmanRunner)):
            result = r.invoke(cli, ["--instances-dir", str(d), "stop", "ghost"])
        assert result.exit_code != 0


class TestRefreshCommand:
    def test_refresh_preserves_by_default(self, tmp_path):
        _make_instance_dir(tmp_path, "testinst", 'overlay_id = "sm-test123"\n')
        mock = MagicMock(spec=PodmanRunner)
        mock.image_exists.return_value = True
        mock.container_exists.return_value = True
        mock.container_running.return_value = False
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=mock):
            result = r.invoke(cli, ["--instances-dir", str(tmp_path), "refresh", "testinst"])
        assert result.exit_code == 0
        assert "Refreshed" in result.output
        assert "committed" in result.output
        mock.commit.assert_called_once_with("sm-test123", "testinst")
        mock.remove.assert_called_once_with("sm-test123")
        # No confirmation prompt should be needed on the default path.
        assert "Discard" not in result.output

    def test_refresh_discard_prompts_and_skips_commit(self, tmp_path):
        _make_instance_dir(tmp_path, "testinst", 'overlay_id = "sm-test123"\n')
        mock = MagicMock(spec=PodmanRunner)
        mock.image_exists.return_value = True
        mock.container_exists.return_value = True
        mock.container_running.return_value = False
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=mock):
            result = r.invoke(
                cli, ["--instances-dir", str(tmp_path), "refresh", "--discard", "testinst"], input="y\n",
            )
        assert result.exit_code == 0
        assert "discarded" in result.output
        mock.commit.assert_not_called()
        mock.remove.assert_called_once_with("sm-test123")

    def test_refresh_discard_aborted(self, tmp_path):
        _make_instance_dir(tmp_path, "testinst", 'overlay_id = "sm-test123"\n')
        mock = MagicMock(spec=PodmanRunner)
        mock.image_exists.return_value = True
        mock.container_exists.return_value = True
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=mock):
            result = r.invoke(
                cli, ["--instances-dir", str(tmp_path), "refresh", "--discard", "testinst"], input="n\n",
            )
        assert result.exit_code != 0
        mock.commit.assert_not_called()
        mock.remove.assert_not_called()

    def test_refresh_nonexistent(self, tmp_path):
        d = tmp_path / "instances"
        d.mkdir()
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=MagicMock(spec=PodmanRunner)):
            result = r.invoke(cli, ["--instances-dir", str(d), "refresh", "ghost"])
        assert result.exit_code != 0

    def test_refresh_missing_container_just_starts(self, tmp_path):
        """No existing overlay container — refresh just creates/starts one."""
        _make_instance_dir(tmp_path, "testinst", 'overlay_id = "sm-test123"\n')
        mock = MagicMock(spec=PodmanRunner)
        mock.image_exists.return_value = True
        mock.container_exists.return_value = False
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=mock):
            result = r.invoke(cli, ["--instances-dir", str(tmp_path), "refresh", "testinst"])
        assert result.exit_code == 0
        mock.commit.assert_not_called()
        mock.run_daemon.assert_called_once()


class TestBuildInstanceCommand:
    def test_build_calls_build(self, tmp_path):
        _make_instance_dir(tmp_path, "testinst")
        mock = MagicMock(spec=PodmanRunner)
        mock.image_exists.return_value = True
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=mock):
            result = r.invoke(cli, ["--instances-dir", str(tmp_path), "build-instance", "testinst"])
        assert result.exit_code == 0
        assert mock.build.called

    def test_build_nonexistent(self, tmp_path):
        d = tmp_path / "instances"
        d.mkdir()
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=MagicMock(spec=PodmanRunner)):
            result = r.invoke(cli, ["--instances-dir", str(d), "build-instance", "ghost"])
        assert result.exit_code != 0


class TestListCommand:
    def test_list_shows_status(self, tmp_path):
        _make_instance_dir(tmp_path, "good")
        mock = MagicMock(spec=PodmanRunner)
        mock.image_exists.return_value = True
        mock.container_running.return_value = True
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=mock):
            result = r.invoke(cli, ["--instances-dir", str(tmp_path), "list"])
        assert result.exit_code == 0
        assert "running" in result.output
        assert "unknown" not in result.output

    def test_list_marks_status_unknown_on_podman_error(self, tmp_path):
        from scratch_monkey.container import PodmanError

        _make_instance_dir(tmp_path, "good")
        _make_instance_dir(tmp_path, "stuck")
        mock = MagicMock(spec=PodmanRunner)
        mock.image_exists.return_value = True

        def running(name):
            if name == "stuck-overlay":
                raise PodmanError("podman 'inspect' timed out: lock stuck")
            return False

        mock.container_running.side_effect = running
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=mock):
            result = r.invoke(cli, ["--instances-dir", str(tmp_path), "list"])
        assert result.exit_code == 0
        lines = {ln.split()[0]: ln for ln in result.stdout.splitlines()[1:] if ln.strip()}
        assert "stopped" in lines["good"]
        assert "unknown" in lines["stuck"]
        assert "stopped" not in lines["stuck"]
        # image query succeeded, so only the OVERLAY column is unknown
        assert lines["stuck"].split()[1:3] == ["yes", "unknown"]
        assert "status unknown for 'stuck'" in result.stderr
        assert "lock stuck" in result.stderr

    def test_list_image_failure_only_marks_image_column(self, tmp_path):
        from scratch_monkey.container import PodmanError

        _make_instance_dir(tmp_path, "inst")
        mock = MagicMock(spec=PodmanRunner)
        mock.image_exists.side_effect = PodmanError("timed out")
        mock.container_running.return_value = True
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=mock):
            result = r.invoke(cli, ["--instances-dir", str(tmp_path), "list"])
        row = result.stdout.splitlines()[1].split()
        assert row[1:3] == ["unknown", "running"]
        assert "status unknown for 'inst'" in result.stderr


class TestTopLevelPodmanErrorHandling:
    """An uncaught PodmanError (e.g. a query timeout) is a clean error, not a traceback."""

    @staticmethod
    def _invoke(tmp_path, args, configure, instance=True):
        from scratch_monkey.container import PodmanError

        if instance:
            _make_instance_dir(tmp_path, "testinst", 'overlay_id = "sm-test123"\n')
        mock = MagicMock(spec=PodmanRunner)
        configure(mock, PodmanError("podman 'inspect' timed out: stuck lock"))
        r = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=mock):
            return r.invoke(cli, ["--instances-dir", str(tmp_path), *args])

    @staticmethod
    def _assert_clean(result):
        assert result.exit_code == 1
        assert "Error: podman 'inspect' timed out: stuck lock" in result.output
        assert "Traceback" not in result.output
        # SystemExit(1), not an escaped PodmanError
        assert isinstance(result.exception, SystemExit)

    def test_build_query_timeout(self, tmp_path):
        result = self._invoke(
            tmp_path, ["build", "-y"], lambda m, e: setattr(m.image_exists, "side_effect", e),
            instance=False,
        )
        self._assert_clean(result)

    def test_build_instance_query_timeout(self, tmp_path):
        result = self._invoke(
            tmp_path,
            ["build-instance", "testinst"],
            lambda m, e: setattr(m.image_exists, "side_effect", e),
        )
        self._assert_clean(result)

    def test_stop_query_timeout(self, tmp_path):
        result = self._invoke(
            tmp_path, ["stop", "testinst"], lambda m, e: setattr(m.container_exists, "side_effect", e)
        )
        self._assert_clean(result)

    def test_stop_command_failure(self, tmp_path):
        def configure(m, e):
            m.container_exists.return_value = True
            m.container_running.return_value = True
            m.stop.side_effect = e

        self._assert_clean(self._invoke(tmp_path, ["stop", "testinst"], configure))

    def test_reset_query_timeout(self, tmp_path):
        result = self._invoke(
            tmp_path,
            ["reset", "-y", "testinst"],
            lambda m, e: setattr(m.container_exists, "side_effect", e),
        )
        self._assert_clean(result)

    def test_run_image_query_timeout(self, tmp_path):
        result = self._invoke(
            tmp_path, ["start", "testinst"], lambda m, e: setattr(m.image_exists, "side_effect", e)
        )
        self._assert_clean(result)

    def test_help_unaffected(self, tmp_path):
        result = CliRunner().invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Usage:" in result.output

    def test_per_command_handlers_still_win(self, tmp_path):
        """list reports its own per-instance errors; the top-level handler isn't involved."""
        result = self._invoke(
            tmp_path, ["list"], lambda m, e: setattr(m.image_exists, "side_effect", e)
        )
        assert result.exit_code == 0
