"""Tests for scratch_monkey.instance module."""

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from scratch_monkey.cli.main import cli
from scratch_monkey.config import ConfigError, InstanceConfig, save
from scratch_monkey.instance import (
    InstanceError,
    clone,
    create,
    delete,
    detect_base_image,
    is_fedora_based,
    list_all,
    rename,
)

# ─── create ───────────────────────────────────────────────────────────────────


class TestCreate:
    def test_creates_directory_structure(self, instances_dir, project_dir):
        inst = create("myproject", instances_dir, "scratch_monkey", project_dir)
        assert inst.directory.is_dir()
        assert (inst.directory / "home").is_dir()
        assert (inst.directory / "scratch.toml").exists()
        assert (inst.directory / "Dockerfile").exists()
        assert (inst.directory / ".env").exists()

    def test_returns_instance_with_correct_name(self, instances_dir, project_dir):
        inst = create("myproject", instances_dir, "scratch_monkey", project_dir)
        assert inst.name == "myproject"

    def test_home_dir_is_inside_instance(self, instances_dir, project_dir):
        inst = create("myproject", instances_dir, "scratch_monkey", project_dir)
        assert inst.home_dir == inst.directory / "home"

    def test_scratch_dockerfile_content(self, instances_dir, project_dir):
        inst = create("myproject", instances_dir, "scratch_monkey", project_dir)
        content = (inst.directory / "Dockerfile").read_text()
        assert "FROM scratch_monkey" in content
        assert "RUN does not" in content

    def test_fedora_dockerfile_content(self, instances_dir, project_dir):
        inst = create("myproject", instances_dir, "scratch_monkey_fedora", project_dir)
        content = (inst.directory / "Dockerfile").read_text()
        assert "FROM scratch_monkey_fedora" in content
        assert "Full Fedora base" in content

    def test_raises_if_already_exists(self, instances_dir, project_dir):
        create("myproject", instances_dir, "scratch_monkey", project_dir)
        with pytest.raises(InstanceError, match="already exists"):
            create("myproject", instances_dir, "scratch_monkey", project_dir)

    def test_invalid_name_raises(self, instances_dir, project_dir):
        from scratch_monkey.config import ConfigError
        with pytest.raises(ConfigError):
            create("-invalid", instances_dir, "scratch_monkey", project_dir)

    def test_creates_without_default_toml(self, instances_dir, tmp_path):
        """Falls back to InstanceConfig() if no scratch.toml.default found."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        inst = create("myproject", instances_dir, "scratch_monkey", project_dir)
        assert (inst.directory / "scratch.toml").exists()


# ─── clone ────────────────────────────────────────────────────────────────────


class TestClone:
    @pytest.fixture
    def source_instance(self, instances_dir, project_dir):
        return create("source", instances_dir, "scratch_monkey", project_dir)

    def test_creates_dest_directory(self, instances_dir, source_instance):
        inst = clone("source", "dest", instances_dir)
        assert inst.directory.is_dir()
        assert (inst.directory / "home").is_dir()

    def test_copies_dockerfile(self, instances_dir, source_instance):
        clone("source", "dest", instances_dir)
        src_df = (instances_dir / "source" / "Dockerfile").read_text()
        dst_df = (instances_dir / "dest" / "Dockerfile").read_text()
        assert src_df == dst_df

    def test_copies_config(self, instances_dir, source_instance):
        clone("source", "dest", instances_dir)
        assert (instances_dir / "dest" / "scratch.toml").exists()

    def test_fresh_home_is_empty(self, instances_dir, source_instance):
        (source_instance.home_dir / "somefile.txt").write_text("data")
        clone("source", "dest", instances_dir)
        assert not (instances_dir / "dest" / "home" / "somefile.txt").exists()

    def test_raises_if_source_missing(self, instances_dir):
        with pytest.raises(InstanceError, match="not found"):
            clone("missing", "dest", instances_dir)

    def test_raises_if_dest_exists(self, instances_dir, project_dir):
        create("source", instances_dir, "scratch_monkey", project_dir)
        create("dest", instances_dir, "scratch_monkey", project_dir)
        with pytest.raises(InstanceError, match="already exists"):
            clone("source", "dest", instances_dir)

    def test_invalid_dest_name_raises(self, instances_dir, source_instance):
        from scratch_monkey.config import ConfigError
        with pytest.raises(ConfigError):
            clone("source", ".invalid", instances_dir)

    def test_clone_tags_image_when_source_has_image(self, instances_dir, source_instance, mock_runner):
        mock_runner.image_exists.return_value = True
        clone("source", "dest", instances_dir, runner=mock_runner)
        mock_runner.tag.assert_called_once_with("source", "dest")

    def test_clone_skips_tag_when_no_image(self, instances_dir, source_instance, mock_runner):
        mock_runner.image_exists.return_value = False
        clone("source", "dest", instances_dir, runner=mock_runner)
        mock_runner.tag.assert_not_called()

    def test_clone_without_runner_succeeds(self, instances_dir, source_instance):
        inst = clone("source", "dest", instances_dir)
        assert inst.name == "dest"


# ─── delete ───────────────────────────────────────────────────────────────────


class TestDelete:
    def test_removes_directory(self, instances_dir, project_dir, mock_runner):
        create("myproject", instances_dir, "scratch_monkey", project_dir)
        delete("myproject", instances_dir, mock_runner)
        assert not (instances_dir / "myproject").exists()

    def test_removes_image_if_exists(self, instances_dir, project_dir, mock_runner):
        create("myproject", instances_dir, "scratch_monkey", project_dir)
        mock_runner.image_exists.return_value = True
        delete("myproject", instances_dir, mock_runner)
        mock_runner.rmi.assert_called_once_with("myproject")

    def test_skips_rmi_if_no_image(self, instances_dir, project_dir, mock_runner):
        create("myproject", instances_dir, "scratch_monkey", project_dir)
        mock_runner.image_exists.return_value = False
        delete("myproject", instances_dir, mock_runner)
        mock_runner.rmi.assert_not_called()

    def test_raises_if_not_found(self, instances_dir, mock_runner):
        with pytest.raises(InstanceError, match="not found"):
            delete("missing", instances_dir, mock_runner)

    def test_delete_removes_overlay_container(self, instances_dir, project_dir, mock_runner):
        create("myproject", instances_dir, "scratch_monkey", project_dir)
        mock_runner.container_exists.return_value = True
        delete("myproject", instances_dir, mock_runner)
        mock_runner.remove.assert_called_with("myproject-overlay", force=True)

    def test_delete_skips_overlay_when_not_exists(self, instances_dir, project_dir, mock_runner):
        create("myproject", instances_dir, "scratch_monkey", project_dir)
        mock_runner.container_exists.return_value = False
        delete("myproject", instances_dir, mock_runner)
        # Verify remove was not called with the overlay name
        # Check that no calls to remove were made for the overlay
        if mock_runner.remove.called:
            # Make sure it wasn't called with the overlay name
            for call in mock_runner.remove.call_args_list:
                assert call[0][0] != "myproject-overlay"


# ─── list_all ─────────────────────────────────────────────────────────────────


class TestListAll:
    def test_empty_dir_returns_empty(self, instances_dir, mock_runner):
        result = list_all(instances_dir, mock_runner)
        assert result == []

    def test_nonexistent_dir_returns_empty(self, tmp_path, mock_runner):
        result = list_all(tmp_path / "nonexistent", mock_runner)
        assert result == []

    def test_lists_instances(self, instances_dir, project_dir, mock_runner):
        create("alpha", instances_dir, "scratch_monkey", project_dir)
        create("beta", instances_dir, "scratch_monkey", project_dir)
        result = list_all(instances_dir, mock_runner)
        names = [i.name for i in result]
        assert "alpha" in names
        assert "beta" in names

    def test_skips_hidden_dirs(self, instances_dir, project_dir, mock_runner):
        create("myproject", instances_dir, "scratch_monkey", project_dir)
        (instances_dir / ".shared").mkdir()
        result = list_all(instances_dir, mock_runner)
        names = [i.name for i in result]
        assert ".shared" not in names

    def test_image_built_reflects_runner(self, instances_dir, project_dir, mock_runner):
        create("myproject", instances_dir, "scratch_monkey", project_dir)
        mock_runner.image_exists.return_value = True
        result = list_all(instances_dir, mock_runner)
        assert result[0].image_built is True

    def test_list_all_populates_base_image(self, instances_dir, project_dir, mock_runner):
        create("scratch-inst", instances_dir, "scratch_monkey", project_dir)
        create("fedora-inst", instances_dir, "scratch_monkey_fedora", project_dir)
        result = list_all(instances_dir, mock_runner)
        by_name = {r.name: r for r in result}
        assert by_name["scratch-inst"].base_image == "scratch_monkey"
        assert by_name["fedora-inst"].base_image == "scratch_monkey_fedora"


    def test_status_error_none_by_default(self, instances_dir, project_dir, mock_runner):
        create("myproject", instances_dir, "scratch_monkey", project_dir)
        assert list_all(instances_dir, mock_runner)[0].status_error is None

    def test_one_failing_instance_does_not_fail_the_list(
        self, instances_dir, project_dir, mock_runner
    ):
        from scratch_monkey.container import PodmanError

        create("alpha", instances_dir, "scratch_monkey", project_dir)
        create("beta", instances_dir, "scratch_monkey", project_dir)
        create("gamma", instances_dir, "scratch_monkey", project_dir)
        mock_runner.image_exists.return_value = True

        def running(name):
            if name == "beta-overlay":
                raise PodmanError("timed out: stuck lock")
            return True

        mock_runner.container_running.side_effect = running
        result = {r.name: r for r in list_all(instances_dir, mock_runner)}
        assert set(result) == {"alpha", "beta", "gamma"}
        assert result["alpha"].overlay_running is True
        assert result["alpha"].status_error is None
        assert result["gamma"].overlay_running is True
        assert result["beta"].overlay_running is False
        assert result["beta"].image_built is True  # image query succeeded
        assert "stuck lock" in result["beta"].status_error
        assert result["beta"].overlay_error == result["beta"].status_error
        assert result["beta"].image_error is None

    def test_image_query_failure_recorded(self, instances_dir, project_dir, mock_runner):
        from scratch_monkey.container import PodmanError

        create("alpha", instances_dir, "scratch_monkey", project_dir)
        mock_runner.image_exists.side_effect = PodmanError("timed out")
        mock_runner.container_running.return_value = True
        info = list_all(instances_dir, mock_runner)[0]
        assert info.image_built is False
        assert info.status_error == "timed out"
        assert info.image_error == "timed out"
        assert info.overlay_error is None

    def test_non_podman_errors_still_propagate(self, instances_dir, project_dir, mock_runner):
        create("alpha", instances_dir, "scratch_monkey", project_dir)
        mock_runner.image_exists.side_effect = RuntimeError("bug")
        with pytest.raises(RuntimeError):
            list_all(instances_dir, mock_runner)


# ─── skel_copy ────────────────────────────────────────────────────────────────


class TestSkelCopy:
    @staticmethod
    def _patch_skel(fake_skel):
        _Orig = Path
        def _side_effect(*a, **kw):
            p = _Orig(*a, **kw)
            if str(p) == "/etc/skel":
                return fake_skel
            return p
        return patch("scratch_monkey.instance.Path", side_effect=_side_effect)

    def test_copies_skel_files(self, instances_dir, project_dir, tmp_path):
        inst = create("myproject", instances_dir, "scratch_monkey", project_dir)
        fake_skel = tmp_path / "fake_skel"
        fake_skel.mkdir()
        (fake_skel / ".bashrc").write_text("# bashrc")
        (fake_skel / ".profile").write_text("# profile")
        with self._patch_skel(fake_skel):
            from scratch_monkey.instance import skel_copy
            copied = skel_copy(inst)
        assert sorted(copied) == [".bashrc", ".profile"]
        assert (inst.home_dir / ".bashrc").read_text() == "# bashrc"

    def test_skips_existing_files(self, instances_dir, project_dir, tmp_path):
        inst = create("myproject", instances_dir, "scratch_monkey", project_dir)
        (inst.home_dir / ".bashrc").write_text("existing")
        fake_skel = tmp_path / "fake_skel"
        fake_skel.mkdir()
        (fake_skel / ".bashrc").write_text("# skel")
        (fake_skel / ".profile").write_text("# profile")
        with self._patch_skel(fake_skel):
            from scratch_monkey.instance import skel_copy
            copied = skel_copy(inst)
        assert copied == [".profile"]
        assert (inst.home_dir / ".bashrc").read_text() == "existing"

    def test_copies_directories(self, instances_dir, project_dir, tmp_path):
        inst = create("myproject", instances_dir, "scratch_monkey", project_dir)
        fake_skel = tmp_path / "fake_skel"
        fake_skel.mkdir()
        (fake_skel / ".config").mkdir()
        (fake_skel / ".config" / "app.conf").write_text("key=val")
        with self._patch_skel(fake_skel):
            from scratch_monkey.instance import skel_copy
            copied = skel_copy(inst)
        assert ".config" in copied
        assert (inst.home_dir / ".config" / "app.conf").read_text() == "key=val"

    def test_returns_empty_when_skel_missing(self, instances_dir, project_dir, tmp_path):
        inst = create("myproject", instances_dir, "scratch_monkey", project_dir)
        with self._patch_skel(tmp_path / "nonexistent"):
            from scratch_monkey.instance import skel_copy
            copied = skel_copy(inst)
        assert copied == []


# ─── detect_base_image / is_fedora_based ─────────────────────────────────────


class TestDetectBaseImage:
    def test_returns_none_if_no_dockerfile(self, tmp_path):
        assert detect_base_image(tmp_path) is None

    def test_returns_last_from(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM builder AS build\nFROM scratch_monkey\n")
        assert detect_base_image(tmp_path) == "scratch_monkey"

    def test_returns_single_from(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM scratch_monkey_fedora\n")
        assert detect_base_image(tmp_path) == "scratch_monkey_fedora"


class TestIsFedoraBased:
    def test_true_for_fedora_image(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM scratch_monkey_fedora\n")
        assert is_fedora_based(tmp_path) is True

    def test_false_for_scratch_image(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM scratch_monkey\n")
        assert is_fedora_based(tmp_path) is False

    def test_false_for_no_dockerfile(self, tmp_path):
        assert is_fedora_based(tmp_path) is False

    def test_uses_last_from_line(self, tmp_path):
        (tmp_path / "Dockerfile").write_text(
            "FROM fedora:latest AS builder\nRUN echo hi\nFROM scratch_monkey\n"
        )
        assert is_fedora_based(tmp_path) is False


# ─── overlay_id in delete ─────────────────────────────────────────────────────


class TestDeleteOverlayId:
    def _make_instance(self, instances_dir, overlay_id: str = "") -> None:
        """Create a minimal instance dir with a given overlay_id in config."""
        inst_dir = instances_dir / "myproject"
        inst_dir.mkdir(parents=True)
        (inst_dir / "home").mkdir()
        (inst_dir / "Dockerfile").write_text("FROM scratch_monkey\n")
        (inst_dir / ".env").touch()
        cfg = InstanceConfig(overlay_id=overlay_id)
        save(inst_dir / "scratch.toml", cfg)

    def test_delete_uses_overlay_id(self, instances_dir, mock_runner):
        """delete() checks the overlay_id container name when overlay_id is set."""
        self._make_instance(instances_dir, overlay_id="sm-deadbeef")
        mock_runner.container_exists.return_value = True
        delete("myproject", instances_dir, mock_runner)
        mock_runner.container_exists.assert_called_with("sm-deadbeef")
        mock_runner.remove.assert_called_with("sm-deadbeef", force=True)

    def test_delete_falls_back_to_legacy_name(self, instances_dir, mock_runner):
        """delete() falls back to '{name}-overlay' when overlay_id is empty."""
        self._make_instance(instances_dir, overlay_id="")
        mock_runner.container_exists.return_value = True
        delete("myproject", instances_dir, mock_runner)
        mock_runner.container_exists.assert_called_with("myproject-overlay")
        mock_runner.remove.assert_called_with("myproject-overlay", force=True)


# ─── overlay_id in list_all ───────────────────────────────────────────────────


class TestListAllOverlayId:
    def test_list_all_uses_overlay_id(self, instances_dir, mock_runner):
        """list_all() checks overlay_id container when overlay_id is set."""
        inst_dir = instances_dir / "testinst"
        inst_dir.mkdir()
        (inst_dir / "home").mkdir()
        (inst_dir / "Dockerfile").write_text("FROM scratch_monkey\n")
        (inst_dir / ".env").touch()
        cfg = InstanceConfig(overlay_id="sm-cafebabe")
        save(inst_dir / "scratch.toml", cfg)

        mock_runner.container_running.return_value = True
        result = list_all(instances_dir, mock_runner)

        assert len(result) == 1
        mock_runner.container_running.assert_called_with("sm-cafebabe")

    def test_list_all_falls_back_to_legacy_name(self, instances_dir, mock_runner):
        """list_all() falls back to '{name}-overlay' when overlay_id is empty."""
        inst_dir = instances_dir / "testinst"
        inst_dir.mkdir()
        (inst_dir / "home").mkdir()
        (inst_dir / "Dockerfile").write_text("FROM scratch_monkey\n")
        (inst_dir / ".env").touch()
        cfg = InstanceConfig(overlay_id="")
        save(inst_dir / "scratch.toml", cfg)

        mock_runner.container_running.return_value = False
        list_all(instances_dir, mock_runner)

        mock_runner.container_running.assert_called_with("testinst-overlay")


# ─── overlay_id in clone ──────────────────────────────────────────────────────


class TestCloneOverlayId:
    def test_clone_clears_overlay_id(self, instances_dir, project_dir, mock_runner):
        """Cloning an instance with overlay_id set results in empty overlay_id on clone."""
        # Create source with overlay_id
        create("source", instances_dir, "scratch_monkey", project_dir)
        src_cfg = InstanceConfig(overlay_id="sm-original")
        save(instances_dir / "source" / "scratch.toml", src_cfg)

        result = clone("source", "dest", instances_dir)

        assert result.config.overlay_id == ""
        # Verify it's also persisted on disk
        from scratch_monkey.config import load
        saved = load(instances_dir / "dest" / "scratch.toml")
        assert saved.overlay_id == ""

    def test_clone_without_overlay_id_stays_empty(self, instances_dir, project_dir):
        """Cloning an instance without overlay_id keeps it empty."""
        create("source", instances_dir, "scratch_monkey", project_dir)
        result = clone("source", "dest", instances_dir)
        assert result.config.overlay_id == ""


# ─── rename ───────────────────────────────────────────────────────────────────


class TestRename:
    def _make_instance(self, instances_dir: Path, name: str, project_dir: Path) -> None:
        """Helper to create a minimal instance directory."""
        create(name, instances_dir, "scratch_monkey", project_dir)

    def test_rename_moves_directory(self, instances_dir, project_dir, mock_runner):
        """rename() renames the instance directory."""
        self._make_instance(instances_dir, "old", project_dir)
        rename("old", "new", instances_dir, mock_runner)
        assert (instances_dir / "new").is_dir()
        assert not (instances_dir / "old").exists()

    def test_rename_retags_image(self, instances_dir, project_dir, mock_runner):
        """rename() calls runner.tag and runner.rmi when the image exists."""
        self._make_instance(instances_dir, "old", project_dir)
        mock_runner.image_exists.return_value = True
        rename("old", "new", instances_dir, mock_runner)
        mock_runner.tag.assert_called_once_with("old", "new")
        mock_runner.rmi.assert_called_once_with("old")

    def test_rename_skips_retag_when_no_image(self, instances_dir, project_dir, mock_runner):
        """rename() skips tag/rmi when no image exists."""
        self._make_instance(instances_dir, "old", project_dir)
        mock_runner.image_exists.return_value = False
        rename("old", "new", instances_dir, mock_runner)
        mock_runner.tag.assert_not_called()
        mock_runner.rmi.assert_not_called()

    def test_rename_cleans_up_legacy_overlay(self, instances_dir, project_dir, mock_runner):
        """rename() removes legacy '{old_name}-overlay' container if present."""
        self._make_instance(instances_dir, "old", project_dir)
        mock_runner.container_exists.return_value = True
        rename("old", "new", instances_dir, mock_runner)
        mock_runner.container_exists.assert_called_with("old-overlay")
        mock_runner.remove.assert_called_with("old-overlay", force=True)

    def test_rename_skips_legacy_overlay_when_absent(self, instances_dir, project_dir, mock_runner):
        """rename() does not call remove when legacy overlay container is absent."""
        self._make_instance(instances_dir, "old", project_dir)
        mock_runner.container_exists.return_value = False
        rename("old", "new", instances_dir, mock_runner)
        mock_runner.remove.assert_not_called()

    def test_rename_raises_if_old_not_found(self, instances_dir, mock_runner):
        """rename() raises InstanceError if the source instance does not exist."""
        with pytest.raises(InstanceError, match="not found"):
            rename("nonexistent", "new", instances_dir, mock_runner)

    def test_rename_raises_if_new_already_exists(self, instances_dir, project_dir, mock_runner):
        """rename() raises InstanceError if the destination already exists."""
        self._make_instance(instances_dir, "old", project_dir)
        self._make_instance(instances_dir, "new", project_dir)
        with pytest.raises(InstanceError, match="already exists"):
            rename("old", "new", instances_dir, mock_runner)

    def test_rename_invalid_name_raises(self, instances_dir, project_dir, mock_runner):
        """rename() raises ConfigError for an invalid destination name."""
        self._make_instance(instances_dir, "old", project_dir)
        with pytest.raises(ConfigError):
            rename("old", "-invalid", instances_dir, mock_runner)

    def test_rename_returns_instance_from_new_dir(self, instances_dir, project_dir, mock_runner):
        """rename() returns an Instance with the new name and directory."""
        self._make_instance(instances_dir, "old", project_dir)
        inst = rename("old", "new", instances_dir, mock_runner)
        assert inst.name == "new"
        assert inst.directory == instances_dir / "new"

    def test_rename_preserves_config(self, instances_dir, project_dir, mock_runner):
        """rename() preserves the instance config after rename."""
        from scratch_monkey.config import load
        self._make_instance(instances_dir, "old", project_dir)
        # Customize config
        cfg = InstanceConfig(wayland=True, ssh=True)
        save(instances_dir / "old" / "scratch.toml", cfg)

        inst = rename("old", "new", instances_dir, mock_runner)

        assert inst.config.wayland is True
        assert inst.config.ssh is True
        # Verify config is on disk too
        saved = load(instances_dir / "new" / "scratch.toml")
        assert saved.wayland is True
        assert saved.ssh is True


# ─── rename CLI ───────────────────────────────────────────────────────────────


class TestRenameCli:
    def test_rename_cli_success(self, instances_dir, project_dir, mock_runner):
        """CLI rename prints success message."""
        create("old", instances_dir, "scratch_monkey", project_dir)
        runner_cli = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=mock_runner):
            result = runner_cli.invoke(
                cli,
                ["--instances-dir", str(instances_dir), "rename", "old", "new"],
            )
        assert result.exit_code == 0
        assert "old" in result.output
        assert "new" in result.output

    def test_rename_cli_error_not_found(self, instances_dir, mock_runner):
        """CLI rename prints error and exits non-zero for nonexistent instance."""
        runner_cli = CliRunner()
        with patch("scratch_monkey.cli.main.PodmanRunner", return_value=mock_runner):
            result = runner_cli.invoke(
                cli,
                ["--instances-dir", str(instances_dir), "rename", "ghost", "new"],
            )
        assert result.exit_code != 0
        assert "Error" in result.output or "Error" in (result.stderr or "")
