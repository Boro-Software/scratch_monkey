"""Pure helpers for the main window's message banner (no Qt/Atom dependency)."""

from __future__ import annotations

ELLIPSIS = "…"

# Longest first line shown in the banner before it is cut with an ellipsis.
BANNER_MAX_CHARS = 100

LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"


def elide_message(text: str, max_len: int = BANNER_MAX_CHARS) -> tuple[str, bool]:
    """Reduce *text* to a single banner line.

    Returns ``(summary, truncated)``.  The summary is the first non-blank line;
    when that line is longer than *max_len* it is cut (at a word boundary if
    there is a reasonable one) and ends with an ellipsis.  A message of several
    lines also gets a trailing ellipsis.  ``truncated`` is True whenever the
    summary lacks part of the text, i.e. when "Details" has something to show.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "", False
    first = lines[0]
    truncated = len(lines) > 1
    if len(first) > max_len:
        cut = first[: max_len - 1]
        space = cut.rfind(" ")
        if space >= max_len // 2:
            cut = cut[:space]
        first = cut.rstrip()
        truncated = True
    return (first + ELLIPSIS if truncated else first), truncated


def banner_content(error: str, warning: str) -> tuple[str, str]:
    """Pick what the banner shows: ``(level, text)``.

    An error takes precedence over a warning (the warning is shown again once
    the error is dismissed or cleared).  ``("", "")`` when there is nothing.
    """
    if error:
        return LEVEL_ERROR, error
    if warning:
        return LEVEL_WARNING, warning
    return "", ""
