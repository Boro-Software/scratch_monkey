"""GUI entry point for scratch-monkey."""

from __future__ import annotations

import sys
from pathlib import Path

import click


def launch(instances_dir: Path) -> None:
    """Launch the GUI application. Invoked by `scratch-monkey --gui`."""
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
