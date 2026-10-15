"""Host-side tool actions: export, editors, file manager and terminal launchers."""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from ...export import ExportError
from ...export import export_command as export_command_fn
from ...instance import Instance
from ..desktop import (
    _find_terminal,
    _open_file_manager,
    _open_in_editor,
    _open_terminal_at,
)

if TYPE_CHECKING:
    from ..models.app import AppModel


class ToolsController:
    """Export commands and launch editors / file manager / terminals on the host."""

    def __init__(self, app: AppModel) -> None:
        self._app = app

    def open_directory(self, path: str) -> None:
        """Open a directory in the file manager."""
        app = self._app
        err = _open_file_manager(path)
        if err:
            app.status_message = err
        else:
            app.status_message = f"Opened file manager at {path}"

    def open_terminal(self, path: str) -> None:
        """Open a terminal at the given directory."""
        app = self._app
        err = _open_terminal_at(path)
        if err:
            app.status_message = err
        else:
            app.status_message = f"Opened terminal at {path}"

    def export_command(self, name: str, cmd: str, bin_name: str = "") -> str:
        """Export a command from an instance. Returns '' on success or error string."""
        app = self._app
        inst_dir = Path(app.instances_dir) / name
        if not inst_dir.is_dir():
            return f"Instance {name!r} not found"
        inst = Instance.from_directory(inst_dir)
        try:
            path = export_command_fn(inst, cmd, bin_name=bin_name)
            app.status_message = f"Exported {cmd!r} from {name!r} to {path}"
        except (ExportError, OSError, RuntimeError) as e:
            return str(e)
        return ""

    def edit_file(self, name: str, file_type: str) -> None:
        """Open an instance file in the user's editor."""
        app = self._app
        inst_dir = Path(app.instances_dir) / name
        if not inst_dir.is_dir():
            app.status_message = f"Instance {name!r} not found"
            return
        file_map = {
            "dockerfile": inst_dir / "Dockerfile",
            "env": inst_dir / ".env",
        }
        target = file_map.get(file_type)
        if target is None:
            app.status_message = f"Unknown file type {file_type!r}"
            return
        err = _open_in_editor(target)
        if err:
            app.status_message = err
        else:
            app.status_message = f"Editing {file_type} for {name!r}..."

    def edit_config(self, name: str) -> None:
        """Open scratch.toml in the user's editor, refreshing config on close.

        The editor-wait thread stays here (not in AppModel) because it blocks on
        the editor subprocess, not on podman; the reload it triggers goes through
        AppModel.defer / AppModel.reload_when_idle like everything else.
        """
        app = self._app
        inst_dir = Path(app.instances_dir) / name
        if not inst_dir.is_dir():
            app.status_message = f"Instance {name!r} not found"
            return
        target = inst_dir / "scratch.toml"
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR", "vi")
        prefix = _find_terminal()
        if not prefix:
            app.status_message = (
                "No terminal emulator found. Install xdg-terminal-exec, "
                "gnome-terminal, or similar."
            )
            return
        proc = subprocess.Popen([*prefix, editor, str(target)])
        app.status_message = f"Editing config for {name!r}..."

        def _wait_and_refresh():
            proc.wait()
            # Reload in the background; deferred if another operation is in flight.
            app.defer(lambda: app.reload_when_idle(f"Reloaded config for {name!r}"))

        threading.Thread(target=_wait_and_refresh, daemon=True).start()
