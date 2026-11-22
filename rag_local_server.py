import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from rag_build_pipeline import get_steam_library_roots
from rag_builder_common import BuildError


ADMIN_TOOLS = {"cot": "Community Online Tools", "vpp": "VPP Admin Tools"}
WORKSHOP_IDS = {"cf": "1559212036", "cot": "1564026768", "vpp": "1828439124"}
WORKSHOP_NAMES = {"cf": {"cf", "communityframework"}, "cot": {"cot", "communityonlinetools"}, "vpp": {"vppadmintools", "vanillaplusplusadmintools"}}
MISSION_PATTERN = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
STOCK_MISSIONS = {"Chernarus": "dayzOffline.chernarusplus", "Livonia": "dayzOffline.enoch", "Sakhal": "dayzOffline.sakhal", "Nasdara": "dayzOffline.nasdara"}
ECONOMY_REPO = "BohemiaInteractive/DayZ-Central-Economy"
ECONOMY_URL = f"https://github.com/{ECONOMY_REPO}/tree/master"
READY_TIMEOUT = 120


@dataclass(frozen=True)
class WorkshopMod:
    path: Path
    kind: str = ""


@dataclass(frozen=True)
class LocalServerPlan:
    game_root: Path
    mission: Path
    root: Path
    port: int
    player_name: str
    mods: tuple
    admin: str
    steam_id: str

    @property
    def server_profile(self):
        return self.root / "profiles"

    @property
    def client_profile(self):
        return self.root / "profilesClient"

    @property
    def config(self):
        return self.root / "serverDZ.cfg"


def find_diag_installs():
    return list(dict.fromkeys(
        str(game)
        for library in get_steam_library_roots()
        for name in ("DayZ", "DayZ Exp")
        if (game := Path(library) / "steamapps" / "common" / name).joinpath("DayZDiag_x64.exe").is_file()
    ))


def find_local_missions(game_root, download_root=None):
    game = Path(game_root)
    roots = [game.parent / "DayZServer" / "mpmissions"]
    roots.extend(Path(library) / "steamapps" / "common" / "DayZServer" / "mpmissions" for library in get_steam_library_roots())
    roots.append(game / "mpmissions")
    if download_root is not None:
        roots.append(Path(download_root))
    choices = []
    for root in dict.fromkeys(roots):
        if root.is_dir():
            choices.extend(path for path in sorted(root.iterdir(), key=lambda path: path.name.casefold()) if valid_mission(path))
    return sorted(choices, key=lambda path: path.name.casefold() != "dayzoffline.chernarusplus")


def valid_mission(path):
    return bool(MISSION_PATTERN.fullmatch(path.name)) and (path / "init.c").is_file()


def download_stock_mission(map_name, download_root, progress=lambda *_: None):
    template = STOCK_MISSIONS[map_name]
    root = Path(download_root).resolve()
    target = root / template
    if valid_mission(target):
        return target

    def open_url(url):
        request = urllib.request.Request(url, headers={"User-Agent": "RaG-PBO-Builder"})
        return urllib.request.urlopen(request, timeout=30)

    api = f"https://api.github.com/repos/{ECONOMY_REPO}"
    with open_url(api + "/git/ref/heads/master") as response:
        commit = json.load(response)["object"]["sha"]
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise BuildError("Bohemia mission download returned an invalid revision.")
    with open_url(f"{api}/git/trees/{commit}?recursive=1") as response:
        tree = json.loads(response.read(8 * 1024 * 1024))
    prefix = template + "/"
    files = [item for item in tree["tree"] if item.get("type") == "blob" and item["path"].startswith(prefix)]
    if tree.get("truncated") or not files or len(files) > 400 or sum(item["size"] for item in files) > 256 * 1024 * 1024:
        raise BuildError("Bohemia mission file list is incomplete or exceeds the download limit.")
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".download-", dir=root) as temp:
        staged = Path(temp) / template
        staged.mkdir()
        for index, item in enumerate(files, 1):
            name = item["path"][len(prefix):]
            parts = name.split("/")
            size = item["size"]
            if item.get("mode") not in {"100644", "100755"} or not all(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", part) for part in parts):
                raise BuildError(f"Unsupported path in Bohemia mission: {name}")
            progress(index, len(files), name)
            path = staged.joinpath(*parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha1(f"blob {size}\0".encode("ascii"))
            remaining = size
            with open_url(f"https://raw.githubusercontent.com/{ECONOMY_REPO}/{commit}/{item['path']}") as response, path.open("wb") as file:
                while remaining:
                    chunk = response.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        break
                    file.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                if remaining or response.read(1) or digest.hexdigest() != item["sha"]:
                    raise BuildError(f"Bohemia mission file did not match its published checksum: {name}")
        if not valid_mission(staged):
            raise BuildError("Downloaded mission did not contain init.c.")
        (staged / "Bohemia-source.txt").write_text(
            f"DayZ Central Economy configuration by Bohemia Interactive\n{ECONOMY_URL}\nRevision: {commit}\n"
            "Arma and DayZ Public License Share Alike\nhttps://www.bohemia.net/community/licenses/arma-and-dayz-public-license-share-alike-adpl-sa\n",
            encoding="utf-8",
        )
        os.replace(staged, target)
    return target


def has_addons(root):
    if not root.is_dir():
        return False
    return any(
        file.is_file() and file.suffix.casefold() == ".pbo"
        for folder in root.iterdir() if folder.is_dir() and folder.name.casefold() == "addons"
        for file in folder.iterdir()
    )


def mod_kind(path):
    meta = path / "meta.cpp"
    if meta.is_file() and meta.stat().st_size <= 65536:
        match = re.search(r"\bpublishedid\s*=\s*(\d+)", meta.read_text(encoding="utf-8-sig", errors="replace"), re.IGNORECASE)
        if match:
            kind = next((key for key, value in WORKSHOP_IDS.items() if match.group(1) == value), "")
            if kind:
                return kind
    name = re.sub(r"[^a-z0-9]+", "", path.name.lower())
    return next((key for key, names in WORKSHOP_NAMES.items() if name in names), "")


def discover_workshop_mods(game_root):
    root = Path(game_root) / "!Workshop"
    if not root.is_dir():
        return []
    mods = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if ";" in path.name or not has_addons(path):
            continue
        mods.append(WorkshopMod(path, mod_kind(path)))
    return mods


def local_mod_folder(folder):
    path = Path(folder).expanduser().absolute()
    if ";" in str(path):
        raise BuildError(f"DayZ mod folders cannot contain a semicolon: {path}")
    if not has_addons(path):
        raise BuildError(f"Local mod has no .pbo files inside its Addons folder: {path}")
    return path


def auto_admin_tool(available, selected):
    candidates = selected or available
    return next((key for key in ("vpp", "cot") if key in candidates), "")


def plan_local_server(settings, build_output, data_root):
    game_path = str(settings.get("game", "")).strip()
    if not game_path:
        raise BuildError("Select your DayZ installation in Local Server.")
    game = Path(game_path).expanduser().resolve()
    if not (game / "DayZDiag_x64.exe").is_file():
        raise BuildError(f"DayZDiag_x64.exe was not found in {game}.")

    mission_path = str(settings.get("mission", "")).strip()
    if mission_path:
        mission = Path(mission_path).expanduser().resolve()
    else:
        map_name = settings.get("map", "Chernarus")
        template = STOCK_MISSIONS.get(map_name, "").casefold()
        mission = next((path.resolve() for path in find_local_missions(game, Path(data_root) / "missions") if path.name.casefold() == template), None)
    if mission is None or not valid_mission(mission):
        raise BuildError("Choose a server mission folder containing init.c, or download a stock mission in Local Server.")

    try:
        port = int(settings.get("port", 2302))
    except (ValueError, TypeError):
        raise BuildError("Enter a game port from 1024 to 65532.") from None
    if not 1024 <= port <= 65532:
        raise BuildError("Enter a game port from 1024 to 65532.")

    output = Path(build_output).resolve() if build_output else None
    if output is not None and not has_addons(output):
        raise BuildError(f"Build Output has no packed Addons folder: {output}")
    mods = discover_workshop_mods(game)
    by_name = {mod.path.name.casefold(): mod for mod in mods}
    chosen = []
    for name in settings.get("mods", []):
        mod = by_name.get(name.casefold())
        if mod is None:
            raise BuildError(f"Workshop mod is missing: {name}. Refresh the list in Local Server.")
        chosen.append(mod)
    for folder in settings.get("local_mods", []):
        path = local_mod_folder(folder)
        chosen.append(WorkshopMod(path, mod_kind(path)))

    tools = {mod.kind: mod for mod in (*mods, *chosen) if mod.kind}
    steam_id = str(settings.get("steam_id", "")).strip()
    if steam_id and not re.fullmatch(r"[0-9]{17}", steam_id):
        raise BuildError("Enter your 17-digit SteamID64.")
    admin = settings.get("admin", "auto")
    if admin == "auto":
        selected_tools = {mod.kind for mod in chosen if mod.kind in ADMIN_TOOLS}
        admin = auto_admin_tool(tools, selected_tools) if steam_id else ""
    if admin:
        if admin not in ADMIN_TOOLS or admin not in tools:
            raise BuildError("Add or install the selected admin tool before configuring admin access.")
        if not steam_id:
            raise BuildError("Enter your SteamID64 to configure the selected admin tool.")
        if not any(mod.kind == admin for mod in chosen):
            chosen.insert(0, tools[admin])
    if any(mod.kind in ADMIN_TOOLS for mod in chosen) and not any(mod.kind == "cf" for mod in chosen):
        if "cf" not in tools:
            raise BuildError("Community Framework (CF) is required when loading COT or VPP Admin Tools.")
        chosen.insert(0, tools["cf"])

    paths = []
    seen = set()
    for mod in sorted(chosen, key=lambda item: item.kind != "cf"):
        resolved = mod.path.resolve()
        if resolved not in seen and (output is None or (resolved != output and mod.path.name.casefold() != output.name.casefold())):
            seen.add(resolved)
            paths.append(mod.path)
    if output is not None:
        paths.append(output)
    if any(";" in str(path) for path in paths):
        raise BuildError("DayZ mod folders cannot contain a semicolon.")

    player_name = str(settings.get("player_name", "")).strip() or "Survivor"
    if '"' in player_name or any(ord(char) < 32 for char in player_name):
        raise BuildError("Player name cannot contain quotes or control characters.")
    identity = "\n".join(os.path.normcase(str(value)) for value in (game, mission, output or "vanilla", admin or "none", port))
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    root = Path(data_root).resolve() / key
    return LocalServerPlan(game, mission, root, port, player_name, tuple(paths), admin, steam_id)


def prepare_local_server(plan):
    plan.root.mkdir(parents=True, exist_ok=True)
    destination = plan.root / "missions" / plan.mission.name
    if not destination.exists():
        destination.parent.mkdir(exist_ok=True)
        def ignore_mission_files(folder, names):
            return [
                name for name in names
                if name.casefold() in {"profiles", "profilesclient"}
                or name.casefold().startswith("storage_")
                or Path(folder, name).is_symlink()
                or Path(folder, name).is_junction()
            ]
        with tempfile.TemporaryDirectory(prefix=".copy-", dir=destination.parent) as temp:
            copied = Path(temp) / destination.name
            shutil.copytree(plan.mission, copied, ignore=ignore_mission_files)
            os.replace(copied, destination)
    if not valid_mission(destination):
        raise BuildError(f"Managed mission is incomplete: {destination}")

    plan.server_profile.mkdir(exist_ok=True)
    plan.client_profile.mkdir(exist_ok=True)
    password_file = plan.root / "join-password.txt"
    if not password_file.is_file():
        password_file.write_text(secrets.token_hex(12) + "\n", encoding="ascii")
    password = password_file.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[a-f0-9]{24}", password):
        raise BuildError(f"Invalid managed join password file: {password_file}")

    if plan.admin:
        configure_local_admin(plan.server_profile, plan.admin, plan.steam_id)

    plan.config.write_text(
        'hostname = "RaG Local Test";\n'
        f'password = "{password}";\n'
        'passwordAdmin = "";\n'
        "maxPlayers = 5;\nBattlEye = 0;\nverifySignatures = 0;\nforceSameBuild = 0;\n"
        f"steamQueryPort = {plan.port + 3};\n"
        'disableVoN = 1;\ntimeStampFormat = "Short";\ndisablePersonalLight = 1;\nlightingConfig = 1;\n'
        'logAverageFps = 1;\nlogMemory = 1;\nlogPlayers = 1;\nlogFile = "server_console.log";\n'
        'serverTime = "2017/06/30/12/00";\nserverTimeAcceleration = 1;\nserverNightTimeAcceleration = 3;\n'
        "serverTimePersistent = 0;\ninstanceId = 1;\nstorageAutoFix = 1;\n"
        "allowFilePatching = 0;\n"
        f"enableCfgGameplayFile = {int((destination / 'cfggameplay.json').is_file())};\n"
        "vppDisablePassword = 1;\n"
        f'class Missions {{ class DayZ {{ template = "{plan.mission.name}"; }}; }};\n',
        encoding="ascii",
    )
    return destination, password


def configure_local_admin(profile, admin, steam_id):
    if admin == "vpp":
        path = profile / "VPPAdminTools" / "Permissions" / "SuperAdmins" / "SuperAdmins.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
        if not any(line.strip() == steam_id for line in existing.splitlines()):
            path.write_text((existing.rstrip("\r\n") + "\n" if existing else "") + steam_id + "\n", encoding="utf-8")
    elif admin == "cot":
        root = profile / "PermissionsFramework"
        roles = root / "Roles"
        players = root / "Players"
        roles.mkdir(parents=True, exist_ok=True)
        players.mkdir(parents=True, exist_ok=True)
        role = roles / "rag_local_admin.txt"
        if not role.exists():
            nodes = ("COT", "Admin", "ESP", "Entity", "Map", "Weather", "Items", "Vehicles", "Teleport", "Camera")
            role.write_text("".join(f"{node} 2\n" for node in nodes), encoding="utf-8")
        digest = hashlib.sha256(steam_id.encode("ascii")).digest()
        names = (steam_id, base64.urlsafe_b64encode(digest).decode().rstrip("="), re.sub(r"[/+=]", "", base64.b64encode(digest).decode()))
        paths = [players / f"{name}.json" for name in dict.fromkeys(names)]
        path = next((candidate for candidate in paths if candidate.is_file()), paths[0])
        record = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {"Roles": ["everyone"]}
        assigned = record.setdefault("Roles", [])
        if not isinstance(assigned, list):
            raise BuildError(f"Invalid COT role list in {path.name}.")
        if "rag_local_admin" not in assigned:
            assigned.append("rag_local_admin")
            path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def local_server_commands(plan, mission, password):
    exe = str(plan.game_root / "DayZDiag_x64.exe")
    common = [exe, "-newErrorsAreWarnings=1", f"-port={plan.port}"]
    if plan.mods:
        common.append("-mod=" + ";".join(str(path) for path in plan.mods))
    server = [*common, "-server", "-ip=127.0.0.1", f"-config={plan.config}", f"-mission={mission}",
              f"-profiles={plan.server_profile}", "-doLogs", "-adminlog"]
    client = [*common, "-connect=127.0.0.1", f"-password={password}", f"-profiles={plan.client_profile}", f"-name={plan.player_name}"]
    return server, client


class LocalServerSession:
    def __init__(self, plan, auto_join):
        self.plan = plan
        self.auto_join = auto_join
        self.server = None
        self.client = None
        self.ready = False
        self.stopping = False
        self.deadline = 0

    def start(self):
        for port in (self.plan.port, self.plan.port + 3):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                    if os.name == "nt":
                        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    probe.bind(("127.0.0.1", port))
            except OSError as error:
                raise BuildError(f"UDP port {port} is already in use or unavailable. Choose a different game port.") from error
        mission, password = prepare_local_server(self.plan)
        server, self.client_command = local_server_commands(self.plan, mission, password)
        self.existing_logs = {path: path.stat().st_mtime_ns for path in self.plan.server_profile.glob("*.RPT")}
        self.server = subprocess.Popen(server, cwd=self.plan.game_root)
        self.deadline = time.monotonic() + READY_TIMEOUT

    def running(self):
        return any(process is not None and process.poll() is None for process in (self.server, self.client))

    def check_ready(self):
        if self.ready or self.server is None or self.server.poll() is not None:
            return self.ready
        for path in self.plan.server_profile.glob("*.RPT"):
            try:
                details = path.stat()
                if self.existing_logs.get(path) == details.st_mtime_ns:
                    continue
                with path.open("rb") as file:
                    file.seek(max(0, details.st_size - 65536))
                    if b"Average server FPS:" in file.read():
                        self.ready = True
                        break
            except OSError:
                continue
        return self.ready

    def join(self):
        if not self.check_ready() or self.server.poll() is not None:
            raise BuildError("Wait until the local server is ready before joining.")
        if self.client is not None and self.client.poll() is None:
            raise BuildError("The managed DayZ client is already running.")
        self.auto_join = False
        self.client = subprocess.Popen(self.client_command, cwd=self.plan.game_root)

    def stop(self):
        self.auto_join = False
        self.stopping = True
        for process in (self.client, self.server):
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
