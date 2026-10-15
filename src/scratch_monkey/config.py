"""TOML config read/write for scratch-monkey instances."""

from __future__ import annotations

import os
import re
import secrets
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Default location for instance directories
DEFAULT_INSTANCES_DIR: Path = Path.home() / "scratch-monkey"

# Valid instance name: starts with alphanum, then alphanum/underscore/dot/dash
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$")


class ConfigError(Exception):
    """Raised for config validation or parse errors."""


def generate_overlay_id() -> str:
    """Generate a unique overlay container ID like 'sm-a1b2c3d4'."""
    return f"sm-{secrets.token_hex(4)}"


# Volume modes scratch-monkey knows about.  ``overlay`` is translated into a
# podman overlay mount (see overlay_mounts.py); anything else, known or not,
# is handed to podman verbatim.
VOLUME_MODE_OVERLAY = "overlay"
KNOWN_VOLUME_MODES = ("ro", "rw", VOLUME_MODE_OVERLAY)


def split_volume_spec(spec: str) -> tuple[str, str, str]:
    """Split ``host:container[:mode]`` into ``(host, container, mode)``.

    ``mode`` is "" when absent.  Anything after a third colon is ignored here
    (it only matters to podman).  Raises ConfigError on a malformed spec.
    """
    validate_volume_spec(spec)
    parts = spec.split(":")
    return parts[0], parts[1], parts[2] if len(parts) > 2 else ""


def validate_volume_spec(spec: str) -> None:
    """Raise ConfigError if a volume spec has empty host or container path.

    Accepts ``host:container`` or ``host:container:mode`` formats.  The mode is
    deliberately not checked here: ``ro``, ``rw`` and ``overlay`` are known, and
    any other mode is passed through to podman unchanged (which rejects it).
    """
    parts = spec.split(":")
    if len(parts) < 2:
        raise ConfigError(f"Invalid volume spec {spec!r}: expected host:container[:mode]")
    host, container = parts[0], parts[1]
    if not host:
        raise ConfigError(f"Invalid volume spec {spec!r}: host path is empty")
    if not container:
        raise ConfigError(f"Invalid volume spec {spec!r}: container path is empty")


def validate_name(name: str) -> None:
    """Raise ConfigError if name is not a valid instance name."""
    if not _NAME_RE.match(name):
        raise ConfigError(
            f"Invalid instance name {name!r}. "
            "Must start with alphanumeric and contain only "
            "alphanumeric, underscore, dot, or dash."
        )


@dataclass
class InstanceConfig:
    """Typed representation of a scratch.toml instance config."""

    cmd: str = "/bin/bash"
    wayland: bool = False
    x11: bool = False
    ssh: bool = False
    podman: bool = False
    home: str = ""
    volumes: list[str] = field(default_factory=list)
    env: list[str] = field(default_factory=list)
    shared: list[str] = field(default_factory=list)
    overlay: bool = False
    sudo: bool = True
    gpu: bool = False
    devices: list[str] = field(default_factory=list)
    overlay_id: str = ""


def load(path: Path) -> InstanceConfig:
    """Parse a scratch.toml file and return an InstanceConfig.

    Missing keys use their dataclass defaults.
    """
    path = Path(path)
    if not path.exists():
        return InstanceConfig()

    with path.open("rb") as f:
        data = tomllib.load(f)

    def _bool(val: object) -> bool:
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ("true", "1", "yes")
        return bool(val)

    def _strlist(val: object) -> list[str]:
        if isinstance(val, list):
            return [str(v) for v in val]
        return []

    config = InstanceConfig(
        cmd=str(data.get("cmd", "/bin/bash")),
        wayland=_bool(data.get("wayland", False)),
        x11=_bool(data.get("x11", False)),
        ssh=_bool(data.get("ssh", False)),
        podman=_bool(data.get("podman", False)),
        home=str(data.get("home", "")),
        volumes=_strlist(data.get("volumes", [])),
        env=_strlist(data.get("env", [])),
        shared=_strlist(data.get("shared", [])),
        overlay=_bool(data.get("overlay", False)),
        sudo=_bool(data.get("sudo", True)),
        gpu=_bool(data.get("gpu", False)),
        devices=_strlist(data.get("devices", [])),
        overlay_id=str(data.get("overlay_id", "")),
    )

    # Validate home path if set
    if config.home:
        if not os.path.isabs(config.home):
            raise ConfigError(
                f"home path must be absolute, got: {config.home!r}"
            )
        if ".." in Path(config.home).parts:
            raise ConfigError(
                f"home path must not contain '..': {config.home!r}"
            )

    # Validate volume specs
    for vol in config.volumes:
        validate_volume_spec(vol)

    return config


def save(path: Path, config: InstanceConfig) -> None:
    """Atomically write an InstanceConfig to a scratch.toml file.

    Writes to a temp file in the same directory, then renames.
    """
    # Validate volume specs before persisting
    for vol in config.volumes:
        validate_volume_spec(vol)

    path = Path(path)
    content = _serialize(config)
    dir_ = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=dir_, prefix=".scratch-monkey-", suffix=".toml.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _serialize(config: InstanceConfig) -> str:
    """Convert an InstanceConfig to TOML text."""
    lines = ["# scratch-monkey instance configuration\n"]

    def _toml_str(s: str) -> str:
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def _toml_strlist(lst: list[str]) -> str:
        items = ", ".join(_toml_str(v) for v in lst)
        return f"[{items}]"

    lines.append(f"cmd = {_toml_str(config.cmd)}\n")
    lines.append(f"wayland = {str(config.wayland).lower()}\n")
    lines.append(f"x11 = {str(config.x11).lower()}\n")
    lines.append(f"ssh = {str(config.ssh).lower()}\n")
    lines.append(f"podman = {str(config.podman).lower()}\n")

    if config.home:
        lines.append(f"home = {_toml_str(config.home)}\n")
    else:
        lines.append('home = ""\n')

    lines.append(f"volumes = {_toml_strlist(config.volumes)}\n")
    lines.append(f"env = {_toml_strlist(config.env)}\n")
    lines.append(f"shared = {_toml_strlist(config.shared)}\n")
    lines.append(f"overlay = {str(config.overlay).lower()}\n")
    lines.append(f"sudo = {str(config.sudo).lower()}\n")
    lines.append(f"gpu = {str(config.gpu).lower()}\n")
    lines.append(f"devices = {_toml_strlist(config.devices)}\n")
    if config.overlay_id:
        lines.append(f"overlay_id = {_toml_str(config.overlay_id)}\n")

    return "".join(lines)
