"""Tests for overlay-mode volumes: spec, mount ids, run args, dirs, reset, delete."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from scratch_monkey.cli.main import cli
from scratch_monkey.config import (
    ConfigError,
    InstanceConfig,
    load,
    save,
    split_volume_spec,
    validate_volume_spec,
)
from scratch_monkey.container import PodmanError, PodmanRunner
from scratch_monkey.export import export_command
from scratch_monkey.instance import Instance, InstanceError, _remove_instance_dir, create, delete
from scratch_monkey.mountpoints import home_mount_targets, parse_mounts, prepare_mountpoints
from scratch_monkey.overlay import ensure_running, refresh
from scratch_monkey.overlay_mounts import (
    OverlayEntry,
    OverlayHostFacts,
    OverlayMountError,
    ensure_overlay_dirs,
    is_overlay_spec,
    list_overlay_entries,
    mount_id,
    overlay_dirs,
    overlay_host_facts,
    overlays_root,
    parse_mountinfo,
    reset_overlays,
    translate_volume,
)
from scratch_monkey.run_args import build_run_args


@pytest.fixture(autouse=True)
def _user(monkeypatch):
    monkeypatch.setenv("USER", "tester")


@pytest.fixture
def lower(tmp_path) -> Path:
    d = tmp_path / "lower"
    d.mkdir()
    return d


def _with_overlay(inst: Instance, lower: Path, dest: str = "/home/tester/.config/foo") -> str:
    inst.config.volumes = [f"{lower}:{dest}:overlay"]
    return dest


# ─── spec ─────────────────────────────────────────────────────────────────────


class TestSpec:
    def test_overlay_spec_valid(self):
        validate_volume_spec("/h:/c:overlay")
        assert split_volume_spec("/h:/c:overlay") == ("/h", "/c", "overlay")
        assert is_overlay_spec("/h:/c:overlay")

    def test_non_overlay_specs(self):
        assert not is_overlay_spec("/h:/c:ro")
        assert not is_overlay_spec("/h:/c")
        assert not is_overlay_spec("garbage")

    def test_unknown_mode_still_accepted_and_passed_through(self, tmp_path):
        validate_volume_spec("/h:/c:bogus")
        assert translate_volume("/h:/c:bogus", tmp_path) == "/h:/c:bogus"

    def test_empty_paths_still_rejected(self):
        with pytest.raises(ConfigError):
            validate_volume_spec(":/c:overlay")
        with pytest.raises(ConfigError):
            validate_volume_spec("/h::overlay")

    def test_round_trip_through_config(self, tmp_path):
        cfg = InstanceConfig(volumes=["/h:/c:overlay", "/a:/b:ro"])
        path = tmp_path / "scratch.toml"
        save(path, cfg)
        assert load(path).volumes == ["/h:/c:overlay", "/a:/b:ro"]

    def test_gui_entry_round_trip(self):
        pytest.importorskip("atom")
        from scratch_monkey.gui.models.entries import VolumeMountEntry

        entry = VolumeMountEntry.from_spec("/h:/c:overlay")
        assert (entry.host_path, entry.container_path, entry.mode) == ("/h", "/c", "overlay")
        assert entry.to_spec() == "/h:/c:overlay"

    def test_shared_volumes_stay_ro_rw(self):
        from scratch_monkey.shared import SharedError, parse_shared_entry

        with pytest.raises(SharedError):
            parse_shared_entry("vol:overlay")


# ─── mount id ─────────────────────────────────────────────────────────────────


class TestMountId:
    def test_deterministic(self):
        assert mount_id("/home/u/.config/foo") == mount_id("/home/u/.config/foo")

    def test_readable_slug_plus_hash(self):
        mid = mount_id("/home/djoyce/.config/foo")
        assert mid.startswith("home-djoyce-.config-foo-")
        assert len(mid.rsplit("-", 1)[1]) == 8

    def test_different_paths_differ(self):
        assert mount_id("/a/b") != mount_id("/a/c")

    def test_slug_collisions_still_differ(self):
        # '/a/b' and '/a-b' have the same slug; the hash tells them apart.
        assert mount_id("/a/b") != mount_id("/a-b")

    def test_normalised(self):
        assert mount_id("/a/b/") == mount_id("/a//b") == mount_id("/a/./b") == mount_id("/a/b")

    def test_sanitises_unsafe_characters(self):
        mid = mount_id("/data/my dir/we:ird,$x")
        assert all(c.isalnum() or c in "._-" for c in mid)

    def test_long_path_slug_truncated(self):
        assert len(mount_id("/" + "x" * 300)) <= 48 + 1 + 8

    def test_root_path(self):
        assert len(mount_id("/")) == 8

    def test_independent_of_host_path(self, tmp_path):
        a = translate_volume("/one:/c/d:overlay", tmp_path)
        b = translate_volume("/two:/c/d:overlay", tmp_path)
        assert a.split(":", 2)[2] == b.split(":", 2)[2]


# ─── build_run_args ───────────────────────────────────────────────────────────


class TestBuildRunArgs:
    def test_translation(self, scratch_instance, lower):
        dest = _with_overlay(scratch_instance, lower)
        args, warnings = build_run_args(scratch_instance)
        upper, work = overlay_dirs(scratch_instance.directory, dest)
        expected = f"{lower}:{dest}:O,upperdir={upper},workdir={work}"
        assert expected in args
        assert args[args.index(expected) - 1] == "-v"
        assert not warnings

    def test_paths_are_absolute_under_dot_overlays(self, scratch_instance, lower):
        dest = _with_overlay(scratch_instance, lower)
        upper, work = overlay_dirs(scratch_instance.directory, dest)
        assert upper.is_absolute() and work.is_absolute()
        assert upper == overlays_root(scratch_instance.directory) / mount_id(dest) / "upper"
        assert work.name == "work" and work.parent == upper.parent

    def test_relative_instance_dir_made_absolute(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        out = translate_volume("/h:/c:overlay", Path("inst"))
        assert f"upperdir={tmp_path}/inst/.overlays/" in out

    def test_pure_no_filesystem_changes(self, scratch_instance, lower):
        _with_overlay(scratch_instance, lower)
        build_run_args(scratch_instance)
        assert not (scratch_instance.directory / ".overlays").exists()

    def test_plain_O_never_emitted(self, scratch_instance, lower):
        _with_overlay(scratch_instance, lower)
        args, _ = build_run_args(scratch_instance)
        assert not [a for a in args if a.endswith(":O")]

    def test_ro_rw_and_unknown_pass_through(self, scratch_instance):
        scratch_instance.config.volumes = ["/a:/b:ro", "/c:/d:rw", "/e:/f:bogus"]
        args, _ = build_run_args(scratch_instance)
        for spec in scratch_instance.config.volumes:
            assert spec in args

    def test_unusable_directory_skipped_with_warning(self, tmp_path):
        inst_dir = tmp_path / "we,ird"
        inst_dir.mkdir()
        (inst_dir / "Dockerfile").write_text("FROM scratch_monkey\n")
        cfg = InstanceConfig(volumes=["/h:/c:overlay"])
        inst = Instance("x", inst_dir, cfg, inst_dir / "home")
        args, warnings = build_run_args(inst)
        assert not [a for a in args if "upperdir" in a]
        assert any("overlay" in w for w in warnings)


# ─── lower-layer limits (graphroot / submounts) ───────────────────────────────


def _volumes(inst: Instance, *specs: str) -> None:
    inst.config.volumes = list(specs)


def _skip_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if "Skipping overlay volume" in w]


class TestLowerLimits:
    def _facts(self, graphroot: Path) -> OverlayHostFacts:
        return OverlayHostFacts(storage_roots=frozenset({os.path.realpath(graphroot)}))

    def test_source_equal_to_graphroot_skipped(self, scratch_instance, tmp_path):
        graph = tmp_path / "storage"
        graph.mkdir()
        _volumes(scratch_instance, f"{graph}:/c/g:overlay")
        args, warnings = build_run_args(scratch_instance, host_facts=self._facts(graph))
        assert not [a for a in args if "upperdir" in a or "/c/g" in a]
        (msg,) = _skip_warnings(warnings)
        assert f"{graph}:/c/g:overlay" in msg
        assert "container storage" in msg and "active mounts" in msg
        assert "subdirectories" in msg and "'ro'" in msg

    def test_ancestor_of_graphroot_skipped(self, scratch_instance, tmp_path):
        graph = tmp_path / "home" / ".local" / "share" / "containers" / "storage"
        graph.mkdir(parents=True)
        dot_local = tmp_path / "home" / ".local"
        _volumes(scratch_instance, f"{dot_local}:/c/l:overlay")
        args, warnings = build_run_args(scratch_instance, host_facts=self._facts(graph))
        assert "/c/l" not in " ".join(args)
        assert len(_skip_warnings(warnings)) == 1

    def test_sibling_and_unrelated_kept(self, scratch_instance, tmp_path):
        graph = tmp_path / "home" / ".local" / "share" / "containers" / "storage"
        graph.mkdir(parents=True)
        cargo = tmp_path / "home" / ".cargo"
        cargo.mkdir()
        share = tmp_path / "home" / ".local" / "share" / "app"
        share.mkdir()
        _volumes(scratch_instance, f"{cargo}:/c/cargo:overlay", f"{share}:/c/app:overlay")
        args, warnings = build_run_args(scratch_instance, host_facts=self._facts(graph))
        assert len([a for a in args if "upperdir=" in a]) == 2
        assert not _skip_warnings(warnings)

    def test_symlinked_source_resolved(self, scratch_instance, tmp_path):
        """/home -> /var/home: graphroot is the real path, the volume uses the link."""
        real_home = tmp_path / "var" / "home"
        graph = real_home / "u" / ".local" / "share" / "containers" / "storage"
        graph.mkdir(parents=True)
        (tmp_path / "home").symlink_to(real_home)
        via_link = tmp_path / "home" / "u" / ".local"
        _volumes(scratch_instance, f"{via_link}:/c/l:overlay")
        args, warnings = build_run_args(scratch_instance, host_facts=self._facts(graph))
        assert "upperdir" not in " ".join(args)
        assert len(_skip_warnings(warnings)) == 1

    def test_symlinked_graphroot_resolved(self, scratch_instance, tmp_path):
        """The reverse: the graphroot reported by podman goes through a symlink."""
        real = tmp_path / "var" / "home" / "u" / ".local" / "share" / "storage"
        real.mkdir(parents=True)
        (tmp_path / "home").symlink_to(tmp_path / "var" / "home")
        runner = MagicMock(spec=PodmanRunner)
        runner.graph_root.return_value = str(tmp_path / "home" / "u" / ".local" / "share" / "storage")
        _volumes(scratch_instance, f"{tmp_path / 'var' / 'home' / 'u' / '.local'}:/c/l:overlay")
        facts = overlay_host_facts(scratch_instance, runner, read_mountinfo=lambda: "")
        args, warnings = build_run_args(scratch_instance, host_facts=facts)
        assert "upperdir" not in " ".join(args)
        assert len(_skip_warnings(warnings)) == 1

    def test_submount_beneath_source_skipped(self, scratch_instance, lower):
        sub = lower / "deep" / "mnt"
        sub.mkdir(parents=True)
        mountinfo = f"36 1 0:30 / {sub} rw - tmpfs tmpfs rw\n"
        runner = MagicMock(spec=PodmanRunner)
        runner.graph_root.return_value = None
        _volumes(scratch_instance, f"{lower}:/c/x:overlay")
        facts = overlay_host_facts(scratch_instance, runner, read_mountinfo=lambda: mountinfo)
        args, warnings = build_run_args(scratch_instance, host_facts=facts)
        assert "upperdir" not in " ".join(args)
        (msg,) = _skip_warnings(warnings)
        assert str(sub) in msg

    def test_source_that_is_itself_a_mountpoint_is_fine(self, scratch_instance, lower):
        mountinfo = f"36 1 0:30 / {lower} rw - tmpfs tmpfs rw\n"
        runner = MagicMock(spec=PodmanRunner)
        runner.graph_root.return_value = None
        _volumes(scratch_instance, f"{lower}:/c/x:overlay")
        facts = overlay_host_facts(scratch_instance, runner, read_mountinfo=lambda: mountinfo)
        args, warnings = build_run_args(scratch_instance, host_facts=facts)
        assert "upperdir" in " ".join(args)
        assert not _skip_warnings(warnings)

    def test_non_overlay_modes_unaffected(self, scratch_instance, tmp_path):
        graph = tmp_path / "storage"
        graph.mkdir()
        _volumes(scratch_instance, f"{graph}:/c/a:ro", f"{graph}:/c/b:rw", f"{graph}:/c/c")
        args, warnings = build_run_args(scratch_instance, host_facts=self._facts(graph))
        for spec in scratch_instance.config.volumes:
            assert spec in args
        assert not _skip_warnings(warnings)

    def test_no_facts_skips_nothing(self, scratch_instance, tmp_path):
        graph = tmp_path / "storage"
        graph.mkdir()
        _volumes(scratch_instance, f"{graph}:/c/g:overlay")
        args, _ = build_run_args(scratch_instance)
        assert "upperdir" in " ".join(args)

    def test_build_run_args_does_not_touch_proc(self, scratch_instance, lower):
        _volumes(scratch_instance, f"{lower}:/c/x:overlay")
        with patch("scratch_monkey.overlay_mounts.read_host_mountinfo") as proc:
            build_run_args(scratch_instance, host_facts=OverlayHostFacts())
        proc.assert_not_called()


class TestHostFacts:
    def test_no_overlay_volumes_no_queries(self, scratch_instance):
        _volumes(scratch_instance, "/a:/b:ro")
        runner = MagicMock(spec=PodmanRunner)
        reader = MagicMock(return_value="")
        facts = overlay_host_facts(scratch_instance, runner, read_mountinfo=reader)
        assert facts == OverlayHostFacts()
        runner.graph_root.assert_not_called()
        reader.assert_not_called()

    def test_gathers_graphroot_and_mounts(self, scratch_instance, tmp_path, lower):
        runner = MagicMock(spec=PodmanRunner)
        runner.graph_root.return_value = str(tmp_path / "g")
        _volumes(scratch_instance, f"{lower}:/c/x:overlay")
        facts = overlay_host_facts(
            scratch_instance, runner, read_mountinfo=lambda: "1 0 0:1 / /mnt/x rw - tmpfs t rw\n"
        )
        assert facts.storage_roots == {os.path.realpath(tmp_path / "g")}
        assert "/mnt/x" in facts.mount_points

    def test_unknown_graphroot_is_ignored(self, scratch_instance, lower):
        runner = MagicMock(spec=PodmanRunner)
        runner.graph_root.return_value = None
        _volumes(scratch_instance, f"{lower}:/c/x:overlay")
        facts = overlay_host_facts(scratch_instance, runner, read_mountinfo=lambda: "")
        assert facts.storage_roots == frozenset()

    def test_parse_mountinfo_decodes_escapes(self):
        text = "1 0 0:1 / /a\\040b rw - tmpfs t rw\n2 1 0:2 / /c rw shared:1 - tmpfs t rw\nbad\n"
        assert parse_mountinfo(text) == {"/a b", "/c"}


class TestEnsureRunningSkipsStorageOverlay:
    def _setup(self, inst, tmp_path):
        graph = tmp_path / "home" / ".local" / "share" / "containers" / "storage"
        graph.mkdir(parents=True)
        _volumes(inst, f"{tmp_path / 'home' / '.local'}:/c/l:overlay")
        runner = MagicMock(spec=PodmanRunner)
        runner.container_exists.return_value = False
        runner.container_running.return_value = False
        runner.exec_capture.return_value = ""
        runner.container_mount_destinations.return_value = []
        runner.graph_root.return_value = str(graph)
        return runner

    def test_skipped_volume_not_passed_and_warned_on_stderr(self, scratch_instance, tmp_path, capsys):
        runner = self._setup(scratch_instance, tmp_path)
        ensure_running(scratch_instance, runner, "scratch_monkey")
        run_args = runner.run_daemon.call_args.args[2]
        assert "/c/l" not in " ".join(run_args)
        assert "Warning: Skipping overlay volume" in capsys.readouterr().err

    def test_on_warning_receives_warnings_instead_of_stderr(self, scratch_instance, tmp_path, capsys):
        runner = self._setup(scratch_instance, tmp_path)
        got: list[str] = []
        ensure_running(scratch_instance, runner, "scratch_monkey", on_warning=got.append)
        assert len(_skip_warnings(got)) == 1
        assert capsys.readouterr().err == ""

    def test_refresh_forwards_on_warning(self, scratch_instance, tmp_path):
        runner = self._setup(scratch_instance, tmp_path)
        runner.container_exists.side_effect = [True, False]
        got: list[str] = []
        refresh(scratch_instance, runner, "scratch_monkey", on_warning=got.append)
        assert len(_skip_warnings(got)) == 1


# ─── directory creation ───────────────────────────────────────────────────────


class TestEnsureDirs:
    def test_creates_upper_and_work_mode_0700(self, scratch_instance, lower):
        dest = _with_overlay(scratch_instance, lower)
        assert ensure_overlay_dirs(scratch_instance) == []
        upper, work = overlay_dirs(scratch_instance.directory, dest)
        for d in (upper, work, upper.parent, upper.parent.parent):
            assert d.is_dir()
            assert d.stat().st_mode & 0o777 == 0o700
            assert d.stat().st_uid == os.getuid()

    def test_idempotent_and_keeps_contents(self, scratch_instance, lower):
        dest = _with_overlay(scratch_instance, lower)
        ensure_overlay_dirs(scratch_instance)
        upper, _ = overlay_dirs(scratch_instance.directory, dest)
        (upper / "f").write_text("x")
        ensure_overlay_dirs(scratch_instance)
        assert (upper / "f").read_text() == "x"

    def test_no_overlay_volumes_creates_nothing(self, scratch_instance):
        scratch_instance.config.volumes = ["/a:/b:ro"]
        ensure_overlay_dirs(scratch_instance)
        assert not (scratch_instance.directory / ".overlays").exists()

    def test_file_source_warns(self, scratch_instance, tmp_path):
        f = tmp_path / "afile"
        f.write_text("x")
        scratch_instance.config.volumes = [f"{f}:/c:overlay"]
        assert any("not a directory" in w for w in ensure_overlay_dirs(scratch_instance))

    def test_mkdir_failure_is_a_warning(self, scratch_instance, lower):
        _with_overlay(scratch_instance, lower)
        with patch.object(Path, "mkdir", side_effect=PermissionError("nope")):
            warnings = ensure_overlay_dirs(scratch_instance)
        assert any("Could not create overlay directory" in w for w in warnings)

    def test_created_before_run_daemon(self, scratch_instance, mock_runner, lower):
        dest = _with_overlay(scratch_instance, lower)
        upper, work = overlay_dirs(scratch_instance.directory, dest)
        seen = {}
        mock_runner.run_daemon.side_effect = lambda *a, **k: seen.update(
            up=upper.is_dir(), wk=work.is_dir()
        )
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        assert seen == {"up": True, "wk": True}
        args = mock_runner.run_daemon.call_args[0][2]
        assert any(a.startswith(f"{lower}:{dest}:O,upperdir={upper}") for a in args)

    def test_created_before_start_of_existing_container(
        self, scratch_instance, mock_runner, lower
    ):
        dest = _with_overlay(scratch_instance, lower)
        upper, work = overlay_dirs(scratch_instance.directory, dest)
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = False
        seen = {}
        mock_runner.start.side_effect = lambda *a, **k: seen.update(
            up=upper.is_dir(), wk=work.is_dir()
        )
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        assert seen == {"up": True, "wk": True}
        mock_runner.run_daemon.assert_not_called()

    def test_recreated_if_deleted_between_restarts(self, scratch_instance, mock_runner, lower):
        dest = _with_overlay(scratch_instance, lower)
        upper, _ = overlay_dirs(scratch_instance.directory, dest)
        ensure_overlay_dirs(scratch_instance)
        upper.rmdir()
        mock_runner.container_exists.return_value = True
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        assert upper.is_dir()

    def test_refresh_recreates_dirs_before_new_container(
        self, scratch_instance, mock_runner, lower
    ):
        dest = _with_overlay(scratch_instance, lower)
        upper, _ = overlay_dirs(scratch_instance.directory, dest)
        mock_runner.container_exists.side_effect = [True, False]
        seen = {}
        mock_runner.run_daemon.side_effect = lambda *a, **k: seen.update(up=upper.is_dir())
        refresh(scratch_instance, mock_runner, "scratch_monkey")
        assert seen == {"up": True}

    def test_warning_printed_for_existing_container(self, scratch_instance, mock_runner, capsys):
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        with patch("scratch_monkey.overlay.ensure_overlay_dirs", return_value=["boom"]):
            ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        assert "boom" in capsys.readouterr().err

    def test_run_instance_non_overlay_mode_creates_dirs(self, tmp_path, lower):
        inst_dir = tmp_path / "insts" / "proj"
        inst_dir.mkdir(parents=True)
        (inst_dir / "home").mkdir()
        (inst_dir / "Dockerfile").write_text("FROM scratch_monkey\n")
        (inst_dir / "scratch.toml").write_text(
            f'overlay = false\nvolumes = ["{lower}:/home/tester/cfg:overlay"]\n'
        )
        runner = MagicMock(spec=PodmanRunner)
        runner.image_exists.return_value = True
        upper = inst_dir / ".overlays" / mount_id("/home/tester/cfg") / "upper"
        seen = {}
        runner.run.side_effect = lambda args: seen.update(up=upper.is_dir())
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=runner):
            result = CliRunner().invoke(
                cli, ["--instances-dir", str(inst_dir.parent), "run", "proj"]
            )
        assert result.exit_code == 0, result.output
        assert seen == {"up": True}
        assert (inst_dir / "home" / "cfg").is_dir()  # mount point pre-created as user


# ─── mountpoints interaction ──────────────────────────────────────────────────


class TestMountpointParsing:
    SPEC = (
        "/src/dir:/home/tester/.config/foo:"
        "O,upperdir=/i/.overlays/x/upper,workdir=/i/.overlays/x/work"
    )

    def test_parse_mounts_takes_second_field_as_destination(self):
        assert parse_mounts(["-v", self.SPEC]) == [("/src/dir", "/home/tester/.config/foo")]

    def test_home_relative_target(self):
        targets = home_mount_targets(["-v", self.SPEC], "/home/tester")
        assert [(s, r.as_posix()) for s, r in targets] == [("/src/dir", ".config/foo")]

    def test_mount_point_precreated_for_overlay(self, scratch_instance, mock_runner, lower):
        _with_overlay(scratch_instance, lower)
        ensure_running(scratch_instance, mock_runner, "scratch_monkey")
        assert (scratch_instance.home_dir / ".config" / "foo").is_dir()

    def test_directory_source_gets_directory_mount_point(self, scratch_instance, lower):
        _with_overlay(scratch_instance, lower)
        args, _ = build_run_args(scratch_instance)
        assert prepare_mountpoints(scratch_instance, args, "/home/tester") == []
        assert (scratch_instance.home_dir / ".config" / "foo").is_dir()


# ─── export ───────────────────────────────────────────────────────────────────


class TestExport:
    def test_fresh_run_path_does_not_use_config_volumes(self, scratch_instance, tmp_path, lower):
        # The wrapper's fresh `podman run` only mounts the instance home (never
        # cfg.volumes), so there is no overlay translation to emit there.
        _with_overlay(scratch_instance, lower)
        out = export_command(scratch_instance, "/bin/foo", bin_dir=tmp_path / "bin")
        text = out.read_text()
        assert "upperdir" not in text
        assert str(lower) not in text


# ─── reset-overlays ───────────────────────────────────────────────────────────


def _instance_dir(tmp_path: Path, volumes: list[str], overlay_id: str = "sm-reset01") -> Path:
    base = tmp_path / "insts"
    base.mkdir(exist_ok=True)
    d = base / "proj"
    d.mkdir()
    (d / "home").mkdir()
    (d / "Dockerfile").write_text("FROM scratch_monkey\n")
    save(d / "scratch.toml", InstanceConfig(volumes=volumes, overlay_id=overlay_id))
    return d


def _invoke(tmp_path, runner, args, input=None):
    with patch("scratch_monkey.cli.main.PodmanRunner", return_value=runner):
        return CliRunner().invoke(
            cli, ["--instances-dir", str(tmp_path / "insts"), *args], input=input
        )


class TestResetOverlays:
    @pytest.fixture
    def setup(self, tmp_path, lower, mock_runner):
        a, b = "/home/tester/a", "/home/tester/b"
        d = _instance_dir(tmp_path, [f"{lower}:{a}:overlay", f"{lower}:{b}:overlay"])
        inst = Instance.from_directory(d)
        ensure_overlay_dirs(inst)
        # an orphan: a mount id that is no longer in the config
        orphan = d / ".overlays" / "gone-12345678"
        (orphan / "upper").mkdir(parents=True)
        (orphan / "work").mkdir()
        (orphan / "upper" / "stale").write_text("x")
        for dest in (a, b):
            up, _ = overlay_dirs(d, dest)
            (up / "changed").write_text("x")
        # Stand-in for `podman unshare rm -rf` (the files are ours in tests).
        mock_runner.unshare_rm_rf.side_effect = lambda paths: [shutil.rmtree(p) for p in paths]
        return d, a, b, orphan

    def test_resets_all_including_orphans(self, setup, tmp_path, mock_runner):
        d, a, b, orphan = setup
        result = _invoke(tmp_path, mock_runner, ["reset-overlays", "proj", "-y"])
        assert result.exit_code == 0, result.output
        paths = mock_runner.unshare_rm_rf.call_args[0][0]
        assert len(paths) == 6
        assert str(orphan / "upper") in paths
        # contents gone, empty dirs recreated for a later `podman start`
        for dest in (a, b):
            up, wk = overlay_dirs(d, dest)
            assert up.is_dir() and wk.is_dir()
            assert list(up.iterdir()) == []
        assert (orphan / "upper").is_dir()
        assert list((orphan / "upper").iterdir()) == []

    def test_only_overlays_paths_passed(self, setup, tmp_path, mock_runner):
        d, *_ = setup
        _invoke(tmp_path, mock_runner, ["reset-overlays", "proj", "-y"])
        for p in mock_runner.unshare_rm_rf.call_args[0][0]:
            assert Path(p).parent.parent == d / ".overlays"
            assert Path(p).name in ("upper", "work")

    def test_mount_filter(self, setup, tmp_path, mock_runner):
        d, a, b, orphan = setup
        result = _invoke(tmp_path, mock_runner, ["reset-overlays", "proj", "--mount", a, "-y"])
        assert result.exit_code == 0, result.output
        paths = mock_runner.unshare_rm_rf.call_args[0][0]
        assert sorted(paths) == sorted(str(p) for p in overlay_dirs(d, a))
        assert (overlay_dirs(d, b)[0] / "changed").exists()
        assert (orphan / "upper" / "stale").exists()

    def test_mount_filter_unknown_is_nothing_to_do(self, setup, tmp_path, mock_runner):
        result = _invoke(
            tmp_path, mock_runner, ["reset-overlays", "proj", "--mount", "/nope", "-y"]
        )
        assert result.exit_code == 0
        assert "No overlay data" in result.output
        mock_runner.unshare_rm_rf.assert_not_called()

    def test_running_container_stopped_first(self, setup, tmp_path, mock_runner):
        order = []
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_runner.stop.side_effect = lambda n, **k: order.append("stop")
        real_rm = mock_runner.unshare_rm_rf.side_effect

        def rm(paths):
            order.append("rm")
            real_rm(paths)

        mock_runner.unshare_rm_rf.side_effect = rm
        result = _invoke(tmp_path, mock_runner, ["reset-overlays", "proj", "-y"])
        assert result.exit_code == 0, result.output
        assert order == ["stop", "rm"]
        mock_runner.stop.assert_called_once_with("sm-reset01")
        mock_runner.start.assert_not_called()
        assert "Stopping overlay container" in result.output
        assert "brings it back" in result.output

    def test_stopped_container_not_stopped_again(self, setup, tmp_path, mock_runner):
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = False
        result = _invoke(tmp_path, mock_runner, ["reset-overlays", "proj", "-y"])
        assert result.exit_code == 0
        mock_runner.stop.assert_not_called()
        assert "brings it back" not in result.output

    def test_confirm_declined(self, setup, tmp_path, mock_runner):
        result = _invoke(tmp_path, mock_runner, ["reset-overlays", "proj"], input="n\n")
        assert result.exit_code != 0
        mock_runner.unshare_rm_rf.assert_not_called()
        mock_runner.stop.assert_not_called()

    def test_confirm_accepted_and_mentions_orphans(self, setup, tmp_path, mock_runner):
        result = _invoke(tmp_path, mock_runner, ["reset-overlays", "proj"], input="y\n")
        assert result.exit_code == 0, result.output
        assert "orphaned" in result.output
        assert "no longer in scratch.toml" in result.output  # listed
        mock_runner.unshare_rm_rf.assert_called_once()

    def test_confirm_mentions_running_container(self, setup, tmp_path, mock_runner):
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        result = _invoke(tmp_path, mock_runner, ["reset-overlays", "proj"], input="n\n")
        assert "will be stopped" in result.output
        mock_runner.stop.assert_not_called()

    def test_nothing_to_do(self, tmp_path, mock_runner):
        _instance_dir(tmp_path, ["/a:/b:ro"])
        result = _invoke(tmp_path, mock_runner, ["reset-overlays", "proj"])
        assert result.exit_code == 0
        assert "No overlay data" in result.output
        mock_runner.unshare_rm_rf.assert_not_called()

    def test_unknown_instance(self, tmp_path, mock_runner):
        (tmp_path / "insts").mkdir()
        result = _invoke(tmp_path, mock_runner, ["reset-overlays", "nope", "-y"])
        assert result.exit_code == 1

    def test_podman_error_reported(self, setup, tmp_path, mock_runner):
        mock_runner.unshare_rm_rf.side_effect = PodmanError("boom")
        result = _invoke(tmp_path, mock_runner, ["reset-overlays", "proj", "-y"])
        assert result.exit_code == 1
        assert "boom" in result.output


class TestResetSafety:
    def test_rejects_paths_outside_overlays(self, scratch_instance, mock_runner, tmp_path):
        evil = OverlayEntry("x", tmp_path / "elsewhere", None)
        with pytest.raises(OverlayMountError):
            reset_overlays(scratch_instance, mock_runner, [evil], "c")
        mock_runner.unshare_rm_rf.assert_not_called()
        mock_runner.stop.assert_not_called()

    def test_rejects_dotdot(self, scratch_instance, mock_runner):
        root = overlays_root(scratch_instance.directory)
        evil = OverlayEntry("..", root / "..", None)
        with pytest.raises(OverlayMountError):
            reset_overlays(scratch_instance, mock_runner, [evil], "c")
        mock_runner.unshare_rm_rf.assert_not_called()

    def test_rejects_symlinked_leaf(self, scratch_instance, mock_runner, tmp_path):
        target = tmp_path / "precious"
        target.mkdir()
        entry_dir = overlays_root(scratch_instance.directory) / "id-1"
        entry_dir.mkdir(parents=True)
        (entry_dir / "upper").symlink_to(target)
        entry = OverlayEntry("id-1", entry_dir, None)
        with pytest.raises(OverlayMountError):
            reset_overlays(scratch_instance, mock_runner, [entry], "c")
        mock_runner.unshare_rm_rf.assert_not_called()
        assert target.exists()

    def test_listing_ignores_symlinks_and_files(self, scratch_instance, tmp_path):
        root = overlays_root(scratch_instance.directory)
        root.mkdir(parents=True)
        (root / "file").write_text("x")
        real = tmp_path / "real"
        real.mkdir()
        (root / "link").symlink_to(real)
        (root / "ok-1").mkdir()
        assert [e.mount_id for e in list_overlay_entries(scratch_instance)] == ["ok-1"]

    def test_listing_marks_orphans(self, scratch_instance, lower):
        dest = _with_overlay(scratch_instance, lower)
        ensure_overlay_dirs(scratch_instance)
        (overlays_root(scratch_instance.directory) / "gone-1").mkdir()
        entries = {e.mount_id: e for e in list_overlay_entries(scratch_instance)}
        assert entries[mount_id(dest)].container_path == dest
        assert entries["gone-1"].orphan

    def test_listing_without_overlays_dir(self, scratch_instance):
        assert list_overlay_entries(scratch_instance) == []


class TestRunnerUnshareRm:
    def test_command(self):
        with patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            PodmanRunner().unshare_rm_rf(["/a/b", "/c/d"])
        assert run.call_args[0][0] == ["podman", "unshare", "rm", "-rf", "--", "/a/b", "/c/d"]

    @pytest.mark.parametrize("bad", ["/", "relative/path", "", "//"])
    def test_refuses_dangerous_paths(self, bad):
        with patch("subprocess.run") as run, pytest.raises(ValueError):
            PodmanRunner().unshare_rm_rf(["/ok", bad])
        run.assert_not_called()

    def test_empty_is_noop(self):
        with patch("subprocess.run") as run:
            PodmanRunner().unshare_rm_rf([])
        run.assert_not_called()


# ─── instance delete fallback ─────────────────────────────────────────────────


def _perm_denied():
    return patch("scratch_monkey.instance.shutil.rmtree", side_effect=PermissionError("x"))


class TestDeleteFallback:
    def _make(self, instances_dir, project_dir):
        create("proj", instances_dir, "scratch_monkey", project_dir)
        return instances_dir / "proj"

    def test_no_fallback_on_success(self, instances_dir, project_dir, mock_runner):
        d = self._make(instances_dir, project_dir)
        delete("proj", instances_dir, mock_runner)
        assert not d.exists()
        mock_runner.unshare_rm_rf.assert_not_called()

    def test_fallback_on_permission_error(self, instances_dir, project_dir, mock_runner):
        d = self._make(instances_dir, project_dir)
        real_rmtree = shutil.rmtree  # captured before the patch replaces shutil.rmtree
        mock_runner.unshare_rm_rf.side_effect = lambda paths: real_rmtree(paths[0])
        with _perm_denied():
            delete("proj", instances_dir, mock_runner)
        mock_runner.unshare_rm_rf.assert_called_once_with([str(d.resolve())])
        assert not d.exists()

    def test_other_errors_do_not_fall_back(self, instances_dir, project_dir, mock_runner):
        self._make(instances_dir, project_dir)
        with patch("scratch_monkey.instance.shutil.rmtree", side_effect=OSError("io")):
            with pytest.raises(OSError):
                delete("proj", instances_dir, mock_runner)
        mock_runner.unshare_rm_rf.assert_not_called()

    def test_fallback_failure_propagates(self, instances_dir, project_dir, mock_runner):
        self._make(instances_dir, project_dir)
        mock_runner.unshare_rm_rf.side_effect = PodmanError("nope")
        with _perm_denied(), pytest.raises(PodmanError):
            delete("proj", instances_dir, mock_runner)

    def test_still_present_after_fallback_is_error(self, instances_dir, project_dir, mock_runner):
        self._make(instances_dir, project_dir)
        with _perm_denied(), pytest.raises(InstanceError, match="Could not fully remove"):
            delete("proj", instances_dir, mock_runner)

    def test_real_unwritable_subdir_triggers_fallback(
        self, instances_dir, project_dir, mock_runner
    ):
        d = self._make(instances_dir, project_dir)
        locked = d / "home" / ".cargo"
        (locked / "bin").mkdir(parents=True)
        locked.chmod(0o500)  # like a subuid-owned parent: nothing inside can be unlinked
        if os.access(locked / "bin", os.W_OK) and os.getuid() == 0:
            locked.chmod(0o700)
            pytest.skip("permissions not enforced for root")

        def fake_unshare(paths):
            locked.chmod(0o700)
            shutil.rmtree(paths[0])

        mock_runner.unshare_rm_rf.side_effect = fake_unshare
        try:
            delete("proj", instances_dir, mock_runner)
        finally:
            if locked.exists():
                locked.chmod(0o700)
        mock_runner.unshare_rm_rf.assert_called_once()
        assert not d.exists()

    def test_validation_refuses_non_child(self, instances_dir, tmp_path, mock_runner):
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        with _perm_denied(), pytest.raises(InstanceError, match="Refusing"):
            _remove_instance_dir(outside, instances_dir, mock_runner)
        mock_runner.unshare_rm_rf.assert_not_called()

    def test_validation_refuses_instances_dir_itself(self, instances_dir, mock_runner):
        with _perm_denied(), pytest.raises(InstanceError, match="Refusing"):
            _remove_instance_dir(instances_dir, instances_dir, mock_runner)
        mock_runner.unshare_rm_rf.assert_not_called()

    def test_validation_refuses_symlink(self, instances_dir, tmp_path, mock_runner):
        target = tmp_path / "elsewhere"
        target.mkdir()
        link = instances_dir / "lnk"
        link.symlink_to(target)
        with _perm_denied(), pytest.raises(InstanceError, match="Refusing"):
            _remove_instance_dir(link, instances_dir, mock_runner)
        mock_runner.unshare_rm_rf.assert_not_called()
        assert target.exists()

    def test_validation_refuses_dotdot_name(self, instances_dir, mock_runner):
        with _perm_denied(), pytest.raises(InstanceError, match="Refusing"):
            _remove_instance_dir(instances_dir / "..", instances_dir, mock_runner)
        mock_runner.unshare_rm_rf.assert_not_called()
