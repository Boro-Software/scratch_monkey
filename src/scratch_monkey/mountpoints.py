"""Mount-point stub handling for instance home directories.

The instance home is a host directory bind-mounted at the container home.
When another mount targets a path *inside* that home which does not exist,
the OCI runtime creates the mount-point directory as container root.  Under
rootless ``--userns=keep-id`` that is the user's first subuid on the host, so
the empty stub lands in the host home owned by a subuid: the user cannot
delete it, and removing the mount does not remove it.

This module avoids and cleans up that situation:

* ``prepare_mountpoints`` pre-creates missing home-relative mount points on the
  host as the current user (before any container is created), records the
  directories it created in ``<instance>/.mountpoints.json`` and removes
  recorded directories that are no longer mount targets (only if empty).
* ``find_stale_stubs`` detects stubs created by earlier versions (empty,
  not owned by the current user, not a current mount target) so that
  ``prune-mountpoints`` can remove them via ``podman unshare rmdir``.
* ``removed_outside_home_targets`` computes which mount destinations outside
  the container home vanished between two container generations, so that the
  stubs baked into the committed writable layer can be ``rmdir``-ed.

The functions here never delete a non-empty directory (``rmdir`` only) and
never touch a host path outside the instance home directory.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import stat
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath

from .instance import Instance

logger = logging.getLogger(__name__)

RECORD_NAME = ".mountpoints.json"

# Exact container paths that are never rmdir-ed from a writable layer, even if
# they were once mount destinations: standard top-level directories (which may
# legitimately exist empty in an image) and the scratch-image host mounts.
PROTECTED_CONTAINER_PATHS = frozenset(
    {
        "/",
        "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64", "/media",
        "/mnt", "/opt", "/proc", "/root", "/run", "/sbin", "/srv", "/sys",
        "/tmp", "/usr", "/var",
        "/usr/local", "/var/usrlocal", "/var/opt",
    }
)  # fmt: skip


# ── run-arg parsing / path mapping (pure) ────────────────────────────────────


def parse_mounts(run_args: Iterable[str]) -> list[tuple[str, str]]:
    """Extract ``(source, container_path)`` pairs from ``-v`` run arguments.

    Accepts ``source:dest`` and ``source:dest:opts``.  Specs with no
    destination are ignored.
    """
    args = list(run_args)
    mounts: list[tuple[str, str]] = []
    for flag, spec in zip(args, args[1:]):
        if flag != "-v":
            continue
        parts = spec.split(":")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        mounts.append((parts[0], parts[1]))
    return mounts


def relative_to_home(container_path: str, container_home: str) -> PurePosixPath | None:
    """Return *container_path* relative to *container_home*, or None.

    None when the path is relative, contains ``..``, is outside the container
    home, or is the container home itself.
    """
    path = PurePosixPath(container_path)
    if not path.is_absolute() or ".." in path.parts:
        return None
    try:
        rel = path.relative_to(PurePosixPath(container_home))
    except ValueError:
        return None
    if not rel.parts:
        return None
    return rel


def host_path_for(container_path: str, container_home: str, home_dir: Path) -> Path | None:
    """Map a container path under the container home to its host path."""
    rel = relative_to_home(container_path, container_home)
    if rel is None:
        return None
    return Path(home_dir).joinpath(*rel.parts)


def home_mount_targets(
    run_args: Iterable[str], container_home: str
) -> list[tuple[str, PurePosixPath]]:
    """Return ``(source, home-relative path)`` for every mount inside the home."""
    targets = []
    for source, dest in parse_mounts(run_args):
        rel = relative_to_home(dest, container_home)
        if rel is not None:
            targets.append((source, rel))
    return targets


def _with_ancestors(rels: Iterable[PurePosixPath]) -> set[str]:
    """Return every rel path in *rels* plus all of their ancestors, as strings."""
    out: set[str] = set()
    for rel in rels:
        for i in range(1, len(rel.parts) + 1):
            out.add(PurePosixPath(*rel.parts[:i]).as_posix())
    return out


def _depth(rel: str) -> int:
    return len(PurePosixPath(rel).parts)


# ── record file ──────────────────────────────────────────────────────────────


def _valid_rel(value: object) -> str | None:
    """Return a normalised home-relative path string, or None if unacceptable."""
    if not isinstance(value, str) or not value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return None
    return path.as_posix()


def load_record(instance: Instance) -> set[str]:
    """Load the set of home-relative directories scratch-monkey created."""
    try:
        data = json.loads((instance.directory / RECORD_NAME).read_text())
        entries = data.get("dirs", [])
    except (OSError, ValueError, AttributeError):
        return set()
    if not isinstance(entries, list):
        return set()
    return {rel for rel in map(_valid_rel, entries) if rel is not None}


def save_record(instance: Instance, dirs: set[str]) -> None:
    """Atomically write the record; remove the file when nothing is recorded."""
    path = instance.directory / RECORD_NAME
    if not dirs:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    content = json.dumps({"version": 1, "dirs": sorted(dirs)}, indent=2) + "\n"
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".mountpoints-", suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── host filesystem operations ───────────────────────────────────────────────


def _safe_dir(home_dir: Path, rel: str) -> Path | None:
    """Return home_dir/rel if every component is a real directory, else None.

    Refuses symlinks anywhere along the way so that an operation can never be
    redirected outside *home_dir*.
    """
    current = Path(home_dir)
    for part in PurePosixPath(rel).parts:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            return None
    return current


def _precreate(home_dir: Path, rel: PurePosixPath, created: set[str]) -> str | None:
    """Create home_dir/rel component by component as the current user.

    Adds each directory it actually creates to *created*.  Returns a warning
    string on failure, None otherwise.  Existing directories are left alone;
    symlinks and non-directories in the way abort quietly (the runtime copes
    with those itself).
    """
    current = Path(home_dir)
    parts: list[str] = []
    for part in rel.parts:
        current = current / part
        parts.append(part)
        if current.is_symlink():
            return None
        if current.is_dir():
            continue
        if current.exists():
            return None
        try:
            current.mkdir()
        except FileExistsError:
            continue
        except OSError as exc:
            return f"Could not pre-create mount point {current}: {exc}"
        created.add(PurePosixPath(*parts).as_posix())
    return None


def cleanup_stale(instance: Instance, keep: set[str], record: set[str]) -> set[str]:
    """Remove recorded directories that are no longer needed (empty only).

    *keep* holds home-relative paths that must stay (current targets and their
    ancestors).  Returns the updated record.  A recorded directory is dropped
    from the record once it is gone or turned out to be non-empty (it now holds
    user data and is no longer ours); on any other failure it stays recorded so
    a later run retries.
    """
    new_record = set(record)
    for rel in sorted((r for r in record if r not in keep), key=_depth, reverse=True):
        path = _safe_dir(instance.home_dir, rel)
        if path is None:
            new_record.discard(rel)
            continue
        try:
            path.rmdir()
        except OSError as exc:
            if exc.errno in (errno.ENOTEMPTY, errno.EEXIST, errno.ENOENT):
                new_record.discard(rel)
            else:
                logger.debug("could not remove mount point %s: %s", path, exc)
            continue
        new_record.discard(rel)
    return new_record


def prepare_mountpoints(
    instance: Instance, run_args: Iterable[str], container_home: str
) -> list[str]:
    """Pre-create home-relative mount points and clean up stale ones.

    Call before creating a container with *run_args*.  Never raises for
    filesystem problems; returns human-readable warnings instead.
    """
    warnings: list[str] = []
    home_dir = Path(instance.home_dir)
    if not home_dir.is_dir():
        return warnings

    targets = home_mount_targets(run_args, container_home)
    record = load_record(instance)
    keep = _with_ancestors(rel for _, rel in targets)

    new_record = cleanup_stale(instance, keep, record)

    created: set[str] = set()
    for source, rel in targets:
        # A file bind mount needs a file stub, not a directory: leave it alone.
        if os.path.exists(source) and not os.path.isdir(source):
            continue
        warning = _precreate(home_dir, rel, created)
        if warning:
            warnings.append(warning)
    new_record |= created

    if new_record != record:
        try:
            save_record(instance, new_record)
        except OSError as exc:
            warnings.append(f"Could not write {instance.directory / RECORD_NAME}: {exc}")
    return warnings


# ── outside-home targets (writable-layer variant) ────────────────────────────


def removed_outside_home_targets(
    old_destinations: Iterable[str],
    new_destinations: Iterable[str],
    container_home: str,
) -> list[str]:
    """Mount destinations that disappeared and may have left a stub behind.

    Only absolute paths outside the container home (home-relative stubs live on
    the host and are handled by ``prepare_mountpoints``) and not in
    ``PROTECTED_CONTAINER_PATHS``.  Deepest first, so nested stubs can be
    removed with plain ``rmdir``.
    """
    new = {PurePosixPath(d).as_posix() for d in new_destinations}
    home = PurePosixPath(container_home)
    result: set[str] = set()
    for dest in old_destinations:
        path = PurePosixPath(dest)
        if not path.is_absolute() or ".." in path.parts:
            continue
        norm = path.as_posix()
        if norm in new or norm in PROTECTED_CONTAINER_PATHS:
            continue
        if path == home or home in path.parents:
            continue
        result.add(norm)
    return sorted(result, key=lambda p: (-_depth(p), p))


# ── detection of pre-existing stubs (prune-mountpoints) ──────────────────────


def _lstat_uid(path: Path) -> int:
    return os.lstat(path).st_uid


def protected_rels(instance: Instance, run_args_by_home: Iterable[tuple[list[str], str]]) -> set[str]:
    """Home-relative paths of all current mount targets and their ancestors."""
    rels: list[PurePosixPath] = []
    for run_args, container_home in run_args_by_home:
        rels.extend(rel for _, rel in home_mount_targets(run_args, container_home))
    return _with_ancestors(rels)


def find_stale_stubs(
    home_dir: Path,
    protected: set[str],
    *,
    owner_of: Callable[[Path], int] | None = None,
    my_uid: int | None = None,
) -> list[Path]:
    """Find removable stub directories under *home_dir*.

    A directory qualifies when it is not a symlink, is owned by someone other
    than *my_uid* (a subuid / container root), is not a current mount target
    or an ancestor of one (*protected*, home-relative posix paths), lives on
    the same filesystem as *home_dir*, and is empty or contains only
    directories that themselves qualify.  The result is ordered deepest first.
    Symlinks are never followed.
    """
    home_dir = Path(home_dir)
    if owner_of is None:
        owner_of = _lstat_uid
    if my_uid is None:
        my_uid = os.getuid()
    try:
        home_dev = os.lstat(home_dir).st_dev
    except OSError:
        return []

    candidates: dict[str, bool] = {}
    # topdown=False visits every directory after all of its subdirectories.
    for dirpath, dirnames, filenames in os.walk(home_dir, topdown=False, followlinks=False):
        here = Path(dirpath)
        if here == home_dir:
            continue
        rel = here.relative_to(home_dir).as_posix()
        ok = False
        try:
            st = os.lstat(here)
            ok = (
                not stat.S_ISLNK(st.st_mode)
                and st.st_dev == home_dev
                and owner_of(here) != my_uid
                and rel not in protected
                and not filenames
                and all(
                    candidates.get(here.joinpath(d).relative_to(home_dir).as_posix(), False)
                    for d in dirnames
                )
            )
        except OSError:
            ok = False
        candidates[rel] = ok

    found = [home_dir / rel for rel, ok in candidates.items() if ok]
    return sorted(found, key=lambda p: (-len(p.parts), str(p)))
