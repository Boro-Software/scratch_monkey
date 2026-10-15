"""Action controllers for the scratch-monkey GUI.

Each controller is a plain Python class holding a reference to the
:class:`~scratch_monkey.gui.models.AppModel` that owns it. The AppModel keeps the
shared state and the async machinery; controllers implement the actions and talk
to the model only through its public controller API (``runner``, ``run_async``,
``run_then_reload``, ``defer``, ``reload_when_idle``, ``poll_status``) and its
public state members.
"""

from .instances import InstanceController
from .overlays import OverlayController
from .shared import SharedVolumeController
from .tools import ToolsController

__all__ = [
    "InstanceController",
    "OverlayController",
    "SharedVolumeController",
    "ToolsController",
]
