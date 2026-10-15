"""Instance lifecycle actions (create, clone, rename, delete) for the GUI."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ...config import InstanceConfig, save
from ...instance import clone, create, delete, rename, skel_copy
from ...run_args import DEFAULT_BASE_IMAGE, FEDORA_IMAGE, PROJECT_DIR

if TYPE_CHECKING:
    from ..models.app import AppModel


class InstanceController:
    """Create, clone, rename and delete instances in the background."""

    def __init__(self, app: AppModel) -> None:
        self._app = app

    def delete(self, name: str) -> None:
        """Delete the named instance (no confirmation — caller must confirm)."""
        app = self._app

        def clear_selection():
            app.selected_instance = ""

        app.run_then_reload(
            f"Deleting {name!r}...",
            lambda: delete(name, Path(app.instances_dir), app.runner),
            success_status=f"Deleted {name!r}",
            after_reload=clear_selection,
            reload_on_error=True,
        )

    def rename(self, old_name: str, new_name: str, on_done: Callable[[str], None]) -> None:
        """Rename an instance in the background.

        on_done(err) is called on the GUI thread with '' on success or an
        error message (including when the app is busy). The selection follows
        the renamed instance.
        """
        app = self._app

        def follow_rename():
            if app.selected_instance == old_name:
                app.selected_instance = new_name

        app.run_then_reload(
            f"Renaming {old_name!r}...",
            lambda: rename(old_name, new_name, Path(app.instances_dir), app.runner),
            success_status=f"Renamed {old_name!r} to {new_name!r}",
            on_done=on_done,
            after_reload=follow_rename,
        )

    def clone(self, source: str, dest: str, on_done: Callable[[str], None]) -> None:
        """Clone an instance in the background and select the clone.

        on_done(err): see rename.
        """
        app = self._app

        def select_clone():
            app.selected_instance = dest

        app.run_then_reload(
            f"Cloning {source!r}...",
            lambda: clone(source, dest, Path(app.instances_dir), app.runner),
            success_status=f"Cloned {source!r} to {dest!r}",
            on_done=on_done,
            after_reload=select_clone,
        )

    def create(
        self,
        name: str,
        on_done: Callable[[str], None],
        *,
        fedora: bool = False,
        skel: bool = False,
        config: InstanceConfig | None = None,
    ) -> None:
        """Create a new instance in the background and select it.

        on_done(err): see rename.
        """
        app = self._app
        base_image = FEDORA_IMAGE if fedora else DEFAULT_BASE_IMAGE
        instances_dir = Path(app.instances_dir)
        project_dir = PROJECT_DIR

        def action():
            inst = create(name, instances_dir, base_image, project_dir)
            if skel:
                skel_copy(inst)
            if config is not None:
                save(inst.directory / "scratch.toml", config)

        def select_new():
            app.selected_instance = name

        app.run_then_reload(
            f"Creating {name!r}...",
            action,
            success_status=f"Created instance {name!r}",
            on_done=on_done,
            after_reload=select_new,
        )
