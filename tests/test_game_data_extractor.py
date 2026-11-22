from pathlib import Path
import os
import subprocess
import threading
import zipfile

import pytest

import rag_game_data_extractor_core
from rag_game_data_extractor_core import (
    MANIFEST_NAME,
    build_extraction_plan,
    extract_game_data,
    get_recommended_workers,
    overlay_dayz_misc_archive,
    parse_extension_list,
)
from rag_pbo_writer import pack_pbo


def make_pbo(tmp_path, relative_path, prefix, files):
    source = tmp_path / f"source_{len(list(tmp_path.glob('source_*')))}_{Path(relative_path).stem}"
    source.mkdir(parents=True, exist_ok=True)

    for name, data in files.items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    output = tmp_path / "game" / relative_path
    pack_pbo(source, output, prefix, lambda message: None)
    return output


def test_parse_extension_list():
    assert parse_extension_list("*.C, cpp; .JSON") == {".c", ".cpp", ".json"}


def test_dlc_archive_replaces_base_prefix(tmp_path):
    make_pbo(
        tmp_path,
        "Addons/data_sakhal.pbo",
        "DZ\\data_sakhal",
        {"base_only.txt": b"base", "shared.txt": b"old"},
    )
    make_pbo(
        tmp_path,
        "sakhal/Addons/data_sakhal.pbo",
        "DZ\\data_sakhal",
        {"dlc_only.txt": b"dlc", "shared.txt": b"new"},
    )
    protected = tmp_path / "game" / "sakhal" / "Addons" / "worlds_sakhal.ebo"
    protected.write_bytes(b"protected")

    plan = build_extraction_plan(tmp_path / "game")
    names = {item.relative_path for item in plan.entries}

    assert "DZ\\data_sakhal\\base_only.txt" not in names
    assert "DZ\\data_sakhal\\dlc_only.txt" in names
    assert plan.shadowed_archives == ["Addons\\data_sakhal.pbo"]
    assert plan.protected_archives == ["sakhal\\Addons\\worlds_sakhal.ebo"]


def test_extract_always_overwrites_without_manifest(tmp_path):
    make_pbo(
        tmp_path,
        "Addons/core.pbo",
        "DZ\\core",
        {"keep.txt": b"keep"},
    )
    output = tmp_path / "output"
    plan = build_extraction_plan(tmp_path / "game")
    first = extract_game_data(plan, output, workers=2)

    assert first["files"] == 2
    assert (output / "DZ" / "core" / "keep.txt").read_bytes() == b"keep"
    assert not (output / MANIFEST_NAME).exists()

    (output / "DZ" / "core" / "keep.txt").write_bytes(b"user change")
    extract_game_data(plan, output, workers=2)
    assert (output / "DZ" / "core" / "keep.txt").read_bytes() == b"keep"


def test_file_progress_events_are_throttled(tmp_path):
    make_pbo(
        tmp_path,
        "Addons/many.pbo",
        "DZ\\many",
        {f"files/{index:04d}.txt": b"x" for index in range(500)},
    )
    output = tmp_path / "output"
    plan = build_extraction_plan(tmp_path / "game")
    events = []
    extract_game_data(plan, output, progress=events.append)
    file_events = [event for event in events if event["type"] == "file"]

    assert len(file_events) < 50
    assert file_events[-1]["current"] == len(plan.entries)


def test_successful_config_conversion_deletes_config_bin(tmp_path, monkeypatch):
    make_pbo(
        tmp_path,
        "Addons/config.pbo",
        "DZ\\config",
        {"config.bin": b"binary config"},
    )
    output = tmp_path / "output"
    cfgconvert = tmp_path / "CfgConvert.exe"
    cfgconvert.write_bytes(b"placeholder")

    def fake_convert(_exe, bin_path, _log):
        cpp_path = Path(bin_path).with_suffix(".cpp")
        cpp_path.write_text("class CfgPatches {};", encoding="utf-8")
        return str(cpp_path)

    monkeypatch.setattr(rag_game_data_extractor_core, "convert_bin_to_cpp", fake_convert)
    plan = build_extraction_plan(tmp_path / "game")
    result = extract_game_data(
        plan,
        output,
        cfgconvert_exe=cfgconvert,
    )

    assert result["converted_configs"] == 1
    assert not (output / "DZ" / "config" / "config.bin").exists()
    assert (output / "DZ" / "config" / "config.cpp").is_file()


@pytest.mark.parametrize("create_dayz_script_folder", [False, True])
def test_vanilla_script_module_layout_is_optional(tmp_path, create_dayz_script_folder):
    files = {
        f"{module}/entities/test.c": module.encode("ascii")
        for module in ("1_Core", "2_GameLib", "3_Game", "4_World", "5_Mission")
    }
    files["4_World/DayZ/already_nested.c"] = b"nested"
    files["other/untouched.c"] = b"other"
    make_pbo(tmp_path, "dta/scripts.pbo", "scripts", files)

    options = {"create_dayz_script_folder": True} if create_dayz_script_folder else {}
    plan = build_extraction_plan(tmp_path / "game", include_extensions=".c", **options)
    names = {item.relative_path for item in plan.entries}
    output = tmp_path / "output"
    extract_game_data(plan, output)

    for module in ("1_Core", "2_GameLib", "3_Game", "4_World", "5_Mission"):
        module_path = f"scripts\\{module}"
        if create_dayz_script_folder:
            module_path += "\\DayZ"
        assert f"{module_path}\\entities\\test.c" in names
        assert (output.joinpath(*module_path.split("\\")) / "entities" / "test.c").read_bytes() == module.encode("ascii")
        if not create_dayz_script_folder and module != "4_World":
            assert not (output / "scripts" / module / "DayZ").exists()

    assert "scripts\\4_World\\DayZ\\already_nested.c" in names
    assert "scripts\\4_World\\DayZ\\DayZ\\already_nested.c" not in names
    assert "scripts\\other\\untouched.c" in names


def test_rapified_material_conversion_is_always_enabled(tmp_path, monkeypatch):
    make_pbo(
        tmp_path,
        "Addons/material.pbo",
        "DZ\\material",
        {"surface.rvmat": b"raP binary material"},
    )
    output = tmp_path / "output"
    cfgconvert = tmp_path / "CfgConvert.exe"
    cfgconvert.write_bytes(b"placeholder")

    def fake_convert(_exe, _source, target, _log):
        Path(target).write_text("ambient[] = {1, 1, 1, 1};", encoding="utf-8")
        return str(target)

    monkeypatch.setattr(rag_game_data_extractor_core, "convert_rap_to_text", fake_convert)
    plan = build_extraction_plan(tmp_path / "game")
    result = extract_game_data(plan, output, cfgconvert_exe=cfgconvert)

    assert result["converted_materials"] == 1
    assert (output / "DZ" / "material" / "surface.rvmat").read_text(encoding="utf-8").startswith("ambient")


def test_recommended_workers_uses_all_cpu_cores(monkeypatch):
    monkeypatch.setattr(rag_game_data_extractor_core.os, "cpu_count", lambda: 24)
    assert get_recommended_workers() == 24


def test_dayz_misc_sources_overlay_vanilla_paths(tmp_path):
    archive_path = tmp_path / "dayz-misc.zip"
    output = tmp_path / "output"
    road = output / "DZ" / "structures" / "roads" / "parts" / "asf1_6konec.p3d"
    road.parent.mkdir(parents=True)
    road.write_bytes(b"binarized")

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("DayZ-Misc-master/Road Parts/Chernarus/asf1_6konec.p3d", b"source")
        archive.writestr("DayZ-Misc-master/Road Parts/Nasdara/asf1takistan_25.p3d", b"nasdara")
        archive.writestr("DayZ-Misc-master/Powerlines/powerline_hv3.xml", b"hv3")
        archive.writestr("DayZ-Misc-master/Powerlines/powerline_vhv.xml", b"vhv")
        archive.writestr("DayZ-Misc-master/Powerlines/powerline_hv2.xml", b"hv2")
        archive.writestr("DayZ-Misc-master/Water/Sakhal/sakhal_50x50_Ice_Lake_clear.p3d", b"ice")
        archive.writestr("DayZ-Misc-master/README.md", b"ADPL-SA")

    result = overlay_dayz_misc_archive(archive_path, output)

    assert result["files"] == 7
    assert road.read_bytes() == b"source"
    assert (
        output / "DZ" / "water_sakhal" / "Ice_Lake" / "sakhal_50x50_Ice_Lake_clear.p3d"
    ).read_bytes() == b"ice"
    assert (output / "DayZ-Misc-README.md").read_bytes() == b"ADPL-SA"
    assert (output / "DZ" / "structures_takistan" / "Roads" / "Parts" / "asf1takistan_25.p3d").read_bytes() == b"nasdara"
    power = output / "DZ" / "structures_takistan" / "Industrial" / "Power"
    assert (power / "powerline_hv3.xml").read_bytes() == b"hv3"
    assert (power / "powerline_vhv.xml").read_bytes() == b"vhv"
    assert (output / "DZ" / "structures_bliss" / "industrial" / "Power" / "powerline_hv2.xml").read_bytes() == b"hv2"
    assert not (output / "DZ" / "structures_bliss" / "industrial" / "Power" / "powerline_hv3.xml").exists()


def test_cleanup_removes_all_selected_base_game_folders_and_preserves_custom_roots(tmp_path):
    roots = ("DZ", "scripts", "Core", "bin", "graphics", "gui", "languagecore", "system")
    for index, root in enumerate(roots):
        make_pbo(tmp_path, f"dta/base_{index}.pbo", root, {"current.txt": b"new"})
    make_pbo(tmp_path, "Addons/custom.pbo", "MyMod", {"current.txt": b"custom"})
    output = tmp_path / "output"
    for root in roots:
        old = output / root.swapcase() / "custom_subfolder" / "stale.txt"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"old")
    custom_roots = ("MyMod", "DZCustom", "script", "scripts_extra", "bin_backup", "@DZ", "Documents")
    for root in custom_roots:
        folder = output / root
        folder.mkdir()
        (folder / "keep.txt").write_bytes(b"keep")
    (output / "root_file.txt").write_bytes(b"keep")
    plan = build_extraction_plan(tmp_path / "game")

    result = extract_game_data(plan, output)

    assert {name.casefold() for name in result["cleaned_folders"]} == {name.casefold() for name in roots}
    for root in roots:
        assert not (output / root.swapcase() / "custom_subfolder").exists()
        assert (output / root / "current.txt").read_bytes() == b"new"
    for root in custom_roots:
        assert (output / root / "keep.txt").read_bytes() == b"keep"
    assert (output / "MyMod" / "current.txt").read_bytes() == b"custom"
    assert (output / "root_file.txt").read_bytes() == b"keep"


def test_cleanup_can_be_disabled(tmp_path):
    make_pbo(tmp_path, "Addons/data.pbo", "DZ", {"current.txt": b"new"})
    output = tmp_path / "output"
    (output / "DZ").mkdir(parents=True)
    (output / "DZ" / "stale.txt").write_bytes(b"keep")
    plan = build_extraction_plan(tmp_path / "game")

    result = extract_game_data(plan, output, clean_base_game_folders=False)

    assert result["cleaned_folders"] == []
    assert (output / "DZ" / "stale.txt").read_bytes() == b"keep"
    assert (output / "DZ" / "current.txt").read_bytes() == b"new"


def test_scripts_only_cleanup_leaves_other_base_game_data_untouched(tmp_path):
    make_pbo(tmp_path, "dta/scripts.pbo", "scripts", {"4_World/current.c": b"new"})
    make_pbo(tmp_path, "Addons/data.pbo", "DZ", {"current.txt": b"data", "current.c": b"script"})
    make_pbo(tmp_path, "dta/core.pbo", "Core", {"current.c": b"core script"})
    output = tmp_path / "output"
    old_script = output / "scripts" / "4_World" / "DayZ" / "stale.c"
    old_script.parent.mkdir(parents=True)
    old_script.write_bytes(b"old")
    (output / "DZ").mkdir()
    (output / "DZ" / "keep.txt").write_bytes(b"keep")
    (output / "Core").mkdir()
    (output / "Core" / "keep.txt").write_bytes(b"keep core")
    plan = build_extraction_plan(tmp_path / "game", include_extensions=".c")

    result = extract_game_data(plan, output)

    assert result["cleaned_folders"] == ["scripts"]
    assert not old_script.exists()
    assert (output / "scripts" / "4_World" / "current.c").read_bytes() == b"new"
    assert (output / "DZ" / "keep.txt").read_bytes() == b"keep"
    assert not (output / "DZ" / "current.txt").exists()
    assert (output / "DZ" / "current.c").read_bytes() == b"script"
    assert (output / "Core" / "keep.txt").read_bytes() == b"keep core"
    assert (output / "Core" / "current.c").read_bytes() == b"core script"


@pytest.mark.parametrize("include_extensions", ["", ".c"])
def test_dayz_misc_overlay_preserves_dz_without_planned_dz_entries(tmp_path, monkeypatch, include_extensions):
    make_pbo(tmp_path, "dta/scripts.pbo", "scripts", {"4_World/current.c": b"new"})
    archive_path = tmp_path / "dayz-misc.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("DayZ-Misc-master/Road Parts/Nasdara/asf1takistan_25.p3d", b"source")
    monkeypatch.setattr(rag_game_data_extractor_core, "_download_dayz_misc", lambda *_: archive_path)
    output = tmp_path / "output"
    (output / "DZ").mkdir(parents=True)
    (output / "DZ" / "stale.txt").write_bytes(b"old")
    old_script = output / "scripts" / "stale.c"
    old_script.parent.mkdir()
    old_script.write_bytes(b"old script")
    road = output / "DZ" / "structures_takistan" / "Roads" / "Parts" / "asf1takistan_25.p3d"
    road.parent.mkdir(parents=True)
    road.write_bytes(b"old road")
    plan = build_extraction_plan(tmp_path / "game", include_extensions=include_extensions)

    result = extract_game_data(plan, output, include_dayz_misc=True)

    assert result["cleaned_folders"] == ["scripts"]
    assert not old_script.exists()
    assert (output / "scripts" / "4_World" / "current.c").read_bytes() == b"new"
    assert (output / "DZ" / "stale.txt").read_bytes() == b"old"
    assert road.read_bytes() == b"source"
    assert not archive_path.exists()


def test_empty_filtered_plan_does_not_clean_base_game_folders(tmp_path):
    make_pbo(tmp_path, "Addons/data.pbo", "DZ", {"current.txt": b"data"})
    output = tmp_path / "output"
    (output / "DZ").mkdir(parents=True)
    (output / "DZ" / "keep.txt").write_bytes(b"keep")
    plan = build_extraction_plan(tmp_path / "game", include_extensions=".c")

    result = extract_game_data(plan, output)

    assert result["cleaned_folders"] == []
    assert (output / "DZ" / "keep.txt").read_bytes() == b"keep"


@pytest.mark.parametrize("source_missing", [False, True])
def test_unusable_source_archive_prevents_cleanup(tmp_path, source_missing):
    archive = make_pbo(tmp_path, "Addons/data.pbo", "DZ", {"current.txt": b"data"})
    plan = build_extraction_plan(tmp_path / "game")
    output = tmp_path / "output"
    (output / "DZ").mkdir(parents=True)
    (output / "DZ" / "keep.txt").write_bytes(b"keep")
    if source_missing:
        archive.unlink()
    else:
        archive.write_bytes(b"changed")

    with pytest.raises((rag_game_data_extractor_core.GameDataExtractorError, FileNotFoundError)):
        extract_game_data(plan, output)

    assert (output / "DZ" / "keep.txt").read_bytes() == b"keep"


def test_cleanup_cannot_remove_source_installation_beneath_output(tmp_path):
    source_root = tmp_path / "DZ"
    make_pbo(source_root, "Addons/data.pbo", "DZ", {"current.txt": b"data"})
    make_pbo(source_root, "dta/bin.pbo", "bin", {"current.txt": b"bin"})
    plan = build_extraction_plan(source_root / "game")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "keep.txt").write_bytes(b"keep")

    with pytest.raises(rag_game_data_extractor_core.GameDataExtractorError, match="containing DayZ source data"):
        extract_game_data(plan, tmp_path)

    assert plan.archives[0].path.exists()
    assert (tmp_path / "bin" / "keep.txt").read_bytes() == b"keep"


def make_directory_link(link, target):
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


def test_linked_base_game_root_is_rejected_before_any_deletion(tmp_path):
    make_pbo(tmp_path, "Addons/data.pbo", "DZ", {"current.txt": b"data"})
    make_pbo(tmp_path, "dta/bin.pbo", "bin", {"current.txt": b"bin"})
    output = tmp_path / "output"
    (output / "bin").mkdir(parents=True)
    (output / "bin" / "keep.txt").write_bytes(b"keep")
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "keep.txt").write_bytes(b"keep")
    link = output / "DZ"
    make_directory_link(link, custom)
    plan = build_extraction_plan(tmp_path / "game")

    with pytest.raises(rag_game_data_extractor_core.GameDataExtractorError, match="linked base-game folder"):
        extract_game_data(plan, output)

    assert (custom / "keep.txt").read_bytes() == b"keep"
    assert (output / "bin" / "keep.txt").read_bytes() == b"keep"


def test_cleanup_does_not_follow_nested_directory_links(tmp_path):
    make_pbo(tmp_path, "Addons/data.pbo", "DZ", {"current.txt": b"new"})
    output = tmp_path / "output"
    (output / "DZ").mkdir(parents=True)
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "keep.txt").write_bytes(b"keep")
    make_directory_link(output / "DZ" / "nested_link", custom)
    plan = build_extraction_plan(tmp_path / "game")

    result = extract_game_data(plan, output)

    assert result["cleaned_folders"] == ["DZ"]
    assert (custom / "keep.txt").read_bytes() == b"keep"
    assert not (output / "DZ" / "nested_link").exists()


@pytest.mark.parametrize("cancel_at_cleanup", [False, True])
def test_cancellation_before_deletion_preserves_existing_data(tmp_path, cancel_at_cleanup):
    make_pbo(tmp_path, "Addons/data.pbo", "DZ", {"current.txt": b"new"})
    output = tmp_path / "output"
    (output / "DZ").mkdir(parents=True)
    (output / "DZ" / "keep.txt").write_bytes(b"keep")
    cancel = threading.Event()
    if not cancel_at_cleanup:
        cancel.set()

    def progress(event):
        if event["type"] == "cleanup":
            cancel.set()

    plan = build_extraction_plan(tmp_path / "game")
    with pytest.raises(rag_game_data_extractor_core.ExtractionCancelled):
        extract_game_data(plan, output, cancel_event=cancel, progress=progress)

    assert (output / "DZ" / "keep.txt").read_bytes() == b"keep"


@pytest.mark.parametrize("clean", [False, True])
@pytest.mark.parametrize("failure", ["extract", "commit", "cancel"])
def test_failed_extraction_restores_previous_output(tmp_path, monkeypatch, clean, failure):
    make_pbo(tmp_path, "Addons/data.pbo", "DZ", {"a.txt": b"new a", "nested/b.txt": b"new b"})
    plan = build_extraction_plan(tmp_path / "game")
    output = tmp_path / "output"
    (output / "DZ" / "empty").mkdir(parents=True)
    (output / "DZ" / "a.txt").write_bytes(b"old a")
    (output / "DZ" / "keep.txt").write_bytes(b"keep")
    (output / MANIFEST_NAME).write_bytes(b"old manifest")
    cancel = threading.Event()
    original_replace = os.replace

    def failing_replace(source, target):
        source = Path(source)
        target = Path(target)
        if "stage" in source.parts and target == output / "DZ" / "nested" / "b.txt":
            if failure == "commit":
                raise OSError("simulated commit failure")
            result = original_replace(source, target)
            if failure == "cancel":
                cancel.set()
            return result
        return original_replace(source, target)

    monkeypatch.setattr(rag_game_data_extractor_core.os, "replace", failing_replace)
    if failure == "extract":
        def failing_extract(*_args):
            raise OSError("simulated read failure")
        monkeypatch.setattr(rag_game_data_extractor_core, "_extract_entry", failing_extract)

    with pytest.raises((OSError, rag_game_data_extractor_core.GameDataExtractorError)):
        extract_game_data(plan, output, workers=1, cancel_event=cancel, clean_base_game_folders=clean)

    assert (output / "DZ" / "a.txt").read_bytes() == b"old a"
    assert (output / "DZ" / "keep.txt").read_bytes() == b"keep"
    assert (output / "DZ" / "empty").is_dir()
    assert not (output / "DZ" / "nested").exists()
    assert (output / MANIFEST_NAME).read_bytes() == b"old manifest"
    assert not list(output.glob(".rag_extract_*"))


def test_conversion_failure_keeps_old_data_and_reports_details(tmp_path, monkeypatch):
    make_pbo(tmp_path, "Addons/config.pbo", "DZ", {"config.bin": b"broken config"})
    output = tmp_path / "output"
    (output / "DZ").mkdir(parents=True)
    (output / "DZ" / "config.cpp").write_bytes(b"working config")
    converter = tmp_path / "CfgConvert.exe"
    converter.write_bytes(b"placeholder")

    def fail_conversion(*_args):
        raise RuntimeError("simulated conversion error")

    monkeypatch.setattr(rag_game_data_extractor_core, "convert_bin_to_cpp", fail_conversion)
    events = []
    with pytest.raises(rag_game_data_extractor_core.GameDataExtractorError, match="Conversion failed"):
        extract_game_data(build_extraction_plan(tmp_path / "game"), output, cfgconvert_exe=converter, progress=events.append)
    assert (output / "DZ" / "config.cpp").read_bytes() == b"working config"
    assert any("simulated conversion error" in event.get("message", "") for event in events)
    assert not list(output.glob(".rag_extract_*"))


def test_disk_space_is_checked_before_download_or_cleanup(tmp_path, monkeypatch):
    make_pbo(tmp_path, "Addons/data.pbo", "DZ", {"current.txt": b"new"})
    plan = build_extraction_plan(tmp_path / "game")
    output = tmp_path / "output"
    (output / "DZ").mkdir(parents=True)
    (output / "DZ" / "keep.txt").write_bytes(b"keep")
    monkeypatch.setattr(rag_game_data_extractor_core, "get_free_space", lambda _path: 0)
    monkeypatch.setattr(rag_game_data_extractor_core, "_download_dayz_misc", lambda *_args: pytest.fail("download should not start"))
    with pytest.raises(rag_game_data_extractor_core.GameDataExtractorError, match="Insufficient disk space"):
        extract_game_data(plan, output, include_dayz_misc=True)
    assert (output / "DZ" / "keep.txt").read_bytes() == b"keep"
    assert not list(output.glob(".rag_extract_*"))
    assert rag_game_data_extractor_core.get_required_space(plan, True) > rag_game_data_extractor_core.get_required_space(plan)


def test_cleanup_preview_reports_exact_scope_and_ignores_links(tmp_path):
    make_pbo(tmp_path, "Addons/data.pbo", "DZ", {"current.txt": b"new"})
    output = tmp_path / "output"
    (output / "DZ" / "nested").mkdir(parents=True)
    (output / "DZ" / "nested" / "old.txt").write_bytes(b"old")
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "keep.txt").write_bytes(b"keep")
    make_directory_link(output / "DZ" / "linked", custom)
    plan = build_extraction_plan(tmp_path / "game")
    preview = rag_game_data_extractor_core.get_cleanup_preview(plan, output)
    assert preview == [{"path": str(output / "DZ"), "files": 1, "bytes": 3}]
    assert rag_game_data_extractor_core.get_cleanup_preview(plan, output, incremental=True) == []
    assert (output / "DZ" / "nested" / "old.txt").read_bytes() == b"old"


@pytest.mark.parametrize("change", ["modified", "added", "removed"])
def test_source_changes_invalidate_cached_plan(tmp_path, change):
    archive = make_pbo(tmp_path, "Addons/data.pbo", "DZ", {"current.txt": b"new"})
    plan = build_extraction_plan(tmp_path / "game")
    assert rag_game_data_extractor_core.is_plan_current(plan)
    if change == "modified":
        archive.write_bytes(b"changed")
    elif change == "added":
        make_pbo(tmp_path, "Addons/extra.pbo", "extra", {"new.txt": b"new"})
    else:
        archive.unlink()
    assert not rag_game_data_extractor_core.is_plan_current(plan)
    with pytest.raises(rag_game_data_extractor_core.GameDataExtractorError, match="rescan"):
        extract_game_data(plan, tmp_path / "output")


def test_incremental_update_verifies_outputs_and_preserves_unrelated_files(tmp_path):
    make_pbo(tmp_path, "Addons/data.pbo", "DZ", {"a.txt": b"new a", "b.txt": b"new b"})
    plan = build_extraction_plan(tmp_path / "game")
    output = tmp_path / "output"
    (output / "DZ").mkdir(parents=True)
    (output / "DZ" / "keep.txt").write_bytes(b"keep")
    first = extract_game_data(plan, output, incremental=True)
    original_mtime = (output / "DZ" / "a.txt").stat().st_mtime_ns
    second = extract_game_data(plan, output, incremental=True)
    assert second["files"] == 0
    assert second["skipped_files"] == len(plan.entries)
    assert (output / "DZ" / "a.txt").stat().st_mtime_ns == original_mtime
    (output / "DZ" / "a.txt").write_bytes(b"edit!")
    (output / "DZ" / "b.txt").unlink()
    third = extract_game_data(plan, output, incremental=True)
    assert third["files"] == 2
    assert third["skipped_files"] == len(plan.entries) - 2
    assert first["cleaned_folders"] == second["cleaned_folders"] == third["cleaned_folders"] == []
    assert (output / "DZ" / "a.txt").read_bytes() == b"new a"
    assert (output / "DZ" / "b.txt").read_bytes() == b"new b"
    assert (output / "DZ" / "keep.txt").read_bytes() == b"keep"


def test_incremental_conversion_tracks_cpp_and_removes_old_binary(tmp_path, monkeypatch):
    make_pbo(tmp_path, "Addons/config.pbo", "DZ", {"config.bin": b"config"})
    converter = tmp_path / "CfgConvert.exe"
    converter.write_bytes(b"placeholder")
    calls = []

    def convert(_exe, source, _log):
        calls.append(source)
        target = Path(source).with_suffix(".cpp")
        target.write_bytes(b"converted")
        return str(target)

    monkeypatch.setattr(rag_game_data_extractor_core, "convert_bin_to_cpp", convert)
    plan = build_extraction_plan(tmp_path / "game")
    output = tmp_path / "output"
    (output / "DZ").mkdir(parents=True)
    (output / "DZ" / "config.bin").write_bytes(b"old")
    extract_game_data(plan, output, incremental=True, cfgconvert_exe=converter)
    second = extract_game_data(plan, output, incremental=True, cfgconvert_exe=converter)
    assert second["files"] == 0
    assert len(calls) == 1
    assert not (output / "DZ" / "config.bin").exists()
    (output / "DZ" / "config.cpp").write_bytes(b"edited")
    extract_game_data(plan, output, incremental=True, cfgconvert_exe=converter)
    assert len(calls) == 2
    assert (output / "DZ" / "config.cpp").read_bytes() == b"converted"


@pytest.mark.parametrize("selection", ["archives", "folders", "profile"])
def test_selective_extraction_preserves_unselected_data(tmp_path, selection):
    make_pbo(tmp_path, "Addons/first.pbo", "DZ\\first", {"a.c": b"a", "asset.p3d": b"model"})
    make_pbo(tmp_path, "Addons/second.pbo", "DZ\\second", {"b.c": b"b"})
    options = {
        "archives": {"selected_archives": ["addons/first.pbo"]},
        "folders": {"selected_folders": ["dz/first"]},
        "profile": {"include_extensions": ".p3d"},
    }[selection]
    plan = build_extraction_plan(tmp_path / "game", **options)
    assert all(item.relative_path.casefold().startswith("dz\\first\\") for item in plan.entries)
    output = tmp_path / "output"
    (output / "DZ" / "second").mkdir(parents=True)
    (output / "DZ" / "second" / "keep.txt").write_bytes(b"keep")
    result = extract_game_data(plan, output)
    assert result["cleaned_folders"] == []
    assert (output / "DZ" / "second" / "keep.txt").read_bytes() == b"keep"
    assert (output / "DZ" / "first" / "asset.p3d").read_bytes() == b"model"
    assert not (output / "DZ" / "second" / "b.c").exists()


def test_progress_reports_throughput_elapsed_and_eta(tmp_path):
    make_pbo(tmp_path, "Addons/data.pbo", "DZ", {"current.txt": b"new"})
    events = []
    result = extract_game_data(build_extraction_plan(tmp_path / "game"), tmp_path / "output", progress=events.append)
    event = [event for event in events if event["type"] == "file"][-1]
    assert event["elapsed_seconds"] > 0
    assert event["bytes_per_second"] > 0
    assert event["eta_seconds"] == 0
    assert result["elapsed_seconds"] > 0


def test_gui_automatically_rescans_before_extracting_changed_sources(tmp_path):
    from types import SimpleNamespace
    from rag_game_data_extractor import GameDataExtractorApp

    archive = make_pbo(tmp_path, "Addons/data.pbo", "DZ", {"current.txt": b"new"})
    plan = build_extraction_plan(tmp_path / "game")
    archive.write_bytes(b"changed")
    requests = []
    app = SimpleNamespace(
        busy=False, plan=plan, plan_signature=("settings",), current_plan_signature=lambda: ("settings",),
        log=lambda _message: None, request_estimate=lambda **kwargs: requests.append(kwargs),
    )
    GameDataExtractorApp.extract(app)
    assert requests == [{"force": True, "extract_when_ready": True}]


@pytest.mark.parametrize("profile,filename", [
    ("Scripts only", "script.c"), ("Configs only", "config.cpp"), ("Textures only", "texture.paa"),
    ("Models only", "model.p3d"), ("Materials only", "material.rvmat"),
])
def test_gui_profiles_filter_real_archive_entries(tmp_path, profile, filename):
    from rag_game_data_extractor import get_profile_extensions

    files = {name: b"data" for name in ("script.c", "config.cpp", "texture.paa", "model.p3d", "material.rvmat")}
    make_pbo(tmp_path, "Addons/data.pbo", "DZ", files)
    plan = build_extraction_plan(tmp_path / "game", include_extensions=get_profile_extensions(profile))
    assert [item.relative_path for item in plan.entries] == ["DZ\\" + filename]


def test_failed_rollback_keeps_backup_for_manual_recovery(tmp_path, monkeypatch):
    make_pbo(tmp_path, "Addons/data.pbo", "DZ", {"a.txt": b"new"})
    plan = build_extraction_plan(tmp_path / "game")
    output = tmp_path / "output"
    (output / "DZ").mkdir(parents=True)
    (output / "DZ" / "keep.txt").write_bytes(b"keep")
    original_replace = os.replace

    def locked_replace(source, target):
        source = Path(source)
        if source.parent.name == "backup" or "stage" in source.parts and Path(target) == output / "DZ" / "a.txt":
            raise OSError("simulated lock")
        return original_replace(source, target)

    monkeypatch.setattr(rag_game_data_extractor_core.os, "replace", locked_replace)
    with pytest.raises(rag_game_data_extractor_core.GameDataExtractorError, match="Rollback incomplete"):
        extract_game_data(plan, output)
    backups = list(output.glob(".rag_extract_*/backup/DZ/keep.txt"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"keep"


def test_source_changes_during_staging_preserve_existing_output(tmp_path):
    archive = make_pbo(tmp_path, "Addons/data.pbo", "DZ", {"current.txt": b"new"})
    plan = build_extraction_plan(tmp_path / "game")
    output = tmp_path / "output"
    (output / "DZ").mkdir(parents=True)
    (output / "DZ" / "keep.txt").write_bytes(b"keep")

    def progress(event):
        if event["type"] == "file" and event["current"] == len(plan.entries):
            details = archive.stat()
            os.utime(archive, ns=(details.st_atime_ns, details.st_mtime_ns + 1_000_000_000))

    with pytest.raises(rag_game_data_extractor_core.GameDataExtractorError, match="changed during extraction"):
        extract_game_data(plan, output, progress=progress)
    assert (output / "DZ" / "keep.txt").read_bytes() == b"keep"
    assert not (output / "DZ" / "current.txt").exists()


def test_invalid_dayz_misc_archive_preserves_existing_output(tmp_path, monkeypatch):
    make_pbo(tmp_path, "Addons/data.pbo", "DZ", {"current.txt": b"new"})
    output = tmp_path / "output"
    (output / "DZ").mkdir(parents=True)
    (output / "DZ" / "keep.txt").write_bytes(b"keep")
    archive = tmp_path / "invalid.zip"
    archive.write_bytes(b"invalid archive")
    monkeypatch.setattr(rag_game_data_extractor_core, "_download_dayz_misc", lambda *_args: archive)
    with pytest.raises(zipfile.BadZipFile):
        extract_game_data(build_extraction_plan(tmp_path / "game"), output, include_dayz_misc=True)
    assert (output / "DZ" / "keep.txt").read_bytes() == b"keep"
    assert not (output / "DZ" / "current.txt").exists()
    assert not list(output.glob(".rag_extract_*"))
