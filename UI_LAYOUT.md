# Enaml Layout Notes

## HGroup: align_widths and fixed-size buttons

HGroup defaults to `align_widths = True`, which forces all children to equal width.
This breaks when you mix stretchy Fields with fixed-size buttons (e.g. a 30px "X" remove button).

**Pattern** (from instance_list.enaml):
```
HGroup:
    align_widths = False
    Field:
        hug_width = 'ignore'    # stretches to fill
    PushButton:
        text = "X"
        minimum_size = (30, 0)
        maximum_size = (30, 16777215)
```

Without `align_widths = False`, the equal-width constraint fights with `minimum_size`/`maximum_size`
on the button and the layout breaks.

## Paired fields share width: use a Container with an equality constraint

Two stretchy `Field`s (`hug_width = 'ignore'`) in an `HGroup` with `align_widths = False`
have no relationship to each other, so the solver may give nearly all the width to one and
squeeze the other (this happened to the host/container path fields of the volume mount
rows). `align_widths = True` is not an option because it also equalizes the combo/button.

**Pattern** (from config_editor.enaml, volume mount rows): use a `Container` with explicit
constraints, name the children, and pin only the fields to each other:
```
from enaml.layout.api import hbox

Container:
    padding = 0
    constraints = [
        hbox(host_field, container_field, mode_combo, remove_btn),
        host_field.width == container_field.width,
    ]
    Field: host_field:
        hug_width = 'ignore'
        minimum_size = (150, 0)      # neither field may collapse
    Field: container_field:
        hug_width = 'ignore'
        minimum_size = (150, 0)
    ObjectCombo: mode_combo:         # keeps its natural size
    PushButton: remove_btn:
        minimum_size = (30, 0)
        maximum_size = (30, 16777215)
```
For an uneven split use e.g. `host_field.width == 1.2 * container_field.width`.
Rows whose second child is a fixed-size button (single Field, or KEY/value with a capped
key) do not need this.

## Dialog default size

Set `initial_size` on the `Dialog` (e.g. `initial_size = (760, 0)` in
`CreateInstanceDialog`); a `0` dimension lets Qt use the content's size hint, and the dialog
stays resizable. Keep the width under ~1000px so it fits a 1366px-wide screen.

If the content is tall (CreateInstanceDialog's config groups), set a fixed default height (`initial_size = (760, 680)`) and wrap only the tall part in a `ScrollArea` (single Container child, `widget_resizable` defaults to True so it keeps full width); the name field, status labels and buttons stay outside it and remain visible.

## Disabling UI during async operations

Setting `enabled = False` on a container (e.g. Splitter) disables all children recursively (Qt propagation).
Use a single binding on the outermost container rather than per-button `enabled` checks.

**Pattern** (from main_window.enaml):
```
Splitter: splitter:
    enabled << not app_model.busy
    SplitItem:
        ...  # all children disabled when busy
```

Do **not** add `enabled << not app_model.busy` to individual buttons inside the splitter — the parent
binding handles it. Only add per-widget `enabled` bindings for things outside the disabled container
(e.g. ToolBar actions).

## Status bar: fixed height to prevent splitter resize

Toggling `visible` on a child widget (e.g. ProgressBar) causes the parent container to recalculate
its preferred size, which can make a `vbox` constraint engine resize the splitter above it.

**Fix**: pin the status bar height with `minimum_size` / `maximum_size` and set `align_widths = False`:
```
HGroup: status_bar:
    align_widths = False
    padding = 0
    trailing_spacer = None
    minimum_size = (0, 28)
    maximum_size = (16777215, 28)
    ProgressBar:
        visible << app_model.busy
        ...
    Label:
        text << app_model.status_message
```

## Collapsible banner named directly in a `vbox()`

A widget referenced by name in a parent's `constraints = [vbox(a, b, c)]` must always
exist (Conditional can't be used for it, since the name wouldn't resolve when the
condition is False). To make such a widget show/hide and take zero space when hidden —
e.g. an error banner above a Splitter — don't rely on `visible` alone to collapse its
size; the "fixed height to prevent resize" trick from the status bar above assumed a
*constant* height, which is wrong here since we want *zero* height when hidden.

**Pattern** (from main_window.enaml's message banner, sits above `splitter` in
`vbox(banner, splitter, status_bar)`):
```
HGroup: banner:
    align_widths = False
    padding = 4
    trailing_spacer = None
    visible << bool(app_model.banner_level)
    minimum_size << (0, 32) if app_model.banner_level else (0, 0)
    maximum_size << (16777215, 32) if app_model.banner_level else (16777215, 0)
    Label:
        text << app_model.banner_summary
        style_class << "error-banner" if app_model.banner_level == "error" else "warning-banner"
        hug_width = 'ignore'
    PushButton:                      # "Details"
        visible << app_model.banner_truncated
        maximum_size = (70, 24)
    PushButton:                      # dismiss
        maximum_size = (30, 24)
```
Binding `minimum_size`/`maximum_size` themselves (not just `visible`) to the same
condition gives the constraint solver a deterministic height in both states, so the
Splitter reclaims the space cleanly instead of the layout being left to
implicit hidden-widget sizing.

### Banner levels, elided text, Details, dismiss

The banner shows one message at a time. `AppModel` has two sources, `error_message` (red,
`error-banner` style) and `warning_message` (amber, `warning-banner`), and derives what the
view binds to via observers: `banner_level` (`""`/`"error"`/`"warning"`), `banner_text` (full
text), `banner_summary` (one line) and `banner_truncated`. **An error takes precedence**: while
`error_message` is set it is shown, and the warning reappears once the error is cleared.
Views bind only to the `banner_*` members; code sets `error_message` / `warning_message`.

- **Elide**: the label only ever holds a single line (`gui/banner.py::elide_message`, pure and
  unit-tested): the first non-blank line, cut at a word boundary near 100 characters, with `…`
  appended whenever text was dropped (long line or further lines). Long text therefore never
  widens the window or runs off screen.
- **Details**: the "Details" button is visible only when `banner_truncated`. It opens
  `BannerDetailsDialog` (`views/dialogs/banner_details_dialog.enaml`) with the full text in a
  read-only, selectable, scrollable `MultilineField` and a Close button.
- **Dismiss**: the dismiss button calls `app_model.dismiss_banner()`, which clears the message
  being shown (`error_message` if set, else `warning_message`). Dismissing the "Could not query
  podman" error also resets `_load_error_banner`, so `_clear_load_error_banner` never mistakes a
  later error for it.
- A new `run_async` action clears both messages when it starts. Controller actions that finish
  with warnings (overlay `start`/`refresh`) collect them in the worker via `on_warning` and set
  `app.warning_message` in `on_success` on the GUI thread.
- The banner sits outside the Splitter, so Details and dismiss stay usable while busy.

## Disabling UI during async operations — do not re-add per-button guards

`enabled << not app_model.busy` on the outer `Splitter` in main_window.enaml already
disables every button in `InstanceListPanel` and `InstanceDetailPanel` recursively
(Qt propagates `enabled=False` to all descendants). Per-button `enabled` bindings were
deliberately removed in favor of this single splitter-level binding (see commit
`c18fae4`) — do not reintroduce them as "redundant safety"; it undoes that refactor
and reintroduces 12+ near-duplicate bindings for no behavioral gain. If a *new* button
is added outside the Splitter (e.g. a ToolBar Action), it needs its own
`enabled << not app_model.busy` binding, same as the existing ToolBar actions.

## Controller layout: where actions live

`AppModel` (`gui/models/app.py`) holds the shared GUI state (`instances`, `selected_instance`,
`status_message`, `error_message`, `warning_message`, `busy`, ...) and the async machinery. Actions live on
plain-Python controller objects in `gui/controllers/` that `AppModel` creates and exposes as
members; views call them as `app_model.<controller>.<action>(...)`:

| Member | Class | Actions |
|---|---|---|
| `app_model.instance_actions` | `InstanceController` | `create`, `clone`, `rename`, `delete` |
| `app_model.overlays` | `OverlayController` | `start`, `stop`, `build`, `enter`, `reset`, `reset_overlay_data`, `refresh` |
| `app_model.shared` | `SharedVolumeController` | `create` |
| `app_model.tools` | `ToolsController` | `export_command`, `edit_file`, `edit_config`, `open_directory`, `open_terminal` |

(`app_model.instances` is the observable list of `InstanceModel`, hence `instance_actions`.)

A **new action** goes on the controller whose domain it belongs to (or a new controller wired up
in `AppModel.__init__` and exported from `gui/controllers/__init__.py`); do not add actions to
`AppModel` itself. Controllers hold a reference to the `AppModel` and use only its small
controller API: the public state members, `runner`, `run_async`, `run_then_reload`, `defer`,
`reload_when_idle` and `poll_status`. They never create worker threads for podman work, never
import `deferred_call` (use `app.defer(fn)`), and never touch underscore-private AppModel
members. Tests patch `scratch_monkey.gui.models.app.deferred_call` as the single point for
running completion handlers synchronously, and patch controller dependencies where the
controller looks them up (e.g. `scratch_monkey.gui.controllers.overlays.overlay_ensure_running`).

## Threading convention: podman work and list reloads never run on the GUI thread

Any controller action that touches podman or reloads the instance list must go through
`app.run_async` (directly, or via `app.run_then_reload` for "do X, then reload the list"). That
gives the action the busy flag, the status-bar `ProgressBar`, and the error handling for
free. Never call `list_all`, `list_shared` or a `PodmanRunner` method from the GUI thread
(there is deliberately no blocking `refresh()`; use `refresh_async()`).

Dialogs that run such an action use the **`on_done` callback + working-state pattern**
(see `RenameDialog`/`CloneDialog` in instance_list.enaml, `CreateInstanceDialog`,
`CreateSharedDialog`):
```
enamldef MyDialog(Dialog): dlg:
    attr working : bool = False
    attr _error_msg : str = ""
    closing ::
        if dlg.working:
            change['value'].ignore()      # no dismissing mid-operation
    Container:
        enabled << not dlg.working        # one binding disables every button/field
        VGroup:
            Label:
                text = "Working..."
                visible << dlg.working
            PushButton:
                clicked ::
                    start_working(dlg)    # clears _error_msg, sets working
                    dlg.app_model.some_controller.some_action(arg, make_on_done(dlg))
```
`some_action(..., on_done)` calls `on_done('')` on the GUI thread on success (the helper
closes the dialog) or `on_done(message)` on failure (shown in `_error_msg`, dialog
re-enabled). If the app is already busy the model still calls `on_done("Busy ...")`, so the
dialog is never stuck. Errors for dialog-driven actions go to the dialog only, not the
red banner. `make_on_done`/`start_working` live in `gui/dialog_helpers.py`; a late success
after the dialog was destroyed (e.g. Esc, which bypasses `closing`) is ignored there, but a late
failure is copied to `app_model.error_message` so it reaches the banner.

## Atom objects and Qt signals

Atom objects do not support `__weakref__`, so `signal.connect(atom_obj.method)` raises
`TypeError: cannot create weak reference to 'AppModel' object`.

**Fix**: wrap in a lambda:
```python
timer.timeout.connect(lambda: app_model.poll_status())
```
