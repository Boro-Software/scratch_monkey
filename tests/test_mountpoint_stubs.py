"""Tests wiring mount-point handling into overlay, the runner and the CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from scratch_monkey.cli.main import cli
from scratch_monkey.container import PodmanError, PodmanRunner
from scratch_monkey.mountpoints import load_record
from scratch_monkey.overlay import ensure_running, refresh


@pytest.fixture(autouse=True)
def _user(monkeypatch):
    monkeypatch.setenv("USER", "tester")


# ─── overlay: prevention ──────────────────────────────────────────────────────


class TestOverlayPrevention:
    def test_home_relative_mount_point_precreated_before_run_daemon(
        self, scratch_instance, mock_runner, tmp_path
    ):
        src = tmp_path / "cargo"
        src.mkdir()
        scratch_instance.config.volumes = [f"{src}:/home/tester/.cargo:ro"]
        seen = {}

        def fake_run_daemon(name, image, run_args):
            seen["exists"] = (scratch_instance.home_dir / ".cargo").is_dir()

        mock_runner.run_daemon.side_effect = fake_run_daemon
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        assert seen == {"exists": True}

    def test_creation_is_recorded(self, scratch_instance, mock_runner, tmp_path):
        scratch_instance.config.volumes = [f"{tmp_path}:/home/tester/.local/bin"]
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        assert load_record(scratch_instance) == {".local", ".local/bin"}

    def test_no_precreation_when_container_exists(self, scratch_instance, mock_runner, tmp_path):
        scratch_instance.config.volumes = [f"{tmp_path}:/home/tester/.cargo"]
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        assert not (scratch_instance.home_dir / ".cargo").exists()

    def test_outside_home_targets_not_created_on_host(
        self, scratch_instance, mock_runner, tmp_path
    ):
        scratch_instance.config.volumes = [f"{tmp_path}:/opt/x", f"{tmp_path}:/home/linuxbrew"]
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        assert list(scratch_instance.home_dir.iterdir()) == []

    def test_stale_recorded_dir_removed_on_next_creation(
        self, scratch_instance, mock_runner, tmp_path
    ):
        scratch_instance.config.volumes = [f"{tmp_path}:/home/tester/.cargo"]
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        assert (scratch_instance.home_dir / ".cargo").is_dir()
        scratch_instance.config.volumes = []
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        assert not (scratch_instance.home_dir / ".cargo").exists()

    def test_precreate_warning_printed(self, scratch_instance, mock_runner, capsys):
        with patch(
            "scratch_monkey.overlay.prepare_mountpoints", return_value=["Could not pre-create x"]
        ):
            ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        assert "Could not pre-create x" in capsys.readouterr().err
        mock_runner.run_daemon.assert_called_once()


# ─── overlay: refresh cleanup of writable-layer stubs ─────────────────────────


class TestRefreshOutsideStubs:
    def _refresh(self, inst, runner, old, new):
        runner.container_exists.side_effect = [True, False]
        runner.container_mount_destinations.side_effect = [old, new]
        return refresh(inst, runner, "scratch_monkey")

    def test_rmdir_for_removed_outside_home_targets_only(self, scratch_instance, mock_runner):
        old = ["/usr", "/opt/x", "/home/linuxbrew", "/home/tester/.cargo", "/opt/keep"]
        new = ["/usr", "/opt/keep"]
        self._refresh(scratch_instance, mock_runner, old, new)
        mock_runner.exec_capture.assert_called_once_with(
            "sm-testtest", ["rmdir", "--", "/home/linuxbrew", "/opt/x"], user="root"
        )

    def test_old_mounts_captured_before_removal(self, scratch_instance, mock_runner):
        calls = []
        mock_runner.container_exists.side_effect = [True, False]
        mock_runner.container_mount_destinations.side_effect = lambda n: (
            calls.append("inspect") or []
        )
        mock_runner.remove.side_effect = lambda *a, **k: calls.append("remove")
        refresh(scratch_instance, mock_runner, "scratch_monkey")
        assert calls.index("inspect") < calls.index("remove")

    def test_no_exec_when_nothing_removed(self, scratch_instance, mock_runner):
        self._refresh(scratch_instance, mock_runner, ["/opt/x"], ["/opt/x"])
        mock_runner.exec_capture.assert_not_called()

    def test_rmdir_runs_after_new_container_created(self, scratch_instance, mock_runner):
        order = []
        mock_runner.run_daemon.side_effect = lambda *a, **k: order.append("create")
        mock_runner.exec_capture.side_effect = lambda *a, **k: order.append("rmdir") or ""
        self._refresh(scratch_instance, mock_runner, ["/opt/x"], [])
        assert order == ["create", "rmdir"]

    def test_rmdir_failure_is_ignored(self, scratch_instance, mock_runner):
        mock_runner.exec_capture.side_effect = PodmanError("rmdir: not empty", returncode=1)
        name = self._refresh(scratch_instance, mock_runner, ["/opt/x"], [])
        assert name == "sm-testtest"

    def test_inspect_failure_does_not_block_refresh(self, scratch_instance, mock_runner):
        mock_runner.container_exists.side_effect = [True, False]
        mock_runner.container_mount_destinations.side_effect = PodmanError("timeout")
        name = refresh(scratch_instance, mock_runner, "scratch_monkey")
        assert name == "sm-testtest"
        mock_runner.remove.assert_called_once()
        mock_runner.exec_capture.assert_not_called()

    def test_protected_paths_never_rmdired(self, scratch_instance, mock_runner):
        self._refresh(scratch_instance, mock_runner, ["/usr/local", "/mnt", "/"], [])
        mock_runner.exec_capture.assert_not_called()

    def test_home_relative_stub_cleanup_happens_on_host_during_refresh(
        self, scratch_instance, mock_runner, tmp_path
    ):
        scratch_instance.config.volumes = [f"{tmp_path}:/home/tester/.cargo"]
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        assert (scratch_instance.home_dir / ".cargo").is_dir()
        scratch_instance.config.volumes = []
        self._refresh(scratch_instance, mock_runner, ["/home/tester/.cargo"], [])
        assert not (scratch_instance.home_dir / ".cargo").exists()
        mock_runner.exec_capture.assert_not_called()


# ─── PodmanRunner ─────────────────────────────────────────────────────────────


def _result(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    r = subprocess.CompletedProcess(args=[], returncode=returncode)
    r.stdout = stdout
    r.stderr = stderr
    return r


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, stderr)
    return proc


class TestRunnerMethods:
    def test_mount_destinations_parsed_one_per_line(self):
        out = "/usr\n/opt/x\n\n/home/me\n"
        with patch("subprocess.Popen", return_value=_proc(0, stdout=out)) as popen:
            dests = PodmanRunner().container_mount_destinations("c")
        assert dests == ["/usr", "/opt/x", "/home/me"]
        args = popen.call_args[0][0]
        assert args[:2] == ["podman", "inspect"] and "c" in args
        assert any(".Mounts" in a and ".Destination" in a for a in args)

    def test_mount_destinations_failure_returns_empty(self):
        with patch("subprocess.Popen", return_value=_proc(125, stderr="no such")):
            assert PodmanRunner().container_mount_destinations("missing") == []

    def test_unshare_rmdir_argv(self):
        with patch("subprocess.run", return_value=_result(0)) as run:
            PodmanRunner().unshare_rmdir(["/h/a/b", "/h/a"])
        assert run.call_args[0][0] == ["podman", "unshare", "rmdir", "--", "/h/a/b", "/h/a"]

    def test_unshare_rmdir_raises_on_failure(self):
        with patch("subprocess.run", return_value=_result(1, stderr="not empty")):
            with pytest.raises(PodmanError):
                PodmanRunner().unshare_rmdir(["/h/a"])


# ─── direct-run path ──────────────────────────────────────────────────────────


def _make_instance(tmp_path: Path, name: str, toml: str = "") -> Path:
    inst_dir = tmp_path / name
    inst_dir.mkdir()
    (inst_dir / "home").mkdir()
    (inst_dir / "Dockerfile").write_text("FROM scratch_monkey\n")
    (inst_dir / "scratch.toml").write_text(toml)
    return inst_dir


def _invoke(tmp_path: Path, runner: MagicMock, *args: str, input: str | None = None):
    with patch("scratch_monkey.cli.main.PodmanRunner", return_value=runner):
        return CliRunner().invoke(cli, ["--instances-dir", str(tmp_path), *args], input=input)


class TestDirectRunPrevention:
    def test_mount_point_created_before_runner_run(self, tmp_path):
        src = tmp_path / "cargo-src"
        src.mkdir()
        inst_dir = _make_instance(tmp_path, "p", f'volumes = ["{src}:/home/tester/.cargo:ro"]\n')
        runner = MagicMock(spec=PodmanRunner)
        runner.image_exists.return_value = True
        seen = {}
        runner.run.side_effect = lambda args: seen.update(
            exists=(inst_dir / "home" / ".cargo").is_dir()
        )

        result = _invoke(tmp_path, runner, "run", "p")
        assert result.exit_code == 0, result.output
        assert seen == {"exists": True}
        args = runner.run.call_args[0][0]
        assert f"{src}:/home/tester/.cargo:ro" in args  # mount args unchanged

    def test_root_run_uses_root_home(self, tmp_path):
        src = tmp_path / "s"
        src.mkdir()
        toml = f'volumes = ["{src}:/root/.cache", "{src}:/home/tester/.cargo"]\n'
        inst_dir = _make_instance(tmp_path, "r", toml)
        runner = MagicMock(spec=PodmanRunner)
        runner.image_exists.return_value = True
        result = _invoke(tmp_path, runner, "run", "r", "--root")
        assert result.exit_code == 0, result.output
        assert (inst_dir / "home" / ".cache").is_dir()
        assert not (inst_dir / "home" / ".cargo").exists()

    def test_no_precreate_for_outside_home(self, tmp_path):
        src = tmp_path / "s"
        src.mkdir()
        inst_dir = _make_instance(tmp_path, "o", f'volumes = ["{src}:/opt/x"]\n')
        runner = MagicMock(spec=PodmanRunner)
        runner.image_exists.return_value = True
        _invoke(tmp_path, runner, "run", "o")
        assert list(inst_dir.joinpath("home").iterdir()) == []


# ─── prune-mountpoints ────────────────────────────────────────────────────────


@pytest.fixture
def foreign(monkeypatch):
    """Make every directory look owned by a subuid."""
    monkeypatch.setattr("scratch_monkey.mountpoints._lstat_uid", lambda p: 524288)


def _runner_with_rmdir(real_remove: bool = True) -> MagicMock:
    runner = MagicMock(spec=PodmanRunner)

    def fake(paths):
        if real_remove:
            for p in paths:
                Path(p).rmdir()

    runner.unshare_rmdir.side_effect = fake
    return runner


class TestPruneMountpoints:
    def _stubbed(self, tmp_path, name="jobhunter", toml=""):
        inst_dir = _make_instance(tmp_path, name, toml)
        (inst_dir / "home" / ".cargo" / "bin").mkdir(parents=True)
        (inst_dir / "home" / ".local" / "bin").mkdir(parents=True)
        return inst_dir

    def test_lists_confirms_and_removes_deepest_first(self, tmp_path, foreign):
        inst_dir = self._stubbed(tmp_path)
        runner = _runner_with_rmdir()
        result = _invoke(tmp_path, runner, "prune-mountpoints", "jobhunter", input="y\n")
        assert result.exit_code == 0, result.output
        assert ".cargo/bin" in result.output and ".local/bin" in result.output
        assert "Remove 4 empty directories?" in result.output
        paths = runner.unshare_rmdir.call_args[0][0]
        home = str(inst_dir / "home")
        assert paths.index(f"{home}/.cargo/bin") < paths.index(f"{home}/.cargo")
        assert paths.index(f"{home}/.local/bin") < paths.index(f"{home}/.local")
        assert not (inst_dir / "home" / ".cargo").exists()
        assert "Removed 4 directories." in result.output

    def test_declining_confirmation_removes_nothing(self, tmp_path, foreign):
        inst_dir = self._stubbed(tmp_path)
        runner = _runner_with_rmdir()
        result = _invoke(tmp_path, runner, "prune-mountpoints", "jobhunter", input="n\n")
        assert result.exit_code != 0
        runner.unshare_rmdir.assert_not_called()
        assert (inst_dir / "home" / ".cargo" / "bin").is_dir()

    def test_yes_skips_confirmation(self, tmp_path, foreign):
        self._stubbed(tmp_path)
        runner = _runner_with_rmdir()
        result = _invoke(tmp_path, runner, "prune-mountpoints", "jobhunter", "--yes")
        assert result.exit_code == 0, result.output
        assert "Remove" not in result.output.replace("Removed", "")
        runner.unshare_rmdir.assert_called_once()

    def test_nothing_to_do_exits_zero(self, tmp_path):
        # Real ownership: everything is ours, so nothing qualifies.
        inst_dir = self._stubbed(tmp_path)
        runner = _runner_with_rmdir()
        result = _invoke(tmp_path, runner, "prune-mountpoints", "jobhunter", "-y")
        assert result.exit_code == 0
        assert "No stale mount-point directories found." in result.output
        runner.unshare_rmdir.assert_not_called()
        assert (inst_dir / "home" / ".cargo" / "bin").is_dir()

    def test_current_mount_targets_and_parents_are_kept(self, tmp_path, foreign):
        src = tmp_path / "src"
        src.mkdir()
        inst_dir = self._stubbed(
            tmp_path, toml=f'volumes = ["{src}:/home/tester/.cargo/bin:ro"]\n'
        )
        runner = _runner_with_rmdir()
        result = _invoke(tmp_path, runner, "prune-mountpoints", "jobhunter", "-y")
        assert result.exit_code == 0, result.output
        assert (inst_dir / "home" / ".cargo" / "bin").is_dir()
        assert (inst_dir / "home" / ".cargo").is_dir()
        assert not (inst_dir / "home" / ".local").exists()

    def test_non_empty_dirs_are_not_listed(self, tmp_path, foreign):
        inst_dir = self._stubbed(tmp_path)
        (inst_dir / "home" / ".local" / "bin" / "tool").write_text("x")
        runner = _runner_with_rmdir()
        _invoke(tmp_path, runner, "prune-mountpoints", "jobhunter", "-y")
        paths = runner.unshare_rmdir.call_args[0][0]
        assert all(".local" not in p for p in paths)
        assert (inst_dir / "home" / ".local" / "bin" / "tool").exists()

    def test_all_covers_every_instance_with_one_confirmation(self, tmp_path, foreign):
        a = self._stubbed(tmp_path, "aaa")
        b = self._stubbed(tmp_path, "bbb")
        (tmp_path / ".shared").mkdir()
        runner = _runner_with_rmdir()
        result = _invoke(tmp_path, runner, "prune-mountpoints", "--all", input="y\n")
        assert result.exit_code == 0, result.output
        assert "aaa:" in result.output and "bbb:" in result.output
        assert result.output.count("?") == 1
        assert runner.unshare_rmdir.call_count == 2
        assert not (a / "home" / ".cargo").exists()
        assert not (b / "home" / ".cargo").exists()

    def test_all_with_nothing_to_do(self, tmp_path):
        _make_instance(tmp_path, "clean")
        runner = _runner_with_rmdir()
        result = _invoke(tmp_path, runner, "prune-mountpoints", "--all")
        assert result.exit_code == 0
        assert "No stale mount-point directories found." in result.output

    def test_requires_name_or_all(self, tmp_path):
        runner = _runner_with_rmdir()
        assert _invoke(tmp_path, runner, "prune-mountpoints").exit_code == 2
        _make_instance(tmp_path, "x")
        assert _invoke(tmp_path, runner, "prune-mountpoints", "x", "--all").exit_code == 2

    def test_unknown_instance(self, tmp_path):
        result = _invoke(tmp_path, _runner_with_rmdir(), "prune-mountpoints", "nope")
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_reports_failures_and_exits_nonzero(self, tmp_path, foreign):
        inst_dir = self._stubbed(tmp_path)
        runner = MagicMock(spec=PodmanRunner)
        runner.unshare_rmdir.side_effect = PodmanError("podman 'unshare' failed: busy")
        result = _invoke(tmp_path, runner, "prune-mountpoints", "jobhunter", "-y")
        assert result.exit_code == 1
        assert "Removed 0 directories." in result.output
        assert "Could not remove" in result.output
        assert str(inst_dir / "home" / ".cargo" / "bin") in result.output
