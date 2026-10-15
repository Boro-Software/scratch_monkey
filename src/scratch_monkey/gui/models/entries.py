"""Volume entry models for the scratch-monkey GUI."""

from __future__ import annotations

try:
    from atom.api import Atom, Bool, Str
except ImportError:
    raise ImportError(
        "The GUI requires the 'enaml' package. "
        "Install it with: uv tool install 'scratch-monkey[gui]'"
    )


class VolumeMountEntry(Atom):
    """Structured representation of a volume mount string like '/host:/container:ro'."""

    host_path = Str()
    container_path = Str()
    mode = Str("ro")

    @classmethod
    def from_spec(cls, spec: str) -> VolumeMountEntry:
        """Parse a volume spec string like '/host:/container' or '/host:/container:ro'."""
        parts = spec.split(":")
        if len(parts) == 3:
            return cls(host_path=parts[0], container_path=parts[1], mode=parts[2])
        elif len(parts) == 2:
            return cls(host_path=parts[0], container_path=parts[1], mode="ro")
        else:
            return cls(host_path=spec, container_path=spec, mode="ro")

    def to_spec(self) -> str:
        """Serialize back to a volume spec string. Always includes the mode suffix."""
        return f"{self.host_path}:{self.container_path}:{self.mode}"


class SharedVolumeEntry(Atom):
    """A shared volume with enabled/disabled state and mode."""

    name = Str()
    enabled = Bool(False)
    mode = Str("rw")
