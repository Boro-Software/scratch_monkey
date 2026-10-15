"""Tests for the main-window banner: elide helper, levels, dismiss, controller warnings."""

import pytest

from scratch_monkey.gui.banner import (
    BANNER_MAX_CHARS,
    ELLIPSIS,
    banner_content,
    elide_message,
)

atom = pytest.importorskip("atom")

from .gui_helpers import make_app, sync_deferred_call, wait  # noqa: E402


class TestElideMessage:
    def test_short_single_line_unchanged(self):
        assert elide_message("podman broke") == ("podman broke", False)

    def test_empty_and_blank(self):
        assert elide_message("") == ("", False)
        assert elide_message("  \n \n") == ("", False)

    def test_exactly_max_len_not_truncated(self):
        text = "x" * BANNER_MAX_CHARS
        assert elide_message(text) == (text, False)

    def test_long_line_cut_with_ellipsis(self):
        summary, truncated = elide_message("y" * 500)
        assert truncated
        assert summary.endswith(ELLIPSIS)
        assert len(summary) == BANNER_MAX_CHARS

    def test_long_line_cut_at_word_boundary(self):
        text = "word " * 60
        summary, truncated = elide_message(text)
        assert truncated
        assert len(summary) <= BANNER_MAX_CHARS
        assert summary.endswith("word" + ELLIPSIS)

    def test_multiline_shows_first_line_only(self):
        summary, truncated = elide_message("first line\nsecond line\nthird")
        assert summary == "first line" + ELLIPSIS
        assert truncated

    def test_leading_blank_lines_skipped(self):
        assert elide_message("\n\n  head  \n")[0] == "head"

    def test_trailing_newline_is_not_multiline(self):
        assert elide_message("one line\n") == ("one line", False)

    def test_custom_max_len(self):
        summary, truncated = elide_message("abcdefghij", max_len=5)
        assert truncated and summary == "abcd" + ELLIPSIS


class TestBannerContent:
    def test_nothing(self):
        assert banner_content("", "") == ("", "")

    def test_error_only(self):
        assert banner_content("bad", "") == ("error", "bad")

    def test_warning_only(self):
        assert banner_content("", "hmm") == ("warning", "hmm")

    def test_error_takes_precedence(self):
        assert banner_content("bad", "hmm") == ("error", "bad")


class TestAppModelBanner:
    def test_error_sets_banner_state(self, tmp_path):
        app, _, _ = make_app(tmp_path)
        assert app.banner_level == "" and app.banner_text == ""
        app.error_message = "boom"
        assert (app.banner_level, app.banner_text, app.banner_summary) == ("error", "boom", "boom")
        assert app.banner_truncated is False

    def test_long_multiline_error_is_elided_but_text_kept(self, tmp_path):
        app, _, _ = make_app(tmp_path)
        msg = "first line\n" + "detail " * 100
        app.error_message = msg
        assert app.banner_summary == "first line" + ELLIPSIS
        assert app.banner_truncated is True
        assert app.banner_text == msg

    def test_warning_sets_banner_state(self, tmp_path):
        app, _, _ = make_app(tmp_path)
        app.warning_message = "careful"
        assert (app.banner_level, app.banner_text) == ("warning", "careful")

    def test_error_takes_precedence_then_warning_returns(self, tmp_path):
        app, _, _ = make_app(tmp_path)
        app.warning_message = "careful"
        app.error_message = "boom"
        assert (app.banner_level, app.banner_text) == ("error", "boom")
        assert app.warning_message == "careful"
        app.dismiss_banner()
        assert app.error_message == ""
        assert (app.banner_level, app.banner_text) == ("warning", "careful")
        app.dismiss_banner()
        assert app.warning_message == ""
        assert (app.banner_level, app.banner_text, app.banner_summary) == ("", "", "")
        assert app.banner_truncated is False

    def test_dismiss_warning_leaves_error_field_alone(self, tmp_path):
        app, _, _ = make_app(tmp_path)
        app.warning_message = "careful"
        app.dismiss_banner()
        assert app.warning_message == "" and app.error_message == ""

    def test_dismiss_with_nothing_is_a_noop(self, tmp_path):
        app, _, _ = make_app(tmp_path)
        app.dismiss_banner()
        assert app.banner_level == ""

    def test_dismissing_load_error_banner_resets_tracking(self, tmp_path):
        app, _, _ = make_app(tmp_path)
        app.error_message = "Could not query podman for: a (x)"
        app._load_error_banner = app.error_message
        app.dismiss_banner()
        assert app.error_message == "" and app._load_error_banner == ""
        # a later, unrelated error is not mistaken for the load-error banner
        app.error_message = "other"
        app._clear_load_error_banner()
        assert app.error_message == "other"

    def test_new_async_action_clears_warning(self, tmp_path):
        app, _, _ = make_app(tmp_path)
        app.warning_message = "old"
        with sync_deferred_call():
            app.run_async("working", lambda: None)
        assert app.warning_message == ""
        assert app.banner_level == ""


def _create(app, project_dir, name="myinst"):
    from unittest.mock import patch

    from .gui_helpers import PROJECT_DIR, create_sync

    with patch(PROJECT_DIR, project_dir):
        create_sync(app, name)


class TestControllerWarnings:
    def test_start_warnings_land_in_warning_field(self, tmp_path):
        import threading
        from unittest.mock import patch

        app, _, project_dir = make_app(tmp_path)
        _create(app, project_dir)

        def fake_ensure(inst, runner, image, *, on_warning=None):
            on_warning("Skipping overlay volume 'x': reason")
            on_warning("second")

        done = threading.Event()
        with (
            sync_deferred_call(done),
            patch("scratch_monkey.gui.controllers.overlays.overlay_ensure_running", fake_ensure),
        ):
            app.overlays.start("myinst")
            wait(done)

        assert app.warning_message == "Skipping overlay volume 'x': reason\nsecond"
        assert app.error_message == ""
        assert app.banner_level == "warning"
        assert app.banner_truncated is True  # two lines -> Details

    def test_refresh_warnings_land_in_warning_field(self, tmp_path):
        import threading
        from unittest.mock import patch

        app, _, project_dir = make_app(tmp_path)
        _create(app, project_dir)

        def fake_refresh(inst, runner, image, *, on_warning=None):
            on_warning("careful")

        done = threading.Event()
        with (
            sync_deferred_call(done),
            patch("scratch_monkey.gui.controllers.overlays.overlay_refresh", fake_refresh),
        ):
            app.overlays.refresh("myinst")
            wait(done)

        assert app.warning_message == "careful"

    def test_no_warnings_leaves_banner_empty(self, tmp_path):
        import threading
        from unittest.mock import patch

        app, _, project_dir = make_app(tmp_path)
        _create(app, project_dir)
        done = threading.Event()
        with (
            sync_deferred_call(done),
            patch("scratch_monkey.gui.controllers.overlays.overlay_ensure_running"),
        ):
            app.overlays.start("myinst")
            wait(done)
        assert app.warning_message == ""
        assert app.banner_level == ""

    def test_failed_start_shows_error_not_warning(self, tmp_path):
        import threading
        from unittest.mock import patch

        app, _, project_dir = make_app(tmp_path)
        _create(app, project_dir)

        def failing(inst, runner, image, *, on_warning=None):
            on_warning("early warning")
            raise RuntimeError("podman broke")

        done = threading.Event()
        with (
            sync_deferred_call(done),
            patch("scratch_monkey.gui.controllers.overlays.overlay_ensure_running", failing),
            patch.object(type(app), "poll_status", lambda self: None),
        ):
            app.overlays.start("myinst")
            wait(done)
        assert app.error_message == "podman broke"
        assert app.warning_message == ""
        assert app.banner_level == "error"
