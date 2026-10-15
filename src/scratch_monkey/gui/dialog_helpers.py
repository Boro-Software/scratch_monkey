"""Shared helper for dialogs that run an AppModel action in the background."""

from __future__ import annotations

from collections.abc import Callable


def make_on_done(dlg) -> Callable[[str], None]:
    """Build the on_done(err) callback for a dialog's background action.

    The dialog must define ``working`` (bool) and ``_error_msg`` (str) attrs.
    Called on the GUI thread with '' on success (closes the dialog) or the
    error message (shown inline; the dialog becomes interactive again).
    A late callback after the dialog was destroyed is ignored.
    """
    def on_done(err: str) -> None:
        if dlg.is_destroyed:
            return
        dlg.working = False
        if err:
            dlg._error_msg = err
        else:
            dlg.close()

    return on_done


def start_working(dlg) -> None:
    """Put a dialog into the in-flight state (inputs disabled, error cleared)."""
    dlg._error_msg = ""
    dlg.working = True
