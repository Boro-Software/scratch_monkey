"""Tests for scratch_monkey.container module."""

import subprocess
from unittest.mock import patch

import pytest

from scratch_monkey.container import PodmanError, PodmanRunner


def make_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    r = subprocess.CompletedProcess(args=[], returncode=returncode)
    r.stdout = stdout
    r.stderr = stderr
    return r


@pytest.fixture
def runner():
    return PodmanRunner()


class TestImageExists:
    def test_returns_true_when_exists(self, runner):
        with patch("subprocess.run", return_value=make_result(0)) as mock_run:
            assert runner.image_exists("myimage") is True
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "image" in args and "exists" in args and "myimage" in args

    def test_returns_false_when_missing(self, runner):
        with patch("subprocess.run", return_value=make_result(1)):
            assert runner.image_exists("missing") is False


class TestContainerExists:
    def test_returns_true_when_exists(self, runner):
        with patch("subprocess.run", return_value=make_result(0)):
            assert runner.container_exists("mycontainer") is True

    def test_returns_false_when_missing(self, runner):
        with patch("subprocess.run", return_value=make_result(1)):
            assert runner.container_exists("mycontainer") is False


class TestContainerRunning:
    def test_returns_true_when_running(self, runner):
        with patch("subprocess.run", return_value=make_result(0, stdout="true")):
            assert runner.container_running("mycontainer") is True

    def test_returns_false_when_not_running(self, runner):
        results = [make_result(0), make_result(0, stdout="false")]
        with patch("subprocess.run", side_effect=results):
            assert runner.container_running("mycontainer") is False

    def test_returns_false_when_not_exists(self, runner):
        with patch("subprocess.run", return_value=make_result(1)):
            assert runner.container_running("mycontainer") is False


class TestContainerStatus:
    def test_returns_empty_when_not_exists(self, runner):
        with patch("subprocess.run", return_value=make_result(1)):
            assert runner.container_status("mycontainer") == ""

    def test_returns_status_string(self, runner):
        results = [make_result(0), make_result(0, stdout="running")]
        with patch("subprocess.run", side_effect=results):
            assert runner.container_status("mycontainer") == "running"


class TestStart:
    def test_calls_start(self, runner):
        with patch("subprocess.run", return_value=make_result(0)) as mock_run:
            runner.start("mycontainer")
            args = mock_run.call_args[0][0]
            assert "start" in args and "mycontainer" in args

    def test_raises_on_failure(self, runner):
        with patch("subprocess.run", return_value=make_result(1, stderr="no such container")):
            with pytest.raises(PodmanError):
                runner.start("missing")


class TestRemove:
    def test_calls_rm(self, runner):
        with patch("subprocess.run", return_value=make_result(0)) as mock_run:
            runner.remove("mycontainer")
            args = mock_run.call_args[0][0]
            assert "rm" in args and "mycontainer" in args

    def test_force_flag(self, runner):
        with patch("subprocess.run", return_value=make_result(0)) as mock_run:
            runner.remove("mycontainer", force=True)
            args = mock_run.call_args[0][0]
            assert "--force" in args

    def test_raises_on_failure(self, runner):
        with patch("subprocess.run", return_value=make_result(1, stderr="error")):
            with pytest.raises(PodmanError):
                runner.remove("missing")


class TestRmi:
    def test_calls_rmi(self, runner):
        with patch("subprocess.run", return_value=make_result(0)) as mock_run:
            runner.rmi("myimage")
            args = mock_run.call_args[0][0]
            assert "rmi" in args and "myimage" in args

    def test_raises_on_failure(self, runner):
        with patch("subprocess.run", return_value=make_result(1, stderr="error")):
            with pytest.raises(PodmanError):
                runner.rmi("missing")


class TestTag:
    def test_tag(self, runner):
        """tag() calls podman tag with source and target."""
        with patch("subprocess.run", return_value=make_result(0)) as mock_run:
            runner.tag("oldimage", "newimage")
            args = mock_run.call_args[0][0]
            assert "tag" in args
            assert "oldimage" in args
            assert "newimage" in args

    def test_tag_order(self, runner):
        """tag() passes source before target."""
        with patch("subprocess.run", return_value=make_result(0)) as mock_run:
            runner.tag("source:latest", "target:latest")
            args = mock_run.call_args[0][0]
            source_idx = args.index("source:latest")
            target_idx = args.index("target:latest")
            assert source_idx < target_idx

    def test_tag_raises_on_failure(self, runner):
        """tag() raises PodmanError if podman exits non-zero."""
        with patch("subprocess.run", return_value=make_result(1, stderr="image not known")):
            with pytest.raises(PodmanError):
                runner.tag("missing", "target")


class TestBuild:
    def test_calls_build_with_tag_and_context(self, runner):
        with patch("subprocess.run", return_value=make_result(0)) as mock_run:
            runner.build("mytag", "/context")
            args = mock_run.call_args[0][0]
            assert "build" in args
            assert "-t" in args
            assert "mytag" in args
            assert "/context" in args

    def test_dockerfile_flag(self, runner):
        with patch("subprocess.run", return_value=make_result(0)) as mock_run:
            runner.build("mytag", "/context", dockerfile="/path/Dockerfile")
            args = mock_run.call_args[0][0]
            assert "-f" in args
            assert "/path/Dockerfile" in args

    def test_raises_on_failure(self, runner):
        with patch("subprocess.run", return_value=make_result(1, stderr="build failed")):
            with pytest.raises(PodmanError):
                runner.build("tag", "ctx")


class TestExecCapture:
    def test_basic_exec_capture(self, runner):
        with patch("subprocess.run", return_value=make_result(0, stdout="output\n")) as mock_run:
            result = runner.exec_capture("mycontainer", ["ls", "/"])
            assert result == "output\n"
            args = mock_run.call_args[0][0]
            assert "exec" in args
            assert "mycontainer" in args
            assert "ls" in args
            assert "--user" not in args

    def test_exec_capture_with_user(self, runner):
        with patch("subprocess.run", return_value=make_result(0, stdout="ok\n")) as mock_run:
            result = runner.exec_capture("mycontainer", ["id"], user="root")
            assert result == "ok\n"
            args = mock_run.call_args[0][0]
            assert "exec" in args
            assert "--user" in args
            user_idx = args.index("--user")
            assert args[user_idx + 1] == "root"
            assert "mycontainer" in args
            assert "id" in args

    def test_exec_capture_user_before_container(self, runner):
        """--user root must appear before container name in command."""
        with patch("subprocess.run", return_value=make_result(0, stdout="")) as mock_run:
            runner.exec_capture("mycontainer", ["whoami"], user="root")
            args = mock_run.call_args[0][0]
            user_idx = args.index("--user")
            container_idx = args.index("mycontainer")
            assert user_idx < container_idx

    def test_exec_capture_raises_on_failure(self, runner):
        with patch("subprocess.run", return_value=make_result(1, stderr="failed")):
            with pytest.raises(PodmanError):
                runner.exec_capture("mycontainer", ["bad-cmd"])


class TestRunDaemon:
    def test_calls_run_with_daemon_args(self, runner):
        with patch("subprocess.run", return_value=make_result(0)) as mock_run:
            runner.run_daemon("mycontainer", "myimage", ["--network=host"])
            args = mock_run.call_args[0][0]
            assert "run" in args
            assert "-dit" in args
            assert "--name" in args
            assert "mycontainer" in args
            assert "myimage" in args
            assert "--init" in args

    def test_captures_output_and_raises_with_stderr_on_failure(self, runner):
        """run_daemon captures stderr so PodmanError carries real failure detail
        (unlike run()/build()/exec, which stream interactively and don't capture)."""
        with patch(
            "subprocess.run",
            return_value=make_result(126, stderr="OCI runtime error: exec format error"),
        ) as mock_run:
            with pytest.raises(PodmanError) as excinfo:
                runner.run_daemon("mycontainer", "myimage", [])
            assert mock_run.call_args.kwargs["capture_output"] is True
            assert "exec format error" in str(excinfo.value)
            assert "exec format error" in excinfo.value.stderr


class TestCommit:
    def test_commit(self, runner):
        with patch("subprocess.run", return_value=make_result(0)) as mock_run:
            runner.commit("mycontainer", "myimage")
            args = mock_run.call_args[0][0]
            assert "commit" in args
            assert "mycontainer" in args
            assert "myimage" in args

    def test_commit_order(self, runner):
        """commit() passes container before image."""
        with patch("subprocess.run", return_value=make_result(0)) as mock_run:
            runner.commit("mycontainer", "myimage")
            args = mock_run.call_args[0][0]
            container_idx = args.index("mycontainer")
            image_idx = args.index("myimage")
            assert container_idx < image_idx

    def test_commit_raises_on_failure(self, runner):
        with patch("subprocess.run", return_value=make_result(1, stderr="no such container")):
            with pytest.raises(PodmanError):
                runner.commit("missing", "myimage")


class TestRunNoCaptureError:
    def test_error_with_none_stderr(self, runner):
        """When capture=False, stderr is None — _run must not crash."""
        result = subprocess.CompletedProcess(args=[], returncode=1)
        result.stdout = None
        result.stderr = None
        with patch("subprocess.run", return_value=result):
            with pytest.raises(PodmanError) as exc_info:
                runner.run(["--rm", "myimage"])
            assert exc_info.value.stderr == ""


class TestPodmanError:
    def test_attributes(self):
        err = PodmanError("failed", returncode=1, stderr="oops")
        assert err.returncode == 1
        assert err.stderr == "oops"
        assert "failed" in str(err)
