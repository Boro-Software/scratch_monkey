"""Podman subprocess wrapper — all podman I/O goes through PodmanRunner."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field


class PodmanError(Exception):
    """Raised when a podman command fails."""

    def __init__(self, message: str, returncode: int = -1, stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


@dataclass
class PodmanRunner:
    """Thin wrapper around podman subprocess calls.

    All methods raise PodmanError on non-zero exit codes.
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
    ) -> subprocess.CompletedProcess[str]:
        cmd = [self.podman_bin, *self.extra_args, *args]
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            input=input,
        )
        if check and result.returncode != 0:
            stderr = result.stderr or ""
            raise PodmanError(
                f"podman {args[0]!r} failed (exit {result.returncode}): {stderr.strip()}",
                returncode=result.returncode,
                stderr=stderr,
            )
        return result

    # ── image queries ─────────────────────────────────────────────────────────

    def image_exists(self, image: str) -> bool:
        """Return True if the named image exists locally."""
        result = self._run(["image", "exists", image], check=False)
        return result.returncode == 0

    # ── container queries ─────────────────────────────────────────────────────

    def container_exists(self, name: str) -> bool:
        """Return True if a container with this name exists (any state)."""
        result = self._run(["container", "exists", name], check=False)
        return result.returncode == 0

    def container_running(self, name: str) -> bool:
        """Return True if the named container is currently running."""
        if not self.container_exists(name):
            return False
        result = self._run(
            ["inspect", name, "--format", "{{.State.Running}}"], check=False
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    def container_status(self, name: str) -> str:
        """Return the container status string, or '' if it doesn't exist."""
        if not self.container_exists(name):
            return ""
        result = self._run(
            ["inspect", name, "--format", "{{.State.Status}}"], check=False
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

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
        """Start a detached/daemon container with the given extra args."""
        self._run(["run", "-d", "--init", "--name", name, *run_args, image, "sleep", "infinity"], capture=False)
