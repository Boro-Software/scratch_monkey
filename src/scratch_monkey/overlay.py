"""Overlay container management for scratch-monkey instances."""

from __future__ import annotations

import logging
import os
import sys

from .config import generate_overlay_id, save
from .container import PodmanError, PodmanRunner
from .instance import Instance, is_fedora_based
from .mountpoints import prepare_mountpoints, removed_outside_home_targets
from .overlay_mounts import ensure_overlay_dirs
from .run_args import build_run_args, container_home_path

logger = logging.getLogger(__name__)


class OverlayError(Exception):
    """Raised for overlay container operation errors."""


def _ensure_overlay_id(instance: Instance) -> str:
    if not instance.config.overlay_id:
        instance.config.overlay_id = generate_overlay_id()
        save(instance.directory / "scratch.toml", instance.config)
    return instance.config.overlay_id


def ensure_running(
    instance: Instance,
    runner: PodmanRunner,
    image: str,
) -> str:
    """Ensure the overlay container for the instance is running.

    Creates it if missing, starts it if stopped.
    Returns the container name.

    For fedora-based instances, creates the host user, grants sudo, and
    (when ``podman`` is enabled) installs the podman client tooling.
    For scratch-based instances, skips user setup entirely.
    """
    container_name = _ensure_overlay_id(instance)

    exists = runner.container_exists(container_name)

    # Overlay-mode volumes need their upper/work directories to exist before
    # the container is created *and* before an existing one is started
    # (podman start fails on a missing upperdir).
    dir_warnings = ensure_overlay_dirs(instance)

    if not exists:
        run_args, warnings = build_run_args(instance)
        warnings += dir_warnings
        # Create home-relative mount points as the current user *before* the
        # runtime does it as container root (a subuid-owned stub on the host).
        warnings += prepare_mountpoints(instance, run_args, container_home_path())
        for w in warnings:
            print(f"Warning: {w}", file=sys.stderr)
        runner.run_daemon(container_name, image, run_args)

        # User setup is only needed for fedora-based instances.
        # Scratch instances mount /etc from the host read-only — the user
        # already exists on the host and sudo works via host config.
        if is_fedora_based(instance.directory):
            _setup_fedora_user(
                container_name,
                runner,
                sudo=instance.config.sudo,
                podman=instance.config.podman,
            )

    else:
        for w in dir_warnings:
            print(f"Warning: {w}", file=sys.stderr)
        if not runner.container_running(container_name):
            runner.start(container_name)

    return container_name


def _setup_fedora_user(
    container_name: str,
    runner: PodmanRunner,
    *,
    sudo: bool = True,
    podman: bool = False,
) -> None:
    """Create the host user in a fedora overlay container.

    If sudo=True (default), also installs the sudo package and grants
    passwordless sudo to the user. If sudo=False, only useradd is run —
    the sudo package is not installed and no sudoers entry is written.

    If podman=True, also does a best-effort install of the podman client
    tooling (podman, podman-compose).  Failure is logged and warned about
    but never raised.
    """
    username = os.environ.get("USER", "user")
    uid = os.getuid()

    if sudo:
        # Install sudo if missing
        try:
            runner.exec_capture(
                container_name,
                ["bash", "-c", "rpm -q sudo &>/dev/null || dnf install -y sudo"],
                user="root",
            )
        except PodmanError as exc:
            logger.debug("sudo install skipped: %s", exc)

    if podman:
        print(
            "Installing podman client tooling in the container (this may take a minute)...",
            file=sys.stderr,
        )
        try:
            runner.exec_capture(
                container_name,
                [
                    "bash", "-c",
                    "rpm -q podman podman-compose &>/dev/null"
                    " || dnf install -y podman podman-compose",
                ],
                user="root",
            )
        except PodmanError as exc:
            logger.debug("podman client install failed: %s", exc)
            print(
                "Warning: could not install podman/podman-compose in the container "
                f"({exc}); install them manually.",
                file=sys.stderr,
            )

    # Create user (ignore error if already exists)
    try:
        runner.exec_capture(
            container_name,
            ["useradd", "-u", str(uid), "-M", "-s", "/bin/bash", username],
            user="root",
        )
    except PodmanError as exc:
        logger.debug("useradd skipped (user may exist): %s", exc)

    if sudo:
        # Grant passwordless sudo — use tee to avoid shell interpolation of username
        sudoers_line = f"{username} ALL=(ALL) NOPASSWD: ALL\n"
        runner.exec_capture(
            container_name,
            ["tee", f"/etc/sudoers.d/{username}"],
            user="root",
            input=sudoers_line,
        )
        runner.exec_capture(
            container_name,
            ["chmod", "440", f"/etc/sudoers.d/{username}"],
            user="root",
        )


def exec_shell(
    instance: Instance,
    runner: PodmanRunner,
    container_name: str,
    *,
    root: bool = False,
    cmd: str = "/bin/bash",
) -> None:
    """Exec an interactive shell in the overlay container.

    If root=True, runs as root with /root as workdir.
    Otherwise runs as the current user with the instance home as workdir.
    """
    user = os.environ.get("USER", "user")
    container_home = f"/home/{user}"

    if root:
        # Ensure /root exists — scratch images don't create it, and podman
        # exec --workdir /root fails with "No such file or directory".
        # Idempotent (harmless on fedora where /root already exists).
        runner.exec_capture(
            container_name, ["mkdir", "-p", "/root"], user="root",
        )
        options = [
            "--user", "root",
            "--workdir", "/root",
            "-e", "HOME=/root",
            "-e", "USER=root",
            "-e", f"SCRATCH_INSTANCE={instance.name}",
        ]
    else:
        options = [
            "--user", user,
            "--workdir", container_home,
            "-e", f"HOME={container_home}",
            "-e", f"USER={user}",
            "-e", f"SCRATCH_INSTANCE={instance.name}",
        ]

    # Forward display/session env vars so graphical apps work inside the
    # overlay.  The sockets are already bind-mounted at container creation
    # time (via build_run_args), but the env vars must be set on each exec
    # to reflect the current host state (DISPLAY can change between sessions).
    cfg = instance.config
    if cfg.wayland:
        uid = os.getuid()
        options += ["-e", "WAYLAND_DISPLAY=wayland-0", "-e", f"XDG_RUNTIME_DIR=/run/user/{uid}"]
    if cfg.x11:
        display = os.environ.get("DISPLAY", "")
        if display:
            options += ["-e", f"DISPLAY={display}"]
            xauth_file = os.environ.get("XAUTHORITY", os.path.expanduser("~/.Xauthority"))
            if os.path.exists(xauth_file):
                options += ["-e", "XAUTHORITY=/tmp/.container-Xauthority"]
    if cfg.ssh:
        ssh_sock = os.environ.get("SSH_AUTH_SOCK", "")
        if ssh_sock and os.path.exists(ssh_sock):
            options += ["-e", f"SSH_AUTH_SOCK={ssh_sock}"]

    runner.exec_interactive(container_name, [cmd], options=options)


def refresh(
    instance: Instance,
    runner: PodmanRunner,
    image: str,
    *,
    discard: bool = False,
) -> str:
    """Recreate the overlay container so it picks up fresh run args.

    `ensure_running()` only ever `podman start`s an existing container, so
    changes to how the container is created (new mounts, new podman flags,
    etc.) never reach it once it exists. `refresh()` fixes that by removing
    and recreating the container.

    By default (discard=False) the container's writable layer is committed
    to the instance's image tag (`instance.name`) *before* removal, so any
    software installed ad-hoc inside the container (e.g. `dnf install vim`)
    is preserved — the recreated container is built from that snapshot.
    Pass discard=True to skip the snapshot and recreate from `image`
    instead, discarding the writable layer entirely.

    If committing fails, the error propagates and the container is left
    in place — the writable layer is never destroyed without a successful
    snapshot first.

    Returns the container name.
    """
    container_name = _ensure_overlay_id(instance)

    if not runner.container_exists(container_name):
        # Nothing to preserve — just ensure it's running.
        return ensure_running(instance, runner, image)

    # Learn the old container's mount destinations from podman itself: after a
    # config change the previous config is no longer known, but the container
    # still records exactly what it was created with.  Must happen before
    # removal.  Best effort — a failed inspect must never block the refresh.
    try:
        old_destinations = runner.container_mount_destinations(container_name)
    except PodmanError as exc:
        logger.debug("could not inspect old mounts: %s", exc)
        old_destinations = []

    if runner.container_running(container_name):
        runner.stop(container_name)

    recreate_image = image
    if not discard:
        # Commit BEFORE remove: if this raises, the container must survive.
        runner.commit(container_name, instance.name)
        recreate_image = instance.name

    runner.remove(container_name)

    ensure_running(instance, runner, recreate_image)
    _remove_stale_outside_stubs(runner, container_name, old_destinations)
    return container_name


def _remove_stale_outside_stubs(
    runner: PodmanRunner, container_name: str, old_destinations: list[str]
) -> None:
    """rmdir mount-point stubs outside the home that are no longer mounted.

    The runtime creates a missing mount-point directory in the container's
    writable layer, and `refresh` commits that layer, so the stub survives the
    removal of the mount.  Remove, as root inside the new container, the
    stubs of destinations that were mounted before but are not any more.
    ``rmdir`` only removes empty directories; failures (non-empty, already
    gone, not a directory) are ignored.  Home-relative stubs live on the host
    and are handled by `prepare_mountpoints`.
    """
    if not old_destinations:
        return
    try:
        new_destinations = runner.container_mount_destinations(container_name)
        stale = removed_outside_home_targets(
            old_destinations, new_destinations, container_home_path()
        )
        if stale:
            runner.exec_capture(container_name, ["rmdir", "--", *stale], user="root")
    except PodmanError as exc:
        logger.debug("stale mount-point cleanup skipped: %s", exc)


def reset(instance: Instance, runner: PodmanRunner) -> bool:
    """Remove the overlay container for an instance.

    Returns True if a container was removed, False if none existed.
    """
    container_name = _ensure_overlay_id(instance)
    if not runner.container_exists(container_name):
        return False
    runner.remove(container_name, force=True)
    return True
