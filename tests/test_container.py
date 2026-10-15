"""Tests for scratch_monkey.container module."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from scratch_monkey.container import (
    QUERY_TIMEOUT,
    TERMINATE_GRACE,
    PodmanError,
    PodmanRunner,
)


def make_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    r = subprocess.CompletedProcess(args=[], returncode=returncode)
    r.stdout = stdout
    r.stderr = stderr
    return r


def make_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """A fake Popen object, as used by queries that run with a timeout."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, stderr)
    return proc


@pytest.fixture
def runner():
    return PodmanRunner()


class TestImageExists:
    def test_returns_true_when_exists(self, runner):
        with patch("subprocess.Popen", return_value=make_proc(0)) as mock_run:
            assert runner.image_exists("myimage") is True
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "image" in args and "exists" in args and "myimage" in args

    def test_returns_false_when_missing(self, runner):
        with patch("subprocess.Popen", return_value=make_proc(1)):
            assert runner.image_exists("missing") is False


class TestContainerExists:
    def test_returns_true_when_exists(self, runner):
        with patch("subprocess.Popen", return_value=make_proc(0)):
            assert runner.container_exists("mycontainer") is True

    def test_returns_false_when_missing(self, runner):
        with patch("subprocess.Popen", return_value=make_proc(1)):
            assert runner.container_exists("mycontainer") is False


class TestContainerRunning:
    def test_returns_true_when_running(self, runner):
        with patch("subprocess.Popen", return_value=make_proc(0, stdout="true")):
            assert runner.container_running("mycontainer") is True

    def test_returns_false_when_not_running(self, runner):
        results = [make_proc(0), make_proc(0, stdout="false")]
        with patch("subprocess.Popen", side_effect=results):
            assert runner.container_running("mycontainer") is False

    def test_returns_false_when_not_exists(self, runner):
        with patch("subprocess.Popen", return_value=make_proc(1)):
            assert runner.container_running("mycontainer") is False


class TestContainerStatus:
    def test_returns_empty_when_not_exists(self, runner):
        with patch("subprocess.Popen", return_value=make_proc(1)):
            assert runner.container_status("mycontainer") == ""

    def test_returns_status_string(self, runner):
        results = [make_proc(0), make_proc(0, stdout="running")]
        with patch("subprocess.Popen", side_effect=results):
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
            assert args[1:] == [
                "run", "-d", "--init", "--name", "mycontainer",
                "--network=host", "myimage", "sleep", "infinity",
            ]
            assert "-dit" not in args
            assert "-it" not in args

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


class TestTimeouts:
    def _hanging_proc(self, ignores_sigterm: bool = False) -> MagicMock:
        """A fake Popen whose first communicate() times out (podman is stuck).

        After terminate(), communicate() succeeds unless ignores_sigterm is set,
        in which case it keeps timing out until kill().
        """
        proc = MagicMock()
        proc.returncode = -15
        state = {"killed": False, "terminated": False}
        proc.terminate.side_effect = lambda: state.update(terminated=True)
        proc.kill.side_effect = lambda: state.update(killed=True)

        def communicate(input=None, timeout=None):
            if state["killed"] or (state["terminated"] and not ignores_sigterm):
                return ("", "")
            raise subprocess.TimeoutExpired("podman", timeout)

        proc.communicate.side_effect = communicate
        return proc

    def test_run_translates_timeout_to_podman_error(self, runner):
        with patch("subprocess.Popen", return_value=self._hanging_proc()):
            with pytest.raises(PodmanError) as excinfo:
                runner._run(["inspect", "sm-1234"], timeout=5)
        msg = str(excinfo.value)
        assert "sm-1234" in msg
        assert "5s" in msg
        assert "podman ps -a" in msg
        assert "podman system renumber" in msg

    def test_timeout_sends_sigterm_first_and_does_not_kill_if_it_exits(self, runner):
        proc = self._hanging_proc()
        with patch("subprocess.Popen", return_value=proc):
            with pytest.raises(PodmanError):
                runner._run(["inspect", "c"], timeout=5)
        proc.terminate.assert_called_once()
        proc.kill.assert_not_called()
        # first wait uses the query timeout, second the SIGTERM grace period
        timeouts = [c.kwargs["timeout"] for c in proc.communicate.call_args_list]
        assert timeouts == [5, TERMINATE_GRACE]

    def test_timeout_kills_only_if_sigterm_is_ignored(self, runner):
        proc = self._hanging_proc(ignores_sigterm=True)
        order = MagicMock()
        order.attach_mock(proc.terminate, "terminate")
        order.attach_mock(proc.kill, "kill")
        order.attach_mock(proc.communicate, "communicate")
        with patch("subprocess.Popen", return_value=proc):
            with pytest.raises(PodmanError, match="did not respond"):
                runner._run(["inspect", "c"], timeout=5)
        # communicate() after kill drains/closes the pipes (a bare wait() would not)
        assert [c[0] for c in order.mock_calls] == [
            "communicate",  # initial, times out
            "terminate",
            "communicate",  # SIGTERM grace, times out
            "kill",
            "communicate",  # reap + close pipes
        ]
        proc.wait.assert_not_called()

    def test_timeout_path_returns_completed_process(self, runner):
        with patch("subprocess.Popen", return_value=make_proc(0, "out", "err")) as popen:
            result = runner._run(["ps"], timeout=7, input="in")
        assert (result.returncode, result.stdout, result.stderr) == (0, "out", "err")
        assert popen.call_args.kwargs["stdin"] == subprocess.PIPE
        assert popen.call_args.kwargs["stdout"] == subprocess.PIPE
        assert popen.call_args.kwargs["text"] is True
        popen.return_value.communicate.assert_called_once_with(input="in", timeout=7)

    def test_timeout_path_honours_capture_false(self, runner):
        with patch("subprocess.Popen", return_value=make_proc(0)) as popen:
            runner._run(["ps"], timeout=7, capture=False)
        assert popen.call_args.kwargs["stdout"] is None
        assert popen.call_args.kwargs["stderr"] is None
        assert popen.call_args.kwargs["stdin"] is None

    def test_timeout_path_still_checks_returncode(self, runner):
        with patch("subprocess.Popen", return_value=make_proc(2, stderr="bad")):
            with pytest.raises(PodmanError, match="bad"):
                runner._run(["ps"], timeout=7)

    def test_no_timeout_uses_subprocess_run(self, runner):
        with patch("subprocess.run", return_value=make_result(0)) as mock_run:
            runner._run(["ps"])
        assert "timeout" not in mock_run.call_args.kwargs

    QUERIES = [
        lambda r: r.image_exists("img"),
        lambda r: r.container_exists("c"),
        lambda r: r.container_running("c"),
        lambda r: r.container_status("c"),
    ]
    QUERY_IDS = ["image_exists", "container_exists", "container_running", "container_status"]

    @pytest.mark.parametrize("call", QUERIES, ids=QUERY_IDS)
    def test_queries_raise_on_timeout_not_false(self, runner, call):
        with patch("subprocess.Popen", return_value=self._hanging_proc()):
            with pytest.raises(PodmanError, match="did not respond"):
                call(runner)

    def test_container_running_raises_when_inspect_times_out(self, runner):
        procs = [make_proc(0), self._hanging_proc()]
        with patch("subprocess.Popen", side_effect=procs):
            with pytest.raises(PodmanError):
                runner.container_running("c")

    @pytest.mark.parametrize("call", QUERIES, ids=QUERY_IDS)
    def test_queries_pass_query_timeout(self, runner, call):
        with patch("subprocess.Popen", return_value=make_proc(0, stdout="true")) as popen:
            call(runner)
        assert popen.call_count >= 1
        for c in popen.return_value.communicate.call_args_list:
            assert c.kwargs["timeout"] == QUERY_TIMEOUT

    LIFECYCLE = [
        lambda r: r.run_daemon("c", "img", []),
        lambda r: r.build("t", "."),
        lambda r: r.stop("c"),
        lambda r: r.start("c"),
        lambda r: r.remove("c"),
        lambda r: r.rmi("img"),
        lambda r: r.tag("a", "b"),
        lambda r: r.commit("c", "img"),
        lambda r: r.run(["img"]),
        lambda r: r.exec_in("c", ["ls"]),
        lambda r: r.exec_interactive("c", ["sh"]),
        lambda r: r.exec_capture("c", ["ls"]),
    ]
    LIFECYCLE_IDS = [
        "run_daemon", "build", "stop", "start", "remove", "rmi", "tag",
        "commit", "run", "exec_in", "exec_interactive", "exec_capture",
    ]

    @pytest.mark.parametrize("call", LIFECYCLE, ids=LIFECYCLE_IDS)
    def test_lifecycle_methods_have_no_timeout(self, runner, call):
        with (
            patch("subprocess.run", return_value=make_result(0)) as mock_run,
            patch("subprocess.Popen") as popen,
        ):
            call(runner)
        assert "timeout" not in mock_run.call_args.kwargs
        popen.assert_not_called()


class TestGraphRoot:
    def test_queries_podman_info(self, runner):
        with patch("subprocess.Popen", return_value=make_proc(0, "/home/u/.local/share/containers/storage\n")) as m:
            assert runner.graph_root() == "/home/u/.local/share/containers/storage"
        cmd = m.call_args.args[0]
        assert cmd[:2] == ["podman", "info"]
        assert "{{.Store.GraphRoot}}" in cmd

    def test_cached_per_runner(self, runner):
        with patch("subprocess.Popen", return_value=make_proc(0, "/g\n")) as m:
            assert runner.graph_root() == "/g"
            assert runner.graph_root() == "/g"
        m.assert_called_once()
        with patch("subprocess.Popen", return_value=make_proc(0, "/other\n")):
            assert PodmanRunner().graph_root() == "/other"

    def test_none_on_nonzero_exit(self, runner):
        with patch("subprocess.Popen", return_value=make_proc(125, "", "boom")):
            assert runner.graph_root() is None

    def test_none_on_empty_output(self, runner):
        with patch("subprocess.Popen", return_value=make_proc(0, "\n")):
            assert runner.graph_root() is None

    def test_none_on_timeout_and_failure_not_cached(self, runner):
        hanging = TestTimeouts()._hanging_proc()
        with patch("subprocess.Popen", return_value=hanging):
            assert runner.graph_root() is None
        with patch("subprocess.Popen", return_value=make_proc(0, "/g\n")):
            assert runner.graph_root() == "/g"

    def test_uses_query_timeout(self, runner):
        with patch.object(runner, "_run", return_value=make_result(0, "/g\n")) as m:
            runner.graph_root()
        assert m.call_args.kwargs["timeout"] == QUERY_TIMEOUT
