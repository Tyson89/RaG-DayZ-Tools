from pathlib import Path
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
