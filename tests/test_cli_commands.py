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
