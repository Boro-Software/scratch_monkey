"""Observable model for a single scratch-monkey instance."""

from __future__ import annotations

from pathlib import Path

try:
    from atom.api import Atom, Bool, List, Str, Value, observe
except ImportError:
    raise ImportError(
        "The GUI requires the 'enaml' package. "
        "Install it with: uv tool install 'scratch-monkey[gui]'"
    )

from ...config import InstanceConfig, save
from ...instance import InstanceInfo, is_fedora_based
from .entries import SharedVolumeEntry, VolumeMountEntry


class InstanceModel(Atom):
    """Observable model for a single scratch-monkey instance."""

    name = Str()
    directory = Str()
    image_built = Bool(False)
    overlay_running = Bool(False)
    overlay_id = Str("")
    base_image = Str("")
    # Non-empty when podman could not be queried for this instance (status unknown).
    status_error = Str("")

    # Config fields
    cmd = Str("/bin/bash")
    wayland = Bool(False)
    ssh = Bool(False)
    podman = Bool(False)
    x11 = Bool(False)
    home = Str("")
    overlay = Bool(False)
    sudo = Bool(True)
    is_fedora = Bool(False)
    gpu = Bool(False)
    devices = List(str)
    shared = List(str)
    volumes = List(str)
    env_vars = List(str)
    volume_entries = List(VolumeMountEntry)
    shared_entries = List(SharedVolumeEntry)

    dirty = Bool(False)
    _saved_config = Value()

    @classmethod
    def from_info(cls, info: InstanceInfo) -> InstanceModel:
        m = cls()
        m.name = info.name
        m.directory = str(info.directory)
        m.image_built = info.image_built
        m.overlay_running = info.overlay_running
        m.overlay_id = info.config.overlay_id
        m.base_image = info.base_image or ""
        m.status_error = info.status_error or ""
        cfg = info.config
        m.cmd = cfg.cmd
        m.wayland = cfg.wayland
        m.ssh = cfg.ssh
        m.podman = cfg.podman
        m.x11 = cfg.x11
        m.home = cfg.home
        m.overlay = cfg.overlay
        m.sudo = cfg.sudo
        m.is_fedora = is_fedora_based(Path(info.directory))
        m.gpu = cfg.gpu
        m.devices = list(cfg.devices)
        m.shared = list(cfg.shared)
        m.volumes = list(cfg.volumes)
        m.env_vars = list(cfg.env)
        m.volume_entries = [VolumeMountEntry.from_spec(v) for v in cfg.volumes]
        m._saved_config = m.to_config()
        return m

    def to_config(self) -> InstanceConfig:
        return InstanceConfig(
            cmd=self.cmd,
            wayland=self.wayland,
            ssh=self.ssh,
            podman=self.podman,
            x11=self.x11,
            home=self.home,
            volumes=[
                e.to_spec() for e in self.volume_entries
                if e.host_path or e.container_path
            ],
            env=list(self.env_vars),
            shared=[
                f"{e.name}:{e.mode}" if e.mode != "rw" else e.name
                for e in self.shared_entries
                if e.enabled
            ],
            overlay=self.overlay,
            sudo=self.sudo,
            gpu=self.gpu,
            devices=list(self.devices),
        )

    @observe(
        'cmd', 'wayland', 'ssh', 'podman', 'x11', 'home', 'overlay', 'sudo', 'gpu', 'devices',
        'volume_entries', 'env_vars', 'shared_entries',
    )
    def _on_config_change(self, change):
        if self._saved_config is not None:
            self.dirty = (self.to_config() != self._saved_config)

    def check_dirty(self):
        """Recompute dirty flag. Call after in-place nested object mutations."""
        if self._saved_config is not None:
            self.dirty = (self.to_config() != self._saved_config)

    def revert(self):
        """Discard changes and restore to last saved config."""
        if self._saved_config is not None:
            cfg = self._saved_config
            self.cmd = cfg.cmd
            self.wayland = cfg.wayland
            self.ssh = cfg.ssh
            self.podman = cfg.podman
            self.x11 = cfg.x11
            self.home = cfg.home
            self.overlay = cfg.overlay
            self.sudo = cfg.sudo
            self.gpu = cfg.gpu
            self.devices = list(cfg.devices)
            self.env_vars = list(cfg.env)
            self.volume_entries = [VolumeMountEntry.from_spec(v) for v in cfg.volumes]
            self.shared = list(cfg.shared)
            self.dirty = False

    def add_volume_entry(self) -> None:
        """Append a new blank volume mount entry."""
        self.volume_entries = [*self.volume_entries, VolumeMountEntry()]

    def remove_volume_entry(self, index: int) -> None:
        """Remove volume entry at the given index."""
        entries = list(self.volume_entries)
        if 0 <= index < len(entries):
            del entries[index]
            self.volume_entries = entries

    def add_env_var(self, value: str = "") -> None:
        """Append a new environment variable entry."""
        self.env_vars = [*self.env_vars, value]

    def remove_env_var(self, index: int) -> None:
        """Remove env var at the given index."""
        entries = list(self.env_vars)
        if 0 <= index < len(entries):
            del entries[index]
            self.env_vars = entries

    def save(self) -> None:
        """Persist the current model state to scratch.toml."""
        config_path = Path(self.directory) / "scratch.toml"
        save(config_path, self.to_config())
        self._saved_config = self.to_config()
        self.dirty = False
