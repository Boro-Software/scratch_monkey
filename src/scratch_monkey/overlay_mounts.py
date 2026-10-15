"""Overlay-mode volume mounts: host directory as a read-only lower layer.

A volume spec ``host:container:overlay`` mounts *host* so that the container
sees a writable directory while the host original is never modified.  Podman
implements this natively as ``-v SRC:DST:O,upperdir=UPPER,workdir=WORK``; with
a user-managed upper/work pair podman never deletes it, so container-side
changes persist across container restarts and re-creation (``refresh``).  Plain
``:O`` (no upperdir) is deliberately never used: it is ephemeral.

Each overlay mount gets a stable per-instance directory pair::

    <instance_dir>/.overlays/<mount-id>/upper
    <instance_dir>/.overlays/<mount-id>/work

``mount-id`` derives from the *container* path only (readable slug plus a short
hash), so it survives a change of the host path.

This module is separate from ``mountpoints.py`` (which deals with stub
directories inside the instance home) because it owns a different on-disk
area (``.overlays``) and a different lifecycle (reset / orphan handling).

Layering: ``run_args`` uses the pure helpers (``translate_volume``); everything
that touches the filesystem or podman lives in the lower half of this module.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

from .config import VOLUME_MODE_OVERLAY, ConfigError, split_volume_spec
from .container import PodmanRunner
from .instance import Instance

OVERLAYS_DIRNAME = ".overlays"
UPPER_NAME = "upper"
WORK_NAME = "work"

_SLUG_MAX = 48
_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


class OverlayMountError(ConfigError):
    """Raised for unusable overlay mounts or unsafe reset paths."""


# ── pure helpers ─────────────────────────────────────────────────────────────


def is_overlay_spec(spec: str) -> bool:
    """True if *spec* is a well-formed ``host:container:overlay`` volume spec."""
    try:
        return split_volume_spec(spec)[2] == VOLUME_MODE_OVERLAY
    except ConfigError:
        return False


def mount_id(container_path: str) -> str:
    """Return the stable mount id for an overlay mounted at *container_path*.

    ``<slug>-<8 hex digits>``: the slug is the normalised path with ``/`` turned
    into ``-`` and anything outside ``[A-Za-z0-9._-]`` into ``_`` (truncated to
    keep names short); the hash of the normalised path keeps distinct paths
    distinct even when their slugs collide.
    """
    norm = posixpath.normpath("/" + container_path.lstrip("/"))
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:8]
    slug = _UNSAFE_RE.sub("_", norm.strip("/").replace("/", "-"))[:_SLUG_MAX].strip("-")
    return f"{slug}-{digest}" if slug else digest


def overlays_root(instance_dir: Path) -> Path:
    """Absolute path of ``<instance_dir>/.overlays``."""
    return Path(os.path.abspath(instance_dir)) / OVERLAYS_DIRNAME


def overlay_dirs(instance_dir: Path, container_path: str) -> tuple[Path, Path]:
    """Return the absolute ``(upper, work)`` directories for a container path."""
    base = overlays_root(instance_dir) / mount_id(container_path)
    return base / UPPER_NAME, base / WORK_NAME


def translate_volume(spec: str, instance_dir: Path) -> str:
    """Translate a volume spec into the value for ``podman run -v``.

    Non-overlay specs are returned unchanged (including ones with unknown
    modes).  ``host:container:overlay`` becomes
    ``host:container:O,upperdir=<abs upper>,workdir=<abs work>``.  Pure: never
    touches the filesystem.  Raises OverlayMountError when the directory paths
    cannot be expressed in podman's option syntax (``,`` or ``:`` in them).
    """
    host, container, mode = split_volume_spec(spec)
    if mode != VOLUME_MODE_OVERLAY:
        return spec
    upper, work = overlay_dirs(instance_dir, container)
    for path in (upper, work):
        if "," in str(path) or ":" in str(path):
            raise OverlayMountError(
                f"overlay directory {path} contains ',' or ':' and cannot be used "
                "as a podman overlay option; move the instances directory"
            )
    return f"{host}:{container}:O,upperdir={upper},workdir={work}"


def overlay_container_paths(volumes: list[str]) -> list[str]:
    """Container paths of every well-formed overlay-mode volume spec."""
    paths = []
    for spec in volumes:
        if is_overlay_spec(spec):
            paths.append(split_volume_spec(spec)[1])
    return paths


# ── directory creation ───────────────────────────────────────────────────────


def ensure_overlay_dirs(instance: Instance) -> list[str]:
    """Create the upper/work directories of every configured overlay mount.

    Created as the current user with mode 0700, so they are never root-owned
    stubs.  Must run before ``podman run`` *and* before ``podman start`` of an
    existing container (a missing upperdir makes start fail).  Idempotent; never
    raises for filesystem problems, returns human-readable warnings instead.
    """
    warnings: list[str] = []
    for spec in instance.config.volumes:
        if not is_overlay_spec(spec):
            continue
        host, container, _ = split_volume_spec(spec)
        try:
            translate_volume(spec, instance.directory)
        except OverlayMountError as exc:
            warnings.append(str(exc))
            continue
        if os.path.exists(host) and not os.path.isdir(host):
            warnings.append(f"overlay source {host} is not a directory; podman will refuse it.")
        for path in overlay_dirs(instance.directory, container):
            try:
                _mkdir_private(path)
            except OSError as exc:
                warnings.append(f"Could not create overlay directory {path}: {exc}")
                break
    return warnings


def _mkdir_private(path: Path) -> None:
    """Create ``.overlays/<id>/<leaf>`` (all three levels) with mode 0700."""
    path.parent.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    path.mkdir(mode=0o700, exist_ok=True)


# ── reset ────────────────────────────────────────────────────────────────────


@dataclass
class OverlayEntry:
    """One ``.overlays/<mount-id>`` directory."""

    mount_id: str
    directory: Path
    container_path: str | None  # None: no longer in the config (orphan)

    @property
    def orphan(self) -> bool:
        return self.container_path is None

    @property
    def upper(self) -> Path:
        return self.directory / UPPER_NAME

    @property
    def work(self) -> Path:
        return self.directory / WORK_NAME


def list_overlay_entries(instance: Instance) -> list[OverlayEntry]:
    """List every overlay directory of the instance, orphans included.

    Looks at what is on disk, not just the config: directories for mounts that
    were removed from the config still hold container-side data.  Symlinks and
    non-directories are ignored.  Sorted by mount id.
    """
    root = overlays_root(instance.directory)
    known = {mount_id(c): c for c in overlay_container_paths(instance.config.volumes)}
    try:
        children = sorted(root.iterdir())
    except OSError:
        return []
    entries = []
    for child in children:
        if child.is_symlink() or not child.is_dir():
            continue
        entries.append(OverlayEntry(child.name, child, known.get(child.name)))
    return entries


def _validate_target(root: Path, path: Path) -> None:
    """Raise OverlayMountError unless *path* is ``root/<id>/{upper,work}``."""
    if not path.is_absolute() or ".." in path.parts:
        raise OverlayMountError(f"refusing to touch {path}: not a normalised absolute path")
    if path.parent.parent != root or path.name not in (UPPER_NAME, WORK_NAME):
        raise OverlayMountError(f"refusing to touch {path}: not inside {root}")
    for part in (root, path.parent, path):
        if part.is_symlink():
            raise OverlayMountError(f"refusing to touch {path}: {part} is a symlink")


def reset_overlays(
    instance: Instance,
    runner: PodmanRunner,
    entries: list[OverlayEntry],
    container_name: str,
) -> bool:
    """Discard container-side changes of *entries*, back to the host originals.

    Stops the overlay container first if it is running (an upperdir must not be
    deleted while mounted) and leaves it stopped.  Removal goes through
    ``podman unshare rm -rf`` because container root writes files owned by a
    subuid.  The empty upper/work directories are recreated afterwards so a
    later ``podman start`` works.  Only ``<instance>/.overlays/<id>/{upper,work}``
    paths are ever passed on.  Returns True if the container was stopped.
    """
    root = overlays_root(instance.directory)
    targets: list[Path] = []
    for entry in entries:
        for path in (entry.upper, entry.work):
            _validate_target(root, path)
            if os.path.lexists(path):
                targets.append(path)

    stopped = False
    if runner.container_exists(container_name) and runner.container_running(container_name):
        runner.stop(container_name)
        stopped = True

    if targets:
        runner.unshare_rm_rf([str(p) for p in targets])
    for entry in entries:
        for path in (entry.upper, entry.work):
            _mkdir_private(path)
    return stopped
