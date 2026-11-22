import os
import subprocess
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

import pbo_core
import rag_build_pipeline as pipeline
import rag_pbo_writer
import rag_relocator_core
import rag_update_check
from rag_publisher_core import PublishRequest, SteamBridgeError, publish_workshop_item, scan_mod_folder
from rag_update_check import UpdateCancelled, download_update, parse_checksum
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


def make_archive(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_bytes(b"new")
    (source / "b.txt").write_bytes(b"other")
    archive = tmp_path / "sample.pbo"
    rag_pbo_writer.pack_pbo(source, archive, "", lambda _line: None)
    return source, archive


def test_pack_rejects_source_size_change_and_preserves_output(tmp_path, monkeypatch):
    source, archive = make_archive(tmp_path)
    before = archive.read_bytes()
    file = source / "a.txt"

    def open_source(path, mode):
        if Path(path) == file and mode == "rb":
            file.write_bytes(b"larger than the declared size")
        return open(path, mode)

    monkeypatch.setattr(rag_pbo_writer, "open", open_source, raising=False)
    with pytest.raises(BuildError, match="Source changed"):
        rag_pbo_writer.pack_pbo(source, archive, "", lambda _line: None)
    assert archive.read_bytes() == before


def test_packed_verification_rejects_payload_corruption(tmp_path):
    _, archive = make_archive(tmp_path)
    header = pbo_core.read_pbo_archive(archive)
    with archive.open("r+b") as output:
        output.seek(header["data_start"])
        output.write(b"X")
    with pytest.raises(BuildError, match="checksum mismatch"):
        rag_pbo_writer.verify_packed_pbo(archive, "", lambda _line: None)


def test_inspector_previews_config_bin_but_rejects_texture_headers(tmp_path, monkeypatch):
    import rag_pbo_inspector_gui as inspector

    source = tmp_path / "source"
    source.mkdir()
    (source / "config.bin").write_bytes(b"binary config")
    (source / "texHeaders.bin").write_bytes(b"texture headers")
    archive = tmp_path / "sample.pbo"
    rag_pbo_writer.pack_pbo(source, archive, "", lambda _line: None)
    entries = {entry.name: entry for entry in pbo_core.read_pbo_archive(archive)["entries"]}
    converter = tmp_path / "CfgConvert.exe"
    converter.touch()
    app = SimpleNamespace(cfgconvert_exe_var=SimpleNamespace(get=lambda: str(converter)), log=lambda _line: None)

    def convert(exe, path, _log):
        assert exe == str(converter)
        assert Path(path).read_bytes() == b"binary config"
        result = Path(path).with_suffix(".cpp")
        result.write_bytes(b"class CfgPatches {};")
        return result

    monkeypatch.setattr(inspector, "convert_bin_to_cpp", convert)
    content, details = inspector.PboInspectorApp.load_bin_preview(app, entries["config.bin"], archive)
    assert content == "class CfgPatches {};"
    assert "converted with CfgConvert" in details
    with pytest.raises(pbo_core.PboError, match="texHeaders.bin is not a config bin"):
        inspector.PboInspectorApp.load_bin_preview(app, entries["texHeaders.bin"], archive)


@pytest.mark.parametrize("extension", ["paa", "PAC", "png", "tga", "jpg", "jpeg"])
def test_inspector_opens_texture_selection_with_texview(tmp_path, monkeypatch, extension):
    import rag_inspector_extract
    import rag_pbo_inspector_gui as inspector

    source = tmp_path / "source"
    source.mkdir()
    texture = source / f"paint_co.{extension}"
    texture.write_bytes(b"texture data")
    archive = tmp_path / "sample.pbo"
    rag_pbo_writer.pack_pbo(source, archive, "", lambda _line: None)
    entry = pbo_core.read_pbo_archive(archive)["entries"][0]
    texview = tmp_path / "TexView.exe"
    texview.touch()
    finished = Event()
    launches = []

    def launch(args, cwd):
        executable, filename = map(Path, args)
        assert executable == texview
        assert cwd == tmp_path
        assert filename.read_bytes() == b"texture data"
        assert filename.suffix == (".jpg" if extension == "jpeg" else "." + extension.lower())
        launches.append(filename)
        return SimpleNamespace(wait=lambda: finished.wait(5))

    started_threads = []
    start_thread = rag_inspector_extract.threading.Thread.start

    def start(thread):
        started_threads.append(thread)
        start_thread(thread)

    monkeypatch.setattr(rag_inspector_extract.subprocess, "Popen", launch)
    monkeypatch.setattr(rag_inspector_extract.threading.Thread, "start", start)
    app = SimpleNamespace(
        texview_exe_var=SimpleNamespace(get=lambda: str(texview)),
        pbo_path_var=SimpleNamespace(get=lambda: str(archive)),
        ensure_current_archive_loaded=lambda: True,
        get_single_selected_entry=lambda: entry,
        log=lambda _line: None,
    )
    app.view_texture = lambda item: inspector.PboInspectorApp.view_texture(app, item)
    try:
        inspector.PboInspectorApp.view_selected_entry(app)
        assert len(launches) == 1
        assert launches[0].is_file()
    finally:
        finished.set()
        for thread in started_threads:
            thread.join(5)
    assert not launches[0].exists()


@pytest.mark.parametrize("admin,folder", [("cot", "@Community-Online-Tools"), ("vpp", "@VPPAdminTools")])
def test_local_server_isolates_mission_and_configures_detected_admin(tmp_path, admin, folder):
    from rag_local_server import local_server_commands, plan_local_server, prepare_local_server

    game = tmp_path / "DayZ"
    mission = game / "mpmissions" / "dayzOffline.chernarusplus"
    mission.mkdir(parents=True)
    (mission / "init.c").write_text("void main() {}", encoding="utf-8")
    (mission / "storage_1").mkdir()
    (mission / "profiles").mkdir()
    (mission / "profiles" / "private.txt").write_text("keep original", encoding="utf-8")
    (game / "DayZDiag_x64.exe").touch()
    workshop = game / "!Workshop"
    output = tmp_path / "@Test Mod"
    for path in [output, *(workshop / name for name in ("@CF", folder, "@Chosen Mod", "@Not selected", output.name))]:
        (path / "addons").mkdir(parents=True)
        (path / "addons" / "test.pbo").touch()
    steam_id = "76561198000000000"
    options = dict(game=str(game), mission=str(mission), port="24102", steam_id=steam_id, mods=["@Chosen Mod", output.name])
    plan = plan_local_server(options, output, tmp_path / "managed")
    assert plan.admin == admin
    assert [path.name for path in plan.mods] == ["@CF", folder, "@Chosen Mod", output.name]
    copy, password = prepare_local_server(plan)
    config = plan.config.read_text()
    assert "BattlEye = 0;" in config
    assert "steamQueryPort = 24105;" in config
    assert "vppDisablePassword = 1;" in config
    assert password and (copy / "init.c").read_bytes() == (mission / "init.c").read_bytes()
    assert not (copy / "storage_1").exists() and not (copy / "profiles").exists()
    assert (mission / "profiles" / "private.txt").read_text() == "keep original"
    server, client = local_server_commands(plan, copy, password)
    assert "-server" in server and "-ip=127.0.0.1" in server
    assert "-connect=127.0.0.1" in client and f"-password={password}" in client
    assert next(flag for flag in server if flag.startswith("-mod=")) == next(flag for flag in client if flag.startswith("-mod="))
    assert f"-profiles={plan.server_profile}" in server and f"-profiles={plan.client_profile}" in client
    prepare_local_server(plan)
    if admin == "vpp":
        ids = plan.server_profile / "VPPAdminTools/Permissions/SuperAdmins/SuperAdmins.txt"
        assert ids.read_text().splitlines() == [steam_id]
    else:
        import json
        players = plan.server_profile / "PermissionsFramework/Players"
        assert json.loads((players / f"{steam_id}.json").read_text())["Roles"] == ["everyone", "rag_local_admin"]
        assert (players.parent / "Roles/rag_local_admin.txt").is_file()


def test_local_server_loads_local_mods_and_explicit_vpp_without_admin_setup(tmp_path):
    from rag_local_server import local_server_commands, plan_local_server, prepare_local_server

    game = tmp_path / "DayZ"
    mission = game / "mpmissions" / "dayzOffline.chernarusplus"
    mission.mkdir(parents=True)
    (mission / "init.c").touch()
    (game / "DayZDiag_x64.exe").touch()
    workshop = game / "!Workshop"
    cf = workshop / "@CF"
    vpp = workshop / "@VPPAdminTools"
    local = tmp_path / "@Local Mod"
    output = tmp_path / "@Build Output"
    for path in (cf, vpp, local, output):
        (path / "Addons").mkdir(parents=True)
        (path / "Addons" / "test.pbo").touch()

    options = dict(game=str(game), mission=str(mission), mods=[vpp.name], local_mods=[str(local), str(local), str(output)])
    plan = plan_local_server(options, output, tmp_path / "managed")
    assert plan.mods == (cf, vpp, local, output)
    assert plan.admin == ""
    staged_mission, password = prepare_local_server(plan)
    assert "vppDisablePassword = 1;" in plan.config.read_text()
    assert not (plan.server_profile / "VPPAdminTools").exists()
    server, client = local_server_commands(plan, staged_mission, password)
    argument = "-mod=" + ";".join(str(path) for path in plan.mods)
    assert argument in server and argument in client
    with pytest.raises(BuildError, match="Local mod has no .pbo files"):
        plan_local_server({**options, "local_mods": [str(tmp_path / "missing")]}, output, tmp_path / "managed")


def test_stock_mission_download_checks_files_before_installing(tmp_path, monkeypatch):
    import hashlib
    import json
    import rag_local_server

    content = b"void main() {}\n"
    digest = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
    branch = json.dumps({"object": {"sha": "a" * 40}}).encode()
    tree = json.dumps({"tree": [{"type": "blob", "mode": "100644", "path": "dayzOffline.chernarusplus/init.c", "size": len(content), "sha": digest},
                                {"type": "blob", "path": "dayzOffline.enoch/ignored.txt", "size": 1}]}).encode()
    response = iter([branch, tree, b"x" * len(content), branch, tree, content])
    monkeypatch.setattr(rag_local_server.urllib.request, "urlopen", lambda *args, **kwargs: BytesIO(next(response)))
    with pytest.raises(BuildError, match="published checksum"):
        rag_local_server.download_stock_mission("Chernarus", tmp_path)
    assert not list(tmp_path.iterdir())
    mission = rag_local_server.download_stock_mission("Chernarus", tmp_path)
    assert (mission / "init.c").read_bytes() == content
    assert (mission / "Bohemia-source.txt").is_file()


def test_post_build_launch_uses_completed_build_output():
    from rag_pbo_builder_gui import RaGPboBuilderApp

    calls = []
    app = SimpleNamespace(
        local_server_settings={"mode": "diag", "game": "wrong installation"},
        start_managed_server=lambda settings, output, **options: calls.append((settings, output, options)) or "",
    )
    saved = {"mode": "diag", "game": "selected installation"}
    result = {"start_server_after_build": True, "local_server": saved, "output_root_dir": "completed output"}
    assert RaGPboBuilderApp.launch_local_server(app, result) == (True, "")
    assert calls == [(saved, "completed output", {"auto_join": True, "manual": False})]


def test_builder_rebuilds_corrupt_cached_output(tmp_path, monkeypatch):
    source = tmp_path / "source"
    addon = source / "addon"
    addon.mkdir(parents=True)
    (addon / "data.txt").write_bytes(b"pack me")
    destination = tmp_path / "mod"
    settings = make_settings(build_parser().parse_args(["build", "--log-file", str(tmp_path / "build.log")]), {
        "source_root": str(source), "output_root": str(destination), "project_root": str(tmp_path),
        "temp_dir": str(tmp_path / "temp"), "use_binarize": False, "convert_config": False, "sign_pbos": False,
    })
    settings["selected_addons"] = ["addon"]
    cache = {}
    monkeypatch.setattr(pipeline, "load_build_cache", lambda: cache)
    monkeypatch.setattr(pipeline, "save_build_cache", lambda _cache: None)

    def build():
        return pipeline.build_all(settings, lambda _line: None, lambda *_progress: None)

    assert build()["built"] == 1
    assert build()["skipped"] == 1
    output = destination / "Addons" / "addon.pbo"
    original = output.read_bytes()
    with output.open("r+b") as file:
        file.seek(-1, os.SEEK_END)
        file.write(bytes([original[-1] ^ 1]))
    assert build()["built"] == 1
    assert output.read_bytes() == original


@pytest.mark.parametrize("failure", ["read", "convert", "commit", "cancel"])
def test_failed_inspector_extraction_keeps_previous_files(tmp_path, monkeypatch, failure):
    _, archive = make_archive(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    (output / "a.txt").write_bytes(b"keep")
    cancel = Event()
    original_read = pbo_core.read_pbo_archive
    original_replace = os.replace

    def truncate_after_scan(path):
        header = original_read(path)
        with archive.open("r+b") as file:
            file.truncate(header["data_start"] + 1)
        return header

    def replace(src, dst):
        if Path(dst) == output / "b.txt":
            if failure == "commit":
                raise OSError("Failed to install file")
            if failure == "cancel":
                cancel.set()
        return original_replace(src, dst)

    def convert(_paths, _check_cancelled):
        if failure == "convert":
            raise RuntimeError("Conversion failed")

    if failure == "read":
        monkeypatch.setattr(pbo_core, "read_pbo_archive", truncate_after_scan)
    monkeypatch.setattr(pbo_core.os, "replace", replace)
    with pytest.raises((pbo_core.PboError, OSError, RuntimeError)):
        pbo_core.extract_pbo_files(archive, output, cancel_event=cancel, prepare=convert)
    assert (output / "a.txt").read_bytes() == b"keep"
    assert sorted(path.name for path in output.iterdir()) == ["a.txt"]


def test_relocator_ignores_junction_and_refuses_it_if_added_after_scan(tmp_path):
    source = tmp_path / "source"
    external = tmp_path / "external"
    child = source / "child"
    external.mkdir()
    child.mkdir(parents=True)
    data = b'path="Old\\data\\asset.p3d";'
    (child / "config.cpp").write_bytes(data)
    (external / "config.cpp").write_bytes(data)
    before = rag_relocator_core.scan_references(source, "Old/data", "New/data")
    (child / "config.cpp").unlink()
    child.rmdir()
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(child), str(external)], check=True, capture_output=True)
    else:
        child.symlink_to(external, target_is_directory=True)
    after = rag_relocator_core.scan_references(source, "Old/data", "New/data")
    assert not after.changes
    with pytest.raises(rag_relocator_core.RelocatorError, match="Linked path refused"):
        rag_relocator_core.apply_scan(before)
    assert (external / "config.cpp").read_bytes() == data


def test_relocator_cancellation_rolls_back_and_backup_restores(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    original = b'path="Old\\data\\asset.p3d";'
    paths = [source / name for name in ("config.cpp", "extra.cpp")]
    for path in paths:
        path.write_bytes(original)
    scan = rag_relocator_core.scan_references(source, "Old/data", "New/data")
    cancel = Event()
    atomic_write = rag_relocator_core._atomic_write

    def write(*args):
        atomic_write(*args)
        cancel.set()

    monkeypatch.setattr(rag_relocator_core, "_atomic_write", write)
    with pytest.raises(rag_relocator_core.RelocationCancelled):
        rag_relocator_core.apply_scan(scan, cancel_event=cancel)
    assert all(path.read_bytes() == original for path in paths)
    monkeypatch.setattr(rag_relocator_core, "_atomic_write", atomic_write)
    result = rag_relocator_core.apply_scan(scan)
    paths[0].write_bytes(b"edited since relocation")
    with pytest.raises(rag_relocator_core.RelocatorError, match="since relocation"):
        rag_relocator_core.restore_backup(source, result.backup_path)
    paths[0].write_bytes(original.replace(b"Old", b"New"))
    restored = rag_relocator_core.restore_backup(source, result.backup_path)
    assert restored.changed_files == 2
    assert restored.backup_path.is_file()
    assert all(path.read_bytes() == original for path in paths)


def test_changed_file_during_staging_blocks_submission_and_saves_log(tmp_path):
    root = tmp_path / "mod"
    (root / "Addons").mkdir(parents=True)
    (root / "Addons" / "test.pbo").write_bytes(b"test")
    second = root / "Addons" / "z.pbo"
    second.write_bytes(b"original")
    report = scan_mod_folder(root)
    backend = SimpleNamespace(update_item=lambda *args, **kwargs: pytest.fail("Upload should not start"))
    logs = tmp_path / "logs"

    def progress(message):
        if message.startswith("Staging 1/"):
            second.write_bytes(b"modified")

    with pytest.raises(ValueError, match="File changed after preflight"):
        publish_workshop_item(PublishRequest(str(root), "1234567890", "Test update", log_directory=str(logs)), report=report, backend=backend, progress=progress)
    assert "z.pbo" in next(logs.glob("*.log")).read_text(encoding="utf-8")


def test_steam_failure_saves_transcript(tmp_path):
    class FailedBackend:
        def update_item(self, request, event_callback=None, log_callback=None):
            log_callback("Steam bridge diagnostic")
            raise SteamBridgeError("STEAM_FAILED", "Upload failed")

    root = tmp_path / "mod"
    (root / "Addons").mkdir(parents=True)
    (root / "Addons" / "test.pbo").write_bytes(b"test")
    logs = tmp_path / "logs"
    with pytest.raises(SteamBridgeError, match="Upload failed"):
        publish_workshop_item(PublishRequest(str(root), "1234567890", "Test update", log_directory=str(logs)), backend=FailedBackend())
    log = next(logs.glob("*.log")).read_text(encoding="utf-8")
    assert "Steam bridge diagnostic" in log
    assert "Upload failed" in log


def test_checksum_rejects_unrelated_or_conflicting_filenames():
    installer = "RaG_PBO_Tools_Setup.exe"
    first, second = "a" * 64, "b" * 64
    assert parse_checksum(f"{first}  Other.exe\n{second}", installer) == ""
    assert parse_checksum(f"{first}  {installer}\n{second} *{installer}", installer) == ""
    assert parse_checksum(first, installer) == first


def test_cancelled_download_preserves_verified_installer_and_allows_retry(tmp_path, monkeypatch):
    payload = b"installer data"
    name = "RaG_PBO_Tools_Setup.exe"
    target = tmp_path / name
    target.write_bytes(b"old installer")
    update = {"installer": {"name": name, "browser_download_url": "https://example.invalid", "digest": "sha256:" + sha256(payload).hexdigest()}}

    def open_response(*args, **kwargs):
        response = BytesIO(payload)
        response.headers = {"Content-Length": str(len(payload))}
        return response

    monkeypatch.setattr(rag_update_check.urllib.request, "urlopen", open_response)
    cancel = Event()
    progress = []

    def on_progress(count, total):
        progress.append((count, total))
        cancel.set()

    with pytest.raises(UpdateCancelled):
        download_update(update, tmp_path, on_progress, cancel)
    assert target.read_bytes() == b"old installer"
    assert progress == [(len(payload), len(payload))]
    assert not list(tmp_path.glob("*.part"))
    cancel.clear()
    assert download_update(update, tmp_path, cancel_event=cancel).read_bytes() == payload
