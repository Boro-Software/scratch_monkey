"""Tests for scratch_monkey.run_args module."""

import os
from unittest.mock import patch

from scratch_monkey.overlay import ensure_running
from scratch_monkey.run_args import build_run_args, gpu_devices

# Save references to real os.path functions before any patching
_real_isdir = os.path.isdir
_real_exists = os.path.exists
_real_expanduser = os.path.expanduser

def _flag_values(args: list[str], flag: str) -> list[str]:
    return [args[i + 1] for i in range(len(args) - 1) if args[i] == flag]


def _mount_destinations(args: list[str]) -> list[str]:
    """Container destinations of every -v (src:dest[:opts]) and --tmpfs (dest[:opts]) arg."""
    dests = [spec.split(":")[1] for spec in _flag_values(args, "-v")]
    dests += [spec.split(":")[0] for spec in _flag_values(args, "--tmpfs")]
    return dests


# ─── gpu_devices ─────────────────────────────────────────────────────────────


class TestGpuDevices:
    def test_detects_dri(self):
        with (
            patch("scratch_monkey.run_args.nvidia_cdi_available", return_value=False),
            patch("os.path.exists", side_effect=lambda p: p == "/dev/dri"),
        ):
            devices = gpu_devices()
        assert "/dev/dri" in devices

    def test_detects_kfd(self):
        with (
            patch("scratch_monkey.run_args.nvidia_cdi_available", return_value=False),
            patch("os.path.exists", side_effect=lambda p: p == "/dev/kfd"),
        ):
            devices = gpu_devices()
        assert "/dev/kfd" in devices

    def test_detects_nvidia_devices_no_cdi(self):
        """Without CDI, individual /dev/nvidia* paths are returned."""
        nvidia_devs = {"/dev/nvidia0", "/dev/nvidiactl", "/dev/nvidia-modeset",
                       "/dev/nvidia-uvm", "/dev/nvidia-uvm-tools"}
        with (
            patch("scratch_monkey.run_args.nvidia_cdi_available", return_value=False),
            patch("os.path.exists", side_effect=lambda p: p in nvidia_devs),
        ):
            devices = gpu_devices()
        for dev in nvidia_devs:
            assert dev in devices
        assert "nvidia.com/gpu=all" not in devices

    def test_nvidia_cdi_returns_cdi_spec(self):
        """With CDI available + NVIDIA GPU, returns CDI spec instead of raw paths."""
        present = {"/dev/nvidia0", "/dev/dri", "/etc/cdi/nvidia.yaml"}
        with (
            patch("scratch_monkey.run_args.nvidia_cdi_available", return_value=True),
            patch("os.path.exists", side_effect=lambda p: p in present),
        ):
            devices = gpu_devices()
        assert "nvidia.com/gpu=all" in devices
        assert "/dev/dri" in devices
        # Raw nvidia paths should NOT be present when CDI is used
        assert "/dev/nvidia0" not in devices
        assert "/dev/nvidiactl" not in devices

    def test_nvidia_cdi_without_gpu_falls_back(self):
        """CDI yaml exists but no NVIDIA GPU — no CDI spec returned."""
        with (
            patch("scratch_monkey.run_args.nvidia_cdi_available", return_value=True),
            patch("os.path.exists", side_effect=lambda p: p == "/dev/dri"),
        ):
            devices = gpu_devices()
        assert "nvidia.com/gpu=all" not in devices
        assert "/dev/dri" in devices

    def test_empty_when_no_gpu(self):
        with (
            patch("scratch_monkey.run_args.nvidia_cdi_available", return_value=False),
            patch("os.path.exists", return_value=False),
        ):
            devices = gpu_devices()
        assert devices == []

    def test_detects_multiple(self):
        present = {"/dev/dri", "/dev/kfd", "/dev/nvidia0"}
        with (
            patch("scratch_monkey.run_args.nvidia_cdi_available", return_value=False),
            patch("os.path.exists", side_effect=lambda p: p in present),
        ):
            devices = gpu_devices()
        assert "/dev/dri" in devices
        assert "/dev/kfd" in devices
        assert "/dev/nvidia0" in devices

    def test_use_cdi_false_skips_cdi(self):
        """use_cdi=False returns raw /dev paths even when CDI is available."""
        present = {"/dev/nvidia0", "/dev/dri", "/etc/cdi/nvidia.yaml"}
        with (
            patch("scratch_monkey.run_args.nvidia_cdi_available", return_value=True),
            patch("os.path.exists", side_effect=lambda p: p in present),
        ):
            devices = gpu_devices(use_cdi=False)
        assert "nvidia.com/gpu=all" not in devices
        assert "/dev/nvidia0" in devices
        assert "/dev/dri" in devices

    def test_use_cdi_true_uses_cdi(self):
        """use_cdi=True (default) returns CDI spec when available."""
        present = {"/dev/nvidia0", "/dev/dri", "/etc/cdi/nvidia.yaml"}
        with (
            patch("scratch_monkey.run_args.nvidia_cdi_available", return_value=True),
            patch("os.path.exists", side_effect=lambda p: p in present),
        ):
            devices = gpu_devices(use_cdi=True)
        assert "nvidia.com/gpu=all" in devices
        assert "/dev/nvidia0" not in devices


# ─── build_run_args userns ───────────────────────────────────────────────────


class TestBuildRunArgsUserns:
    def test_userns_keep_id_in_scratch_args(self, scratch_instance):
        """--userns=keep-id must appear in run args for scratch instances."""
        args, _warnings = build_run_args(scratch_instance)
        assert "--userns=keep-id" in args

    def test_userns_keep_id_in_fedora_args(self, fedora_instance):
        """--userns=keep-id must appear in run args for fedora instances."""
        args, _warnings = build_run_args(fedora_instance)
        assert "--userns=keep-id" in args

    def test_build_run_args_root_excludes_userns(self, scratch_instance):
        """root=True should exclude --userns=keep-id and use /root as HOME."""
        args, _warnings = build_run_args(scratch_instance, root=True)
        assert "--userns=keep-id" not in args
        env_args = [args[i + 1] for i in range(len(args)) if args[i] == "-e" and i + 1 < len(args)]
        assert "HOME=/root" in env_args


# ─── build_run_args gpu + devices ────────────────────────────────────────────


class TestBuildRunArgsGpuAndDevices:
    def test_gpu_flag_adds_detected_devices(self, scratch_instance):
        scratch_instance.config.gpu = True
        with patch("scratch_monkey.run_args.gpu_devices", return_value=["/dev/dri", "/dev/kfd"]):
            args, _warnings = build_run_args(scratch_instance)
        assert "--device" in args
        assert "/dev/dri" in args
        assert "/dev/kfd" in args

    def test_gpu_false_nogpu_devices(self, scratch_instance):
        scratch_instance.config.gpu = False
        with patch("scratch_monkey.run_args.gpu_devices", return_value=["/dev/dri"]):
            args, _warnings = build_run_args(scratch_instance)
        # gpu_devices should not be consulted at all
        assert "/dev/dri" not in args

    def test_extra_devices_added(self, scratch_instance):
        scratch_instance.config.devices = ["/dev/video0", "/dev/bus/usb"]
        args, _warnings = build_run_args(scratch_instance)
        assert "--device" in args
        assert "/dev/video0" in args
        assert "/dev/bus/usb" in args

    def test_no_devices_when_empty(self, scratch_instance):
        scratch_instance.config.gpu = False
        scratch_instance.config.devices = []
        args, _warnings = build_run_args(scratch_instance)
        assert "--device" not in args

    def test_gpu_and_extra_devices_combined(self, scratch_instance):
        scratch_instance.config.gpu = True
        scratch_instance.config.devices = ["/dev/video0"]
        with patch("scratch_monkey.run_args.gpu_devices", return_value=["/dev/dri"]):
            args, _warnings = build_run_args(scratch_instance)
        assert "/dev/dri" in args
        assert "/dev/video0" in args

    def test_gpu_cdi_device_spec(self, fedora_instance):
        """CDI spec passed as --device for fedora instances when NVIDIA CDI is available."""
        fedora_instance.config.gpu = True
        with patch("scratch_monkey.run_args.gpu_devices", return_value=["nvidia.com/gpu=all", "/dev/dri"]):
            args, _warnings = build_run_args(fedora_instance)
        assert "nvidia.com/gpu=all" in args
        assert "/dev/dri" in args
        # Both should be preceded by --device
        idx_cdi = args.index("nvidia.com/gpu=all")
        assert args[idx_cdi - 1] == "--device"
        idx_dri = args.index("/dev/dri")
        assert args[idx_dri - 1] == "--device"

    def test_scratch_instance_skips_cdi(self, scratch_instance):
        """Scratch instances pass use_cdi=False — no CDI specs, only raw /dev paths."""
        scratch_instance.config.gpu = True
        with patch("scratch_monkey.run_args.gpu_devices", wraps=gpu_devices) as mock_gpu:
            with (
                patch("scratch_monkey.run_args.nvidia_cdi_available", return_value=True),
                patch("scratch_monkey.run_args.os.path.exists",
                       side_effect=lambda p: p in {"/dev/nvidia0", "/dev/dri"}),
                patch("scratch_monkey.run_args.os.access", return_value=True),
            ):
                args, _warnings = build_run_args(scratch_instance)
        mock_gpu.assert_called_once_with(use_cdi=False)
        assert "nvidia.com/gpu=all" not in args

    def test_gpu_warning_no_devices_found(self, scratch_instance):
        """Warning when GPU enabled but no devices detected."""
        scratch_instance.config.gpu = True
        with patch("scratch_monkey.run_args.gpu_devices", return_value=[]):
            _args, warnings = build_run_args(scratch_instance)
        assert any("no GPU devices found" in w for w in warnings)

    def test_gpu_warning_permission_denied(self, scratch_instance):
        """Warning when GPU devices exist but render node is not accessible."""
        scratch_instance.config.gpu = True

        def mock_exists(path):
            if path == "/dev/dri/renderD128":
                return True
            return _real_exists(path)

        with (
            patch("scratch_monkey.run_args.gpu_devices", return_value=["/dev/dri"]),
            patch("scratch_monkey.run_args.os.path.exists", side_effect=mock_exists),
            patch("scratch_monkey.run_args.os.access", return_value=False),
        ):
            _args, warnings = build_run_args(scratch_instance)
        assert any("renderD128" in w and "not accessible" in w for w in warnings)
        assert any("video" in w and "render" in w for w in warnings)

    def test_gpu_no_warning_when_accessible(self, scratch_instance):
        """No GPU warning when devices are present and render node accessible."""
        scratch_instance.config.gpu = True

        def mock_exists(path):
            if path == "/dev/dri/renderD128":
                return True
            return _real_exists(path)

        with (
            patch("scratch_monkey.run_args.gpu_devices", return_value=["/dev/dri"]),
            patch("scratch_monkey.run_args.os.path.exists", side_effect=mock_exists),
            patch("scratch_monkey.run_args.os.access", return_value=True),
        ):
            _args, warnings = build_run_args(scratch_instance)
        assert not any("GPU" in w for w in warnings)


# ─── build_run_args shared volumes ────────────────────────────────────────────


class TestBuildRunArgsSharedVolumes:
    """Tests for shared volume expansion in build_run_args."""

    def test_shared_volume_rw_mount(self, scratch_instance, mock_runner, tmp_path):
        """Shared volume with default rw mode mounts without :ro suffix."""
        instances_dir = scratch_instance.directory.parent
        shared_dir = instances_dir / ".shared" / "comms"
        shared_dir.mkdir(parents=True)
        scratch_instance.config.shared = ["comms"]

        mock_runner.container_exists.return_value = False
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        call_args = mock_runner.run_daemon.call_args
        run_args = call_args[0][2]
        mount = f"{shared_dir}:/shared/comms"
        assert mount in run_args

    def test_shared_volume_ro_mount(self, scratch_instance, mock_runner, tmp_path):
        """Shared volume with :ro suffix mounts with :ro."""
        instances_dir = scratch_instance.directory.parent
        shared_dir = instances_dir / ".shared" / "comms"
        shared_dir.mkdir(parents=True)
        scratch_instance.config.shared = ["comms:ro"]

        mock_runner.container_exists.return_value = False
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        call_args = mock_runner.run_daemon.call_args
        run_args = call_args[0][2]
        mount = f"{shared_dir}:/shared/comms:ro"
        assert mount in run_args

    def test_missing_shared_volume_skipped(self, scratch_instance, mock_runner):
        """Missing shared volume directory is silently skipped."""
        scratch_instance.config.shared = ["nonexistent"]

        mock_runner.container_exists.return_value = False
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        call_args = mock_runner.run_daemon.call_args
        run_args = call_args[0][2]
        assert not any("nonexistent" in arg for arg in run_args)


# ─── build_run_args features ──────────────────────────────────────────────────


class TestBuildRunArgsFeatures:
    """Tests for build_run_args: wayland, SSH, env vars, hostname, volumes, .env file."""

    def test_hostname_includes_instance_name(self, scratch_instance):
        args, _warnings = build_run_args(scratch_instance)
        hostname_idx = args.index("--hostname")
        hostname = args[hostname_idx + 1]
        assert hostname.startswith("myinstance.")  # uses fixture name

    def test_env_vars_added(self, scratch_instance):
        scratch_instance.config.env = ["FOO=bar", "BAZ=qux"]
        args, _warnings = build_run_args(scratch_instance)
        assert "FOO=bar" in args
        assert "BAZ=qux" in args

    def test_extra_volumes_added(self, scratch_instance):
        scratch_instance.config.volumes = ["/host/path:/container/path"]
        args, _warnings = build_run_args(scratch_instance)
        assert "/host/path:/container/path" in args

    def test_invalid_volume_skipped_with_warning(self, scratch_instance):
        """Invalid volume specs are skipped instead of passed to podman."""
        scratch_instance.config.volumes = ["::ro", "/valid:/path:ro"]
        args, warnings = build_run_args(scratch_instance)
        assert "::ro" not in args
        assert "/valid:/path:ro" in args
        assert any("Skipping invalid volume" in w for w in warnings)

    def test_empty_host_volume_skipped(self, scratch_instance):
        """Volume with empty host path is skipped with warning."""
        scratch_instance.config.volumes = [":/container:ro"]
        args, warnings = build_run_args(scratch_instance)
        assert ":/container:ro" not in args
        assert any("empty host or container" in w for w in warnings)

    def test_env_file_included_when_exists(self, scratch_instance):
        # .env already exists (created by fixture)
        args, _warnings = build_run_args(scratch_instance)
        assert "--env-file" in args

    def test_env_file_skipped_when_missing(self, scratch_instance):
        env_file = scratch_instance.directory / ".env"
        env_file.unlink()
        args, _warnings = build_run_args(scratch_instance)
        assert "--env-file" not in args

    def test_wayland_warning_when_socket_missing(self, scratch_instance):
        scratch_instance.config.wayland = True
        with patch("os.path.exists", return_value=False):
            args, warnings = build_run_args(scratch_instance)
        assert any("Wayland" in w for w in warnings)
        assert "WAYLAND_DISPLAY=wayland-0" not in args

    def test_ssh_warning_when_sock_missing(self, scratch_instance):
        scratch_instance.config.ssh = True
        with patch.dict(os.environ, {"SSH_AUTH_SOCK": ""}, clear=False):
            args, warnings = build_run_args(scratch_instance)
        assert any("SSH" in w for w in warnings)

    def test_podman_socket_mounted_when_present(self, scratch_instance):
        scratch_instance.config.podman = True
        sock = "/run/user/4242/podman/podman.sock"
        with (
            patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/4242"}),
            patch("os.path.exists", side_effect=lambda p: p == sock or _real_exists(p)),
        ):
            args, warnings = build_run_args(scratch_instance)
        assert f"{sock}:{sock}" in args
        assert f"CONTAINER_HOST=unix://{sock}" in args
        assert f"DOCKER_HOST=unix://{sock}" in args
        assert not any("odman" in w for w in warnings)

    def test_podman_socket_mounted_when_present_root(self, scratch_instance):
        scratch_instance.config.podman = True
        sock = "/run/user/4242/podman/podman.sock"
        with (
            patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/4242"}),
            patch("os.path.exists", side_effect=lambda p: p == sock or _real_exists(p)),
        ):
            args, _warnings = build_run_args(scratch_instance, root=True)
        assert f"{sock}:{sock}" in args
        assert "--userns=keep-id" not in args

    def test_podman_socket_falls_back_to_uid_path(self, scratch_instance):
        scratch_instance.config.podman = True
        sock = f"/run/user/{os.getuid()}/podman/podman.sock"
        env = {k: v for k, v in os.environ.items() if k != "XDG_RUNTIME_DIR"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch("os.path.exists", side_effect=lambda p: p == sock or _real_exists(p)),
        ):
            args, _warnings = build_run_args(scratch_instance)
        assert f"{sock}:{sock}" in args

    def test_podman_warning_when_socket_missing(self, scratch_instance):
        scratch_instance.config.podman = True
        with patch("os.path.exists", side_effect=lambda p: False if "podman.sock" in str(p) else _real_exists(p)):
            args, warnings = build_run_args(scratch_instance)
        assert any("systemctl --user enable --now podman.socket" in w for w in warnings)
        assert not any("podman.sock" in a for a in args)
        assert not any(a.startswith(("CONTAINER_HOST=", "DOCKER_HOST=")) for a in args)

    def test_podman_disabled_adds_nothing(self, scratch_instance):
        assert scratch_instance.config.podman is False
        args, warnings = build_run_args(scratch_instance)
        assert not any("podman.sock" in a for a in args)
        assert not any(a.startswith(("CONTAINER_HOST=", "DOCKER_HOST=")) for a in args)
        assert not any("odman" in w for w in warnings)

    def test_scratch_instance_has_host_mounts(self, scratch_instance):
        args, _warnings = build_run_args(scratch_instance)
        assert "/usr:/usr:ro" in args
        assert "/etc:/etc:ro" in args

    def test_scratch_instance_has_tmpfs(self, scratch_instance):
        """Scratch instances get tmpfs mounts for /tmp and /root with mode=1777."""
        args, _warnings = build_run_args(scratch_instance)
        # Collect all tmpfs targets
        tmpfs_targets = [args[i + 1] for i in range(len(args)) if args[i] == "--tmpfs" and i + 1 < len(args)]
        assert "/tmp:rw,nosuid,nodev,mode=1777" in tmpfs_targets
        assert "/root:rw,nosuid,nodev,mode=1777" in tmpfs_targets

    def test_scratch_root_has_single_root_mount(self, scratch_instance):
        """root=True: /root is only the home bind mount, no /root tmpfs."""
        args, _warnings = build_run_args(scratch_instance, root=True)
        dests = _mount_destinations(args)
        assert dests.count("/root") == 1
        assert f"{scratch_instance.home_dir}:/root" in args
        tmpfs_dests = [d.split(":")[0] for d in _flag_values(args, "--tmpfs")]
        assert "/root" not in tmpfs_dests
        assert "/tmp" in tmpfs_dests

    def test_scratch_non_root_keeps_root_tmpfs(self, scratch_instance):
        args, _warnings = build_run_args(scratch_instance, root=False)
        tmpfs_dests = [d.split(":")[0] for d in _flag_values(args, "--tmpfs")]
        assert "/root" in tmpfs_dests
        assert "/tmp" in tmpfs_dests

    def test_fedora_root_unchanged(self, fedora_instance):
        args, _warnings = build_run_args(fedora_instance, root=True)
        assert f"{fedora_instance.home_dir}:/root" in args
        assert "--tmpfs" not in args
        assert _mount_destinations(args).count("/root") == 1

    def test_no_duplicate_mount_destinations(self, scratch_instance, fedora_instance):
        """Podman rejects duplicate destinations across -v and --tmpfs."""
        for inst in (scratch_instance, fedora_instance):
            for root in (False, True):
                args, _warnings = build_run_args(inst, root=root)
                dests = _mount_destinations(args)
                assert len(dests) == len(set(dests)), (inst.name, root, dests)

    def test_fedora_instance_no_tmpfs(self, fedora_instance):
        """Fedora instances do not get tmpfs mounts (they have their own)."""
        args, _warnings = build_run_args(fedora_instance)
        assert "--tmpfs" not in args

    def test_fedora_instance_no_host_mounts(self, fedora_instance):
        args, _warnings = build_run_args(fedora_instance)
        assert "/usr:/usr:ro" not in args

    def test_scratch_instance_env(self, scratch_instance):
        """Verify SCRATCH_INSTANCE env var is set."""
        args, _warnings = build_run_args(scratch_instance)
        env_args = [args[i + 1] for i in range(len(args)) if args[i] == "-e" and i + 1 < len(args)]
        assert "SCRATCH_INSTANCE=myinstance" in env_args  # fixture name

    def test_shared_volume_warning_when_missing(self, scratch_instance):
        scratch_instance.config.shared = ["nonexistent"]
        args, warnings = build_run_args(scratch_instance)
        assert any("nonexistent" in w for w in warnings)


# ─── build_run_args X11 ─────────────────────────────────────────────────────


class TestBuildRunArgsX11:
    """Tests for X11 display socket sharing in build_run_args."""

    def test_x11_socket_and_display_detected(self, scratch_instance, tmp_path):
        """X11 mounts DISPLAY, socket dir, and Xauthority when all present."""
        scratch_instance.config.x11 = True
        xauth_path = str(tmp_path / ".Xauthority")

        def mock_isdir(path):
            if path == "/tmp/.X11-unix":
                return True
            return _real_isdir(path)

        def mock_exists(path):
            if str(path) == xauth_path:
                return True
            return _real_exists(path)

        def mock_expanduser(path):
            if path == "~/.Xauthority":
                return xauth_path
            return _real_expanduser(path)

        with (
            patch.dict(os.environ, {"DISPLAY": ":0", "USER": "testuser"}, clear=False),
            patch("scratch_monkey.run_args.os.path.isdir", side_effect=mock_isdir),
            patch("scratch_monkey.run_args.os.path.exists", side_effect=mock_exists),
            patch("scratch_monkey.run_args.os.path.expanduser", side_effect=mock_expanduser),
        ):
            os.environ.pop("XAUTHORITY", None)
            os.environ["DISPLAY"] = ":0"
            args, warnings = build_run_args(scratch_instance, is_fedora=False)

        assert "-v" in args
        assert "/tmp/.X11-unix:/tmp/.X11-unix:ro" in args
        assert "DISPLAY=:0" in args
        assert f"{xauth_path}:/tmp/.container-Xauthority:ro" in args
        assert "XAUTHORITY=/tmp/.container-Xauthority" in args
        assert len(warnings) == 0

    def test_x11_missing_display(self, scratch_instance):
        """Warning when DISPLAY is empty."""
        scratch_instance.config.x11 = True
        with patch.dict(os.environ, {"DISPLAY": ""}, clear=False):
            args, warnings = build_run_args(scratch_instance)
        assert any("x11" in w.lower() for w in warnings)
        assert "/tmp/.X11-unix:/tmp/.X11-unix:ro" not in args

    def test_x11_missing_socket_dir(self, scratch_instance):
        """Warning when X11 socket dir does not exist."""
        scratch_instance.config.x11 = True

        def mock_isdir(path):
            if path == "/tmp/.X11-unix":
                return False
            return _real_isdir(path)

        with (
            patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False),
            patch("scratch_monkey.run_args.os.path.isdir", side_effect=mock_isdir),
        ):
            args, warnings = build_run_args(scratch_instance)
        assert any("x11" in w.lower() for w in warnings)
        assert "/tmp/.X11-unix:/tmp/.X11-unix:ro" not in args

    def test_x11_missing_xauthority(self, scratch_instance, tmp_path):
        """Socket and DISPLAY mounted but no XAUTHORITY when file missing."""
        scratch_instance.config.x11 = True
        xauth_path = str(tmp_path / ".Xauthority-nonexistent")

        def mock_isdir(path):
            if path == "/tmp/.X11-unix":
                return True
            return _real_isdir(path)

        def mock_exists(path):
            if str(path) == xauth_path:
                return False
            return _real_exists(path)

        def mock_expanduser(path):
            if path == "~/.Xauthority":
                return xauth_path
            return _real_expanduser(path)

        with (
            patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False),
            patch("scratch_monkey.run_args.os.path.isdir", side_effect=mock_isdir),
            patch("scratch_monkey.run_args.os.path.exists", side_effect=mock_exists),
            patch("scratch_monkey.run_args.os.path.expanduser", side_effect=mock_expanduser),
        ):
            os.environ.pop("XAUTHORITY", None)
            args, warnings = build_run_args(scratch_instance, is_fedora=False)

        assert "/tmp/.X11-unix:/tmp/.X11-unix:ro" in args
        assert "DISPLAY=:0" in args
        # No XAUTHORITY mount or env
        assert "/tmp/.container-Xauthority:ro" not in " ".join(args)
        assert "XAUTHORITY=/tmp/.container-Xauthority" not in args

    def test_x11_and_gpu_combined(self, scratch_instance):
        """Both X11 and GPU args present when both enabled."""
        scratch_instance.config.x11 = True
        scratch_instance.config.gpu = True

        def mock_isdir(path):
            if path == "/tmp/.X11-unix":
                return True
            return _real_isdir(path)

        def mock_exists(path):
            if str(path).endswith(".Xauthority"):
                return False
            return _real_exists(path)

        def mock_expanduser(path):
            if path == "~/.Xauthority":
                return "/home/testuser/.Xauthority"
            return _real_expanduser(path)

        with (
            patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False),
            patch("scratch_monkey.run_args.os.path.isdir", side_effect=mock_isdir),
            patch("scratch_monkey.run_args.os.path.exists", side_effect=mock_exists),
            patch("scratch_monkey.run_args.os.path.expanduser", side_effect=mock_expanduser),
            patch("scratch_monkey.run_args.gpu_devices", return_value=["/dev/dri"]),
        ):
            os.environ.pop("XAUTHORITY", None)
            args, warnings = build_run_args(scratch_instance, is_fedora=False)

        # X11 mounts present
        assert "/tmp/.X11-unix:/tmp/.X11-unix:ro" in args
        assert "DISPLAY=:0" in args
        # GPU device present
        assert "--device" in args
        assert "/dev/dri" in args
