"""Podman subprocess wrapper — all podman I/O goes through PodmanRunner."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

# Seconds to wait for fast read-only queries (inspect/exists). A stuck container
# lock makes these block forever, so they must time out rather than hang the
# CLI or GUI.
QUERY_TIMEOUT: float = 15

# Seconds to let a timed-out podman process exit after SIGTERM before SIGKILL.
TERMINATE_GRACE: float = 5


class PodmanError(Exception):
    """Raised when a podman command fails."""

    def __init__(self, message: str, returncode: int = -1, stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


@dataclass
class PodmanRunner:
    """Thin wrapper around podman subprocess calls.

    All methods raise PodmanError on non-zero exit codes. Read-only queries
    also raise PodmanError if podman does not answer within QUERY_TIMEOUT.
    Business logic lives elsewhere; this class is fully mockable.
    """

    podman_bin: str = "podman"
    extra_args: list[str] = field(default_factory=list)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _run(
        self,
        args: list[str],
        *,
        check: bool = True,
        capture: bool = True,
        input: str | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [self.podman_bin, *self.extra_args, *args]
        if timeout is None:
            result = subprocess.run(cmd, capture_output=capture, text=True, input=input)
        else:
            try:
                result = self._run_with_timeout(cmd, capture, input, timeout)
            except subprocess.TimeoutExpired as exc:
                target = " ".join(args)
                raise PodmanError(
                    f"podman {target!r} timed out: podman did not respond within "
                    f"{timeout:g}s; a container lock may be stuck. Check with "
                    "`podman ps -a`; if it also hangs, quit any long-running podman "
                    "clients (e.g. Podman Desktop) or run `podman system renumber` / reboot.",
                ) from exc
        if check and result.returncode != 0:
            stderr = result.stderr or ""
            raise PodmanError(
                f"podman {args[0]!r} failed (exit {result.returncode}): {stderr.strip()}",
                returncode=result.returncode,
                stderr=stderr,
            )
        return result

    @staticmethod
    def _run_with_timeout(
        cmd: list[str], capture: bool, input: str | None, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        """Like subprocess.run, but stop podman gracefully on timeout.

        subprocess.run kills a timed-out child with SIGKILL. Podman's container
        locks are shared-memory mutexes that a SIGKILLed process never releases,
        so killing a podman that holds one would create the very stuck-lock state
        we are trying to survive. SIGTERM lets podman run its shutdown handler and
        release locks; SIGKILL is only a last resort if it ignores SIGTERM.

        Raises subprocess.TimeoutExpired once the process has been stopped.
        """
        pipe = subprocess.PIPE if capture else None
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if input is not None else None,
            stdout=pipe,
            stderr=pipe,
            text=True,
        )
        try:
            stdout, stderr = proc.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            proc.terminate()
            try:
                proc.communicate(timeout=TERMINATE_GRACE)
            except subprocess.TimeoutExpired:
                proc.kill()
                # communicate() (not wait()) drains and closes the pipes.
                proc.communicate()
            raise subprocess.TimeoutExpired(cmd, timeout) from exc
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

    # ── image queries ─────────────────────────────────────────────────────────

    def image_exists(self, image: str) -> bool:
        """Return True if the named image exists locally."""
        result = self._run(["image", "exists", image], check=False, timeout=QUERY_TIMEOUT)
        return result.returncode == 0

    # ── container queries ─────────────────────────────────────────────────────

    def container_exists(self, name: str) -> bool:
        """Return True if a container with this name exists (any state)."""
        result = self._run(["container", "exists", name], check=False, timeout=QUERY_TIMEOUT)
        return result.returncode == 0

    def container_running(self, name: str) -> bool:
        """Return True if the named container is currently running."""
        if not self.container_exists(name):
            return False
        result = self._run(
            ["inspect", name, "--format", "{{.State.Running}}"],
            check=False,
            timeout=QUERY_TIMEOUT,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    def container_status(self, name: str) -> str:
        """Return the container status string, or '' if it doesn't exist."""
        if not self.container_exists(name):
            return ""
        result = self._run(
            ["inspect", name, "--format", "{{.State.Status}}"],
            check=False,
            timeout=QUERY_TIMEOUT,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def container_mount_destinations(self, name: str) -> list[str]:
        """Return the destination paths of every mount of the named container.

        Works for stopped containers too.  Returns [] if the container does not
        exist or cannot be inspected.
        """
        result = self._run(
            ["inspect", name, "--format", "{{range .Mounts}}{{.Destination}}\n{{end}}"],
            check=False,
            timeout=QUERY_TIMEOUT,
        )
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.splitlines() if line.strip()]

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        """Run `podman run <args>` and return the result."""
        return self._run(["run", *args], capture=False)

    def exec_in(self, container: str, exec_args: list[str]) -> subprocess.CompletedProcess[str]:
        """Run `podman exec <container> <exec_args>` and return the result."""
        return self._run(["exec", container, *exec_args], capture=False)

    def exec_interactive(self, container: str, exec_args: list[str], *, options: list[str] | None = None) -> None:
        """Run an interactive `podman exec` (no capture, for TTY use).

        Options (--user, --workdir, -e, etc.) go before the container name.
        exec_args is the command to run inside the container.
        """
        opts = options or []
        self._run(["exec", "-it", *opts, container, *exec_args], capture=False)

    def exec_capture(
        self, container: str, exec_args: list[str], *, user: str | None = None, input: str | None = None,
    ) -> str:
        """Run `podman exec` and return captured stdout."""
        cmd = ["exec"]
        if user:
            cmd += ["--user", user]
        if input is not None:
            cmd.append("-i")
        cmd.append(container)
        cmd.extend(exec_args)
        result = self._run(cmd, input=input)
        return result.stdout

    def start(self, name: str) -> None:
        """Start a stopped container."""
        self._run(["start", name])

    def stop(self, name: str, *, time: int = 10) -> None:
        """Stop a running container."""
        self._run(["stop", "--time", str(time), name])

    def remove(self, name: str, *, force: bool = False) -> None:
        """Remove a container."""
        args = ["rm"]
        if force:
            args.append("--force")
        args.append(name)
        self._run(args)

    def rmi(self, image: str) -> None:
        """Remove an image."""
        self._run(["rmi", image])

    def unshare_rmdir(self, paths: list[str]) -> None:
        """Run `podman unshare rmdir -- <paths>`.

        Inside the user namespace the caller owns files that belong to a subuid
        on the host, so this can remove empty directories created by container
        root.  ``rmdir`` never removes a non-empty directory.  Paths are
        processed in order (pass deepest first); raises PodmanError if any
        removal failed — the others are still attempted.
        """
        self._run(["unshare", "rmdir", "--", *paths])

    def unshare_rm_rf(self, paths: list[str]) -> None:
        """Run `podman unshare rm -rf -- <paths>`.

        Inside the user namespace the caller owns files that belong to a subuid
        on the host (written by container root), so this can delete trees that
        plain ``rm``/``shutil.rmtree`` cannot.  Destructive: callers must have
        validated the paths.  As a last line of defence, only absolute paths
        other than ``/`` are accepted.
        """
        if not paths:
            return
        for path in paths:
            if not path.startswith("/") or path.rstrip("/") == "":
                raise ValueError(f"unshare_rm_rf refuses non-absolute or root path {path!r}")
        self._run(["unshare", "rm", "-rf", "--", *paths])

    def tag(self, source: str, target: str) -> None:
        """Tag an image with a new name."""
        self._run(["tag", source, target])

    def commit(self, container: str, image: str) -> None:
        """Commit a container's writable layer to an image tag."""
        self._run(["commit", container, image])

    def build(
        self,
        tag: str,
        context: str,
        *,
        dockerfile: str | None = None,
    ) -> None:
        """Build an image. Streams output to terminal (no capture)."""
        args = ["build", "-t", tag]
        if dockerfile:
            args += ["-f", dockerfile]
        args.append(context)
        self._run(args, capture=False)

    def run_daemon(self, name: str, image: str, run_args: list[str]) -> None:
        """Start a detached/daemon container with the given extra args.

        Unlike run()/build()/the exec methods, this is non-interactive and
        only ever prints a container id on success, so we capture output —
        that way a failure raises PodmanError with real stderr detail
        instead of an empty message.

        An explicit keep-alive command (`sleep infinity`) is required: with
        --init, catatonit is PID 1 and execs the image's default command, but
        images such as the FROM-scratch base image define no CMD, so without
        a command catatonit has nothing to exec and the container dies
        immediately.  No TTY is needed; interactive sessions use `podman exec -it`.
        """
        self._run(["run", "-d", "--init", "--name", name, *run_args, image, "sleep", "infinity"], capture=True)
