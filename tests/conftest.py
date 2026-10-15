"""Shared test fixtures for scratch-monkey tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scratch_monkey.config import InstanceConfig
from scratch_monkey.config import save as cfg_save
from scratch_monkey.container import PodmanRunner
from scratch_monkey.instance import Instance


@pytest.fixture
def mock_runner() -> MagicMock:
    """A PodmanRunner mock with sensible defaults."""
    runner = MagicMock(spec=PodmanRunner)
    runner.image_exists.return_value = False
    runner.container_exists.return_value = False
    runner.container_running.return_value = False
    runner.container_status.return_value = ""
    runner.exec_capture.return_value = ""
    runner.container_mount_destinations.return_value = []
    return runner


@pytest.fixture
def instances_dir(tmp_path: Path) -> Path:
    """A temporary instances directory."""
    d = tmp_path / "scratch-monkey"
    d.mkdir()
    return d


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A temporary project directory with scratch.toml.default."""
    p = tmp_path / "project"
    p.mkdir()
    (p / "scratch.toml.default").write_text(
        "# scratch-monkey instance configuration\n"
    )
    return p


@pytest.fixture
def scratch_instance(tmp_path: Path) -> Instance:
    """A scratch-based instance with preset overlay_id for overlay/run_args tests."""
    inst_dir = tmp_path / "myinstance"
    inst_dir.mkdir()
    (inst_dir / "home").mkdir()
    (inst_dir / "Dockerfile").write_text("FROM scratch_monkey\n")
    cfg = InstanceConfig(overlay_id="sm-testtest")
    (inst_dir / ".env").touch()
    cfg_save(inst_dir / "scratch.toml", cfg)
    return Instance(
        name="myinstance",
        directory=inst_dir,
        config=cfg,
        home_dir=inst_dir / "home",
    )


@pytest.fixture
def fedora_instance(tmp_path: Path) -> Instance:
    """A fedora-based instance with preset overlay_id for overlay/run_args tests."""
    inst_dir = tmp_path / "fedorainst"
    inst_dir.mkdir()
    (inst_dir / "home").mkdir()
    (inst_dir / "Dockerfile").write_text("FROM scratch_monkey_fedora\n")
    cfg = InstanceConfig(overlay_id="sm-fedotest")
    (inst_dir / ".env").touch()
    cfg_save(inst_dir / "scratch.toml", cfg)
    return Instance(
        name="fedorainst",
        directory=inst_dir,
        config=cfg,
        home_dir=inst_dir / "home",
    )


