import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from rag_builder_common import BuildError
from rag_local_server import ADMIN_TOOLS, STOCK_MISSIONS, auto_admin_tool, discover_workshop_mods, find_diag_installs, find_local_missions, local_mod_folder, mod_kind, valid_mission


ADMIN_CHOICES = {"Auto-detect": "auto", "Off": "", **{name: key for key, name in ADMIN_TOOLS.items()}}
MODES = {"Built-in DayZ Diag": "diag", "Custom launcher": "custom"}


class LocalServerWindow(tk.Toplevel):
    def __init__(self, builder):
        super().__init__(builder)
        self.builder = builder
        self.title("Local DayZ Server")
        self.transient(builder)
        builder.set_window_icon(self)
        left = max(0, builder.winfo_rootx() + (builder.winfo_width() - 760) // 2)
        top = max(0, builder.winfo_rooty() + (builder.winfo_height() - 515) // 2)
        self.geometry(f"760x515+{left}+{top}")
        self.minsize(630, 495)
        self.protocol("WM_DELETE_WINDOW", self.close)

        settings = builder.local_server_settings
        installs = find_diag_installs()
        self.game = tk.StringVar(value=settings.get("game") or (installs[0] if installs else ""))
        self.mission = tk.StringVar(value=settings.get("mission", ""))
        self.map_name = settings.get("map", "Chernarus")
        self.mission_choice = tk.StringVar()
        self.mission_status = tk.StringVar()
        self.player = tk.StringVar(value=settings.get("player_name", "Survivor"))
        self.port = tk.StringVar(value=settings.get("port", "2302"))
        self.steam_id = tk.StringVar(value=settings.get("steam_id", ""))
        self.admin = tk.StringVar(value=next((name for name, key in ADMIN_CHOICES.items() if key == settings.get("admin", "auto")), "Auto-detect"))
        self.mode = tk.StringVar(value=next((name for name, key in MODES.items() if key == settings.get("mode")), "Built-in DayZ Diag"))
        self.admin_status = tk.StringVar()
        self.mod_count = tk.StringVar()
        self.mods_summary = tk.StringVar()
        self.output_label = tk.StringVar()
        self.setup_hint = tk.StringVar()
        self.search = tk.StringVar()
        self.selected_only = tk.BooleanVar()
        self.chosen_mods = set(settings.get("mods", []))
        self.local_mods = list(settings.get("local_mods", []))
        self.mod_names = []
        self.missions = {}
        self.workshop_kinds = {}
        self.local_tools = set()
        self.tools = set()
        self.downloading = False
        self.game_ready = False
        self.launch_state = None

        body = ttk.Frame(self, padding=(14, 12))
        body.pack(fill="both", expand=True)
        status = ttk.Frame(body, style="Card.TFrame", padding=(12, 9))
        status.pack(fill="x", pady=(0, 10))
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=builder.local_server_status, style="FieldName.TLabel", wraplength=300, font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")
        self.run_after_build = ttk.Checkbutton(status, text="Launch after build", variable=builder.start_server_after_build_var, command=self.save)
        self.run_after_build.grid(row=0, column=1, sticky="e", padx=(8, 0))

        footer = ttk.Frame(body, padding=(0, 10, 0, 0))
        footer.pack(side="bottom", fill="x")
        ttk.Button(footer, text="Save & Close", command=self.close).pack(side="left")
        ttk.Button(footer, text="Open Server Files", command=builder.open_managed_server_files).pack(side="left", padx=(8, 0))
        launch = ttk.Frame(footer)
        launch.pack(side="right")
        self.secondary = ttk.Button(launch, text="Start Server Only", command=lambda: self.start(False))
        self.secondary.pack(side="left", padx=(0, 8))
        self.primary = builder._make_action_button(launch, "Start & Join", lambda: self.start(True), primary=True, large=True)

        style = ttk.Style(self)
        style.configure("LocalServer.TNotebook", background="#24262b", bordercolor="#4a505b", lightcolor="#4a505b", darkcolor="#4a505b", borderwidth=1)
        style.configure("LocalServer.TNotebook.Tab", background="#2f3238", foreground="#b8bec8", padding=(13, 6), bordercolor="#4a505b", lightcolor="#2f3238", darkcolor="#2f3238")
        style.map("LocalServer.TNotebook.Tab", background=[("selected", "#4a505b"), ("active", "#383c44")], foreground=[("selected", "#ffffff")])
        style.configure("LocalServer.Treeview", background="#292c32", fieldbackground="#292c32", foreground="#f1f1f1", bordercolor="#4a505b", rowheight=23)
        style.map("LocalServer.Treeview", background=[("selected", "#4a505b")], foreground=[("selected", "#ffffff")])
        style.configure("LocalServer.Treeview.Heading", background="#383c44", foreground="#f1f1f1", bordercolor="#4a505b", lightcolor="#383c44", darkcolor="#383c44")
        style.map("LocalServer.Treeview.Heading", background=[("active", "#383c44")])
        self.tabs = ttk.Notebook(body, style="LocalServer.TNotebook")
        self.tabs.pack(fill="both", expand=True)
        self.play_tab = ttk.Frame(self.tabs, padding=12)
        self.mods_tab = ttk.Frame(self.tabs, padding=12)
        self.local_mods_tab = ttk.Frame(self.tabs, padding=12)
        self.settings_tab = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(self.play_tab, text="Play")
        self.tabs.add(self.mods_tab, text="Workshop mods")
        self.tabs.add(self.local_mods_tab, text="Local mods")
        self.tabs.add(self.settings_tab, text="Settings")
        self.build_play_tab()
        self.build_mods_tab()
        self.build_local_mods_tab()
        self.build_settings_tab(installs)
        self.refresh()

    def build_play_tab(self):
        mission = ttk.LabelFrame(self.play_tab, text="Mission", padding=9)
        mission.pack(fill="x", pady=(0, 8))
        mission.columnconfigure(0, weight=1)
        self.mission_combo = ttk.Combobox(mission, textvariable=self.mission_choice, state="readonly")
        self.mission_combo.grid(row=0, column=0, sticky="ew")
        self.mission_combo.bind("<<ComboboxSelected>>", self.select_mission)
        self.download_button = ttk.Button(mission, text="Download", command=self.download_mission)
        self.download_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Button(mission, text="Browse Mission", command=self.browse_mission).grid(row=0, column=2, padx=(8, 0))
        ttk.Label(mission, textvariable=self.mission_status, style="FieldMuted.TLabel", wraplength=535).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        mods = ttk.LabelFrame(self.play_tab, text="Mods for this session", padding=9)
        mods.pack(fill="x", pady=(0, 8))
        ttk.Label(mods, textvariable=self.output_label, style="FieldName.TLabel").pack(side="left")
        ttk.Button(mods, text="Choose Workshop Mods", command=lambda: self.tabs.select(self.mods_tab)).pack(side="right")
        ttk.Label(mods, textvariable=self.mods_summary, style="FieldMuted.TLabel").pack(side="right", padx=(8, 10))

        admin = ttk.LabelFrame(self.play_tab, text="Admin access (optional)", padding=9)
        admin.pack(fill="x", pady=(0, 6))
        admin.columnconfigure(3, weight=1)
        ttk.Label(admin, text="SteamID64", style="FieldName.TLabel").grid(row=0, column=0, sticky="w")
        self.steam_entry = ttk.Entry(admin, textvariable=self.steam_id, width=22)
        self.steam_entry.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(admin, text="Admin tool", style="FieldName.TLabel").grid(row=0, column=2, sticky="w", padx=(12, 8))
        self.admin_combo = ttk.Combobox(admin, textvariable=self.admin, state="readonly", width=23)
        self.admin_combo.grid(row=0, column=3, sticky="ew")
        self.admin_combo.bind("<<ComboboxSelected>>", self.update_admin)
        ttk.Label(admin, textvariable=self.admin_status, style="FieldMuted.TLabel", wraplength=535).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))

        self.setup_hint_label = ttk.Label(self.play_tab, textvariable=self.setup_hint, wraplength=535)

    def build_mods_tab(self):
        tab = self.mods_tab
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(1, weight=1)
        ttk.Label(tab, text="Search").grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(tab, textvariable=self.search).grid(row=0, column=1, sticky="ew")
        ttk.Checkbutton(tab, text="Selected only", variable=self.selected_only, command=self.render_mods).grid(row=0, column=2, padx=8)
        ttk.Button(tab, text="Refresh", command=self.refresh).grid(row=0, column=3, columnspan=2, sticky="e")

        self.mod_table = ttk.Treeview(tab, columns=("enabled", "mod"), show="headings", selectmode="browse", height=5, style="LocalServer.Treeview")
        self.mod_table.heading("enabled", text="Load")
        self.mod_table.heading("mod", text="Workshop mod")
        self.mod_table.column("enabled", width=55, stretch=False, anchor="center")
        self.mod_table.column("mod", width=350)
        self.mod_table.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=(8, 6))
        scroll = ttk.Scrollbar(tab, command=self.mod_table.yview)
        scroll.grid(row=1, column=4, sticky="ns", pady=(8, 6))
        self.mod_table.configure(yscrollcommand=scroll.set)
        self.mod_table.bind("<ButtonRelease-1>", self.toggle_mod)
        self.mod_table.bind("<space>", self.toggle_mod)
        self.search.trace_add("write", lambda *_: self.render_mods())
        ttk.Label(tab, textvariable=self.mod_count).grid(row=2, column=0, columnspan=2, sticky="w")
        actions = ttk.Frame(tab)
        actions.grid(row=2, column=2, columnspan=3, sticky="e")
        ttk.Button(actions, text="Select All", command=self.select_all_mods).pack(side="left")
        ttk.Button(actions, text="Clear Selection", command=self.clear_mods).pack(side="left", padx=(8, 0))

    def build_local_mods_tab(self):
        tab = self.local_mods_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        ttk.Label(tab, text="Add a mod folder that contains packed PBOs under Addons.").grid(row=0, column=0, sticky="w")
        self.local_mod_table = ttk.Treeview(tab, columns=("name", "folder"), show="headings", selectmode="extended", height=5, style="LocalServer.Treeview")
        self.local_mod_table.heading("name", text="Local mod", anchor="w")
        self.local_mod_table.heading("folder", text="Folder", anchor="w")
        self.local_mod_table.column("name", width=140, stretch=False)
        self.local_mod_table.column("folder", width=380)
        self.local_mod_table.grid(row=1, column=0, sticky="nsew", pady=(8, 6))
        scroll = ttk.Scrollbar(tab, command=self.local_mod_table.yview)
        scroll.grid(row=1, column=1, sticky="ns", pady=(8, 6))
        self.local_mod_table.configure(yscrollcommand=scroll.set)
        actions = ttk.Frame(tab)
        actions.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Button(actions, text="Add Local Mod", command=self.add_local_mod).pack(side="left")
        self.remove_local_button = ttk.Button(actions, text="Remove Selected", command=self.remove_local_mods, state="disabled")
        self.remove_local_button.pack(side="left", padx=(8, 0))
        self.local_mod_table.bind("<<TreeviewSelect>>", lambda _: self.update_remove_local_button())

    def build_settings_tab(self, installs):
        setup = ttk.LabelFrame(self.settings_tab, text="DayZ and launch settings", padding=10)
        setup.pack(fill="x")
        setup.columnconfigure(1, weight=1)
        ttk.Label(setup, text="DayZ installation", style="FieldName.TLabel").grid(row=0, column=0, sticky="w")
        game_combo = ttk.Combobox(setup, textvariable=self.game, values=installs)
        game_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        game_combo.bind("<<ComboboxSelected>>", self.refresh)
        game_combo.bind("<Return>", self.refresh)
        game_combo.bind("<FocusOut>", lambda event: self.refresh() if self.game.get().strip() != self.scanned_game else None)
        ttk.Button(setup, text="Browse", command=self.browse_game).grid(row=0, column=2, padx=(8, 0))
        for row, (label, variable, width) in enumerate((("Game port", self.port, 7), ("Player name", self.player, 22)), start=1):
            ttk.Label(setup, text=label, style="FieldName.TLabel").grid(row=row, column=0, sticky="w", pady=(8, 0))
            ttk.Entry(setup, textvariable=variable, width=width).grid(row=row, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
        ttk.Label(setup, text="After-build launcher", style="FieldName.TLabel").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(setup, textvariable=self.mode, values=tuple(MODES), state="readonly").grid(row=3, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))
        ttk.Label(setup, text="Set the custom launcher in Builder Options.", style="FieldMuted.TLabel").grid(row=4, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(4, 0))

    def refresh(self, event=None):
        game = self.game.get().strip()
        self.scanned_game = game
        self.game_ready = bool(game and (Path(game) / "DayZDiag_x64.exe").is_file())
        download_root = self.builder.local_server_root / "missions"
        existing = find_local_missions(game, download_root) if self.game_ready else []
        current = Path(self.mission.get()) if self.mission.get() else None
        if current and not valid_mission(current):
            current = None
        if current and current not in existing:
            existing.append(current)
        self.missions.clear()
        selected = ""
        for name, template in STOCK_MISSIONS.items():
            matches = [path for path in existing if path.name.casefold() == template.casefold()]
            if not matches:
                label = f"{name} · Download required"
                self.missions[label] = (name, None)
                if name == self.map_name and current is None:
                    selected = label
                continue
            for index, path in enumerate(matches):
                source = "Downloaded" if path.parent == download_root else "Installed" if index == 0 else path.parent.parent.name
                label = self.add_mission(f"{name} · {source}", name, path)
                if path == current or (not selected and name == self.map_name and current is None):
                    selected = label
        stock = {template.casefold() for template in STOCK_MISSIONS.values()}
        for path in existing:
            if path.name.casefold() not in stock:
                label = self.add_mission(f"{path.name} · Custom", "", path)
                if path == current:
                    selected = label
        self.mission_combo.configure(values=tuple(self.missions))
        self.mission_choice.set(selected or next(iter(self.missions)))
        self.select_mission()

        mods = discover_workshop_mods(game) if self.game_ready else []
        self.mod_names = [mod.path.name for mod in mods]
        if self.game_ready:
            self.chosen_mods.intersection_update(self.mod_names)
        self.workshop_kinds = {mod.path.name: mod.kind for mod in mods}
        self.render_local_mods()
        self.update_admin_choices()
        self.render_mods()
        if self.game_ready:
            self.setup_hint_label.pack_forget()
        else:
            self.setup_hint.set("Choose a DayZ installation in Settings to start a session.")
            self.setup_hint_label.pack(anchor="w", pady=(2, 0))

    def update_admin_choices(self):
        self.local_tools = {mod_kind(Path(folder)) for folder in self.local_mods if Path(folder).is_dir()}
        self.tools = set(self.workshop_kinds.values()) | self.local_tools
        available = ("Auto-detect", "Off", *(name for key, name in ADMIN_TOOLS.items() if key in self.tools))
        self.admin_combo.configure(values=available)
        if self.admin.get() not in available:
            self.admin.set("Auto-detect")
        self.update_admin()

    def add_mission(self, label, name, path):
        unique = label
        number = 2
        while unique in self.missions:
            unique = f"{label} ({number})"
            number += 1
        self.missions[unique] = (name, path)
        return unique

    def select_mission(self, event=None):
        self.map_name, path = self.missions[self.mission_choice.get()]
        self.mission.set(str(path) if path else "")
        if not self.downloading:
            self.update_mission_status()
        self.update_session_state()

    def update_mission_status(self):
        mission = Path(self.mission.get()) if self.mission.get() else None
        downloaded = bool(mission and mission.parent == self.builder.local_server_root / "missions")
        can_download = bool(self.map_name and not downloaded)
        label = "Download Copy" if mission else "Download Mission"
        if can_download:
            self.download_button.configure(text=label, state="normal")
            self.download_button.grid()
        else:
            self.download_button.grid_remove()
        if not mission:
            self.mission_status.set("Download the selected map from Bohemia or browse for an existing mission.")
        elif downloaded:
            self.mission_status.set("Downloaded from Bohemia · Ready")
        else:
            self.mission_status.set(f"{mission.name} · Ready")

    def update_admin(self, event=None):
        choice = ADMIN_CHOICES[self.admin.get()]
        found = [name for key, name in ADMIN_TOOLS.items() if key in self.tools]
        available = choice != "" and bool(found) and "cf" in self.tools
        self.steam_entry.configure(state="normal" if available else "disabled")
        if not found:
            text = "Install or add Community Online Tools or VPP Admin Tools to enable this."
        elif "cf" not in self.tools:
            text = "Community Framework (CF) is required for the detected admin tools."
        elif choice == "":
            text = "Admin access is off."
        else:
            selected = {self.workshop_kinds.get(name) for name in self.chosen_mods} | self.local_tools
            tool = choice if choice != "auto" else auto_admin_tool(self.tools, selected & ADMIN_TOOLS.keys())
            text = f"Enter your SteamID64 to grant admin access with {ADMIN_TOOLS[tool]}."
        self.admin_status.set(text)

    def selected(self):
        return sorted(self.chosen_mods, key=str.casefold)

    def render_mods(self):
        selected = self.mod_table.selection()
        top = self.mod_table.yview()[0]
        self.mod_table.delete(*self.mod_table.get_children())
        query = self.search.get().strip().casefold()
        for name in self.mod_names:
            enabled = name in self.chosen_mods
            if (not self.selected_only.get() or enabled) and query in name.casefold():
                self.mod_table.insert("", "end", iid=name, values=("☑" if enabled else "☐", name.lstrip("@")))
        if selected and self.mod_table.exists(selected[0]):
            self.mod_table.selection_set(selected[0])
            self.mod_table.focus(selected[0])
        self.mod_table.yview_moveto(top)
        self.refresh_mod_counts()
        self.update_admin()

    def toggle_mod(self, event):
        name = self.mod_table.identify_row(event.y) if event.type == tk.EventType.ButtonRelease else self.mod_table.focus()
        if name:
            if name in self.chosen_mods:
                self.chosen_mods.remove(name)
            else:
                self.chosen_mods.add(name)
            self.render_mods()
        return "break"

    def clear_mods(self):
        self.chosen_mods.clear()
        self.render_mods()

    def select_all_mods(self):
        self.chosen_mods.update(self.mod_names)
        self.render_mods()

    def refresh_mod_counts(self):
        count = len(set(self.mod_names) & self.chosen_mods)
        local = len(self.local_mods)
        self.mod_count.set(f"{count} of {len(self.mod_names)} selected")
        self.mods_summary.set(f"{count} Workshop · {local} local")
        self.tabs.tab(self.mods_tab, text=f"Workshop mods ({count})")
        self.tabs.tab(self.local_mods_tab, text=f"Local mods ({local})")

    def render_local_mods(self):
        self.local_mod_table.delete(*self.local_mod_table.get_children())
        for index, folder in enumerate(self.local_mods):
            self.local_mod_table.insert("", "end", iid=str(index), values=(Path(folder).name, folder))
        self.update_remove_local_button()
        self.refresh_mod_counts()

    def update_remove_local_button(self):
        self.remove_local_button.configure(state="normal" if self.local_mod_table.selection() else "disabled")

    def add_local_mod(self):
        initial = self.game.get() or str(Path.home())
        path = filedialog.askdirectory(parent=self, title="Select a local mod folder containing Addons", initialdir=initial)
        if not path:
            return
        try:
            folder = local_mod_folder(path)
            if folder.resolve() in {Path(value).resolve() for value in self.local_mods}:
                return
        except (BuildError, OSError) as error:
            messagebox.showerror("Local DayZ Server", str(error), parent=self)
            return
        self.local_mods.append(str(folder))
        self.render_local_mods()
        self.update_admin_choices()

    def remove_local_mods(self):
        for index in sorted(map(int, self.local_mod_table.selection()), reverse=True):
            self.local_mods.pop(index)
        self.render_local_mods()
        self.update_admin_choices()

    def update_session_state(self):
        builder = self.builder
        output = builder.output_root_var.get().strip()
        name = Path(output).name if output else ""
        if name:
            name = name if len(name) <= 23 else name[:22] + "…"
        output_text = f"Build output: {name}" if name else "No build output selected"
        if self.output_label.get() != output_text:
            self.output_label.set(output_text)

        session = builder.local_server_session
        if builder.local_server_busy:
            state = "preparing"
        elif session and session.running():
            if session.stopping:
                state = "stopping"
            elif session.client and session.client.poll() is None:
                state = "client"
            elif not session.ready or session.server.poll() is not None:
                state = "starting"
            else:
                state = "ready"
        else:
            state = "stopped" if self.game_ready and self.mission.get() else "setup"
        if self.launch_state == state:
            return
        self.launch_state = state
        text = {"preparing": "Preparing…", "starting": "Starting…", "ready": "Join Server", "client": "Client Running", "stopping": "Stopping…"}.get(state, "Start & Join")
        self.primary.configure(text=text, state="normal" if state in {"ready", "stopped"} else "disabled",
                               command=builder.join_managed_server if state == "ready" else lambda: self.start(True))
        if state in {"starting", "ready", "client", "stopping"}:
            self.secondary.configure(text="Stop Session", state="disabled" if state == "stopping" else "normal", command=builder.stop_managed_server)
        else:
            self.secondary.configure(text="Start Server Only", state="normal" if state == "stopped" else "disabled", command=lambda: self.start(False))

    def browse_game(self):
        path = filedialog.askdirectory(parent=self, title="Select DayZ installation", initialdir=self.game.get() or str(Path.home()))
        if path:
            self.game.set(path)
            self.refresh()

    def browse_mission(self):
        path = filedialog.askdirectory(parent=self, title="Select mission folder containing init.c", initialdir=self.mission.get() or str(Path.home()))
        if path:
            if not valid_mission(Path(path)):
                messagebox.showerror("Local DayZ Server", "Choose a mission folder containing init.c, named like dayzOffline.chernarusplus.", parent=self)
                return
            self.mission.set(path)
            self.refresh()

    def save(self):
        self.builder.local_server_settings = {
            "mode": MODES[self.mode.get()], "game": self.game.get().strip(), "mission": self.mission.get(), "map": self.map_name,
            "player_name": self.player.get().strip(), "port": self.port.get().strip(), "steam_id": self.steam_id.get().strip(),
            "admin": ADMIN_CHOICES[self.admin.get()], "mods": self.selected(),
            "local_mods": list(self.local_mods),
        }
        self.builder.save_path_settings()

    def close(self):
        self.save()
        self.destroy()

    def start(self, join):
        self.save()
        self.builder.start_managed_server(self.builder.local_server_settings, self.builder.output_root_var.get().strip(), join)
        self.update_session_state()

    def download_mission(self):
        if self.downloading or not self.map_name:
            return
        self.downloading = True
        self.download_button.configure(state="disabled")
        self.mission_status.set(f"Getting {self.map_name} from Bohemia…")
        self.builder.download_local_mission(self.map_name, self)

    def download_progress(self, current, total, name):
        self.mission_status.set(f"Downloading mission · {current}/{total} files")

    def download_finished(self, path, error):
        self.downloading = False
        if error:
            self.update_mission_status()
            messagebox.showerror("Local DayZ Server", error, parent=self)
        else:
            self.mission.set(str(path))
            self.refresh()
