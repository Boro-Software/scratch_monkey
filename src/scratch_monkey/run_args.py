"""Shared run arguments, constants, and helpers for scratch-monkey."""

from __future__ import annotations

import os
import socket
from pathlib import Path

from .config import ConfigError, validate_volume_spec
from .instance import Instance, is_fedora_based
from .shared import parse_shared_entry

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_BASE_IMAGE = "scratch_monkey"
FEDORA_IMAGE = "scratch_monkey_fedora"

# Path to this package's project directory (where Dockerfiles live)
PROJECT_DIR = Path(__file__).parent.parent.parent


def podman_socket_path() -> str:
    """Return the host's rootless podman API socket path.

    Prefers ``$XDG_RUNTIME_DIR/podman/podman.sock``; falls back to
    ``/run/user/<uid>/podman/podman.sock``.
    """
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "") or f"/run/user/{os.getuid()}"
    return f"{runtime_dir}/podman/podman.sock"


def short_hostname() -> str:
    """Return the short (non-FQDN) hostname."""
    return socket.gethostname().split(".")[0]


def nvidia_cdi_available() -> bool:
    """Check if NVIDIA Container Device Interface is configured."""
    return os.path.exists("/etc/cdi/nvidia.yaml")


def gpu_devices(*, use_cdi: bool = True) -> list[str]:
    """Detect available GPU device paths.

    When use_cdi is True (default), returns CDI device specs (e.g.
    "nvidia.com/gpu=all") when NVIDIA CDI is available.  When False,
    always returns raw /dev paths — appropriate for scratch instances
    where host /usr and /etc are already mounted and CDI file injection
    is unnecessary (and can fail on stale CDI specs).
    """
    devices = []

    # Prefer NVIDIA CDI — it handles device nodes, driver libs, and symlinks.
    # Skip CDI for scratch instances: host mounts already provide driver files.
    if use_cdi and nvidia_cdi_available() and os.path.exists("/dev/nvidia0"):
        devices.append("nvidia.com/gpu=all")
    else:
        # Manual NVIDIA device passthrough (no driver libs injected)
        for name in ("nvidia0", "nvidiactl", "nvidia-modeset", "nvidia-uvm", "nvidia-uvm-tools"):
            path = f"/dev/{name}"
            if os.path.exists(path):
                devices.append(path)

    if os.path.exists("/dev/dri"):
        devices.append("/dev/dri")
    if os.path.exists("/dev/kfd"):
        devices.append("/dev/kfd")

    return devices


def build_run_args(
    instance: Instance,
    *,
    is_fedora: bool | None = None,
    root: bool = False,
) -> tuple[list[str], list[str]]:
    """Build the podman run arguments for a container.

    Shared between overlay mode (overlay.py) and direct-run mode (cli/main.py).
    Returns (args, warnings) where warnings is a list of human-readable messages.
    """
    cfg = instance.config
    user = os.environ.get("USER", "user")
    warnings: list[str] = []

    if root:
        container_home = "/root"
    else:
        container_home = f"/home/{user}"

    if is_fedora is None:
        is_fedora = is_fedora_based(instance.directory)

    args = [
        "--security-opt", "label=disable",
        "--network", "host",
        "--hostname", f"{instance.name}.{short_hostname()}",
        "-e", f"HOME={container_home}",
        "-e", f"USER={user}",
        "-e", f"SCRATCH_INSTANCE={instance.name}",
        "-v", f"{instance.home_dir}:{container_home}",
    ]

    if not root:
        args.append("--userns=keep-id")

    # Host system mounts for scratch-based (non-fedora) instances
    if not is_fedora:
        args += [
            "-v", "/usr:/usr:ro",
            "-v", "/etc:/etc:ro",
            "-v", "/var/usrlocal:/var/usrlocal:ro",
            "-v", "/var/opt:/var/opt:ro",
            "-v", "/var/usrlocal:/usr/local:ro",
            "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777",
        ]
        # In root mode the home bind mount already provides /root; a second
        # mount there makes podman fail with "duplicate mount destination".
        if not root:
            args += ["--tmpfs", "/root:rw,nosuid,nodev,mode=1777"]

    # Wayland
    if cfg.wayland:
        uid = os.getuid()
        wayland_sock = f"/run/user/{uid}/wayland-0"
        if os.path.exists(wayland_sock):
            args += [
                "-v", f"{wayland_sock}:{wayland_sock}",
                "-e", "WAYLAND_DISPLAY=wayland-0",
                "-e", f"XDG_RUNTIME_DIR=/run/user/{uid}",
            ]
        else:
            warnings.append(f"Wayland socket not found at {wayland_sock}, skipping.")

    # X11
    if cfg.x11:
        display = os.environ.get("DISPLAY", "")
        x11_sock_dir = "/tmp/.X11-unix"
        xauth_file = os.environ.get("XAUTHORITY", os.path.expanduser("~/.Xauthority"))

        if display and os.path.isdir(x11_sock_dir):
            args += ["-v", f"{x11_sock_dir}:{x11_sock_dir}:ro"]
            args += ["-e", f"DISPLAY={display}"]
            if os.path.exists(xauth_file):
                container_xauth = "/tmp/.container-Xauthority"
                args += ["-v", f"{xauth_file}:{container_xauth}:ro"]
                args += ["-e", f"XAUTHORITY={container_xauth}"]
        else:
            warnings.append("x11 enabled but DISPLAY not set or X11 socket dir not found")

    # SSH
    if cfg.ssh:
        ssh_sock = os.environ.get("SSH_AUTH_SOCK", "")
        if ssh_sock and os.path.exists(ssh_sock):
            args += [
                "-v", f"{ssh_sock}:{ssh_sock}",
                "-e", f"SSH_AUTH_SOCK={ssh_sock}",
            ]
        else:
            warnings.append("SSH_AUTH_SOCK not set or socket missing, skipping.")

    # Podman socket passthrough (opt-in; grants host-level container access).
    # The socket is 0600 and owned by the host user.  Rootless --userns=keep-id
    # maps that user to itself, and root=True (no keep-id) maps container root
    # to the host user, so the socket is reachable either way.
    if cfg.podman:
        podman_sock = podman_socket_path()
        if os.path.exists(podman_sock):
            args += [
                "-v", f"{podman_sock}:{podman_sock}",
                "-e", f"CONTAINER_HOST=unix://{podman_sock}",
                "-e", f"DOCKER_HOST=unix://{podman_sock}",
            ]
        else:
            warnings.append(
                f"Podman socket not found at {podman_sock}, skipping. "
                "Enable it on the host with: systemctl --user enable --now podman.socket"
            )

    # .env file
    env_file = instance.directory / ".env"
    if env_file.exists():
        args += ["--env-file", str(env_file)]

    # Extra volumes
    for vol in cfg.volumes:
        try:
            validate_volume_spec(vol)
        except ConfigError:
            warnings.append(f"Skipping invalid volume spec {vol!r}: empty host or container path.")
            continue
        args += ["-v", vol]

    # Shared volumes
    instances_dir = instance.directory.parent
    for shared_entry in cfg.shared:
        shared_name, mode = parse_shared_entry(shared_entry)
        shared_path = instances_dir / ".shared" / shared_name
        if shared_path.is_dir():
            mount_spec = f"{shared_path}:/shared/{shared_name}"
            if mode == "ro":
                mount_spec += ":ro"
            args += ["-v", mount_spec]
        else:
            warnings.append(f"Shared volume {shared_name!r} not found, skipping.")

    # Extra env vars
    for var in cfg.env:
        args += ["-e", var]

    # GPU passthrough
    if cfg.gpu:
        devs = gpu_devices(use_cdi=is_fedora)
        for dev in devs:
            args += ["--device", dev]
        if not devs:
            warnings.append("GPU enabled but no GPU devices found on host.")
        elif "/dev/dri" in devs:
            # Check the render node, not the directory — /dev/dri is typically
            # root-owned 755 but the device nodes inside have their own perms.
            render_node = "/dev/dri/renderD128"
            if os.path.exists(render_node) and not os.access(render_node, os.R_OK | os.W_OK):
                warnings.append(
                    "GPU enabled but /dev/dri/renderD128 is not accessible. "
                    "Add your user to the 'video' and 'render' groups: "
                    "sudo usermod -aG video,render $USER"
                )

    # Extra devices
    for dev in cfg.devices:
        args += ["--device", dev]

    return args, warnings
