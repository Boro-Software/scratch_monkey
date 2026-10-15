"""Tests for the scratch-monkey top-level ``--gui`` flag."""

from __future__ import annotations

import builtins
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from scratch_monkey.cli.main import cli
from scratch_monkey.container import PodmanError


def _block_enaml_import(name, *args, _real=builtins.__import__, **kwargs):
    if name == "enaml":
        raise ImportError("No module named 'enaml'")
    return _real(name, *args, **kwargs)


class TestGuiFlag:
    def test_gui_launches_with_default_instances_dir(self):
        runner = CliRunner()
        mock_launch = MagicMock()
        with patch("scratch_monkey.gui.main.launch", mock_launch):
            result = runner.invoke(cli, ["--gui"])
        assert result.exit_code == 0
        mock_launch.assert_called_once_with(Path.home() / "scratch-monkey")

    def test_gui_instances_dir_option_before_flag(self, tmp_path):
        runner = CliRunner()
        mock_launch = MagicMock()
        custom_dir = tmp_path / "custom-instances"
        with patch("scratch_monkey.gui.main.launch", mock_launch):
            result = runner.invoke(cli, ["--instances-dir", str(custom_dir), "--gui"])
        assert result.exit_code == 0
        mock_launch.assert_called_once_with(custom_dir)

    def test_gui_instances_dir_option_after_flag(self, tmp_path):
        runner = CliRunner()
        mock_launch = MagicMock()
        custom_dir = tmp_path / "custom-instances"
        with patch("scratch_monkey.gui.main.launch", mock_launch):
            result = runner.invoke(cli, ["--gui", "--instances-dir", str(custom_dir)])
        assert result.exit_code == 0
        mock_launch.assert_called_once_with(custom_dir)

    def test_gui_env_var(self, tmp_path):
        runner = CliRunner()
        mock_launch = MagicMock()
        env_dir = tmp_path / "env-instances"
        with patch("scratch_monkey.gui.main.launch", mock_launch):
            result = runner.invoke(cli, ["--gui"], env={"SCRATCH_MONKEY_INSTANCES_DIR": str(env_dir)})
        assert result.exit_code == 0
        mock_launch.assert_called_once_with(env_dir)

    def test_gui_option_takes_precedence_over_env_var(self, tmp_path):
        runner = CliRunner()
        mock_launch = MagicMock()
        env_dir = tmp_path / "env-instances"
        cli_dir = tmp_path / "cli-instances"
        with patch("scratch_monkey.gui.main.launch", mock_launch):
            result = runner.invoke(
                cli,
                ["--instances-dir", str(cli_dir), "--gui"],
                env={"SCRATCH_MONKEY_INSTANCES_DIR": str(env_dir)},
            )
        assert result.exit_code == 0
        mock_launch.assert_called_once_with(cli_dir)

    def test_help_lists_gui_flag(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"], terminal_width=200)
        assert result.exit_code == 0
        assert "--gui" in result.output
        assert "Launch the graphical interface" in result.output

    def test_no_gui_subcommand(self):
        assert "gui" not in cli.commands

    def test_gui_with_subcommand_is_usage_error(self):
        runner = CliRunner()
        mock_launch = MagicMock()
        with patch("scratch_monkey.gui.main.launch", mock_launch):
            result = runner.invoke(cli, ["--gui", "list"])
        assert result.exit_code == 2
        assert "--gui cannot be combined with a command" in result.output
        mock_launch.assert_not_called()

    def test_gui_missing_deps_exits_1_with_message(self):
        """--gui exits 1 with the install hint when enaml cannot be imported."""
        runner = CliRunner()
        with patch("builtins.__import__", side_effect=_block_enaml_import):
            result = runner.invoke(cli, ["--gui"])
        assert result.exit_code == 1
        assert "GUI dependencies not installed" in result.output
        assert "uv tool install --editable '.[gui]'" in result.output

    def test_gui_launch_podman_error_reported_cleanly(self):
        runner = CliRunner()
        mock_launch = MagicMock(side_effect=PodmanError("podman timed out"))
        with patch("scratch_monkey.gui.main.launch", mock_launch):
            result = runner.invoke(cli, ["--gui"])
        assert result.exit_code == 1
        assert "Error: podman timed out" in result.output
        assert not isinstance(result.exception, PodmanError)


class TestNoGuiFlagBehaviour:
    """invoke_without_command=True must not change existing behaviour."""

    def test_no_args_prints_help_with_usage_exit_code(self):
        result = CliRunner().invoke(cli, [])
        assert result.exit_code == 2
        assert "Usage:" in result.output
        assert "COMMAND" in result.output

    def test_options_without_command_is_missing_command_error(self):
        result = CliRunner().invoke(cli, ["--base-image", "foo"])
        assert result.exit_code == 2
        assert "Missing command" in result.output

    def test_gui_not_launched_for_regular_commands(self, tmp_path):
        mock_launch = MagicMock()
        with patch("scratch_monkey.gui.main.launch", mock_launch):
            result = CliRunner().invoke(cli, ["--instances-dir", str(tmp_path), "list"])
        assert result.exit_code == 0
        mock_launch.assert_not_called()


class TestLaunchImportGuard:
    """Tests for launch() import guard behavior."""

    def test_launch_exits_on_missing_enaml(self):
        """launch() exits with code 1 when enaml is not installed."""
        from scratch_monkey.gui import main as gui_main

        with patch("builtins.__import__", side_effect=_block_enaml_import):
            with pytest.raises(SystemExit) as exc_info:
                gui_main.launch(Path("/tmp/test"))
        assert exc_info.value.code == 1
