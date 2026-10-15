"""Root application model for the scratch-monkey GUI."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

try:
    from atom.api import Atom, Bool, List, Str, Typed, Value
except ImportError:
    raise ImportError(
        "The GUI requires the 'enaml' package. "
        "Install it with: uv tool install 'scratch-monkey[gui]'"
    )

try:
    from enaml.application import deferred_call
except ImportError:
    def deferred_call(fn): fn()  # fallback for tests without enaml app

from ...container import PodmanRunner
from ...instance import InstanceInfo, list_all, query_statuses
from ...shared import list_shared, parse_shared_entry
from ..controllers import (
    InstanceController,
    OverlayController,
    SharedVolumeController,
    ToolsController,
)
from .entries import SharedVolumeEntry
from .instance import InstanceModel


class _ActionFailed(Exception):
    """An action failed in a worker; carries a best-effort list reload.

    Raised by AppModel.run_then_reload so the error handler can refresh the
    instance list (e.g. after a partially-completed delete) without doing any
    podman work on the GUI thread. str() is the underlying error message.
    """

    def __init__(self, cause: Exception, snapshot: tuple | None) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.snapshot = snapshot


class AppModel(Atom):
    """Root application model for the scratch-monkey GUI.

    Owns the shared GUI state and the async machinery. Actions live on the
    controllers it exposes; views call e.g. ``app_model.overlays.start(name)``:

    - ``instance_actions``: InstanceController (create/clone/rename/delete;
      not ``instances``, which is the instance list)
    - ``overlays``: OverlayController (start/stop/build/enter/reset/refresh)
    - ``shared``: SharedVolumeController (create)
    - ``tools``: ToolsController (export, editors, file manager, terminal)

    Controller API (what controllers may use besides the public state members):
    ``runner``, ``run_async``, ``run_then_reload``, ``defer``,
    ``reload_when_idle`` and ``poll_status``.
    """

    instances_dir = Str()
    instances = List(InstanceModel)
    selected_instance = Str("")
    # selected is an observable Value member kept in sync with selected_instance.
    # Using Value() (not @property) so Enaml bindings re-fire when it changes.
    selected = Value()
    status_message = Str("")
    error_message = Str("")
    busy = Bool(False)
    _polling = Bool(False)
    available_shared = List(str)
    # PodmanRunner stored as Value so Atom allows non-member attributes
    _runner = Value()
    # Status text of the operation currently in flight, shown if a second
    # action is clicked while busy (see run_async).
    _busy_status = Str("")
    # Completion status of a post-editor config reload that was requested while
    # busy ("" = none pending); honored as soon as the in-flight operation ends.
    _reload_pending = Str("")
    # Text of the "Could not query podman for: ..." banner last set by
    # _set_instances ("" = none), so poll_status can clear exactly that banner
    # once the failing instances answer again.
    _load_error_banner = Str("")

    # Action controllers (plain Python objects), created in __init__.
    instance_actions = Typed(InstanceController)
    overlays = Typed(OverlayController)
    shared = Typed(SharedVolumeController)
    tools = Typed(ToolsController)

    def __init__(self, instances_dir: Path, runner: PodmanRunner | None = None) -> None:
        super().__init__()
        self.instances_dir = str(instances_dir)
        self._runner = runner or PodmanRunner()
        self.instance_actions = InstanceController(self)
        self.overlays = OverlayController(self)
        self.shared = SharedVolumeController(self)
        self.tools = ToolsController(self)
        # No podman work here: podman can hang or be slow, so the initial load
        # is started by the caller via refresh_async() once the window is up.

    # ── observers ─────────────────────────────────────────────────────────────

    def _observe_selected_instance(self, change: dict) -> None:
        """Keep self.selected in sync when selected_instance changes."""
        name = change["value"]
        self.selected = next((i for i in self.instances if i.name == name), None)
        if self.selected:
            self.init_shared_entries(self.selected)

    def _observe_instances(self, change: dict) -> None:
        """Re-resolve selected after the instance list is refreshed."""
        self.selected = next((i for i in self.instances if i.name == self.selected_instance), None)
        if self.selected:
            self.init_shared_entries(self.selected)

    # ── controller API ────────────────────────────────────────────────────────
    # The small surface controllers use: runner, run_async, run_then_reload,
    # defer, reload_when_idle (plus poll_status and the public state members).

    @property
    def runner(self) -> PodmanRunner:
        """The PodmanRunner. Its methods block: call them from worker threads only."""
        return self._runner

    def defer(self, fn: Callable[[], None]) -> None:
        """Run fn on the GUI thread. The single deferred_call point for controllers."""
        deferred_call(fn)

    def reload_when_idle(self, done_status: str) -> None:
        """Reload the instance list in the background, now or once the app is idle.

        If another operation is in flight the reload is queued and started by
        _run_pending_reload when it ends. done_status is the completion message.
        """
        if self.busy:
            self._reload_pending = done_status
        else:
            self.refresh_async("Reloading config...", done_status=done_status)

    # ── async helper ──────────────────────────────────────────────────────────

    def _busy_message(self) -> str:
        return f"Busy — please wait ({self._busy_status!r} still running)"

    def run_async(self, status: str, work, on_success=None, on_error=None) -> bool:
        """Run work() in a background thread with busy indicator.

        Convention: any model or controller action that touches podman or
        reloads the instance list must go through here (or run_then_reload),
        never run on the GUI thread.

        Args:
            status: Status message shown while working.
            work: Callable that does the blocking work. Called in a daemon thread.
            on_success: Optional callback(result) called on the GUI thread after work completes.
            on_error: Optional callback(exception) called on the GUI thread if work raises.

        Returns:
            True if the work was started, False if rejected because the app is
            already busy (callers with a completion callback must report that).
        """
        if self.busy:
            self.status_message = self._busy_message()
            return False
        self.busy = True
        self._busy_status = status
        self.status_message = status
        self.error_message = ""

        def _thread():
            try:
                result = work()
            except Exception as exc:
                _exc = exc
                def _on_err(e=_exc):
                    self.busy = False
                    self.error_message = str(e)
                    if on_error:
                        on_error(e)
                    else:
                        self.status_message = f"Error: {e}"
                    self._run_pending_reload()
                deferred_call(_on_err)
            else:
                def _on_ok():
                    self.busy = False
                    if on_success:
                        on_success(result)
                    self._run_pending_reload()
                deferred_call(_on_ok)

        threading.Thread(target=_thread, daemon=True).start()
        return True

    def _run_pending_reload(self) -> None:
        """Start a deferred reload (see reload_when_idle) once the app is idle."""
        if self._reload_pending and not self.busy:
            done_status, self._reload_pending = self._reload_pending, ""
            self.refresh_async("Reloading config...", done_status=done_status)

    def _load_snapshot(self) -> tuple[list[InstanceInfo], list[str]]:
        """Query the instance list and shared volumes. Blocking: worker threads only."""
        instances_dir = Path(self.instances_dir)
        infos = list_all(instances_dir, self._runner)
        avail = [v.name for v in list_shared(instances_dir)]
        return infos, avail

    def _apply_snapshot(self, snapshot: tuple[list[InstanceInfo], list[str]]) -> None:
        infos, avail = snapshot
        self.available_shared = avail
        self._set_instances(infos)

    def run_then_reload(
        self,
        status: str,
        action: Callable[[], object],
        *,
        success_status: str,
        on_done: Callable[[str], None] | None = None,
        after_reload: Callable[[], None] | None = None,
        reload_on_error: bool = False,
    ) -> None:
        """Run action() then reload the instance list, all in one background job.

        Args:
            status: Status message shown while working.
            action: Blocking work (podman/disk). Runs in the worker thread.
            success_status: Status message set once the list has been reloaded.
            on_done: Optional completion callback for dialogs, called on the GUI
                thread with '' on success or the error message. Also called with
                a busy message if the app is already busy, so a dialog is never
                left waiting. When given, the error is reported there instead of
                in the error banner.
            after_reload: Optional callable run on the GUI thread after the new
                list is applied (use it to adjust the selection).
            reload_on_error: Also reload the list (best effort) when action fails,
                e.g. after a partially-completed delete.
        """
        def work():
            try:
                action()
            except Exception as exc:
                snapshot = None
                if reload_on_error:
                    try:
                        snapshot = self._load_snapshot()
                    except Exception:
                        pass
                raise _ActionFailed(exc, snapshot) from exc
            return self._load_snapshot()

        def on_success(snapshot):
            self._apply_snapshot(snapshot)
            if after_reload:
                after_reload()
            self.status_message = success_status
            if on_done:
                on_done("")

        def on_error(exc):
            cause = exc.cause if isinstance(exc, _ActionFailed) else exc
            if isinstance(exc, _ActionFailed) and exc.snapshot is not None:
                self._apply_snapshot(exc.snapshot)
            msg = str(cause) or type(cause).__name__
            self.status_message = f"Error: {msg}"
            # A dialog shows the error itself; otherwise use the banner.
            self.error_message = "" if on_done else msg
            if on_done:
                on_done(msg)

        if not self.run_async(status, work, on_success, on_error) and on_done:
            on_done(self._busy_message())

    # ── actions ───────────────────────────────────────────────────────────────

    def has_unsaved_changes(self) -> bool:
        """Check if any instance has unsaved config changes."""
        return any(inst.dirty for inst in self.instances)

    def init_shared_entries(self, model: InstanceModel) -> None:
        """Build SharedVolumeEntry list from available shared + instance config."""
        config_shared: dict[str, str] = {}
        for entry in model.shared:
            try:
                name, mode = parse_shared_entry(entry)
            except Exception:
                name, mode = entry, "rw"
            config_shared[name] = mode

        entries = []
        for name in self.available_shared:
            enabled = name in config_shared
            mode = config_shared.get(name, "rw")
            entries.append(SharedVolumeEntry(name=name, enabled=enabled, mode=mode))
        model.shared_entries = entries

    def _set_instances(self, infos: list[InstanceInfo]) -> None:
        """Replace the instance list and surface per-instance query failures."""
        self.instances = [InstanceModel.from_info(i) for i in infos]
        failed = [m for m in self.instances if m.status_error]
        if failed:
            details = "; ".join(f"{m.name} ({m.status_error})" for m in failed)
            self.error_message = f"Could not query podman for: {details}"
            self._load_error_banner = self.error_message
        else:
            self._load_error_banner = ""

    def refresh_async(self, status: str = "Refreshing...", done_status: str = "") -> None:
        """Reload all instances from disk in a background thread.

        Also used for the initial load after the window opens (with a
        "Loading instances..." status). Failures set error_message via run_async.
        done_status overrides the default "Loaded N instance(s)" completion message.
        """
        def on_success(snapshot):
            self._apply_snapshot(snapshot)
            self.status_message = done_status or f"Loaded {len(self.instances)} instance(s)"

        def on_error(exc):
            self.status_message = f"Error loading instances: {exc}"

        self.run_async(status, self._load_snapshot, on_success, on_error)

    def _clear_load_error_banner(self) -> None:
        """Drop the "Could not query podman" banner once no instance has a status error."""
        if (
            self._load_error_banner
            and self.error_message == self._load_error_banner
            and not any(m.status_error for m in self.instances)
        ):
            self.error_message = ""
            self._load_error_banner = ""

    def poll_status(self) -> None:
        """Lightweight status poller — updates overlay_running/image_built in-place."""
        if self.busy or self._polling:
            return
        self._polling = True

        # Snapshot what we need on the GUI thread
        snapshot = [
            (inst.name, inst.overlay_id or f"{inst.name}-overlay")
            for inst in self.instances
        ]
        runner = self._runner

        def _poll():
            try:
                # Concurrent, so N stuck instances cost one timeout, not N.
                statuses = query_statuses(runner, snapshot)
                results = {name: st for (name, _), st in zip(snapshot, statuses)}
            except Exception:
                deferred_call(lambda: setattr(self, '_polling', False))
                return

            def _apply():
                for inst in self.instances:
                    res = results.get(inst.name)
                    if res is None:
                        continue
                    # A failed query leaves that field at its last known value.
                    if res.image_error is None and inst.image_built != res.image_built:
                        inst.image_built = res.image_built
                    if res.overlay_error is None and inst.overlay_running != res.overlay_running:
                        inst.overlay_running = res.overlay_running
                    status_error = res.status_error or ""
                    if inst.status_error != status_error:
                        inst.status_error = status_error
                self._clear_load_error_banner()
                self._polling = False

            deferred_call(_apply)

        threading.Thread(target=_poll, daemon=True).start()

    def new_instance_model(self) -> InstanceModel:
        """Create a fresh InstanceModel with shared entries initialized."""
        m = InstanceModel()
        self.init_shared_entries(m)
        return m
