"""Tests for scratch_monkey.export module."""

import stat

import pytest

from scratch_monkey.config import InstanceConfig
from scratch_monkey.export import (
    _EXPORT_MAGIC,
    ExportError,
    _build_display_setup,
    export_command,
    unexport,
)
from scratch_monkey.instance import Instance


@pytest.fixture
def instance(tmp_path):
    inst_dir = tmp_path / "myinstance"
    inst_dir.mkdir()
    home_dir = inst_dir / "home"
    home_dir.mkdir()
    (inst_dir / "Dockerfile").write_text("FROM scratch_monkey\n")
    return Instance(
        name="myinstance",
        directory=inst_dir,
        config=InstanceConfig(),
        home_dir=home_dir,
    )


@pytest.fixture
def bin_dir(tmp_path):
    d = tmp_path / "bin"
    d.mkdir()
    return d


# ─── export_command ───────────────────────────────────────────────────────────


class TestExportCommand:
    def test_creates_script(self, instance, bin_dir):
        out = export_command(instance, "/usr/bin/git", bin_dir=bin_dir)
        assert out.exists()
        assert out.name == "git"

    def test_custom_bin_name(self, instance, bin_dir):
        out = export_command(instance, "/usr/bin/git", bin_name="mygit", bin_dir=bin_dir)
        assert out.name == "mygit"

    def test_script_is_executable(self, instance, bin_dir):
        out = export_command(instance, "/usr/bin/git", bin_dir=bin_dir)
        assert out.stat().st_mode & stat.S_IEXEC

    def test_script_has_shebang(self, instance, bin_dir):
        out = export_command(instance, "/usr/bin/git", bin_dir=bin_dir)
        content = out.read_text()
        assert content.startswith("#!/bin/sh")

    def test_script_has_magic_comment(self, instance, bin_dir):
        out = export_command(instance, "/usr/bin/git", bin_dir=bin_dir)
        content = out.read_text()
        assert _EXPORT_MAGIC in content

    def test_script_contains_instance_name(self, instance, bin_dir):
        out = export_command(instance, "/usr/bin/git", bin_dir=bin_dir)
        content = out.read_text()
        assert "myinstance" in content

    def test_script_contains_command(self, instance, bin_dir):
        out = export_command(instance, "/usr/bin/git", bin_dir=bin_dir)
        content = out.read_text()
        assert "/usr/bin/git" in content

    def test_script_contains_home_dir(self, instance, bin_dir):
        out = export_command(instance, "/usr/bin/git", bin_dir=bin_dir)
        content = out.read_text()
        assert str(instance.home_dir) in content

    def test_script_checks_scratch_instance_env(self, instance, bin_dir):
        out = export_command(instance, "/usr/bin/git", bin_dir=bin_dir)
        content = out.read_text()
        assert "SCRATCH_INSTANCE" in content

    def test_script_checks_overlay_container(self, instance, bin_dir):
        out = export_command(instance, "/usr/bin/git", bin_dir=bin_dir)
        content = out.read_text()
        assert "myinstance-overlay" in content

    def test_creates_bin_dir_if_missing(self, instance, tmp_path):
        new_bin_dir = tmp_path / "new" / "bin"
        assert not new_bin_dir.exists()
        export_command(instance, "/usr/bin/git", bin_dir=new_bin_dir)
        assert new_bin_dir.exists()

    def test_export_uses_overlay_id(self, tmp_path, bin_dir):
        """When overlay_id is set, the exported script uses it instead of '{name}-overlay'."""
        inst_dir = tmp_path / "myinstance"
        inst_dir.mkdir()
        home_dir = inst_dir / "home"
        home_dir.mkdir()
        (inst_dir / "Dockerfile").write_text("FROM scratch_monkey\n")
        inst = Instance(
            name="myinstance",
            directory=inst_dir,
            config=InstanceConfig(overlay_id="sm-abcd1234"),
            home_dir=home_dir,
        )
        out = export_command(inst, "/usr/bin/git", bin_dir=bin_dir)
        content = out.read_text()
        assert "sm-abcd1234" in content
        assert "myinstance-overlay" not in content


# ─── unexport ─────────────────────────────────────────────────────────────────


class TestUnexport:
    def test_removes_exported_script(self, instance, bin_dir):
        out = export_command(instance, "/usr/bin/git", bin_dir=bin_dir)
        assert out.exists()
        unexport("git", bin_dir=bin_dir)
        assert not out.exists()

    def test_raises_if_file_missing(self, bin_dir):
        with pytest.raises(ExportError, match="No file found"):
            unexport("nonexistent", bin_dir=bin_dir)

    def test_raises_if_not_scratch_monkey_export(self, bin_dir):
        script = bin_dir / "myscript"
        script.write_text("#!/bin/sh\necho hello\n")
        with pytest.raises(ExportError, match="does not look like"):
            unexport("myscript", bin_dir=bin_dir)

    def test_does_not_remove_non_export(self, bin_dir):
        script = bin_dir / "myscript"
        script.write_text("#!/bin/sh\necho hello\n")
        try:
            unexport("myscript", bin_dir=bin_dir)
        except ExportError:
            pass
        assert script.exists()


# ─── _build_display_setup ────────────────────────────────────────────────────


class TestBuildDisplaySetup:
    def test_no_display_features(self):
        """With defaults, only empty _dexec/_drun are defined."""
        result = _build_display_setup(InstanceConfig())
        assert '_dexec=""' in result
        assert '_drun=""' in result
        assert "WAYLAND_DISPLAY" not in result
        assert "DISPLAY" not in result
        assert "SSH_AUTH_SOCK" not in result

    def test_wayland_enabled(self):
        """Wayland setup checks socket and sets WAYLAND_DISPLAY + XDG_RUNTIME_DIR."""
        result = _build_display_setup(InstanceConfig(wayland=True))
        assert "WAYLAND_DISPLAY=wayland-0" in result
        assert "XDG_RUNTIME_DIR=/run/user/$_uid" in result
        assert "/run/user/$_uid/wayland-0" in result
        # Both exec and run variants
        assert "_dexec=" in result
        assert "_drun=" in result

    def test_x11_enabled(self):
        """X11 setup checks DISPLAY and forwards it plus XAUTHORITY."""
        result = _build_display_setup(InstanceConfig(x11=True))
        assert "DISPLAY=$DISPLAY" in result
        assert "XAUTHORITY=/tmp/.container-Xauthority" in result
        assert "/tmp/.X11-unix:/tmp/.X11-unix:ro" in result

    def test_ssh_enabled(self):
        """SSH setup checks SSH_AUTH_SOCK and forwards it."""
        result = _build_display_setup(InstanceConfig(ssh=True))
        assert "SSH_AUTH_SOCK=$SSH_AUTH_SOCK" in result
        # Run variant includes volume mount
        assert "-v $SSH_AUTH_SOCK:$SSH_AUTH_SOCK" in result

    def test_podman_enabled(self):
        result = _build_display_setup(InstanceConfig(podman=True))
        assert "podman/podman.sock" in result
        assert "CONTAINER_HOST=unix://$_psock" in result
        assert "DOCKER_HOST=unix://$_psock" in result
        assert "-v $_psock:$_psock" in result

    def test_podman_disabled(self):
        assert "podman.sock" not in _build_display_setup(InstanceConfig())

    def test_all_features(self):
        """All features combined produce all forwarding blocks."""
        result = _build_display_setup(InstanceConfig(wayland=True, x11=True, ssh=True))
        assert "WAYLAND_DISPLAY" in result
        assert "DISPLAY=$DISPLAY" in result
        assert "SSH_AUTH_SOCK" in result


# ─── export display forwarding ───────────────────────────────────────────────


class TestExportDisplayForwarding:
    def test_wayland_export_includes_forwarding(self, tmp_path, bin_dir):
        """Export with wayland=True generates script with Wayland forwarding."""
        inst_dir = tmp_path / "wayinst"
        inst_dir.mkdir()
        (inst_dir / "home").mkdir()
        (inst_dir / "Dockerfile").write_text("FROM scratch_monkey\n")
        inst = Instance(
            name="wayinst",
            directory=inst_dir,
            config=InstanceConfig(wayland=True),
            home_dir=inst_dir / "home",
        )
        out = export_command(inst, "/usr/bin/app", bin_dir=bin_dir)
        content = out.read_text()
        assert "WAYLAND_DISPLAY" in content
        assert "XDG_RUNTIME_DIR" in content

    def test_x11_export_includes_forwarding(self, tmp_path, bin_dir):
        """Export with x11=True generates script with X11 forwarding."""
        inst_dir = tmp_path / "x11inst"
        inst_dir.mkdir()
        (inst_dir / "home").mkdir()
        (inst_dir / "Dockerfile").write_text("FROM scratch_monkey\n")
        inst = Instance(
            name="x11inst",
            directory=inst_dir,
            config=InstanceConfig(x11=True),
            home_dir=inst_dir / "home",
        )
        out = export_command(inst, "/usr/bin/app", bin_dir=bin_dir)
        content = out.read_text()
        assert "DISPLAY=$DISPLAY" in content
        assert "XAUTHORITY" in content
        assert "/tmp/.X11-unix" in content

    def test_no_display_export_has_empty_vars(self, instance, bin_dir):
        """Export with no display features still defines _dexec/_drun (empty)."""
        out = export_command(instance, "/usr/bin/git", bin_dir=bin_dir)
        content = out.read_text()
        assert '_dexec=""' in content
        assert '_drun=""' in content
        assert "WAYLAND_DISPLAY" not in content
        assert "DISPLAY=$DISPLAY" not in content

    def test_exec_path_uses_dexec(self, instance, bin_dir):
        """The podman exec lines reference $_dexec."""
        out = export_command(instance, "/usr/bin/git", bin_dir=bin_dir)
        content = out.read_text()
        assert "podman exec -it $_dexec" in content
        assert "podman exec -i $_dexec" in content

    def test_run_path_uses_drun(self, instance, bin_dir):
        """The podman run line references $_drun."""
        out = export_command(instance, "/usr/bin/git", bin_dir=bin_dir)
        content = out.read_text()
        assert "$_drun" in content
