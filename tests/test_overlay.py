"""Tests for scratch_monkey.overlay module."""

import os
from unittest.mock import patch

import pytest

from scratch_monkey.config import InstanceConfig
from scratch_monkey.container import PodmanError
from scratch_monkey.instance import Instance
from scratch_monkey.overlay import (
    _ensure_overlay_id,
    _setup_fedora_user,
    ensure_running,
    exec_shell,
    refresh,
    reset,
)

# ─── _ensure_overlay_id ───────────────────────────────────────────────────────


def test_ensure_overlay_id_returns_existing(scratch_instance):
    """When overlay_id is already set, _ensure_overlay_id returns it directly."""
    scratch_instance.config.overlay_id = "sm-aabbccdd"
    result = _ensure_overlay_id(scratch_instance)
    assert result == "sm-aabbccdd"


def test_ensure_overlay_id_generates_and_saves_when_empty(tmp_path):
    """When overlay_id is empty, _ensure_overlay_id generates one and saves it."""
    inst_dir = tmp_path / "geninstance"
    inst_dir.mkdir()
    (inst_dir / "home").mkdir()
    (inst_dir / "Dockerfile").write_text("FROM scratch_monkey\n")
    (inst_dir / "scratch.toml").write_text("")
    (inst_dir / ".env").touch()
    inst = Instance(
        name="geninstance",
        directory=inst_dir,
        config=InstanceConfig(),
        home_dir=inst_dir / "home",
    )
    assert inst.config.overlay_id == ""

    result = _ensure_overlay_id(inst)

    # Should have generated a valid overlay_id
    import re
    assert re.fullmatch(r"sm-[0-9a-f]{8}", result), f"Bad format: {result!r}"
    # Should have mutated config
    assert inst.config.overlay_id == result
    # Should have persisted to disk
    from scratch_monkey.config import load
    saved = load(inst_dir / "scratch.toml")
    assert saved.overlay_id == result


# ─── ensure_running ───────────────────────────────────────────────────────────


class TestEnsureRunning:
    def test_creates_container_if_missing(self, scratch_instance, mock_runner):
        mock_runner.container_exists.return_value = False
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        mock_runner.run_daemon.assert_called_once()

    def test_starts_stopped_container(self, scratch_instance, mock_runner):
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = False
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        mock_runner.start.assert_called_once_with("sm-testtest")
        mock_runner.run_daemon.assert_not_called()

    def test_does_nothing_if_already_running(self, scratch_instance, mock_runner):
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        mock_runner.run_daemon.assert_not_called()
        mock_runner.start.assert_not_called()

    def test_returns_container_name(self, scratch_instance, mock_runner):
        name = ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        assert name == "sm-testtest"

    def test_skips_user_setup_for_scratch(self, scratch_instance, mock_runner):
        """Scratch instances must NOT run useradd/sudoers setup."""
        mock_runner.container_exists.return_value = False
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        # exec_capture should not be called for useradd on scratch instances
        for call_args in mock_runner.exec_capture.call_args_list:
            args = call_args[0][1]  # exec_args list
            assert "useradd" not in args, "useradd must not run on scratch instances"

    def test_runs_user_setup_for_fedora(self, fedora_instance, mock_runner):
        """Fedora instances MUST run useradd + sudoers setup."""
        mock_runner.container_exists.return_value = False
        mock_runner.exec_capture.return_value = ""
        ensure_running(fedora_instance, mock_runner, "scratch_monkey_fedora")
        # Should have called exec_capture for user setup
        mock_runner.exec_capture.assert_called()
        all_calls = mock_runner.exec_capture.call_args_list
        # At least one call should involve useradd
        useradd_called = any(
            "useradd" in str(c) for c in all_calls
        )
        assert useradd_called, "useradd should be called for fedora instances"

    def test_includes_host_mounts_for_scratch(self, scratch_instance, mock_runner):
        mock_runner.container_exists.return_value = False
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        call_args = mock_runner.run_daemon.call_args
        run_args = call_args[0][2]  # third positional arg is run_args list
        assert "/usr:/usr:ro" in run_args

    def test_no_host_mounts_for_fedora(self, fedora_instance, mock_runner):
        mock_runner.container_exists.return_value = False
        mock_runner.exec_capture.return_value = ""
        ensure_running(fedora_instance, mock_runner, "scratch_monkey_fedora")
        call_args = mock_runner.run_daemon.call_args
        run_args = call_args[0][2]
        assert "/usr:/usr:ro" not in run_args

    def test_setup_fedora_user_sudoers_uses_tee(self, fedora_instance, mock_runner):
        """Verify sudoers is written via tee+stdin, not shell interpolation."""
        mock_runner.container_exists.return_value = False
        mock_runner.container_running.return_value = False
        mock_runner.exec_capture.return_value = ""
        ensure_running(fedora_instance, mock_runner, "scratch_monkey_fedora")

        calls = mock_runner.exec_capture.call_args_list
        tee_call = None
        chmod_call = None
        for c in calls:
            exec_args = c[0][1] if len(c[0]) > 1 else []
            if exec_args and exec_args[0] == "tee":
                tee_call = c
            if exec_args and exec_args[0] == "chmod":
                chmod_call = c

        assert tee_call is not None, "Expected a tee call for sudoers"
        tee_args = tee_call[0][1]
        assert "/etc/sudoers.d/" in tee_args[1]
        assert "NOPASSWD" in tee_call[1].get("input", "")
        assert tee_call[1].get("user") == "root"

        assert chmod_call is not None, "Expected a chmod call"
        assert chmod_call[0][1] == ["chmod", "440", tee_args[1]]
        assert chmod_call[1].get("user") == "root"

    def test_prints_warnings_to_stderr(self, scratch_instance, mock_runner, capsys):
        """Warnings from build_run_args should be printed to stderr."""
        mock_runner.container_exists.return_value = False
        scratch_instance.config.wayland = True
        with patch("os.path.exists", return_value=False):
            ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        captured = capsys.readouterr()
        assert "Warning:" in captured.err

    def test_fedora_sudo_false_skips_sudoers(self, fedora_instance, mock_runner):
        """config.sudo=False propagates to _setup_fedora_user."""
        mock_runner.container_exists.return_value = False
        mock_runner.exec_capture.return_value = ""
        fedora_instance.config.sudo = False
        ensure_running(fedora_instance, mock_runner, "scratch_monkey_fedora")
        all_calls = mock_runner.exec_capture.call_args_list
        sudoers_called = any("sudoers" in str(c) for c in all_calls)
        assert not sudoers_called

    def test_fedora_sudo_true_writes_sudoers(self, fedora_instance, mock_runner):
        """config.sudo=True (default) still writes sudoers."""
        mock_runner.container_exists.return_value = False
        mock_runner.exec_capture.return_value = ""
        fedora_instance.config.sudo = True
        ensure_running(fedora_instance, mock_runner, "scratch_monkey_fedora")
        all_calls = mock_runner.exec_capture.call_args_list
        sudoers_called = any("sudoers" in str(c) for c in all_calls)
        assert sudoers_called


    def test_fedora_podman_installs_clients(self, fedora_instance, mock_runner):
        mock_runner.container_exists.return_value = False
        mock_runner.exec_capture.return_value = ""
        fedora_instance.config.podman = True
        ensure_running(fedora_instance, mock_runner, "scratch_monkey_fedora")
        assert any("podman-compose" in str(c) for c in mock_runner.exec_capture.call_args_list)

    def test_fedora_podman_off_skips_install(self, fedora_instance, mock_runner):
        mock_runner.container_exists.return_value = False
        mock_runner.exec_capture.return_value = ""
        ensure_running(fedora_instance, mock_runner, "scratch_monkey_fedora")
        assert not any("podman-compose" in str(c) for c in mock_runner.exec_capture.call_args_list)

    def test_scratch_podman_never_installs(self, scratch_instance, mock_runner):
        mock_runner.container_exists.return_value = False
        scratch_instance.config.podman = True
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        mock_runner.exec_capture.assert_not_called()


# ─── _setup_fedora_user ───────────────────────────────────────────────────────


class TestSetupFedoraUser:
    def test_calls_useradd(self, mock_runner):
        mock_runner.exec_capture.return_value = ""
        with patch.dict(os.environ, {"USER": "testuser"}):
            _setup_fedora_user("mycontainer", mock_runner)
        all_calls = mock_runner.exec_capture.call_args_list
        useradd_called = any("useradd" in str(c) for c in all_calls)
        assert useradd_called

    def test_calls_sudoers(self, mock_runner):
        mock_runner.exec_capture.return_value = ""
        with patch.dict(os.environ, {"USER": "testuser"}):
            _setup_fedora_user("mycontainer", mock_runner)
        all_calls = mock_runner.exec_capture.call_args_list
        sudoers_called = any("sudoers" in str(c) for c in all_calls)
        assert sudoers_called

    def test_ignores_useradd_error(self, mock_runner):
        """useradd may fail if user already exists — should be tolerated."""
        mock_runner.exec_capture.side_effect = [
            "",  # sudo install
            PodmanError("useradd: user exists"),  # useradd
            "",  # sudoers (tee)
            "",  # chmod
        ]
        with patch.dict(os.environ, {"USER": "testuser"}):
            _setup_fedora_user("mycontainer", mock_runner)  # should not raise

    def test_exec_capture_called_with_user_root(self, mock_runner):
        """All exec_capture calls must use user='root' for privileged setup."""
        mock_runner.exec_capture.return_value = ""
        with patch.dict(os.environ, {"USER": "testuser"}):
            _setup_fedora_user("mycontainer", mock_runner)
        for call in mock_runner.exec_capture.call_args_list:
            assert call.kwargs.get("user") == "root", (
                f"exec_capture must be called with user='root', got: {call}"
            )

    def test_no_chown_called(self, mock_runner):
        """chown must NOT be called — --userns=keep-id handles ownership."""
        mock_runner.exec_capture.return_value = ""
        with patch.dict(os.environ, {"USER": "testuser"}):
            _setup_fedora_user("mycontainer", mock_runner)
        all_calls = mock_runner.exec_capture.call_args_list
        chown_called = any("chown" in str(c) for c in all_calls)
        assert not chown_called, "chown must not be called when --userns=keep-id is used"

    def test_skips_sudo_when_disabled(self, mock_runner):
        """sudo=False skips sudo install and sudoers but keeps useradd."""
        mock_runner.exec_capture.return_value = ""
        with patch.dict(os.environ, {"USER": "testuser"}):
            _setup_fedora_user("mycontainer", mock_runner, sudo=False)
        all_calls = mock_runner.exec_capture.call_args_list
        # useradd should still be called
        useradd_called = any("useradd" in str(c) for c in all_calls)
        assert useradd_called
        # sudoers should NOT be called
        sudoers_called = any("sudoers" in str(c) for c in all_calls)
        assert not sudoers_called
        # sudo install should NOT be called
        sudo_install_called = any("dnf install" in str(c) for c in all_calls)
        assert not sudo_install_called

    def test_only_useradd_when_sudo_false(self, mock_runner):
        """With sudo=False, exactly 1 exec_capture call (useradd)."""
        mock_runner.exec_capture.return_value = ""
        with patch.dict(os.environ, {"USER": "testuser"}):
            _setup_fedora_user("mycontainer", mock_runner, sudo=False)
        assert len(mock_runner.exec_capture.call_args_list) == 1


class TestSetupFedoraPodman:
    def test_installs_when_enabled(self, mock_runner):
        mock_runner.exec_capture.return_value = ""
        with patch.dict(os.environ, {"USER": "testuser"}):
            _setup_fedora_user("c", mock_runner, podman=True)
        installs = [c for c in mock_runner.exec_capture.call_args_list if "podman-compose" in str(c)]
        assert len(installs) == 1
        assert installs[0].kwargs.get("user") == "root"
        assert "rpm -q podman podman-compose" in installs[0][0][1][-1]
        assert "dnf install -y podman podman-compose" in installs[0][0][1][-1]

    def test_install_notice_printed_when_enabled(self, mock_runner, capsys):
        mock_runner.exec_capture.return_value = ""
        with patch.dict(os.environ, {"USER": "testuser"}):
            _setup_fedora_user("c", mock_runner, podman=True)
        assert "Installing podman client tooling in the container" in capsys.readouterr().err

    def test_not_installed_by_default(self, mock_runner, capsys):
        mock_runner.exec_capture.return_value = ""
        with patch.dict(os.environ, {"USER": "testuser"}):
            _setup_fedora_user("c", mock_runner)
        assert not any("podman-compose" in str(c) for c in mock_runner.exec_capture.call_args_list)
        assert "Installing podman client tooling" not in capsys.readouterr().err

    def test_install_failure_warns_and_continues(self, mock_runner, capsys):
        def fake(container, args, **kw):
            if "podman-compose" in str(args):
                raise PodmanError("dnf failed")
            return ""

        mock_runner.exec_capture.side_effect = fake
        with patch.dict(os.environ, {"USER": "testuser"}):
            _setup_fedora_user("c", mock_runner, podman=True)  # must not raise
        assert "Warning" in capsys.readouterr().err
        # useradd and sudoers still ran after the failed install
        all_calls = str(mock_runner.exec_capture.call_args_list)
        assert "useradd" in all_calls
        assert "sudoers" in all_calls

    def test_podman_without_sudo(self, mock_runner):
        mock_runner.exec_capture.return_value = ""
        with patch.dict(os.environ, {"USER": "testuser"}):
            _setup_fedora_user("c", mock_runner, sudo=False, podman=True)
        calls = [str(c) for c in mock_runner.exec_capture.call_args_list]
        assert any("podman-compose" in c for c in calls)
        assert not any("sudoers" in c for c in calls)


# ─── exec_shell ───────────────────────────────────────────────────────────────


class TestExecShell:
    def test_exec_as_user(self, scratch_instance, mock_runner):
        with patch.dict(os.environ, {"USER": "testuser"}):
            exec_shell(scratch_instance, mock_runner, "myinstance-overlay")
        mock_runner.exec_interactive.assert_called_once()
        call_args = mock_runner.exec_interactive.call_args
        container = call_args[0][0]
        cmd_args = call_args[0][1]
        options = call_args[1].get("options", [])
        assert container == "myinstance-overlay"
        assert "--user" in options
        assert "testuser" in options
        # container_name should NOT be in options (passed as separate param)
        assert "myinstance-overlay" not in options
        assert "myinstance-overlay" not in cmd_args

    def test_exec_as_root(self, scratch_instance, mock_runner):
        mock_runner.exec_capture.return_value = ""
        exec_shell(scratch_instance, mock_runner, "myinstance-overlay", root=True)

        # Should mkdir -p /root before exec
        mock_runner.exec_capture.assert_called_once_with(
            "myinstance-overlay", ["mkdir", "-p", "/root"], user="root",
        )

        call_args = mock_runner.exec_interactive.call_args
        container = call_args[0][0]
        options = call_args[1].get("options", [])
        assert container == "myinstance-overlay"
        assert "--user" in options
        user_idx = options.index("--user")
        assert options[user_idx + 1] == "root"
        assert "/root" in options

    def test_exec_as_user_no_mkdir(self, scratch_instance, mock_runner):
        """Non-root entry must NOT run mkdir -p /root."""
        with patch.dict(os.environ, {"USER": "testuser"}):
            exec_shell(scratch_instance, mock_runner, "myinstance-overlay")
        mock_runner.exec_capture.assert_not_called()

    def test_passes_cmd(self, scratch_instance, mock_runner):
        exec_shell(scratch_instance, mock_runner, "myinstance-overlay", cmd="/bin/zsh")
        cmd_args = mock_runner.exec_interactive.call_args[0][1]
        assert "/bin/zsh" in cmd_args

    def test_exec_forwards_wayland_env(self, scratch_instance, mock_runner):
        """When wayland is enabled, exec passes WAYLAND_DISPLAY and XDG_RUNTIME_DIR."""
        scratch_instance.config.wayland = True
        with patch.dict(os.environ, {"USER": "testuser"}):
            exec_shell(scratch_instance, mock_runner, "myinstance-overlay")
        options = mock_runner.exec_interactive.call_args[1].get("options", [])
        assert "-e" in options
        assert "WAYLAND_DISPLAY=wayland-0" in options
        # XDG_RUNTIME_DIR should match current uid
        xdg_entries = [o for o in options if o.startswith("XDG_RUNTIME_DIR=")]
        assert len(xdg_entries) == 1
        assert xdg_entries[0] == f"XDG_RUNTIME_DIR=/run/user/{os.getuid()}"

    def test_exec_forwards_x11_env(self, scratch_instance, mock_runner):
        """When x11 is enabled and DISPLAY is set, exec passes DISPLAY and XAUTHORITY."""
        scratch_instance.config.x11 = True
        with (
            patch.dict(os.environ, {"USER": "testuser", "DISPLAY": ":1"}),
            patch("os.path.exists", return_value=True),
        ):
            exec_shell(scratch_instance, mock_runner, "myinstance-overlay")
        options = mock_runner.exec_interactive.call_args[1].get("options", [])
        assert "DISPLAY=:1" in options
        assert "XAUTHORITY=/tmp/.container-Xauthority" in options

    def test_exec_forwards_x11_no_xauthority(self, scratch_instance, mock_runner):
        """When x11 is enabled but no xauth file exists, XAUTHORITY is omitted."""
        scratch_instance.config.x11 = True
        with (
            patch.dict(os.environ, {"USER": "testuser", "DISPLAY": ":0"}),
            patch("os.path.exists", return_value=False),
            patch("os.path.expanduser", return_value="/home/testuser/.Xauthority"),
        ):
            exec_shell(scratch_instance, mock_runner, "myinstance-overlay")
        options = mock_runner.exec_interactive.call_args[1].get("options", [])
        assert "DISPLAY=:0" in options
        xauth_entries = [o for o in options if "XAUTHORITY" in o]
        assert len(xauth_entries) == 0

    def test_exec_forwards_ssh_env(self, scratch_instance, mock_runner):
        """When ssh is enabled and SSH_AUTH_SOCK is set, exec passes it."""
        scratch_instance.config.ssh = True
        with (
            patch.dict(os.environ, {"USER": "testuser", "SSH_AUTH_SOCK": "/tmp/ssh-agent.sock"}),
            patch("os.path.exists", return_value=True),
        ):
            exec_shell(scratch_instance, mock_runner, "myinstance-overlay")
        options = mock_runner.exec_interactive.call_args[1].get("options", [])
        assert "SSH_AUTH_SOCK=/tmp/ssh-agent.sock" in options

    def test_exec_no_display_env_by_default(self, scratch_instance, mock_runner):
        """When display sharing is disabled, no display vars in exec options."""
        with patch.dict(os.environ, {"USER": "testuser"}):
            exec_shell(scratch_instance, mock_runner, "myinstance-overlay")
        options = mock_runner.exec_interactive.call_args[1].get("options", [])
        for opt in options:
            assert "WAYLAND_DISPLAY" not in opt
            assert "XDG_RUNTIME_DIR" not in opt
            assert "DISPLAY" not in opt
            assert "XAUTHORITY" not in opt
            assert "SSH_AUTH_SOCK" not in opt


# ─── reset ────────────────────────────────────────────────────────────────────


class TestReset:
    def test_removes_container(self, scratch_instance, mock_runner):
        mock_runner.container_exists.return_value = True
        result = reset(scratch_instance, mock_runner)
        assert result is True
        mock_runner.remove.assert_called_once_with("sm-testtest", force=True)

    def test_returns_false_if_no_container(self, scratch_instance, mock_runner):
        mock_runner.container_exists.return_value = False
        result = reset(scratch_instance, mock_runner)
        assert result is False
        mock_runner.remove.assert_not_called()


# ─── refresh ──────────────────────────────────────────────────────────────────


class TestRefresh:
    def test_creates_if_missing(self, scratch_instance, mock_runner):
        """No container yet — nothing to preserve, just ensure_running()."""
        mock_runner.container_exists.return_value = False
        name = refresh(scratch_instance, mock_runner, "scratch_monkey")
        assert name == "sm-testtest"
        mock_runner.run_daemon.assert_called_once()
        mock_runner.commit.assert_not_called()
        mock_runner.stop.assert_not_called()
        mock_runner.remove.assert_not_called()

    def test_stopped_container_not_stopped_again(self, scratch_instance, mock_runner):
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = False
        refresh(scratch_instance, mock_runner, "scratch_monkey")
        mock_runner.stop.assert_not_called()

    def test_running_container_is_stopped(self, scratch_instance, mock_runner):
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        refresh(scratch_instance, mock_runner, "scratch_monkey")
        mock_runner.stop.assert_called_once_with("sm-testtest")

    def test_commits_to_instance_tag_by_default(self, scratch_instance, mock_runner):
        mock_runner.container_exists.return_value = True
        refresh(scratch_instance, mock_runner, "scratch_monkey")
        mock_runner.commit.assert_called_once_with("sm-testtest", "myinstance")

    def test_commit_happens_before_remove(self, scratch_instance, mock_runner):
        mock_runner.container_exists.return_value = True
        calls = []
        mock_runner.commit.side_effect = lambda *a: calls.append("commit")
        mock_runner.remove.side_effect = lambda *a, **k: calls.append("remove")
        refresh(scratch_instance, mock_runner, "scratch_monkey")
        assert calls == ["commit", "remove"]

    def test_removes_container_after_commit(self, scratch_instance, mock_runner):
        mock_runner.container_exists.return_value = True
        refresh(scratch_instance, mock_runner, "scratch_monkey")
        mock_runner.remove.assert_called_once_with("sm-testtest")

    def test_discard_skips_commit(self, scratch_instance, mock_runner):
        mock_runner.container_exists.return_value = True
        refresh(scratch_instance, mock_runner, "scratch_monkey", discard=True)
        mock_runner.commit.assert_not_called()
        mock_runner.remove.assert_called_once_with("sm-testtest")

    def test_commit_failure_prevents_remove(self, scratch_instance, mock_runner):
        """If commit fails, the container must be left in place."""
        mock_runner.container_exists.return_value = True
        mock_runner.commit.side_effect = PodmanError("commit failed")
        with pytest.raises(PodmanError):
            refresh(scratch_instance, mock_runner, "scratch_monkey")
        mock_runner.remove.assert_not_called()

    def test_recreate_uses_instance_tag_as_image(self, scratch_instance, mock_runner):
        """After a preserving refresh, recreation must run from the committed tag."""
        mock_runner.container_exists.side_effect = [True, False]
        refresh(scratch_instance, mock_runner, "scratch_monkey")
        mock_runner.run_daemon.assert_called_once()
        image_arg = mock_runner.run_daemon.call_args[0][1]
        assert image_arg == "myinstance"

    def test_discard_recreates_with_original_image(self, scratch_instance, mock_runner):
        mock_runner.container_exists.side_effect = [True, False]
        refresh(scratch_instance, mock_runner, "scratch_monkey", discard=True)
        mock_runner.run_daemon.assert_called_once()
        image_arg = mock_runner.run_daemon.call_args[0][1]
        assert image_arg == "scratch_monkey"

    def test_recreation_uses_fresh_run_args(self, scratch_instance, mock_runner):
        """Recreation must build run args fresh, not reuse whatever the old
        container was started with."""
        mock_runner.container_exists.side_effect = [True, False]
        refresh(scratch_instance, mock_runner, "scratch_monkey")
        run_args = mock_runner.run_daemon.call_args[0][2]
        assert "/usr:/usr:ro" in run_args

    def test_returns_container_name(self, scratch_instance, mock_runner):
        mock_runner.container_exists.side_effect = [True, False]
        name = refresh(scratch_instance, mock_runner, "scratch_monkey")
        assert name == "sm-testtest"
