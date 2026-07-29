import fnmatch
import locale
import os
import subprocess


class BuildError(Exception):
    pass


class BuildCancelled(BuildError):
    pass


EXCLUDE_DIRS = {".git", ".svn", ".vscode", ".idea", "__pycache__"}
EXCLUDE_FILES = {".gitignore", ".gitattributes", "thumbs.db", "desktop.ini", ".ds_store", "$prefix$", "$pboprefix$", "$prefix$.txt", "$pboprefix$.txt"}
EXCLUDE_EXTENSIONS = {".delete"}
DEFAULT_EXCLUDE_FILE_EXTENSIONS = ".h,.hpp,.png,.cpp,.txt,.dep,.bak,.log,.pew,.tga,.bat,.psd,.cmd,.mcr,.fbx,.max"
DEFAULT_EXCLUDE_FOLDER_NAMES = "source,temp"

ZERO = bytes([0])
WIN_SEP = chr(92)
COPY_CHUNK_SIZE = 1024 * 1024
PBO_VERSION_MAGIC = 0x56657273


def safe_ascii(value, label):
    try:
        return value.encode("ascii")
    except UnicodeEncodeError:
        raise BuildError(f"{label} contains non-ASCII characters: {value}")


def parse_exclude_patterns(raw_patterns):
    if not raw_patterns:
        return []
    raw_patterns = raw_patterns.replace(";", ",").replace("\r", "").replace("\n", ",")
    return [item.strip() for item in raw_patterns.split(",") if item.strip()]


def parse_exclude_file_extensions(raw_extensions):
    result = []
    for item in parse_exclude_patterns(raw_extensions):
        value = item.lower()
        if value.startswith("*."):
            value = value[1:]
        elif not value.startswith("."):
            value = "." + value
        if value.count(".") == 1 and value not in result:
            result.append(value)
    return result


def parse_exclude_folder_names(raw_folder_names):
    result = []
    for item in parse_exclude_patterns(raw_folder_names):
        value = item.lower()
        if value not in result:
            result.append(value)
    return result


def get_exclusion_setting_values(settings):
    legacy_patterns = parse_exclude_patterns(settings.get("exclude_patterns", ""))

    if "exclude_file_extensions" in settings:
        file_extensions = str(settings.get("exclude_file_extensions", "")).strip()
    elif legacy_patterns:
        file_extensions = ",".join(
            pattern[1:] for pattern in legacy_patterns if pattern.startswith("*.") and pattern.count(".") == 1
        )
    else:
        file_extensions = DEFAULT_EXCLUDE_FILE_EXTENSIONS

    if "exclude_folder_names" in settings:
        folder_names = str(settings.get("exclude_folder_names", "")).strip()
    else:
        legacy_folders = [
            pattern for pattern in legacy_patterns
            if not any(char in pattern for char in "*?[]") and "." not in pattern
        ]
        folder_names = ",".join(dict.fromkeys(parse_exclude_folder_names(
            DEFAULT_EXCLUDE_FOLDER_NAMES + "," + ",".join(legacy_folders)
        )))

    return file_extensions, folder_names


def get_exclusion_rules(settings):
    file_extensions, folder_names = get_exclusion_setting_values(settings)
    return {
        "file_extensions": set(parse_exclude_file_extensions(file_extensions)),
        "folder_names": set(parse_exclude_folder_names(folder_names)),
    }


def matches_exclude_pattern(name, patterns):
    if not patterns:
        return False
    value = name.lower()
    for pattern in patterns:
        test = pattern.strip().lower()
        if test and (value == test or fnmatch.fnmatch(value, test)):
            return True
    return False


def should_skip_dir(dirname, extra_patterns=None):
    name = dirname.lower()
    if isinstance(extra_patterns, dict):
        return name in EXCLUDE_DIRS or name in extra_patterns.get("folder_names", set())
    return name in EXCLUDE_DIRS or matches_exclude_pattern(name, extra_patterns)


def should_skip_file(filename, extra_patterns=None):
    name = filename.lower()
    if name in {"config.cpp", "config.bin"}:
        return False
    if name in EXCLUDE_FILES or os.path.splitext(name)[1].lower() in EXCLUDE_EXTENSIONS:
        return True
    if isinstance(extra_patterns, dict):
        return os.path.splitext(name)[1].lower() in extra_patterns.get("file_extensions", set())
    return matches_exclude_pattern(name, extra_patterns)


def source_file_should_be_staged(filename):
    return not should_skip_file(filename)


def get_subprocess_creationflags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def get_hidden_startupinfo():
    if os.name != "nt":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return startupinfo


def get_subprocess_text_encoding():
    return locale.getpreferredencoding(False) or "utf-8"


def run_hidden_text_subprocess(cmd, cwd=None):
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        encoding=get_subprocess_text_encoding(),
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=get_subprocess_creationflags(),
        startupinfo=get_hidden_startupinfo(),
    )


def normalize_working_dir(project_root):
    value = project_root.rstrip(WIN_SEP + "/")
    if len(value) == 2 and value[1] == ":":
        return value + WIN_SEP
    return value


def try_relpath(path, start):
    try:
        return os.path.relpath(path, start)
    except ValueError:
        return ""


def path_is_same_mount(path, start):
    return bool(try_relpath(path, start))


def get_safe_temp_name(name):
    safe = name.strip() if name else "addon"
    safe = safe.replace("/", "_").replace(WIN_SEP, "_").replace(":", "_")
    return safe or "addon"


def read_pbo_prefix_file(source_dir):
    names = {"$pboprefix$", "$prefix$", "$pboprefix$.txt", "$prefix$.txt"}
    try:
        entries = os.listdir(source_dir)
    except OSError:
        return ""
    for entry in entries:
        if entry.lower() not in names:
            continue
        path = os.path.join(source_dir, entry)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8-sig", errors="ignore") as file:
                for line in file:
                    prefix = line.strip().strip('"').strip("'")
                    if prefix:
                        return prefix.replace("/", WIN_SEP).strip(WIN_SEP + "/")
        except OSError:
            return ""
    return ""


def get_pbo_prefix(pbo_base_name, source_dir=None):
    file_prefix = read_pbo_prefix_file(source_dir) if source_dir else ""
    return file_prefix or pbo_base_name


def format_duration(seconds):
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"
