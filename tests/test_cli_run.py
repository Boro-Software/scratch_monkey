"""Tests for scratch_monkey.cli.main _run_instance GPU and devices support."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from scratch_monkey.cli.main import cli
from scratch_monkey.container import PodmanRunner


def _make_instance_dir(tmp_path: Path, name: str, toml_content: str = "") -> Path:
    """Create a minimal instance directory structure."""
    inst_dir = tmp_path / name
    inst_dir.mkdir()
    (inst_dir / "home").mkdir()
    (inst_dir / "Dockerfile").write_text("FROM scratch_monkey\n")
    (inst_dir / "scratch.toml").write_text(toml_content)
    return inst_dir


class TestRunInstanceGpu:
    """Tests for GPU passthrough args in _run_instance (non-overlay path)."""

    def _invoke_run(self, tmp_path: Path, name: str, runner: MagicMock) -> list:
        """Invoke CLI 'run <name>' and return the podman args passed to runner.run."""
        instances_dir = tmp_path
        cli_runner = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=runner):
            result = cli_runner.invoke(
                cli,
                ["--instances-dir", str(instances_dir), "run", name],
            )
        return result

    def test_run_instance_with_gpu(self, tmp_path):
        """When gpu=true, --device args for detected GPU devices are added."""
        _make_instance_dir(tmp_path, "gpuinst", "gpu = true\n")
        runner = MagicMock(spec=PodmanRunner)
        runner.image_exists.return_value = True

        detected = ["/dev/dri", "/dev/kfd"]
        with patch("scratch_monkey.run_args.gpu_devices", return_value=detected):
            cli_runner = CliRunner()
            with patch("scratch_monkey.cli.main.PodmanRunner", return_value=runner):
                cli_runner.invoke(
                    cli,
                    ["--instances-dir", str(tmp_path), "run", "gpuinst"],
                )

        assert runner.run.called
        podman_args = runner.run.call_args[0][0]
        assert "--device" in podman_args
        assert "/dev/dri" in podman_args
        assert "/dev/kfd" in podman_args

    def test_run_instance_no_gpu_when_false(self, tmp_path):
        """When gpu=false (default), no GPU --device args appear."""
        _make_instance_dir(tmp_path, "nogpu", "gpu = false\n")
        runner = MagicMock(spec=PodmanRunner)
        runner.image_exists.return_value = True

        with patch("scratch_monkey.run_args.gpu_devices", return_value=["/dev/dri"]):
            cli_runner = CliRunner()
            with patch("scratch_monkey.cli.main.PodmanRunner", return_value=runner):
                cli_runner.invoke(
                    cli,
                    ["--instances-dir", str(tmp_path), "run", "nogpu"],
                )

        assert runner.run.called
        podman_args = runner.run.call_args[0][0]
        assert "/dev/dri" not in podman_args

    def test_run_instance_with_devices(self, tmp_path):
        """When devices list is set, --device args for each device are added."""
        _make_instance_dir(
            tmp_path, "devinst",
            'devices = ["/dev/video0", "/dev/bus/usb"]\n',
        )
        runner = MagicMock(spec=PodmanRunner)
        runner.image_exists.return_value = True

        cli_runner = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=runner):
            cli_runner.invoke(
                cli,
                ["--instances-dir", str(tmp_path), "run", "devinst"],
            )

        assert runner.run.called
        podman_args = runner.run.call_args[0][0]
        assert "--device" in podman_args
        assert "/dev/video0" in podman_args
        assert "/dev/bus/usb" in podman_args

    def test_run_instance_gpu_and_devices_combined(self, tmp_path):
        """GPU passthrough and extra devices can both be present."""
        _make_instance_dir(
            tmp_path, "combo",
            'gpu = true\ndevices = ["/dev/video0"]\n',
        )
        runner = MagicMock(spec=PodmanRunner)
        runner.image_exists.return_value = True

        with patch("scratch_monkey.run_args.gpu_devices", return_value=["/dev/dri"]):
            cli_runner = CliRunner()
            with patch("scratch_monkey.cli.main.PodmanRunner", return_value=runner):
                cli_runner.invoke(
                    cli,
                    ["--instances-dir", str(tmp_path), "run", "combo"],
                )

        assert runner.run.called
        podman_args = runner.run.call_args[0][0]
        assert "/dev/dri" in podman_args
        assert "/dev/video0" in podman_args


class TestRunPodmanFlag:
    def _run(self, tmp_path, args, toml=""):
        _make_instance_dir(tmp_path, "pinst", toml)
        runner = MagicMock(spec=PodmanRunner)
        runner.image_exists.return_value = True
        with (
            patch("scratch_monkey.cli.main.PodmanRunner", return_value=runner),
            patch("scratch_monkey.run_args.os.path.exists", return_value=True),
        ):
            result = CliRunner().invoke(
                cli, ["--instances-dir", str(tmp_path), "run", "pinst", *args]
            )
        return result, runner

    def test_podman_flag_sets_config(self, tmp_path):
        result, runner = self._run(tmp_path, ["--podman"])
        assert result.exit_code == 0, result.output
        podman_args = runner.run.call_args[0][0]
        assert any(a.startswith("CONTAINER_HOST=unix://") for a in podman_args)
        assert any(a.startswith("DOCKER_HOST=unix://") for a in podman_args)

    def test_no_flag_no_socket(self, tmp_path):
        result, runner = self._run(tmp_path, [])
        assert result.exit_code == 0, result.output
        podman_args = runner.run.call_args[0][0]
        assert not any(a.startswith("CONTAINER_HOST=") for a in podman_args)

    def test_config_enables_podman(self, tmp_path):
        result, runner = self._run(tmp_path, [], toml="podman = true\n")
        assert result.exit_code == 0, result.output
        podman_args = runner.run.call_args[0][0]
        assert any(a.startswith("CONTAINER_HOST=") for a in podman_args)

    def test_help_mentions_risk(self):
        result = CliRunner().invoke(cli, ["run", "--help"])
        assert "--podman" in result.output
        assert "host-level" in result.output


class TestListShowsPodman:
    def test_list_shows_podman_true(self, tmp_path):
        _make_instance_dir(tmp_path, "pl", "podman = true\n")
        runner = MagicMock(spec=PodmanRunner)
        runner.image_exists.return_value = False
        runner.container_exists.return_value = False
        runner.container_running.return_value = False
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=runner):
            result = CliRunner().invoke(cli, ["--instances-dir", str(tmp_path), "list"])
        assert "podman=true" in result.output


class TestRunInstanceOverlayLowerLimits:
    """The direct-run path skips overlay volumes podman's storage lives in."""

    def test_overlay_volume_containing_graphroot_skipped(self, tmp_path):
        graph = tmp_path / "h" / ".local" / "share" / "containers" / "storage"
        graph.mkdir(parents=True)
        src = tmp_path / "h" / ".local"
        _make_instance_dir(tmp_path, "inst", f'volumes = ["{src}:/c/l:overlay"]\n')
        runner = MagicMock(spec=PodmanRunner)
        runner.image_exists.return_value = True
        runner.graph_root.return_value = str(graph)

        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=runner):
            result = CliRunner().invoke(cli, ["--instances-dir", str(tmp_path), "run", "inst"])

        podman_args = runner.run.call_args[0][0]
        assert "/c/l" not in " ".join(podman_args)
        assert "Skipping overlay volume" in result.output
