"""GUI entry point for scratch-monkey."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ..config import DEFAULT_INSTANCES_DIR


def launch(instances_dir: Path) -> None:
    """Launch the GUI application. Called by both entry points."""
    try:
        import enaml

        from .models import AppModel
    except ImportError:
        click.echo(
            "Error: GUI dependencies not installed.\n"
            "Install with: uv tool install --editable '.[gui]'",
            err=True,
        )
        sys.exit(1)

    from ..container import PodmanRunner

    runner = PodmanRunner()
    app_model = AppModel(instances_dir, runner)

    with enaml.imports():
        from .views.main_window import ScratchMonkeyWindow

    from enaml.qt.qt_application import QtApplication
    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QIcon

    app = QtApplication()

    icon_path = str(Path(__file__).parent / "icons" / "scratch-monkey.svg")
    app_icon = QIcon(icon_path)
    app._qapp.setWindowIcon(app_icon)

    view = ScratchMonkeyWindow(app_model=app_model)
    view.show()

    # Load instances after the window is up so a slow/hung podman never blocks startup.
    QTimer.singleShot(0, lambda: app_model.refresh_async("Loading instances..."))

    poll_timer = QTimer()
    poll_timer.timeout.connect(lambda: app_model.poll_status())
    poll_timer.start(5000)

    app.start()
    poll_timer.stop()


@click.command()
@click.option(
    "--instances-dir",
    envvar="SCRATCH_MONKEY_INSTANCES_DIR",
    default=str(DEFAULT_INSTANCES_DIR),
    show_default=True,
    show_envvar=True,
    help="Directory where instances are stored.",
)
def gui_cli(instances_dir: str) -> None:
    """Launch the scratch-monkey GUI."""
    launch(Path(instances_dir))
