"""Overlay container actions (start/stop/reset/refresh, terminal launchers)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ...instance import Instance, detect_base_image
from ...overlay import ensure_running as overlay_ensure_running
from ...overlay import refresh as overlay_refresh
from ...overlay import reset as overlay_reset
from ...run_args import DEFAULT_BASE_IMAGE
from ..desktop import _launch_in_terminal

if TYPE_CHECKING:
    from ..models.app import AppModel


class OverlayController:
    """Drive an instance's overlay container from the GUI."""

    def __init__(self, app: AppModel) -> None:
        self._app = app

    def _image_for(self, name: str, inst_dir: Path) -> str:
        """Per-instance build if one exists, else the base image.

        Runs a podman query, so call it from the worker thread only.
        """
        if self._app.runner.image_exists(name):
            return name
        return detect_base_image(inst_dir) or DEFAULT_BASE_IMAGE

    def enter(self, name: str, *, root: bool = False) -> None:
        """Open a terminal running scratch-monkey enter for the named instance."""
        app = self._app
        cmd = ["scratch-monkey", "enter", name]
        if root:
            cmd.append("--root")
        err = _launch_in_terminal(cmd)
        if err:
            app.status_message = err
        else:
            app.status_message = f"Opened terminal for {name!r}" + (" (root)" if root else "")

    def build(self, name: str) -> None:
        """Open a terminal running scratch-monkey build-instance."""
        app = self._app
        err = _launch_in_terminal(["scratch-monkey", "build-instance", name])
        if err:
            app.status_message = err
        else:
            app.status_message = f"Building {name!r} in terminal..."

    def start(self, name: str) -> None:
        """Start (or create) the overlay container for the named instance."""
        app = self._app
        inst_dir = Path(app.instances_dir) / name
        if not inst_dir.is_dir():
            app.status_message = f"Instance {name!r} not found"
            return
        inst = Instance.from_directory(inst_dir)

        def work():
            image = self._image_for(name, inst_dir)
            overlay_ensure_running(inst, app.runner, image)

        def on_success(_result):
            for m in app.instances:
                if m.name == name:
                    m.overlay_running = True
                    break
            app.status_message = f"Started {name!r}"

        def on_error(exc):
            app.status_message = f"Error starting {name!r}: {exc}"
            app.poll_status()

        app.run_async(f"Starting {name!r}...", work, on_success, on_error)

    def stop(self, name: str) -> None:
        """Stop the overlay container for the named instance."""
        app = self._app
        inst_model = next((i for i in app.instances if i.name == name), None)
        if inst_model is None:
            app.status_message = f"Instance {name!r} not found"
            return
        overlay_name = inst_model.overlay_id or f"{name}-overlay"

        def work():
            app.runner.stop(overlay_name)

        def on_success(_result):
            for inst in app.instances:
                if inst.name == name:
                    inst.overlay_running = False
                    break
            app.status_message = f"Stopped {name!r}"

        def on_error(exc):
            app.status_message = f"Error stopping {name!r}: {exc}"
            app.poll_status()

        app.run_async(f"Stopping {name!r}...", work, on_success, on_error)

    def reset(self, name: str) -> None:
        """Remove the overlay container for the named instance."""
        app = self._app
        inst_dir = Path(app.instances_dir) / name
        if not inst_dir.is_dir():
            app.status_message = f"Instance {name!r} not found"
            return
        inst = Instance.from_directory(inst_dir)

        def work():
            return overlay_reset(inst, app.runner)

        def on_success(removed):
            for inst in app.instances:
                if inst.name == name:
                    inst.overlay_running = False
                    break
            if removed:
                app.status_message = f"Overlay container for {name!r} removed."
            else:
                app.status_message = f"No overlay container found for {name!r}"

        def on_error(exc):
            app.status_message = f"Error: {exc}"
            app.poll_status()

        app.run_async(f"Resetting overlay for {name!r}...", work, on_success, on_error)

    def refresh(self, name: str) -> None:
        """Recreate the overlay container for the named instance, preserving installs."""
        app = self._app
        inst_dir = Path(app.instances_dir) / name
        if not inst_dir.is_dir():
            app.status_message = f"Instance {name!r} not found"
            return
        inst = Instance.from_directory(inst_dir)

        def work():
            image = self._image_for(name, inst_dir)
            overlay_refresh(inst, app.runner, image)

        def on_success(_result):
            for m in app.instances:
                if m.name == name:
                    m.overlay_running = True
                    break
            app.status_message = f"Refreshed {name!r} (installs preserved)"

        def on_error(exc):
            app.status_message = f"Error refreshing {name!r}: {exc}"
            app.poll_status()

        app.run_async(f"Refreshing {name!r}...", work, on_success, on_error)
