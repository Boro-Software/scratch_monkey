"""Tests for scratch_monkey.config module."""


import re

import pytest

from scratch_monkey.config import (
    ConfigError,
    InstanceConfig,
    _serialize,
    generate_overlay_id,
    load,
    save,
    validate_name,
    validate_volume_spec,
)

# ─── validate_volume_spec ─────────────────────────────────────────────────────


class TestValidateVolumeSpec:
    def test_valid_host_container(self):
        validate_volume_spec("/host:/container")  # should not raise

    def test_valid_host_container_mode(self):
        validate_volume_spec("/host:/container:ro")  # should not raise

    def test_valid_rw_mode(self):
        validate_volume_spec("/data:/mnt/data:rw")  # should not raise

    def test_empty_host_raises(self):
        with pytest.raises(ConfigError, match="host path is empty"):
            validate_volume_spec(":/container:ro")

    def test_empty_container_raises(self):
        with pytest.raises(ConfigError, match="container path is empty"):
            validate_volume_spec("/host::ro")

    def test_both_empty_raises(self):
        with pytest.raises(ConfigError, match="host path is empty"):
            validate_volume_spec("::ro")

    def test_single_element_raises(self):
        with pytest.raises(ConfigError, match="expected host:container"):
            validate_volume_spec("/only-one-path")


class TestLoadVolumeValidation:
    def test_load_rejects_empty_host(self, tmp_path):
        toml = tmp_path / "scratch.toml"
        toml.write_text('volumes = [":/container:ro"]\n')
        with pytest.raises(ConfigError, match="host path is empty"):
            load(toml)

    def test_load_rejects_empty_container(self, tmp_path):
        toml = tmp_path / "scratch.toml"
        toml.write_text('volumes = ["/host::ro"]\n')
        with pytest.raises(ConfigError, match="container path is empty"):
            load(toml)

    def test_load_accepts_valid_volumes(self, tmp_path):
        toml = tmp_path / "scratch.toml"
        toml.write_text('volumes = ["/a:/b:ro", "/c:/d"]\n')
        cfg = load(toml)
        assert cfg.volumes == ["/a:/b:ro", "/c:/d"]


class TestSaveVolumeValidation:
    def test_save_rejects_empty_volume_spec(self, tmp_path):
        path = tmp_path / "scratch.toml"
        cfg = InstanceConfig(volumes=["::ro"])
        with pytest.raises(ConfigError, match="host path is empty"):
            save(path, cfg)

    def test_save_does_not_write_on_invalid_volume(self, tmp_path):
        path = tmp_path / "scratch.toml"
        cfg = InstanceConfig(volumes=[":/bad:ro"])
        with pytest.raises(ConfigError):
            save(path, cfg)
        assert not path.exists()


# ─── validate_name ────────────────────────────────────────────────────────────


class TestValidateName:
    def test_simple_alphanum(self):
        validate_name("myproject")  # should not raise

    def test_starts_with_digit(self):
        validate_name("2cool")  # should not raise

    def test_with_dash(self):
        validate_name("my-project")  # should not raise

    def test_with_dot(self):
        validate_name("my.project")  # should not raise

    def test_with_underscore(self):
        validate_name("my_project")  # should not raise

    def test_empty_raises(self):
        with pytest.raises(ConfigError):
            validate_name("")

    def test_starts_with_dash_raises(self):
        with pytest.raises(ConfigError):
            validate_name("-myproject")

    def test_starts_with_dot_raises(self):
        with pytest.raises(ConfigError):
            validate_name(".myproject")

    def test_special_chars_raise(self):
        with pytest.raises(ConfigError):
            validate_name("my project")

    def test_slash_raises(self):
        with pytest.raises(ConfigError):
            validate_name("my/project")


# ─── load ─────────────────────────────────────────────────────────────────────


class TestLoad:
    def test_missing_file_returns_defaults(self, tmp_path):
        cfg = load(tmp_path / "nonexistent.toml")
        assert cfg == InstanceConfig()

    def test_empty_file_returns_defaults(self, tmp_path):
        toml = tmp_path / "scratch.toml"
        toml.write_text("")
        cfg = load(toml)
        assert cfg == InstanceConfig()

    def test_parses_cmd(self, tmp_path):
        toml = tmp_path / "scratch.toml"
        toml.write_text('cmd = "/bin/zsh"\n')
        cfg = load(toml)
        assert cfg.cmd == "/bin/zsh"

    def test_parses_booleans(self, tmp_path):
        toml = tmp_path / "scratch.toml"
        toml.write_text("wayland = true\nssh = true\noverlay = true\n")
        cfg = load(toml)
        assert cfg.wayland is True
        assert cfg.ssh is True
        assert cfg.overlay is True

    def test_parses_string_arrays(self, tmp_path):
        toml = tmp_path / "scratch.toml"
        toml.write_text(
            'volumes = ["/host/path:/container/path:ro", "/another:/path"]\n'
        )
        cfg = load(toml)
        assert cfg.volumes == ["/host/path:/container/path:ro", "/another:/path"]

    def test_parses_shared_list(self, tmp_path):
        toml = tmp_path / "scratch.toml"
        toml.write_text('shared = ["comms", "data"]\n')
        cfg = load(toml)
        assert cfg.shared == ["comms", "data"]

    def test_parses_env_list(self, tmp_path):
        toml = tmp_path / "scratch.toml"
        toml.write_text('env = ["FOO=bar", "BAZ=qux"]\n')
        cfg = load(toml)
        assert cfg.env == ["FOO=bar", "BAZ=qux"]

    def test_commented_lines_ignored(self, tmp_path):
        toml = tmp_path / "scratch.toml"
        toml.write_text("# wayland = true\n# ssh = true\n")
        cfg = load(toml)
        assert cfg.wayland is False
        assert cfg.ssh is False

    def test_parses_home(self, tmp_path):
        toml = tmp_path / "scratch.toml"
        toml.write_text('home = "/custom/home"\n')
        cfg = load(toml)
        assert cfg.home == "/custom/home"

    def test_full_config(self, tmp_path):
        toml = tmp_path / "scratch.toml"
        toml.write_text(
            'cmd = "/bin/zsh"\n'
            "wayland = true\n"
            "ssh = false\n"
            'home = ""\n'
            'volumes = ["/a:/b:ro"]\n'
            'env = ["X=1"]\n'
            'shared = ["comms"]\n'
            "overlay = true\n"
        )
        cfg = load(toml)
        assert cfg.cmd == "/bin/zsh"
        assert cfg.wayland is True
        assert cfg.ssh is False
        assert cfg.volumes == ["/a:/b:ro"]
        assert cfg.env == ["X=1"]
        assert cfg.shared == ["comms"]
        assert cfg.overlay is True

    def test_load_gpu_and_devices(self, tmp_path):
        toml = tmp_path / "scratch.toml"
        toml.write_text('gpu = true\ndevices = ["/dev/dri"]\n')
        cfg = load(toml)
        assert cfg.gpu is True
        assert cfg.devices == ["/dev/dri"]

    def test_default_gpu_false(self, tmp_path):
        toml = tmp_path / "scratch.toml"
        toml.write_text("")
        cfg = load(toml)
        assert cfg.gpu is False
        assert cfg.devices == []


# ─── save ─────────────────────────────────────────────────────────────────────


class TestSave:
    def test_roundtrip_defaults(self, tmp_path):
        path = tmp_path / "scratch.toml"
        cfg = InstanceConfig()
        save(path, cfg)
        loaded = load(path)
        assert loaded == cfg

    def test_roundtrip_custom_values(self, tmp_path):
        path = tmp_path / "scratch.toml"
        cfg = InstanceConfig(
            cmd="/bin/zsh",
            wayland=True,
            ssh=True,
            podman=True,
            home="/custom",
            volumes=["/a:/b:ro"],
            env=["X=1"],
            shared=["comms"],
            overlay=True,
        )
        save(path, cfg)
        loaded = load(path)
        assert loaded == cfg

    def test_podman_defaults_false(self, tmp_path):
        assert InstanceConfig().podman is False
        toml = tmp_path / "scratch.toml"
        toml.write_text("")
        assert load(toml).podman is False

    def test_podman_parsed(self, tmp_path):
        toml = tmp_path / "scratch.toml"
        toml.write_text("podman = true\n")
        assert load(toml).podman is True

    def test_podman_written_explicitly(self, tmp_path):
        path = tmp_path / "scratch.toml"
        save(path, InstanceConfig())
        assert "podman = false\n" in path.read_text()
        save(path, InstanceConfig(podman=True))
        assert "podman = true\n" in path.read_text()
        assert load(path).podman is True

    def test_save_gpu_and_devices(self, tmp_path):
        path = tmp_path / "scratch.toml"
        cfg = InstanceConfig(gpu=True, devices=["/dev/dri", "/dev/kfd"])
        save(path, cfg)
        loaded = load(path)
        assert loaded.gpu is True
        assert loaded.devices == ["/dev/dri", "/dev/kfd"]

    def test_atomic_write_creates_file(self, tmp_path):
        path = tmp_path / "scratch.toml"
        assert not path.exists()
        save(path, InstanceConfig())
        assert path.exists()

    def test_atomic_write_no_tmp_leftover(self, tmp_path):
        path = tmp_path / "scratch.toml"
        save(path, InstanceConfig())
        tmp_files = list(tmp_path.glob(".scratch-monkey-*.tmp"))
        assert tmp_files == []

    def test_overwrites_existing(self, tmp_path):
        path = tmp_path / "scratch.toml"
        save(path, InstanceConfig(wayland=True))
        save(path, InstanceConfig(wayland=False))
        loaded = load(path)
        assert loaded.wayland is False


# ─── _serialize ───────────────────────────────────────────────────────────────


class TestSerialize:
    def test_contains_all_keys(self):
        text = _serialize(InstanceConfig())
        assert "cmd" in text
        assert "wayland" in text
        assert "ssh" in text
        assert "home" in text
        assert "volumes" in text
        assert "env" in text
        assert "shared" in text
        assert "overlay" in text
        assert "gpu" in text
        assert "devices" in text

    def test_bool_lowercase(self):
        text = _serialize(InstanceConfig(wayland=True, overlay=False))
        assert "wayland = true" in text
        assert "overlay = false" in text

    def test_list_format(self):
        text = _serialize(InstanceConfig(volumes=["/a:/b:ro"]))
        assert 'volumes = ["/a:/b:ro"]' in text

    def test_escapes_quotes_in_strings(self):
        text = _serialize(InstanceConfig(cmd='/bin/bash -c "echo hi"'))
        assert '\\"' in text


# ─── generate_overlay_id ──────────────────────────────────────────────────────


class TestGenerateOverlayId:
    def test_format(self):
        """overlay_id must match 'sm-' followed by exactly 8 hex chars."""
        oid = generate_overlay_id()
        assert re.fullmatch(r"sm-[0-9a-f]{8}", oid), f"Bad format: {oid!r}"

    def test_unique(self):
        """Two generated IDs must differ."""
        assert generate_overlay_id() != generate_overlay_id()


# ─── sudo ─────────────────────────────────────────────────────────────────────


class TestSudo:
    def test_default_sudo_true(self, tmp_path):
        """Missing sudo key defaults to True."""
        toml = tmp_path / "scratch.toml"
        toml.write_text("")
        cfg = load(toml)
        assert cfg.sudo is True

    def test_parses_sudo_false(self, tmp_path):
        """Explicit sudo = false is parsed correctly."""
        toml = tmp_path / "scratch.toml"
        toml.write_text("sudo = false\n")
        cfg = load(toml)
        assert cfg.sudo is False

    def test_sudo_serialized(self):
        """sudo field appears in serialized output."""
        text = _serialize(InstanceConfig(sudo=False))
        assert "sudo = false" in text

    def test_roundtrip_sudo_false(self, tmp_path):
        """sudo=False survives save + load."""
        path = tmp_path / "scratch.toml"
        cfg = InstanceConfig(sudo=False)
        save(path, cfg)
        loaded = load(path)
        assert loaded.sudo is False


# ─── overlay_id round-trip ────────────────────────────────────────────────────


class TestOverlayIdConfig:
    def test_load_overlay_id(self, tmp_path):
        toml = tmp_path / "scratch.toml"
        toml.write_text('overlay_id = "sm-abcd1234"\n')
        cfg = load(toml)
        assert cfg.overlay_id == "sm-abcd1234"

    def test_load_missing_overlay_id_defaults_empty(self, tmp_path):
        toml = tmp_path / "scratch.toml"
        toml.write_text("")
        cfg = load(toml)
        assert cfg.overlay_id == ""

    def test_serialize_includes_overlay_id_when_set(self):
        cfg = InstanceConfig(overlay_id="sm-deadbeef")
        text = _serialize(cfg)
        assert 'overlay_id = "sm-deadbeef"' in text

    def test_serialize_omits_overlay_id_when_empty(self):
        cfg = InstanceConfig(overlay_id="")
        text = _serialize(cfg)
        assert "overlay_id" not in text

    def test_roundtrip_with_overlay_id(self, tmp_path):
        path = tmp_path / "scratch.toml"
        cfg = InstanceConfig(overlay_id="sm-12345678")
        save(path, cfg)
        loaded = load(path)
        assert loaded.overlay_id == "sm-12345678"
