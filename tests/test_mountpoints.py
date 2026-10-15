"""Tests for scratch_monkey.mountpoints (pure logic, tmp dirs only)."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath

import pytest

from scratch_monkey.instance import Instance
from scratch_monkey.mountpoints import (
    RECORD_NAME,
    find_stale_stubs,
    host_path_for,
    load_record,
    parse_mounts,
    prepare_mountpoints,
    relative_to_home,
    removed_outside_home_targets,
    save_record,
)

HOME = "/home/tester"


@pytest.fixture
def inst(scratch_instance: Instance) -> Instance:
    return scratch_instance


def _args(*specs: str) -> list[str]:
    out: list[str] = ["--network", "host"]
    for spec in specs:
        out += ["-v", spec]
    return out


# ── parsing / mapping ────────────────────────────────────────────────────────


def test_parse_mounts_handles_modes_and_ignores_garbage():
    args = ["-v", "/a:/b:ro", "-e", "X=1", "-v", "/c:/d", "-v", "bad", "-v", ":/x"]
    assert parse_mounts(args) == [("/a", "/b"), ("/c", "/d")]


def test_relative_to_home_basic():
    assert relative_to_home("/home/tester/.cargo/bin", HOME) == PurePosixPath(".cargo/bin")


def test_relative_to_home_normalises_slashes():
    assert relative_to_home("/home/tester//.cargo/", HOME) == PurePosixPath(".cargo")


@pytest.mark.parametrize(
    "dest",
    [
        "/home/tester",  # the home itself
        "/home/tester/",  # the home itself
        "/home/tester/../other",  # traversal
        "/home/tester/a/../../x",  # traversal
        "/home/other/.cargo",  # a different home
        "/home/testerx/.cargo",  # prefix but not a path parent
        "/opt/x",
        "relative/path",
        "",
    ],
)
def test_relative_to_home_rejects(dest):
    assert relative_to_home(dest, HOME) is None


def test_host_path_for_maps_under_home_dir(tmp_path):
    assert host_path_for("/home/tester/.cargo/bin", HOME, tmp_path) == tmp_path / ".cargo" / "bin"


def test_host_path_for_root_home(tmp_path):
    assert host_path_for("/root/.cache", "/root", tmp_path) == tmp_path / ".cache"
    # The user's home is *not* under /root.
    assert host_path_for("/home/tester/.cache", "/root", tmp_path) is None


def test_host_path_for_traversal_never_escapes(tmp_path):
    assert host_path_for("/home/tester/../../etc", HOME, tmp_path) is None


# ── record ───────────────────────────────────────────────────────────────────


def test_record_roundtrip_and_removal(inst):
    save_record(inst, {".cargo", ".cargo/bin"})
    assert load_record(inst) == {".cargo", ".cargo/bin"}
    save_record(inst, set())
    assert not (inst.directory / RECORD_NAME).exists()
    save_record(inst, set())  # removing a missing file is fine


def test_record_write_leaves_no_temp_files(inst):
    save_record(inst, {"a"})
    assert [p.name for p in inst.directory.iterdir() if p.name.endswith(".tmp")] == []


def test_load_record_ignores_corrupt_and_unsafe_entries(inst):
    path = inst.directory / RECORD_NAME
    path.write_text("not json")
    assert load_record(inst) == set()
    path.write_text(json.dumps({"dirs": ["/etc", "../x", "a/../../b", "", 5, "ok/dir"]}))
    assert load_record(inst) == {"ok/dir"}
    path.write_text(json.dumps({"dirs": "oops"}))
    assert load_record(inst) == set()
    path.write_text(json.dumps([1, 2]))
    assert load_record(inst) == set()


# ── prepare_mountpoints: pre-creation ────────────────────────────────────────


def test_precreate_creates_missing_dirs_and_records_them(inst, tmp_path):
    src = tmp_path / "cargo-src"
    src.mkdir()
    args = _args(f"{src}:{HOME}/.cargo/bin:ro")
    warnings = prepare_mountpoints(inst, args, HOME)
    assert warnings == []
    assert (inst.home_dir / ".cargo" / "bin").is_dir()
    assert load_record(inst) == {".cargo", ".cargo/bin"}


def test_precreate_records_only_dirs_it_created(inst, tmp_path):
    (inst.home_dir / ".cargo").mkdir()  # pre-existing, not ours
    args = _args(f"{tmp_path}:{HOME}/.cargo/bin")
    prepare_mountpoints(inst, args, HOME)
    assert (inst.home_dir / ".cargo" / "bin").is_dir()
    assert load_record(inst) == {".cargo/bin"}


def test_precreate_existing_dir_not_recorded(inst, tmp_path):
    (inst.home_dir / ".cargo").mkdir()
    prepare_mountpoints(inst, _args(f"{tmp_path}:{HOME}/.cargo"), HOME)
    assert load_record(inst) == set()
    assert not (inst.directory / RECORD_NAME).exists()


def test_precreate_nonexistent_source_still_creates(inst):
    prepare_mountpoints(inst, _args(f"/nonexistent/src:{HOME}/data"), HOME)
    assert (inst.home_dir / "data").is_dir()


def test_precreate_named_volume_source(inst):
    prepare_mountpoints(inst, _args(f"myvolume:{HOME}/data"), HOME)
    assert (inst.home_dir / "data").is_dir()


def test_precreate_skips_file_source(inst, tmp_path):
    f = tmp_path / "gitconfig"
    f.write_text("x")
    prepare_mountpoints(inst, _args(f"{f}:{HOME}/.gitconfig:ro"), HOME)
    assert not (inst.home_dir / ".gitconfig").exists()
    assert load_record(inst) == set()


def test_precreate_ignores_targets_outside_home(inst, tmp_path):
    before = sorted(p.name for p in inst.home_dir.iterdir())
    args = _args(
        f"{tmp_path}:/opt/x",
        f"{tmp_path}:/home/linuxbrew",
        f"{tmp_path}:/shared/vol",
        f"{tmp_path}:/run/user/1000/wayland-0",
    )
    prepare_mountpoints(inst, args, HOME)
    assert sorted(p.name for p in inst.home_dir.iterdir()) == before
    assert load_record(inst) == set()


def test_precreate_ignores_home_itself(inst, tmp_path):
    args = _args(f"{inst.home_dir}:{HOME}")
    prepare_mountpoints(inst, args, HOME)
    assert list(inst.home_dir.iterdir()) == []


def test_precreate_traversal_never_touches_outside(inst, tmp_path):
    outside = inst.directory / "escaped"
    args = _args(f"{tmp_path}:{HOME}/../escaped", f"{tmp_path}:{HOME}/a/../../escaped")
    prepare_mountpoints(inst, args, HOME)
    assert not outside.exists()
    assert list(inst.home_dir.iterdir()) == []


def test_precreate_does_not_follow_symlinks_out_of_home(inst, tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    (inst.home_dir / "link").symlink_to(target)
    prepare_mountpoints(inst, _args(f"{tmp_path}:{HOME}/link/sub"), HOME)
    assert list(target.iterdir()) == []
    assert load_record(inst) == set()


def test_precreate_root_home(inst, tmp_path):
    args = _args(f"{tmp_path}:/root/.cache", f"{tmp_path}:{HOME}/.cargo")
    prepare_mountpoints(inst, args, "/root")
    assert (inst.home_dir / ".cache").is_dir()
    assert not (inst.home_dir / ".cargo").exists()


def test_precreate_missing_home_dir_is_noop(inst, tmp_path):
    inst.home_dir.rmdir()
    assert prepare_mountpoints(inst, _args(f"{tmp_path}:{HOME}/x"), HOME) == []
    assert not inst.home_dir.exists()


def test_precreate_mkdir_failure_returns_warning(inst, tmp_path, monkeypatch):
    real_mkdir = Path.mkdir

    def boom(self, *a, **kw):
        if self.name == "bad":
            raise PermissionError(13, "denied")
        return real_mkdir(self, *a, **kw)

    monkeypatch.setattr(Path, "mkdir", boom)
    warnings = prepare_mountpoints(inst, _args(f"{tmp_path}:{HOME}/bad"), HOME)
    assert len(warnings) == 1
    assert "bad" in warnings[0]


# ── prepare_mountpoints: cleanup ─────────────────────────────────────────────


def test_cleanup_removes_recorded_empty_dirs_no_longer_targeted(inst, tmp_path):
    prepare_mountpoints(inst, _args(f"{tmp_path}:{HOME}/.cargo/bin"), HOME)
    assert (inst.home_dir / ".cargo" / "bin").is_dir()

    prepare_mountpoints(inst, _args(), HOME)
    assert not (inst.home_dir / ".cargo").exists()  # deepest first, then parent
    assert load_record(inst) == set()
    assert not (inst.directory / RECORD_NAME).exists()


def test_cleanup_keeps_non_empty_dirs_and_drops_them_from_record(inst, tmp_path):
    prepare_mountpoints(inst, _args(f"{tmp_path}:{HOME}/.cargo/bin"), HOME)
    (inst.home_dir / ".cargo" / "bin" / "tool").write_text("x")

    prepare_mountpoints(inst, _args(), HOME)
    assert (inst.home_dir / ".cargo" / "bin" / "tool").exists()
    assert load_record(inst) == set()


def test_cleanup_keeps_current_targets_and_their_parents(inst, tmp_path):
    prepare_mountpoints(
        inst, _args(f"{tmp_path}:{HOME}/.cargo/bin", f"{tmp_path}:{HOME}/.cargo/reg"), HOME
    )
    prepare_mountpoints(inst, _args(f"{tmp_path}:{HOME}/.cargo/reg"), HOME)
    assert not (inst.home_dir / ".cargo" / "bin").exists()
    assert (inst.home_dir / ".cargo" / "reg").is_dir()
    assert (inst.home_dir / ".cargo").is_dir()
    assert load_record(inst) == {".cargo", ".cargo/reg"}


def test_cleanup_never_touches_unrecorded_dirs(inst):
    (inst.home_dir / "mine" / "empty").mkdir(parents=True)
    prepare_mountpoints(inst, _args(), HOME)
    assert (inst.home_dir / "mine" / "empty").is_dir()


def test_cleanup_ignores_recorded_path_that_became_symlink(inst, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (inst.home_dir / "x").symlink_to(outside)
    save_record(inst, {"x"})
    prepare_mountpoints(inst, _args(), HOME)
    assert outside.is_dir()  # symlink target untouched
    assert (inst.home_dir / "x").is_symlink()
    assert load_record(inst) == set()


def test_cleanup_recorded_already_gone_is_dropped(inst):
    save_record(inst, {"gone"})
    prepare_mountpoints(inst, _args(), HOME)
    assert load_record(inst) == set()


def test_cleanup_recorded_dir_with_root_owned_child_is_kept(inst, tmp_path):
    """A leftover stub child (non-empty parent) is never removed by cleanup."""
    prepare_mountpoints(inst, _args(f"{tmp_path}:{HOME}/a"), HOME)
    (inst.home_dir / "a" / "stub").mkdir()
    prepare_mountpoints(inst, _args(), HOME)
    assert (inst.home_dir / "a" / "stub").is_dir()


def test_file_source_target_recorded_dir_cleaned_when_unmounted(inst, tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    prepare_mountpoints(inst, _args(f"{f}:{HOME}/f"), HOME)
    assert load_record(inst) == set()


# ── removed_outside_home_targets ─────────────────────────────────────────────


def test_removed_outside_basic_and_deepest_first():
    old = ["/opt/x", "/opt/x/y", "/home/linuxbrew", "/usr", "/tmp"]
    new = ["/usr", "/tmp"]
    assert removed_outside_home_targets(old, new, HOME) == [
        "/opt/x/y",
        "/home/linuxbrew",
        "/opt/x",
    ]


def test_removed_outside_excludes_home_relative_and_still_mounted():
    old = [f"{HOME}/.cargo", HOME, "/opt/x", "/opt/keep"]
    new = ["/opt/keep"]
    assert removed_outside_home_targets(old, new, HOME) == ["/opt/x"]


def test_removed_outside_excludes_protected_and_weird_paths():
    old = ["/", "/mnt", "/opt", "/usr/local", "/var/usrlocal", "rel", "/a/../etc", "/data"]
    assert removed_outside_home_targets(old, [], HOME) == ["/data"]


def test_removed_outside_normalises_new_destinations():
    assert removed_outside_home_targets(["/opt/x"], ["/opt/x/"], HOME) == []


# ── find_stale_stubs ─────────────────────────────────────────────────────────

OTHER = 524288


def _owners(*stub_names: str, home: Path):
    """Owner function: listed relative paths are 'owned by a subuid'."""
    stubs = {(home / n) for n in stub_names}

    def owner(path: Path) -> int:
        return OTHER if path in stubs else 1000

    return owner


def test_find_stale_nested_candidates(tmp_path):
    (tmp_path / ".cargo" / "bin").mkdir(parents=True)
    owner = _owners(".cargo", ".cargo/bin", home=tmp_path)
    found = find_stale_stubs(tmp_path, set(), owner_of=owner, my_uid=1000)
    assert found == [tmp_path / ".cargo" / "bin", tmp_path / ".cargo"]


def test_find_stale_requires_foreign_owner(tmp_path):
    (tmp_path / "mine").mkdir()
    assert find_stale_stubs(tmp_path, set(), owner_of=lambda p: 1000, my_uid=1000) == []


def test_find_stale_skips_non_empty(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "file").write_text("x")
    (tmp_path / "b").mkdir()
    owner = _owners("a", "b", home=tmp_path)
    assert find_stale_stubs(tmp_path, set(), owner_of=owner, my_uid=1000) == [tmp_path / "b"]


def test_find_stale_parent_not_candidate_if_child_is_not(tmp_path):
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    (tmp_path / ".local" / "bin" / "x").write_text("1")
    (tmp_path / ".local" / "lib").mkdir()
    owner = _owners(".local", ".local/bin", ".local/lib", home=tmp_path)
    found = find_stale_stubs(tmp_path, set(), owner_of=owner, my_uid=1000)
    assert found == [tmp_path / ".local" / "lib"]


def test_find_stale_candidate_under_user_owned_parent(tmp_path):
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    owner = _owners(".local/bin", home=tmp_path)
    found = find_stale_stubs(tmp_path, set(), owner_of=owner, my_uid=1000)
    assert found == [tmp_path / ".local" / "bin"]  # user-owned .local stays


def test_find_stale_foreign_parent_with_user_owned_child_not_candidate(tmp_path):
    (tmp_path / "a" / "mine").mkdir(parents=True)
    owner = _owners("a", home=tmp_path)
    assert find_stale_stubs(tmp_path, set(), owner_of=owner, my_uid=1000) == []


def test_find_stale_excludes_current_targets_and_parents(tmp_path):
    (tmp_path / ".cargo" / "bin").mkdir(parents=True)
    (tmp_path / ".cargo" / "old").mkdir()
    owner = _owners(".cargo", ".cargo/bin", ".cargo/old", home=tmp_path)
    protected = {".cargo", ".cargo/bin"}
    found = find_stale_stubs(tmp_path, protected, owner_of=owner, my_uid=1000)
    assert found == [tmp_path / ".cargo" / "old"]


def test_find_stale_does_not_follow_or_list_symlinks(tmp_path):
    real = tmp_path.parent / (tmp_path.name + "-outside")
    real.mkdir()
    (real / "inner").mkdir()
    (tmp_path / "link").symlink_to(real)
    (tmp_path / "dirlink_holder").mkdir()
    (tmp_path / "dirlink_holder" / "l").symlink_to(real)
    owner = lambda p: OTHER  # noqa: E731 - everything "foreign"
    found = find_stale_stubs(tmp_path, set(), owner_of=owner, my_uid=1000)
    assert found == []  # symlink is not removable; holder isn't empty


def test_find_stale_empty_home_and_missing_home(tmp_path):
    assert find_stale_stubs(tmp_path, set(), owner_of=lambda p: OTHER, my_uid=1000) == []
    assert find_stale_stubs(tmp_path / "nope", set(), owner_of=lambda p: OTHER, my_uid=1000) == []


def test_find_stale_never_returns_home_itself(tmp_path):
    found = find_stale_stubs(tmp_path, set(), owner_of=lambda p: OTHER, my_uid=1000)
    assert tmp_path not in found


def test_find_stale_default_owner_uses_real_uid(tmp_path):
    """Dirs we just created are owned by us, so nothing is a candidate."""
    (tmp_path / "x" / "y").mkdir(parents=True)
    assert find_stale_stubs(tmp_path, set()) == []
    # ...but pretending to be another uid makes everything foreign.
    found = find_stale_stubs(tmp_path, set(), my_uid=os.getuid() + 1)
    assert found == [tmp_path / "x" / "y", tmp_path / "x"]

