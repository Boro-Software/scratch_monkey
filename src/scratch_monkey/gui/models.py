"""Atom-based observable models for the scratch-monkey GUI."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

try:
    from atom.api import Atom, Bool, List, Str, Value, observe
except ImportError:
    raise ImportError(
        "The GUI requires the 'enaml' package. "
        "Install it with: uv tool install 'scratch-monkey[gui]'"
    )

try:
    from enaml.application import deferred_call
except ImportError:
    def deferred_call(fn): fn()  # fallback for tests without enaml app

from ..config import InstanceConfig, save
from ..container import PodmanError, PodmanRunner
from ..export import ExportError
from ..export import export_command as export_command_fn
from ..instance import (
    Instance,
    InstanceInfo,
    clone,
    create,
    delete,
    detect_base_image,
    is_fedora_based,
    list_all,
    rename,
    skel_copy,
)
from ..overlay import ensure_running as overlay_ensure_running
from ..overlay import refresh as overlay_refresh
from ..overlay import reset as overlay_reset
from ..run_args import DEFAULT_BASE_IMAGE, FEDORA_IMAGE, PROJECT_DIR
from ..shared import list_shared, parse_shared_entry


class _ActionFailed(Exception):
    """An action failed in a worker; carries a best-effort list reload.

    Raised by AppModel._run_then_reload so the error handler can refresh the
    instance list (e.g. after a partially-completed delete) without doing any
    podman work on the GUI thread. str() is the underlying error message.
    """

    def __init__(self, cause: Exception, snapshot: tuple | None) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.snapshot = snapshot


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


class InstanceModel(Atom):
    """Observable model for a single scratch-monkey instance."""

    name = Str()
    directory = Str()
    image_built = Bool(False)
    overlay_running = Bool(False)
    overlay_id = Str("")
    base_image = Str("")
    # Non-empty when podman could not be queried for this instance (status unknown).
    status_error = Str("")

    # Config fields
    cmd = Str("/bin/bash")
    wayland = Bool(False)
    ssh = Bool(False)
    x11 = Bool(False)
    home = Str("")
    overlay = Bool(False)
    sudo = Bool(True)
    is_fedora = Bool(False)
    gpu = Bool(False)
    devices = List(str)
    shared = List(str)
    volumes = List(str)
    env_vars = List(str)
    volume_entries = List(VolumeMountEntry)
    shared_entries = List(SharedVolumeEntry)

    dirty = Bool(False)
    _saved_config = Value()

    @classmethod
    def from_info(cls, info: InstanceInfo) -> InstanceModel:
        m = cls()
        m.name = info.name
        m.directory = str(info.directory)
        m.image_built = info.image_built
        m.overlay_running = info.overlay_running
        m.overlay_id = info.config.overlay_id
        m.base_image = info.base_image or ""
        m.status_error = info.status_error or ""
        cfg = info.config
        m.cmd = cfg.cmd
        m.wayland = cfg.wayland
        m.ssh = cfg.ssh
        m.x11 = cfg.x11
        m.home = cfg.home
        m.overlay = cfg.overlay
        m.sudo = cfg.sudo
        m.is_fedora = is_fedora_based(Path(info.directory))
        m.gpu = cfg.gpu
        m.devices = list(cfg.devices)
        m.shared = list(cfg.shared)
        m.volumes = list(cfg.volumes)
        m.env_vars = list(cfg.env)
        m.volume_entries = [VolumeMountEntry.from_spec(v) for v in cfg.volumes]
        m._saved_config = m.to_config()
        return m

    def to_config(self) -> InstanceConfig:
        return InstanceConfig(
            cmd=self.cmd,
            wayland=self.wayland,
            ssh=self.ssh,
            x11=self.x11,
            home=self.home,
            volumes=[
                e.to_spec() for e in self.volume_entries
                if e.host_path or e.container_path
            ],
            env=list(self.env_vars),
            shared=[
                f"{e.name}:{e.mode}" if e.mode != "rw" else e.name
                for e in self.shared_entries
                if e.enabled
            ],
            overlay=self.overlay,
            sudo=self.sudo,
            gpu=self.gpu,
            devices=list(self.devices),
        )

    @observe(
        'cmd', 'wayland', 'ssh', 'x11', 'home', 'overlay', 'sudo', 'gpu', 'devices',
        'volume_entries', 'env_vars', 'shared_entries',
    )
    def _on_config_change(self, change):
        if self._saved_config is not None:
            self.dirty = (self.to_config() != self._saved_config)

    def check_dirty(self):
        """Recompute dirty flag. Call after in-place nested object mutations."""
        if self._saved_config is not None:
            self.dirty = (self.to_config() != self._saved_config)

    def revert(self):
        """Discard changes and restore to last saved config."""
        if self._saved_config is not None:
            cfg = self._saved_config
            self.cmd = cfg.cmd
            self.wayland = cfg.wayland
            self.ssh = cfg.ssh
            self.x11 = cfg.x11
            self.home = cfg.home
            self.overlay = cfg.overlay
            self.sudo = cfg.sudo
            self.gpu = cfg.gpu
            self.devices = list(cfg.devices)
            self.env_vars = list(cfg.env)
            self.volume_entries = [VolumeMountEntry.from_spec(v) for v in cfg.volumes]
            self.shared = list(cfg.shared)
            self.dirty = False

    def add_volume_entry(self) -> None:
        """Append a new blank volume mount entry."""
        self.volume_entries = [*self.volume_entries, VolumeMountEntry()]

    def remove_volume_entry(self, index: int) -> None:
        """Remove volume entry at the given index."""
        entries = list(self.volume_entries)
        if 0 <= index < len(entries):
            del entries[index]
            self.volume_entries = entries

    def add_env_var(self, value: str = "") -> None:
        """Append a new environment variable entry."""
        self.env_vars = [*self.env_vars, value]

    def remove_env_var(self, index: int) -> None:
        """Remove env var at the given index."""
        entries = list(self.env_vars)
        if 0 <= index < len(entries):
            del entries[index]
            self.env_vars = entries

    def save(self) -> None:
        """Persist the current model state to scratch.toml."""
        config_path = Path(self.directory) / "scratch.toml"
        save(config_path, self.to_config())
        self._saved_config = self.to_config()
        self.dirty = False


class AppModel(Atom):
    """Root application model for the scratch-monkey GUI."""

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
    # action is clicked while busy (see _run_async).
    _busy_status = Str("")
    # Completion status of a post-editor config reload that was requested while
    # busy ("" = none pending); honored as soon as the in-flight operation ends.
    _reload_pending = Str("")

    def __init__(self, instances_dir: Path, runner: PodmanRunner | None = None) -> None:
        super().__init__()
        self.instances_dir = str(instances_dir)
        self._runner = runner or PodmanRunner()
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

    # ── async helper ──────────────────────────────────────────────────────────

    def _busy_message(self) -> str:
        return f"Busy — please wait ({self._busy_status!r} still running)"

    def _run_async(self, status: str, work, on_success=None, on_error=None) -> bool:
        """Run work() in a background thread with busy indicator.

        Convention: any model method that touches podman or reloads the
        instance list must go through here, never run on the GUI thread.

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
        """Start a deferred reload (see edit_config) once the app is idle."""
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

    def _run_then_reload(
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

        if not self._run_async(status, work, on_success, on_error) and on_done:
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

    def refresh_async(self, status: str = "Refreshing...", done_status: str = "") -> None:
        """Reload all instances from disk in a background thread.

        Also used for the initial load after the window opens (with a
        "Loading instances..." status). Failures set error_message via _run_async.
        done_status overrides the default "Loaded N instance(s)" completion message.
        """
        def on_success(snapshot):
            self._apply_snapshot(snapshot)
            self.status_message = done_status or f"Loaded {len(self.instances)} instance(s)"

        def on_error(exc):
            self.status_message = f"Error loading instances: {exc}"

        self._run_async(status, self._load_snapshot, on_success, on_error)

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
                results = {}
                for name, overlay_name in snapshot:
                    try:
                        running = runner.container_running(overlay_name)
                        built = runner.image_exists(name)
                    except PodmanError:
                        # Timed out / failed for this instance: keep its last
                        # known state and move on to the others.
                        continue
                    results[name] = (running, built)
            except Exception:
                deferred_call(lambda: setattr(self, '_polling', False))
                return

            def _apply():
                for inst in self.instances:
                    pair = results.get(inst.name)
                    if pair is None:
                        continue
                    running, built = pair
                    if inst.overlay_running != running:
                        inst.overlay_running = running
                    if inst.image_built != built:
                        inst.image_built = built
                self._polling = False

            deferred_call(_apply)

        threading.Thread(target=_poll, daemon=True).start()

    def open_directory(self, path: str) -> None:
        """Open a directory in the file manager."""
        err = _open_file_manager(path)
        if err:
            self.status_message = err
        else:
            self.status_message = f"Opened file manager at {path}"

    def open_terminal(self, path: str) -> None:
        """Open a terminal at the given directory."""
        err = _open_terminal_at(path)
        if err:
            self.status_message = err
        else:
            self.status_message = f"Opened terminal at {path}"

    def enter_instance(self, name: str, *, root: bool = False) -> None:
        """Open a terminal running scratch-monkey enter for the named instance."""
        cmd = ["scratch-monkey", "enter", name]
        if root:
            cmd.append("--root")
        err = _launch_in_terminal(cmd)
        if err:
            self.status_message = err
        else:
            self.status_message = f"Opened terminal for {name!r}" + (" (root)" if root else "")

    def build_instance(self, name: str) -> None:
        """Open a terminal running scratch-monkey build-instance."""
        err = _launch_in_terminal(["scratch-monkey", "build-instance", name])
        if err:
            self.status_message = err
        else:
            self.status_message = f"Building {name!r} in terminal..."

    def start_instance(self, name: str) -> None:
        """Start (or create) the overlay container for the named instance."""
        instances_dir = Path(self.instances_dir)
        inst_dir = instances_dir / name
        if not inst_dir.is_dir():
            self.status_message = f"Instance {name!r} not found"
            return
        inst = Instance.from_directory(inst_dir)

        def work():
            # Determine image: per-instance build or base image (podman query,
            # so it must run here in the worker, not on the GUI thread)
            if self._runner.image_exists(name):
                image = name
            else:
                image = detect_base_image(inst_dir) or DEFAULT_BASE_IMAGE
            overlay_ensure_running(inst, self._runner, image)

        def on_success(_result):
            for m in self.instances:
                if m.name == name:
                    m.overlay_running = True
                    break
            self.status_message = f"Started {name!r}"

        def on_error(exc):
            self.poll_status()
            self.status_message = f"Error starting {name!r}: {exc}"

        self._run_async(f"Starting {name!r}...", work, on_success, on_error)

    def stop_instance(self, name: str) -> None:
        """Stop the overlay container for the named instance."""
        inst_model = next((i for i in self.instances if i.name == name), None)
        if inst_model is None:
            self.status_message = f"Instance {name!r} not found"
            return
        overlay_name = inst_model.overlay_id or f"{name}-overlay"

        def work():
            self._runner.stop(overlay_name)

        def on_success(_result):
            for inst in self.instances:
                if inst.name == name:
                    inst.overlay_running = False
                    break
            self.status_message = f"Stopped {name!r}"

        def on_error(exc):
            self.poll_status()
            self.status_message = f"Error stopping {name!r}: {exc}"

        self._run_async(f"Stopping {name!r}...", work, on_success, on_error)

    def reset_overlay(self, name: str) -> None:
        """Remove the overlay container for the named instance."""
        instances_dir = Path(self.instances_dir)
        inst_dir = instances_dir / name
        if not inst_dir.is_dir():
            self.status_message = f"Instance {name!r} not found"
            return
        inst = Instance.from_directory(inst_dir)

        def work():
            return overlay_reset(inst, self._runner)

        def on_success(removed):
            for inst in self.instances:
                if inst.name == name:
                    inst.overlay_running = False
                    break
            if removed:
                self.status_message = f"Overlay container for {name!r} removed."
            else:
                self.status_message = f"No overlay container found for {name!r}"

        def on_error(exc):
            self.poll_status()
            self.status_message = f"Error: {exc}"

        self._run_async(f"Resetting overlay for {name!r}...", work, on_success, on_error)

    def refresh_overlay(self, name: str) -> None:
        """Recreate the overlay container for the named instance, preserving installs."""
        instances_dir = Path(self.instances_dir)
        inst_dir = instances_dir / name
        if not inst_dir.is_dir():
            self.status_message = f"Instance {name!r} not found"
            return
        inst = Instance.from_directory(inst_dir)

        def work():
            # Determine image: per-instance build or base image (mirrors start_instance)
            if self._runner.image_exists(name):
                image = name
            else:
                image = detect_base_image(inst_dir) or DEFAULT_BASE_IMAGE
            overlay_refresh(inst, self._runner, image)

        def on_success(_result):
            for m in self.instances:
                if m.name == name:
                    m.overlay_running = True
                    break
            self.status_message = f"Refreshed {name!r} (installs preserved)"

        def on_error(exc):
            self.poll_status()
            self.status_message = f"Error refreshing {name!r}: {exc}"

        self._run_async(f"Refreshing {name!r}...", work, on_success, on_error)

    def delete_instance(self, name: str) -> None:
        """Delete the named instance (no confirmation — caller must confirm)."""
        def clear_selection():
            self.selected_instance = ""

        self._run_then_reload(
            f"Deleting {name!r}...",
            lambda: delete(name, Path(self.instances_dir), self._runner),
            success_status=f"Deleted {name!r}",
            after_reload=clear_selection,
            reload_on_error=True,
        )

    def rename_instance(self, old_name: str, new_name: str, on_done: Callable[[str], None]) -> None:
        """Rename an instance in the background.

        on_done(err) is called on the GUI thread with '' on success or an
        error message (including when the app is busy). The selection follows
        the renamed instance.
        """
        def follow_rename():
            if self.selected_instance == old_name:
                self.selected_instance = new_name

        self._run_then_reload(
            f"Renaming {old_name!r}...",
            lambda: rename(old_name, new_name, Path(self.instances_dir), self._runner),
            success_status=f"Renamed {old_name!r} to {new_name!r}",
            on_done=on_done,
            after_reload=follow_rename,
        )

    def clone_instance(self, source: str, dest: str, on_done: Callable[[str], None]) -> None:
        """Clone an instance in the background and select the clone.

        on_done(err): see rename_instance.
        """
        def select_clone():
            self.selected_instance = dest

        self._run_then_reload(
            f"Cloning {source!r}...",
            lambda: clone(source, dest, Path(self.instances_dir), self._runner),
            success_status=f"Cloned {source!r} to {dest!r}",
            on_done=on_done,
            after_reload=select_clone,
        )

    def export_command(self, name: str, cmd: str, bin_name: str = "") -> str:
        """Export a command from an instance. Returns '' on success or error string."""
        instances_dir = Path(self.instances_dir)
        inst_dir = instances_dir / name
        if not inst_dir.is_dir():
            return f"Instance {name!r} not found"
        inst = Instance.from_directory(inst_dir)
        try:
            path = export_command_fn(inst, cmd, bin_name=bin_name)
            self.status_message = f"Exported {cmd!r} from {name!r} to {path}"
        except (ExportError, OSError, RuntimeError) as e:
            return str(e)
        return ""

    def edit_file(self, name: str, file_type: str) -> None:
        """Open an instance file in the user's editor."""
        instances_dir = Path(self.instances_dir)
        inst_dir = instances_dir / name
        if not inst_dir.is_dir():
            self.status_message = f"Instance {name!r} not found"
            return
        file_map = {
            "dockerfile": inst_dir / "Dockerfile",
            "env": inst_dir / ".env",
        }
        target = file_map.get(file_type)
        if target is None:
            self.status_message = f"Unknown file type {file_type!r}"
            return
        err = _open_in_editor(target)
        if err:
            self.status_message = err
        else:
            self.status_message = f"Editing {file_type} for {name!r}..."

    def edit_config(self, name: str) -> None:
        """Open scratch.toml in the user's editor, refreshing config on close."""
        instances_dir = Path(self.instances_dir)
        inst_dir = instances_dir / name
        if not inst_dir.is_dir():
            self.status_message = f"Instance {name!r} not found"
            return
        target = inst_dir / "scratch.toml"
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR", "vi")
        prefix = _find_terminal()
        if not prefix:
            self.status_message = (
                "No terminal emulator found. Install xdg-terminal-exec, "
                "gnome-terminal, or similar."
            )
            return
        proc = subprocess.Popen([*prefix, editor, str(target)])
        self.status_message = f"Editing config for {name!r}..."

        def _wait_and_refresh():
            proc.wait()

            def _update():
                # Reload in the background. If another operation is in flight
                # the reload is deferred until it finishes (_run_pending_reload).
                done_status = f"Reloaded config for {name!r}"
                if self.busy:
                    self._reload_pending = done_status
                else:
                    self.refresh_async("Reloading config...", done_status=done_status)

            deferred_call(_update)

        threading.Thread(target=_wait_and_refresh, daemon=True).start()

    def new_instance_model(self) -> InstanceModel:
        """Create a fresh InstanceModel with shared entries initialized."""
        m = InstanceModel()
        self.init_shared_entries(m)
        return m

    def create_shared_volume(self, name: str, on_done: Callable[[str], None]) -> None:
        """Create a new shared volume in the background.

        on_done(err): see rename_instance.
        """
        from ..config import validate_name
        from ..shared import create_shared

        def action():
            validate_name(name)
            create_shared(name, Path(self.instances_dir))

        self._run_then_reload(
            f"Creating shared volume {name!r}...",
            action,
            success_status=f"Created shared volume {name!r}",
            on_done=on_done,
        )

    def create_instance(
        self,
        name: str,
        on_done: Callable[[str], None],
        *,
        fedora: bool = False,
        skel: bool = False,
        config: InstanceConfig | None = None,
    ) -> None:
        """Create a new instance in the background and select it.

        on_done(err): see rename_instance.
        """
        base_image = FEDORA_IMAGE if fedora else DEFAULT_BASE_IMAGE
        instances_dir = Path(self.instances_dir)
        project_dir = PROJECT_DIR

        def action():
            inst = create(name, instances_dir, base_image, project_dir)
            if skel:
                skel_copy(inst)
            if config is not None:
                save(inst.directory / "scratch.toml", config)

        def select_new():
            self.selected_instance = name

        self._run_then_reload(
            f"Creating {name!r}...",
            action,
            success_status=f"Created instance {name!r}",
            on_done=on_done,
            after_reload=select_new,
        )
