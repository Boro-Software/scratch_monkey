"""Export/unexport commands from scratch-monkey instances to ~/.local/bin."""

from __future__ import annotations

import os
import shlex
import stat
import sys
from pathlib import Path
from string import Template

from .config import InstanceConfig
from .instance import Instance
from .run_args import short_hostname


class ExportError(Exception):
    """Raised for export/unexport errors."""


_EXPORT_MAGIC = "# scratch-monkey export"

_SHADOW_WARN_COMMANDS = frozenset({
    "bash", "cat", "cd", "chmod", "chown", "cp", "curl", "diff", "echo",
    "env", "find", "git", "grep", "kill", "less", "ln", "ls", "man",
    "mkdir", "more", "mv", "pip", "podman", "ps", "python", "python3",
    "rm", "rmdir", "sed", "sh", "ssh", "sudo", "tail", "tar", "tee",
    "touch", "uv", "vi", "vim", "wget", "which", "zsh",
})

_SCRIPT_TEMPLATE = Template(
    """\
#!/bin/sh
# scratch-monkey export
# instance: ${instance_name}
# command: ${cmd}
if [ "$${SCRATCH_INSTANCE:-}" = "${instance_name}" ]; then
    exec ${cmd} "$$@"
fi
${display_setup}if podman container inspect "${overlay_name}" \\
    --format '{{.State.Status}}' 2>/dev/null | grep -q '^running$$'; then
    if [ -t 1 ]; then
        exec podman exec -it $$_dexec "${overlay_name}" ${cmd} "$$@"
    else
        exec podman exec -i $$_dexec "${overlay_name}" ${cmd} "$$@"
    fi
fi
if podman image exists "${instance_name}" 2>/dev/null; then
    _image="${instance_name}"
else
    _image="${base_image}"
fi
if [ -t 1 ]; then _tty="-t"; else _tty=""; fi
exec podman run --rm -i $$_tty \\
    --security-opt label=disable \\
    --network=host \\
    --hostname "${hostname}" \\
    -e HOME="${container_home}" \\
    -e USER="${username}" \\
    -e SCRATCH_INSTANCE="${instance_name}" \\
    -v "${home_dir}:${container_home}" \\
    $$_drun \\
    "$$_image" ${cmd} "$$@"
"""
)


def _build_display_setup(cfg: InstanceConfig) -> str:
    """Build a shell snippet that sets ``_dexec`` and ``_drun`` variables.

    ``_dexec`` holds ``-e`` flags for ``podman exec`` (env vars only —
    sockets are already bind-mounted on the running overlay container).
    ``_drun`` holds both ``-v`` and ``-e`` flags for ``podman run``
    (fresh container, needs socket mounts *and* env vars).

    The generated code checks the host environment at invocation time so
    the forwarded values always reflect the current session.
    """
    lines: list[str] = ['_dexec=""', '_drun=""']

    if cfg.wayland:
        lines += [
            '_uid="$(id -u)"',
            'if [ -S "/run/user/$_uid/wayland-0" ]; then',
            '    _dexec="$_dexec -e WAYLAND_DISPLAY=wayland-0 -e XDG_RUNTIME_DIR=/run/user/$_uid"',
            '    _drun="$_drun -v /run/user/$_uid/wayland-0:/run/user/$_uid/wayland-0'
            ' -e WAYLAND_DISPLAY=wayland-0 -e XDG_RUNTIME_DIR=/run/user/$_uid"',
            "fi",
        ]

    if cfg.x11:
        lines += [
            'if [ -n "${DISPLAY:-}" ]; then',
            '    _dexec="$_dexec -e DISPLAY=$DISPLAY"',
            '    _drun="$_drun -e DISPLAY=$DISPLAY"',
            '    if [ -d "/tmp/.X11-unix" ]; then',
            '        _drun="$_drun -v /tmp/.X11-unix:/tmp/.X11-unix:ro"',
            "    fi",
            '    _xauth="${XAUTHORITY:-$HOME/.Xauthority}"',
            '    if [ -f "$_xauth" ]; then',
            '        _dexec="$_dexec -e XAUTHORITY=/tmp/.container-Xauthority"',
            '        _drun="$_drun -v $_xauth:/tmp/.container-Xauthority:ro'
            ' -e XAUTHORITY=/tmp/.container-Xauthority"',
            "    fi",
            "fi",
        ]

    if cfg.ssh:
        lines += [
            'if [ -n "${SSH_AUTH_SOCK:-}" ] && [ -S "$SSH_AUTH_SOCK" ]; then',
            '    _dexec="$_dexec -e SSH_AUTH_SOCK=$SSH_AUTH_SOCK"',
            '    _drun="$_drun -v $SSH_AUTH_SOCK:$SSH_AUTH_SOCK -e SSH_AUTH_SOCK=$SSH_AUTH_SOCK"',
            "fi",
        ]

    if cfg.podman:
        # Overlay containers already carry the socket mount and env (set at
        # creation), so only the fresh ``podman run`` path needs them here.
        lines += [
            '_psock="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/podman/podman.sock"',
            'if [ -S "$_psock" ]; then',
            '    _drun="$_drun -v $_psock:$_psock -e CONTAINER_HOST=unix://$_psock'
            ' -e DOCKER_HOST=unix://$_psock"',
            "fi",
        ]

    return "\n".join(lines) + "\n"


def export_command(
    instance: Instance,
    cmd: str,
    bin_name: str = "",
    base_image: str = "scratch_monkey",
    bin_dir: Path | None = None,
) -> Path:
    """Generate a wrapper script for a command in an instance.

    The script is placed in bin_dir (default: ~/.local/bin).
    Returns the path of the created script.
    """
    if not bin_name:
        bin_name = Path(cmd).name
    if not bin_name:
        raise ExportError(f"Cannot derive binary name from command: {cmd!r}")
    if "/" in bin_name or bin_name in (".", ".."):
        raise ExportError(f"Invalid binary name: {bin_name!r}")

    if bin_name in _SHADOW_WARN_COMMANDS:
        print(
            f"Warning: '{bin_name}' shadows a common system command. "
            f"This export will take precedence over the real '{bin_name}' in your PATH.",
            file=sys.stderr,
        )

    if bin_dir is None:
        bin_dir = Path.home() / ".local" / "bin"
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)

    out_path = bin_dir / bin_name
    username = os.environ.get("USER", "user")
    container_home = f"/home/{username}"
    hostname = f"{instance.name}.{short_hostname()}"

    content = _SCRIPT_TEMPLATE.substitute(
        instance_name=instance.name,
        overlay_name=instance.config.overlay_id or f"{instance.name}-overlay",
        cmd=shlex.quote(cmd),
        base_image=base_image,
        hostname=hostname,
        container_home=container_home,
        username=username,
        home_dir=str(instance.home_dir),
        display_setup=_build_display_setup(instance.config),
    )

    out_path.write_text(content)
    out_path.chmod(out_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP)
    return out_path


def unexport(bin_name: str, bin_dir: Path | None = None) -> None:
    """Remove an exported command from ~/.local/bin.

    Validates the magic comment before removing to avoid deleting arbitrary files.
    Raises ExportError if the file is missing or not a scratch-monkey export.
    """
    if "/" in bin_name or bin_name in (".", ".."):
        raise ExportError(f"Invalid binary name: {bin_name!r}")

    if bin_dir is None:
        bin_dir = Path.home() / ".local" / "bin"
    bin_dir = Path(bin_dir)
    out_path = bin_dir / bin_name

    if not out_path.exists():
        raise ExportError(f"No file found at {out_path}")

    content = out_path.read_text()
    if _EXPORT_MAGIC not in content:
        raise ExportError(
            f"{out_path} does not look like a scratch-monkey export, not removing."
        )

    out_path.unlink()


