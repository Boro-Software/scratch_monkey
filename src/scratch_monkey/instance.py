"""Instance lifecycle management for scratch-monkey."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import InstanceConfig, load, save, validate_name
from .container import PodmanError, PodmanRunner


class InstanceError(Exception):
    """Raised for instance operation errors."""


@dataclass
class Instance:
    """Represents a scratch-monkey instance."""

    name: str
    directory: Path
    config: InstanceConfig
    home_dir: Path

    @classmethod
    def from_directory(cls, directory: Path) -> Instance:
        """Load an Instance from an existing instance directory."""
        name = directory.name
        config = load(directory / "scratch.toml")
        if config.home:
            home_dir = Path(config.home)
        else:
            home_dir = directory / "home"
        return cls(name=name, directory=directory, config=config, home_dir=home_dir)


# ─── Creation ─────────────────────────────────────────────────────────────────

_SCRATCH_DOCKERFILE = """\
FROM {base_image}
# Add your customizations here.
# COPY and ADD work. RUN does not (no shell in image).
# Use multi-stage builds to pull binaries from other images.
"""

_FEDORA_DOCKERFILE = """\
FROM {base_image}
# Add your customizations here.
# Full Fedora base — RUN, COPY, and ADD all work.
"""


def create(
    name: str,
    instances_dir: Path,
    base_image: str,
    project_dir: Path,
) -> Instance:
    """Create a new instance directory.

    Raises InstanceError if the instance already exists or the name is invalid.
    """
    validate_name(name)
    instances_dir = Path(instances_dir)
    project_dir = Path(project_dir)
    instance_dir = instances_dir / name

    if instance_dir.exists():
        raise InstanceError(f"Instance {name!r} already exists at {instance_dir}")

    instance_dir.mkdir(parents=True)
    (instance_dir / "home").mkdir()

    # Copy default config
    default_toml = project_dir / "scratch.toml.default"
    if default_toml.exists():
        shutil.copy(default_toml, instance_dir / "scratch.toml")
    else:
        save(instance_dir / "scratch.toml", InstanceConfig())

    # Generate Dockerfile
    if "fedora" in base_image:
        dockerfile_content = _FEDORA_DOCKERFILE.format(base_image=base_image)
    else:
        dockerfile_content = _SCRATCH_DOCKERFILE.format(base_image=base_image)
    (instance_dir / "Dockerfile").write_text(dockerfile_content)

    # Create empty .env
    (instance_dir / ".env").touch()

    config = load(instance_dir / "scratch.toml")
    home_dir = instance_dir / "home"
    return Instance(name=name, directory=instance_dir, config=config, home_dir=home_dir)


def clone(source: str, dest: str, instances_dir: Path, runner: PodmanRunner | None = None) -> Instance:
    """Clone an existing instance (copies Dockerfile + config, fresh home/).

    Raises InstanceError if source doesn't exist or dest already exists.
    If a runner is provided and the source has a built image, tags it for the dest.
    """
    validate_name(dest)
    instances_dir = Path(instances_dir)
    src_dir = instances_dir / source
    dst_dir = instances_dir / dest

    if not src_dir.is_dir():
        raise InstanceError(f"Source instance {source!r} not found at {src_dir}")
    if dst_dir.exists():
        raise InstanceError(f"Destination instance {dest!r} already exists at {dst_dir}")

    dst_dir.mkdir(parents=True)
    (dst_dir / "home").mkdir()

    shutil.copy(src_dir / "Dockerfile", dst_dir / "Dockerfile")
    shutil.copy(src_dir / "scratch.toml", dst_dir / "scratch.toml")

    env_src = src_dir / ".env"
    if env_src.exists():
        shutil.copy(env_src, dst_dir / ".env")
    else:
        (dst_dir / ".env").touch()

    config = load(dst_dir / "scratch.toml")
    if config.overlay_id:
        config.overlay_id = ""
        save(dst_dir / "scratch.toml", config)

    if runner is not None and runner.image_exists(source):
        runner.tag(source, dest)

    home_dir = dst_dir / "home"
    return Instance(name=dest, directory=dst_dir, config=config, home_dir=home_dir)


def delete(name: str, instances_dir: Path, runner: PodmanRunner) -> None:
    """Remove an instance directory and its podman image.

    Raises InstanceError if the instance does not exist.
    """
    validate_name(name)
    instances_dir = Path(instances_dir)
    instance_dir = instances_dir / name

    if not instance_dir.is_dir():
        raise InstanceError(f"Instance {name!r} not found at {instance_dir}")

    # Remove overlay container if it exists
    cfg = load(instance_dir / "scratch.toml")
    overlay_name = cfg.overlay_id if cfg.overlay_id else f"{name}-overlay"
    if runner.container_exists(overlay_name):
        runner.remove(overlay_name, force=True)

    if runner.image_exists(name):
        runner.rmi(name)

    shutil.rmtree(instance_dir)


def rename(old_name: str, new_name: str, instances_dir: Path, runner: PodmanRunner) -> Instance:
    """Rename an instance: directory, image tag, and clean up legacy overlay.

    Raises InstanceError if old doesn't exist, new already exists, or name is invalid.
    Returns the Instance loaded from the new directory.
    """
    validate_name(new_name)
    instances_dir = Path(instances_dir)
    old_dir = instances_dir / old_name
    new_dir = instances_dir / new_name

    if not old_dir.is_dir():
        raise InstanceError(f"Instance {old_name!r} not found at {old_dir}")
    if new_dir.exists():
        raise InstanceError(f"Instance {new_name!r} already exists at {new_dir}")

    # Clean up legacy overlay container ({old_name}-overlay) if present.
    # The decoupled overlay_id container is unaffected by the rename.
    legacy_overlay = f"{old_name}-overlay"
    if runner.container_exists(legacy_overlay):
        runner.remove(legacy_overlay, force=True)

    # Retag image if it exists
    if runner.image_exists(old_name):
        runner.tag(old_name, new_name)
        runner.rmi(old_name)

    # Rename directory
    old_dir.rename(new_dir)

    return Instance.from_directory(new_dir)


@dataclass
class InstanceInfo:
    """Summary info for a listed instance."""

    name: str
    directory: Path
    image_built: bool
    overlay_running: bool
    config: InstanceConfig
    base_image: str | None = None
    # Set when podman could not be queried (e.g. timed out on a stuck container
    # lock). The corresponding bool above is then unknown and reported as False.
    image_error: str | None = None
    overlay_error: str | None = None

    @property
    def status_error(self) -> str | None:
        """First query error for this instance, or None if all queries succeeded."""
        return self.image_error or self.overlay_error


def list_all(instances_dir: Path, runner: PodmanRunner) -> list[InstanceInfo]:
    """Return info for all instances under instances_dir."""
    instances_dir = Path(instances_dir)
    if not instances_dir.is_dir():
        return []

    results = []
    for entry in sorted(instances_dir.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue  # skip .shared and other hidden dirs
        config_path = entry / "scratch.toml"
        config = load(config_path)
        image_built = False
        overlay_running = False
        image_error = overlay_error = None
        try:
            image_built = runner.image_exists(entry.name)
        except PodmanError as e:
            image_error = str(e)
        overlay_name = config.overlay_id if config.overlay_id else f"{entry.name}-overlay"
        try:
            overlay_running = runner.container_running(overlay_name)
        except PodmanError as e:
            overlay_error = str(e)
        base_image = detect_base_image(entry)
        results.append(
            InstanceInfo(
                name=entry.name,
                directory=entry,
                image_built=image_built,
                overlay_running=overlay_running,
                config=config,
                base_image=base_image,
                image_error=image_error,
                overlay_error=overlay_error,
            )
        )
    return results


def skel_copy(instance: Instance) -> list[str]:
    """Copy /etc/skel dotfiles into the instance's home directory.

    Returns a list of copied filenames.
    """
    skel_dir = Path("/etc/skel")
    if not skel_dir.is_dir():
        return []

    copied = []
    for entry in skel_dir.iterdir():
        if entry.name in (".", ".."):
            continue
        dest = instance.home_dir / entry.name
        if dest.exists():
            continue
        if entry.is_dir():
            shutil.copytree(entry, dest)
        else:
            shutil.copy2(entry, dest)
        copied.append(entry.name)
    return copied


def detect_base_image(instance_dir: Path) -> str | None:
    """Return the final FROM image name from the instance's Dockerfile, or None."""
    dockerfile = instance_dir / "Dockerfile"
    if not dockerfile.exists():
        return None
    last_from = None
    for line in dockerfile.read_text().splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("FROM "):
            last_from = stripped.split()[1]
    return last_from


def is_fedora_based(instance_dir: Path) -> bool:
    """Return True if the instance Dockerfile's final FROM image is fedora-based."""
    base = detect_base_image(instance_dir)
    if base is None:
        return False
    return "fedora" in base.lower()
