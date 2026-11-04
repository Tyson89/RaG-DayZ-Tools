from pathlib import Path
from types import SimpleNamespace

import pytest

import rag_build_pipeline as pipeline
from rag_builder_common import BuildError
from rag_pbo_builder_cli import build_parser, make_settings


@pytest.mark.parametrize("code", ["3221225477", "-1073741819", "0xC0000005"])
def test_binarize_access_violation_is_not_a_signing_failure(code):
    diagnostics = pipeline.get_build_failure_diagnostics(
        f"Binarize failed with exit code {code}: D:/temp/staging",
        ["Using private key: test.biprivatekey", "Created signature: previous.pbo.test.bisign"],
    )
    assert [item["title"] for item in diagnostics] == ["Binarize crashed with an access violation"]


@pytest.mark.parametrize("error", [
    "DSSignFile failed with exit code 1: addon.pbo",
    "DSSignFile finished but no .bisign was created for: addon.pbo",
    "DSSignFile.exe not found. Select the DayZ Tools DSSignFile.exe path.",
    "Private key not found. Select your .biprivatekey file.",
    "Selected private key does not end with .biprivatekey.",
    "Signed build expected a .bisign but none was created for: addon.pbo",
    "Could not remove old signature: addon.pbo.test.bisign",
    "Backup validation failed. Missing backup signature: addon.pbo.test.bisign",
    "Published output verification failed. Signature is missing for: addon.pbo",
])
def test_actual_signing_failures_remain_diagnosed(error):
    assert [item["title"] for item in pipeline.get_build_failure_diagnostics(error)] == ["Signing failed"]


def test_other_tool_crash_is_not_attributed_to_binarize():
    diagnostics = pipeline.get_build_failure_diagnostics("DSSignFile failed with exit code 0xC0000005: addon.pbo")
    assert [item["title"] for item in diagnostics] == ["Signing failed"]


def test_successful_signing_context_does_not_diagnose_unrelated_failure():
    assert pipeline.get_build_failure_diagnostics(
        "Binarize failed with exit code 1: staging",
        ["Using private key: test.biprivatekey", "Created signature: previous.pbo.test.bisign"],
    ) == []


def test_default_scan_uses_dayz_data_without_unrelated_projects(tmp_path):
    dayz_data = tmp_path / "DZ"
    dayz_data.mkdir()
    (tmp_path / "unrelated_mod").mkdir()
    assert pipeline.get_binarize_addon_folders({"project_root": str(tmp_path)}) == [str(dayz_data)]


def test_nonstandard_workdrive_retains_root_fallback(tmp_path):
    messages = []
    assert pipeline.get_binarize_addon_folders({"project_root": str(tmp_path)}, messages.append) == [str(tmp_path)]
    assert any("No DZ folder found" in message for message in messages)


def test_explicit_scan_folders_replace_default_and_preserve_order(tmp_path):
    (tmp_path / "DZ").mkdir()
    dependencies = tmp_path / "dependencies"
    dependencies.mkdir()
    models = tmp_path / "custom models"
    models.mkdir()
    settings = {
        "project_root": str(tmp_path),
        "binarize_addon_folders": f'"{dependencies}";{models}\n{dependencies}',
    }
    assert pipeline.get_binarize_addon_folders(settings) == [str(dependencies), str(models)]


def test_missing_explicit_scan_folder_aborts_instead_of_broadening_scan(tmp_path):
    (tmp_path / "DZ").mkdir()
    settings = {"project_root": str(tmp_path), "binarize_addon_folders": str(tmp_path / "missing")}
    with pytest.raises(BuildError, match="Binarize addon folder does not exist"):
        pipeline.get_binarize_addon_folders(settings)


def test_drive_root_scan_is_absolute(monkeypatch):
    monkeypatch.setattr(pipeline.os.path, "isdir", lambda value: value == "P:\\")
    settings = {"project_root": "P:", "binarize_addon_folders": "P:"}
    assert pipeline.get_binarize_addon_folders(settings) == ["P:\\"]


def test_scan_scope_changes_invalidate_build_cache(tmp_path):
    (tmp_path / "DZ").mkdir()
    dependencies = tmp_path / "dependencies"
    dependencies.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    settings = {
        "project_root": str(tmp_path), "use_binarize": True,
        "convert_config": False, "sign_pbos": False, "max_processes": 1,
    }
    original = pipeline.compute_addon_state_hash(str(source), "test", settings)
    changed = pipeline.compute_addon_state_hash(str(source), "test", {
        **settings, "binarize_addon_folders": str(dependencies),
    })
    assert original != changed


def test_disabled_binarize_ignores_missing_scan_folders(tmp_path):
    settings = {
        "project_root": str(tmp_path), "use_binarize": False,
        "convert_config": False, "sign_pbos": False, "max_processes": 1,
        "binarize_addon_folders": str(tmp_path / "missing"),
    }
    assert pipeline.compute_addon_state_hash(str(tmp_path), "test", settings)


def test_binarize_command_uses_scoped_scan_and_keeps_asset_root(tmp_path, monkeypatch):
    dayz_data = tmp_path / "DZ"
    dayz_data.mkdir()
    source = tmp_path / "staging"
    source.mkdir()
    calls = []

    def run(cmd, cwd):
        calls.append((cmd, cwd))
        return SimpleNamespace(returncode=3221225477, stdout="Updating base class ->House")

    monkeypatch.setattr(pipeline, "run_hidden_text_subprocess", run)
    folders = pipeline.get_binarize_addon_folders({"project_root": str(tmp_path)})
    with pytest.raises(BuildError, match="Binarize failed with exit code 3221225477"):
        pipeline.run_dayz_binarize(
            str(source), str(tmp_path / "binarized"), "binarize.exe", str(tmp_path),
            str(tmp_path / "temp"), 1, "", lambda message: None, addon_folders=folders,
        )
    cmd, cwd = calls[0]
    assert [arg for arg in cmd if arg.startswith("-addon=")] == [f"-addon={dayz_data}"]
    assert f"-binpath={tmp_path}" in cmd
    assert cwd == str(tmp_path)
    assert cmd[-2] == str(source)


@pytest.mark.parametrize("override", [False, True])
def test_cli_preserves_or_overrides_scan_folders(tmp_path, override):
    arguments = ["build", "--log-file", str(tmp_path / "build.log")]
    if override:
        arguments.extend(["--binarize-addon-folders", "P:/DZ;P:/dependencies"])
    settings = make_settings(build_parser().parse_args(arguments), {"binarize_addon_folders": "P:/saved"})
    assert settings["binarize_addon_folders"] == ("P:/DZ;P:/dependencies" if override else "P:/saved")
