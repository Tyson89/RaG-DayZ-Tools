import os
import re
import shutil
import tempfile
import threading
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from pbo_core import (
    COPY_CHUNK_SIZE,
    get_pbo_entry_unpacked_size,
    get_safe_pbo_extract_path,
    is_pbo_entry_supported,
    read_pbo_archive,
    read_pbo_entry_payload,
)
from rag_inspector_extract import (
    convert_bin_to_cpp,
    convert_rap_to_text,
    is_cfgconvert_candidate_bin_path,
    is_rap_text_convert_candidate_path,
    is_rapified_data,
)


DAYZ_APP_ID = "221100"
DAYZ_EXPERIMENTAL_APP_ID = "1024020"
MANIFEST_NAME = ".rag_game_data_extractor.json"
DAYZ_SCRIPT_MODULES = {
    "1_core",
    "2_gamelib",
    "3_game",
    "4_world",
    "5_mission",
}
DAYZ_MISC_REPO_URL = "https://github.com/BohemiaInteractive/DayZ-Misc"
DAYZ_MISC_DOWNLOAD_URL = "https://codeload.github.com/BohemiaInteractive/DayZ-Misc/zip/refs/heads/master"
DAYZ_MISC_ESTIMATED_BYTES = 68_463_918
DAYZ_MISC_ESTIMATED_FILES = 291
DAYZ_MISC_MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024
DAYZ_MISC_MAX_UNPACKED_BYTES = 256 * 1024 * 1024
DAYZ_MISC_MAX_FILES = 5_000
DAYZ_MISC_FOLDER_TARGETS = {
    ("Road Parts", "Chernarus"): ("DZ", "structures", "roads", "parts"),
    ("Road Parts", "Livonia"): ("DZ", "structures_bliss", "roads", "parts"),
    ("Road Parts", "Sakhal"): ("DZ", "structures_sakhal", "roads", "parts"),
    ("Water", "Chernarus"): ("DZ", "water", "Ponds"),
    ("Water", "Sakhal"): ("DZ", "water_sakhal", "Ice_Lake"),
}
DAYZ_MISC_SOURCE_TARGETS = {
    "Body parts": ("DZ", "characters", "bodies"),
    "Character Proxies": ("DZ", "characters", "proxies"),
    "Powerlines": ("DZ", "structures_bliss", "industrial", "Power"),
    "Rig and Animations": ("DZ", "characters", "animations"),
}


class GameDataExtractorError(Exception):
    pass


class ExtractionCancelled(GameDataExtractorError):
    pass


@dataclass(frozen=True)
class GameInstall:
    name: str
    app_id: str
    path: Path


@dataclass
class ArchiveSource:
    path: Path
    relative_path: str
    prefix: str
    entries: list
    size: int
    mtime_ns: int
    priority: int


@dataclass
class PlannedEntry:
    archive: ArchiveSource
    entry: object
    relative_path: str

    @property
    def output_size(self):
        return get_pbo_entry_unpacked_size(self.entry)


@dataclass
class ExtractionPlan:
    game_root: Path
    archives: list = field(default_factory=list)
    entries: list = field(default_factory=list)
    protected_archives: list = field(default_factory=list)
    shadowed_archives: list = field(default_factory=list)
    unsupported_entries: list = field(default_factory=list)
    filtered_entries: int = 0
    conflicts: list = field(default_factory=list)

    @property
    def total_bytes(self):
        return sum(item.output_size for item in self.entries)


def _decode_steam_path(value):
    return value.replace("\\\\", os.sep).replace("/", os.sep)


def _parse_steam_library_paths(text):
    paths = []
    seen = set()

    for match in re.finditer(r'"path"\s+"([^"]+)"', text or "", re.IGNORECASE):
        path = Path(_decode_steam_path(match.group(1).strip()))
        key = os.path.normcase(os.path.abspath(path))

        if key not in seen:
            seen.add(key)
            paths.append(path)

    return paths


def _registry_steam_paths():
    if os.name != "nt":
        return []

    try:
        import winreg
    except Exception:
        return []

    paths = []
    keys = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Valve\Steam", "InstallPath"),
    ]

    for hive, key_path, value_name in keys:
        try:
            with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ) as key:
                value, _kind = winreg.QueryValueEx(key, value_name)

            if value:
                paths.append(Path(value))
        except OSError:
            continue

    return paths


def get_steam_library_roots():
    pf86 = os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")
    pf = os.environ.get("ProgramFiles", "C:/Program Files")
    roots = [Path(pf86) / "Steam", Path(pf) / "Steam"]
    roots.extend(_registry_steam_paths())
    result = []
    seen = set()

    def add(path):
        key = os.path.normcase(os.path.abspath(path))

        if key not in seen:
            seen.add(key)
            result.append(Path(path))

    for root in roots:
        add(root)

    for root in list(result):
        library_file = root / "steamapps" / "libraryfolders.vdf"

        try:
            text = library_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for path in _parse_steam_library_paths(text):
            add(path)

    return result


def _read_manifest_install_dir(path):
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""

    match = re.search(r'"installdir"\s+"([^"]+)"', text, re.IGNORECASE)
    return _decode_steam_path(match.group(1).strip()) if match else ""


def find_dayz_installations():
    definitions = [
        ("DayZ Stable", DAYZ_APP_ID, "DayZ"),
        ("DayZ Experimental", DAYZ_EXPERIMENTAL_APP_ID, "DayZ Exp"),
    ]
    installs = []
    seen = set()

    for library in get_steam_library_roots():
        for name, app_id, fallback_dir in definitions:
            manifest = library / "steamapps" / f"appmanifest_{app_id}.acf"
            install_dir = _read_manifest_install_dir(manifest) or fallback_dir
            candidate = library / "steamapps" / "common" / install_dir
            key = os.path.normcase(os.path.abspath(candidate))
            has_game_data = any(
                any((candidate / folder).glob("*.pbo"))
                for folder in ("Addons", "dta")
                if (candidate / folder).is_dir()
            )

            if candidate.is_dir() and (manifest.is_file() or has_game_data) and key not in seen:
                seen.add(key)
                installs.append(GameInstall(name, app_id, candidate))

    return installs


def find_cfgconvert():
    for library in get_steam_library_roots():
        candidate = library / "steamapps" / "common" / "DayZ Tools" / "Bin" / "CfgConvert" / "CfgConvert.exe"

        if candidate.is_file():
            return str(candidate)

    return ""


def parse_extension_list(value):
    extensions = set()

    for token in re.split(r"[,;\s]+", value or ""):
        token = token.strip().lower()

        if not token:
            continue

        if token.startswith("*."):
            token = token[1:]
        elif token.startswith("*"):
            token = token[1:]

        if not token.startswith("."):
            token = "." + token

        if any(char in token for char in "\\/:"):
            raise GameDataExtractorError(f"Invalid extension filter: {token}")

        extensions.add(token)

    return extensions


def discover_game_archives(game_root):
    root = Path(game_root)

    if not root.is_dir():
        raise GameDataExtractorError(f"DayZ folder does not exist: {root}")

    locations = []

    for name in ("Addons", "dta"):
        path = root / name

        if path.is_dir():
            locations.append((path, 0))

    for folder in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if not folder.is_dir() or folder.name.startswith(("@", "!")):
            continue

        if folder.name.casefold() in {"addons", "dta"}:
            continue

        addon_dir = folder / "Addons"

        if addon_dir.is_dir():
            locations.append((addon_dir, 1))

    pbo_paths = []
    protected = {}

    for location, priority in locations:
        for path in sorted(location.glob("*.pbo"), key=lambda item: item.name.casefold()):
            pbo_paths.append((path, priority))

        for path in sorted(location.glob("*.ebo"), key=lambda item: item.name.casefold()):
            protected[path.name.casefold()] = path

    if not pbo_paths:
        raise GameDataExtractorError(f"No official PBO archives found under: {root}")

    return pbo_paths, list(protected.values())


def _normalize_relative_path(prefix, entry_name):
    normalized_prefix = prefix.strip("\\/") if prefix else ""
    normalized_entry = entry_name.strip("\\/") if entry_name else ""
    entry_parts = [part for part in re.split(r"[\\/]+", normalized_entry) if part]

    if (
        normalized_prefix.casefold() == "scripts"
        and entry_parts
        and entry_parts[0].casefold() in DAYZ_SCRIPT_MODULES
        and (len(entry_parts) < 2 or entry_parts[1].casefold() != "dayz")
    ):
        entry_parts.insert(1, "DayZ")

    normalized_entry = "\\".join(entry_parts)
    combined = "\\".join(value for value in (normalized_prefix, normalized_entry) if value)

    if not combined:
        raise GameDataExtractorError("PBO entry produced an empty output path.")

    parts = []

    for part in re.split(r"[\\/]+", combined):
        if not part or part == ".":
            continue

        if part == ".." or ":" in part or "\x00" in part:
            raise GameDataExtractorError(f"Unsafe PBO output path: {combined}")

        parts.append(part)

    if not parts:
        raise GameDataExtractorError(f"Unsafe PBO output path: {combined}")

    return "\\".join(parts)


def build_extraction_plan(game_root, include_extensions="", exclude_extensions="", progress=None):
    root = Path(game_root).resolve()
    include = parse_extension_list(include_extensions)
    exclude = parse_extension_list(exclude_extensions)
    pbo_paths, protected = discover_game_archives(root)
    sources = []

    for index, (pbo_path, priority) in enumerate(pbo_paths, 1):
        if progress:
            progress({
                "type": "scan",
                "current": index,
                "total": len(pbo_paths),
                "message": f"Reading {pbo_path.name}",
            })

        archive = read_pbo_archive(pbo_path)
        prefix = archive["properties"].get("prefix", "").strip("\\/") or pbo_path.stem
        stat = pbo_path.stat()
        sources.append(ArchiveSource(
            path=pbo_path,
            relative_path=str(pbo_path.relative_to(root)).replace("/", "\\"),
            prefix=prefix,
            entries=archive["entries"],
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            priority=priority,
        ))

    winners = {}
    shadowed = []

    for source in sources:
        key = source.prefix.replace("/", "\\").strip("\\").casefold()
        previous = winners.get(key)

        if previous is None or source.priority >= previous.priority:
            if previous is not None:
                shadowed.append(previous.relative_path)

            winners[key] = source
        else:
            shadowed.append(source.relative_path)

    selected_sources = sorted(winners.values(), key=lambda item: item.relative_path.casefold())
    plan = ExtractionPlan(
        game_root=root,
        archives=selected_sources,
        protected_archives=[str(path.relative_to(root)).replace("/", "\\") for path in protected],
        shadowed_archives=shadowed,
    )
    claims = {}

    for source in selected_sources:
        for entry in source.entries:
            extension = Path(entry.name.replace("\\", "/")).suffix.casefold()

            if include and extension not in include:
                plan.filtered_entries += 1
                continue

            if extension in exclude:
                plan.filtered_entries += 1
                continue

            if not is_pbo_entry_supported(entry):
                plan.unsupported_entries.append(f"{source.relative_path}: {entry.name}")
                continue

            relative_path = _normalize_relative_path(source.prefix, entry.name)
            key = relative_path.casefold()
            item = PlannedEntry(source, entry, relative_path)
            previous = claims.get(key)

            if previous is not None:
                plan.conflicts.append(f"{relative_path}: {previous.archive.relative_path} -> {source.relative_path}")

            claims[key] = item

    plan.entries = sorted(claims.values(), key=lambda item: (item.archive.relative_path.casefold(), item.entry.offset))
    return plan


def _extract_entry(source_file, item, output_root):
    target = get_safe_pbo_extract_path(output_root, item.relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".rag_", suffix=".tmp", dir=target.parent)

    try:
        with os.fdopen(fd, "wb") as output:
            if item.entry.packing_method == 0:
                source_file.seek(item.entry.offset)
                remaining = item.entry.data_size

                while remaining:
                    chunk = source_file.read(min(COPY_CHUNK_SIZE, remaining))

                    if not chunk:
                        raise GameDataExtractorError(f"Unexpected end of PBO: {item.archive.relative_path} / {item.entry.name}")

                    output.write(chunk)
                    remaining -= len(chunk)
            else:
                output.write(read_pbo_entry_payload(source_file, item.entry))

        os.replace(temp_name, target)

        if item.entry.timestamp > 0:
            try:
                os.utime(target, (item.entry.timestamp, item.entry.timestamp))
            except (OSError, OverflowError, ValueError):
                pass

        return target
    except Exception:
        try:
            os.remove(temp_name)
        except OSError:
            pass

        raise


def _download_dayz_misc(cancel_event, progress):
    fd, archive_name = tempfile.mkstemp(prefix="rag_dayz_misc_", suffix=".zip")
    os.close(fd)
    archive_path = Path(archive_name)
    request = urllib.request.Request(
        DAYZ_MISC_DOWNLOAD_URL,
        headers={"User-Agent": "RaG-Game-Data-Extractor"},
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response, open(archive_path, "wb") as output:
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0

            while True:
                if cancel_event.is_set():
                    raise ExtractionCancelled("Extraction cancelled.")

                chunk = response.read(COPY_CHUNK_SIZE)

                if not chunk:
                    break

                downloaded += len(chunk)

                if downloaded > DAYZ_MISC_MAX_DOWNLOAD_BYTES:
                    raise GameDataExtractorError("DayZ-Misc download exceeded the safety limit.")

                output.write(chunk)

                if progress:
                    progress({
                        "type": "misc_download",
                        "bytes": downloaded,
                        "total_bytes": total,
                        "message": "Downloading official DayZ-Misc source assets...",
                    })

        if not zipfile.is_zipfile(archive_path):
            raise GameDataExtractorError("Downloaded DayZ-Misc archive is invalid.")

        return archive_path
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise


def _dayz_misc_target(relative_parts):
    if not relative_parts:
        return None

    if relative_parts == ("README.md",):
        return ("DayZ-Misc-README.md",)

    if len(relative_parts) >= 3:
        target = DAYZ_MISC_FOLDER_TARGETS.get(relative_parts[:2])

        if target:
            return target + relative_parts[2:]

    target = DAYZ_MISC_SOURCE_TARGETS.get(relative_parts[0])

    if target and len(relative_parts) >= 2:
        return target + relative_parts[1:]

    return None


def overlay_dayz_misc_archive(archive_path, output_root, cancel_event=None, progress=None):
    output_root = Path(output_root).resolve()
    cancel_event = cancel_event or threading.Event()
    mapped = []
    total_unpacked = 0

    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue

            parts = Path(info.filename.replace("\\", "/")).parts

            if len(parts) < 2 or any(part in {"", ".", ".."} for part in parts):
                raise GameDataExtractorError("DayZ-Misc archive contains an unsafe path.")

            target_parts = _dayz_misc_target(tuple(parts[1:]))

            if not target_parts:
                continue

            total_unpacked += info.file_size

            if len(mapped) >= DAYZ_MISC_MAX_FILES or total_unpacked > DAYZ_MISC_MAX_UNPACKED_BYTES:
                raise GameDataExtractorError("DayZ-Misc archive exceeded the safety limit.")

            mapped.append((info, target_parts))

        for index, (info, target_parts) in enumerate(mapped, 1):
            if cancel_event.is_set():
                raise ExtractionCancelled("Extraction cancelled.")

            target = get_safe_pbo_extract_path(output_root, "\\".join(target_parts))
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=".rag_", suffix=".tmp", dir=target.parent)

            try:
                with archive.open(info) as source, os.fdopen(fd, "wb") as output:
                    shutil.copyfileobj(source, output, COPY_CHUNK_SIZE)

                os.replace(temp_name, target)
            except Exception:
                try:
                    os.remove(temp_name)
                except OSError:
                    pass

                raise

            if progress:
                progress({
                    "type": "misc",
                    "current": index,
                    "total": len(mapped),
                    "message": f"Installing DayZ-Misc {index:,}/{len(mapped):,}",
                })

    return {"files": len(mapped), "bytes": total_unpacked}


def has_conversion_candidates(plan):
    return any(
        is_cfgconvert_candidate_bin_path(item.relative_path)
        or is_rap_text_convert_candidate_path(item.relative_path)
        for item in plan.entries
    )


def extract_game_data(
    plan,
    output_root,
    workers=None,
    cfgconvert_exe="",
    include_dayz_misc=False,
    cancel_event=None,
    progress=None,
):
    output_root = Path(output_root).resolve()

    if output_root == plan.game_root or plan.game_root in output_root.parents:
        raise GameDataExtractorError("Output folder cannot be inside DayZ installation.")

    output_root.mkdir(parents=True, exist_ok=True)
    try:
        (output_root / MANIFEST_NAME).unlink(missing_ok=True)
    except OSError:
        pass

    cancel_event = cancel_event or threading.Event()
    workers = get_recommended_workers() if workers is None else max(1, int(workers))
    entries_by_archive = {}
    dayz_misc_archive = None

    if include_dayz_misc:
        if progress:
            progress({"type": "phase", "message": "Downloading official DayZ-Misc source assets..."})

        try:
            dayz_misc_archive = _download_dayz_misc(cancel_event, progress)
        except ExtractionCancelled:
            raise
        except Exception as error:
            raise GameDataExtractorError(f"DayZ-Misc download failed: {error}") from error

    for item in plan.entries:
        entries_by_archive.setdefault(item.archive.relative_path, []).append(item)

    counters = {"files": 0, "bytes": 0, "last_progress": 0.0}
    counter_lock = threading.Lock()

    def emit_file(item):
        now = time.monotonic()

        with counter_lock:
            counters["files"] += 1
            counters["bytes"] += item.output_size

            current = counters["files"]
            processed_bytes = counters["bytes"]
            should_emit = (
                current == 1
                or current == len(plan.entries)
                or now - counters["last_progress"] >= 0.1
            )

            if should_emit:
                counters["last_progress"] = now

        if progress and should_emit:
            progress({
                "type": "file",
                "current": current,
                "total": len(plan.entries),
                "bytes": processed_bytes,
                "total_bytes": plan.total_bytes,
                "message": item.relative_path,
            })

    def extract_archive(source):
        if cancel_event.is_set():
            raise ExtractionCancelled("Extraction cancelled.")

        archive_items = entries_by_archive.get(source.relative_path, [])
        if progress:
            progress({"type": "archive", "message": f"Extracting {source.relative_path}"})

        with open(source.path, "rb") as source_file:
            for item in archive_items:
                if cancel_event.is_set():
                    raise ExtractionCancelled("Extraction cancelled.")

                _extract_entry(source_file, item, output_root)
                emit_file(item)

    errors = []

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rag_extract") as pool:
        futures = {pool.submit(extract_archive, source): source for source in plan.archives}

        for future in as_completed(futures):
            try:
                future.result()
            except ExtractionCancelled:
                cancel_event.set()
            except Exception as error:
                cancel_event.set()
                errors.append(f"{futures[future].relative_path}: {error}")

    if cancel_event.is_set() and not errors:
        if dayz_misc_archive:
            dayz_misc_archive.unlink(missing_ok=True)

        raise ExtractionCancelled("Extraction cancelled.")

    if errors:
        if dayz_misc_archive:
            dayz_misc_archive.unlink(missing_ok=True)

        raise GameDataExtractorError("Extraction failed: " + errors[0])

    converted_configs = 0
    converted_materials = 0
    conversion_errors = []
    conversion_items = [
        item
        for item in plan.entries
        if is_cfgconvert_candidate_bin_path(item.relative_path)
        or is_rap_text_convert_candidate_path(item.relative_path)
    ]

    if conversion_items:
        if progress:
            progress({"type": "phase", "message": "Converting extracted data..."})

        if not cfgconvert_exe or not Path(cfgconvert_exe).is_file():
            conversion_errors.append("CfgConvert.exe not found. Extracted binary files were kept.")
        else:
            def convert_item(item):
                if cancel_event.is_set():
                    raise ExtractionCancelled("Extraction cancelled.")

                target = get_safe_pbo_extract_path(output_root, item.relative_path)
                configs = 0
                materials = 0
                item_errors = []

                if is_rap_text_convert_candidate_path(target):
                    try:
                        with open(target, "rb") as file:
                            rapified = is_rapified_data(file.read(16))

                        if rapified:
                            temp_target = target.with_name(f"{target.stem}.__rag_text{target.suffix}")
                            converted_path = convert_rap_to_text(cfgconvert_exe, target, temp_target, lambda message: None)
                            os.replace(converted_path, target)
                            materials = 1
                    except Exception as error:
                        item_errors.append(f"{item.relative_path}: {error}")

                if is_cfgconvert_candidate_bin_path(target):
                    try:
                        convert_bin_to_cpp(cfgconvert_exe, target, lambda message: None)

                        if target.name.casefold() == "config.bin":
                            target.unlink()

                        configs = 1
                    except Exception as error:
                        item_errors.append(f"{item.relative_path}: {error}")

                return configs, materials, item_errors

            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rag_convert") as pool:
                futures = [pool.submit(convert_item, item) for item in conversion_items]

                for index, future in enumerate(as_completed(futures), 1):
                    try:
                        configs, materials, item_errors = future.result()
                        converted_configs += configs
                        converted_materials += materials
                        conversion_errors.extend(item_errors)
                    except ExtractionCancelled:
                        cancel_event.set()

                    if progress:
                        progress({
                            "type": "convert",
                            "current": index,
                            "total": len(conversion_items),
                            "message": f"Converting {index:,}/{len(conversion_items):,}",
                        })

            if cancel_event.is_set():
                if dayz_misc_archive:
                    dayz_misc_archive.unlink(missing_ok=True)

                raise ExtractionCancelled("Extraction cancelled.")

    misc_result = {"files": 0, "bytes": 0}

    if dayz_misc_archive:
        if progress:
            progress({"type": "phase", "message": "Installing official DayZ-Misc source assets..."})

        try:
            misc_result = overlay_dayz_misc_archive(
                dayz_misc_archive,
                output_root,
                cancel_event=cancel_event,
                progress=progress,
            )
        finally:
            dayz_misc_archive.unlink(missing_ok=True)

    return {
        "archives": len(plan.archives),
        "files": len(plan.entries),
        "bytes": plan.total_bytes,
        "converted_configs": converted_configs,
        "converted_materials": converted_materials,
        "conversion_errors": conversion_errors,
        "dayz_misc_files": misc_result["files"],
        "dayz_misc_bytes": misc_result["bytes"],
        "workers": workers,
        "output_root": str(output_root),
    }


def get_free_space(path):
    candidate = Path(path)

    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent

    try:
        return shutil.disk_usage(candidate).free
    except OSError:
        return 0


def get_recommended_workers():
    return max(1, os.cpu_count() or 1)
