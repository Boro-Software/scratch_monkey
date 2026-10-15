"""Shared volume actions for the GUI."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.app import AppModel


class SharedVolumeController:
    """Create shared volumes in the background."""

    def __init__(self, app: AppModel) -> None:
        self._app = app

    def create(self, name: str, on_done: Callable[[str], None]) -> None:
        """Create a new shared volume in the background.

        on_done(err): '' on success or an error message, on the GUI thread
        (see InstanceController.rename).
        """
        from ...config import validate_name
        from ...shared import create_shared

        app = self._app

        def action():
            validate_name(name)
            create_shared(name, Path(app.instances_dir))

        app.run_then_reload(
            f"Creating shared volume {name!r}...",
            action,
            success_status=f"Created shared volume {name!r}",
            on_done=on_done,
        )
