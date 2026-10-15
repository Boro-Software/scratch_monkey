"""Desktop integration helpers (terminal, editor, file manager) for the GUI."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _find_terminal() -> list[str]:
    """Return a command prefix to launch a terminal emulator.

    Tries xdg-terminal-exec first (standard portable method), then
    common terminals. Returns an empty list if none are found.
    """
    candidates = [
        (["xdg-terminal-exec"], []),
        (["gnome-terminal"], ["--"]),
        (["konsole"], ["-e"]),
        (["xfce4-terminal"], ["-e"]),
        (["alacritty"], ["-e"]),
        (["kitty"], []),
        (["foot"], []),
        (["xterm"], ["-e"]),
    ]
    for base, sep in candidates:
        if shutil.which(base[0]):
            return base + sep
    return []


def _launch_in_terminal(cmd: list[str]) -> str:
    """Launch cmd in a terminal emulator. Returns an error string or ''."""
    prefix = _find_terminal()
    if not prefix:
        return "No terminal emulator found. Install xdg-terminal-exec, gnome-terminal, or similar."
    subprocess.Popen([*prefix, *cmd])
    return ""


def _open_in_editor(file_path: Path) -> str:
    """Open a file in the user's editor. Returns an error string or ''."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR", "vi")
    return _launch_in_terminal([editor, str(file_path)])


def _open_file_manager(path: str) -> str:
    """Open a directory in the file manager via xdg-open. Returns error string or ''."""
    if not shutil.which("xdg-open"):
        return "xdg-open not found. Cannot open file manager."
    subprocess.Popen(["xdg-open", path])
    return ""


def _open_terminal_at(path: str) -> str:
    """Launch a terminal emulator with cwd set to path. Returns error string or ''."""
    prefix = _find_terminal()
    if not prefix:
        return "No terminal emulator found. Install xdg-terminal-exec, gnome-terminal, or similar."
    subprocess.Popen(prefix, cwd=path)
    return ""
