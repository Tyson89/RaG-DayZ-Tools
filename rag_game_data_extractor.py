"""
RaG Game Data Extractor

Standalone UI for extracting official DayZ game PBOs.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from pbo_core import format_byte_size
from rag_game_data_extractor_core import (
    DAYZ_MISC_ESTIMATED_BYTES,
    DAYZ_MISC_ESTIMATED_FILES,
    DAYZ_MISC_REPO_URL,
    ExtractionCancelled,
    build_extraction_plan,
    extract_game_data,
    find_cfgconvert,
    find_dayz_installations,
    get_free_space,
    get_recommended_workers,
    has_conversion_candidates,
)
from rag_inspector_settings import resource_path
from rag_licence import make_licence_button
from rag_updater_launcher import launch_updater
from rag_version import APP_VERSION


APP_TITLE = "RaG Game Data Extractor"
APP_ICON_FILE = os.path.join("assets", "installer.ico")
GRAPHITE_BG = "#24262b"
GRAPHITE_HEADER = "#1f2126"
GRAPHITE_CARD = "#2f3238"
GRAPHITE_CARD_SOFT = "#383c44"
GRAPHITE_FIELD = "#292c32"
GRAPHITE_BORDER = "#4a505b"
GRAPHITE_TEXT = "#f1f1f1"
GRAPHITE_MUTED = "#b8bec8"
GRAPHITE_ACCENT = "#a74747"
GRAPHITE_ACCENT_DARK = "#7f3434"
GRAPHITE_SUCCESS = "#7fb087"
GRAPHITE_SUCCESS_DARK = "#41684a"
PROFILES = ("Everything", "Scripts only")


def get_settings_path():
    base = os.environ.get("LOCALAPPDATA")
    folder = (Path(base) if base else Path.home()) / "RaG_Game_Data_Extractor"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "settings.json"


def load_settings():
    try:
        data = json.loads(get_settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(data):
    path = get_settings_path()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, path)


def get_default_output_path():
    return "P:\\" if Path("P:/").is_dir() else str(Path.home() / "DayZ Extracted")


def get_profile_extensions(profile):
    return ".c" if profile == "Scripts only" else ""


class GameDataExtractorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.plan = None
        self.plan_signature = None
        self.busy = False
        self.estimate_generation = 0
        self.installations = {}
        detected_cfgconvert = find_cfgconvert()
        saved_output = self.settings.get("output_path", "")
        legacy_output = str(Path.home() / "DayZ Extracted")

        if Path("P:/").is_dir() and (not saved_output or os.path.normcase(saved_output) == os.path.normcase(legacy_output)):
            saved_output = "P:\\"

        self.install_var = tk.StringVar()
        self.game_path_var = tk.StringVar(value=self.settings.get("game_path", ""))
        self.output_path_var = tk.StringVar(value=saved_output or get_default_output_path())
        saved_profile = self.settings.get("profile", "Everything")
        self.profile_var = tk.StringVar(value=saved_profile if saved_profile in PROFILES else "Everything")
        self.cfgconvert_var = tk.StringVar(value=self.settings.get("cfgconvert_exe", detected_cfgconvert))
        self.include_dayz_misc_var = tk.BooleanVar(value=self.settings.get("include_dayz_misc", False))
        self.summary_var = tk.StringVar(value="Calculating estimated output...")
        self.status_var = tk.StringVar(value="Detecting game data...")
        self.progress_var = tk.DoubleVar(value=0)

        self.title(APP_TITLE)
        self.set_default_window_size()
        self.minsize(860, 680)
        self.configure(bg=GRAPHITE_BG)
        self.set_window_icon()
        self.apply_theme()
        self.build_ui()
        self.detect_installations(schedule_estimate=False)
        self.after(100, self.poll_events)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        if len(sys.argv) > 1 and Path(sys.argv[1]).is_dir():
            self.game_path_var.set(str(Path(sys.argv[1]).resolve()))

        self.after(150, self.request_estimate)

    def set_default_window_size(self):
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        width = min(1100, max(900, screen_width - 120))
        height = min(840, max(700, screen_height - 140))
        left = max(0, (screen_width - width) // 2)
        top = max(0, (screen_height - height) // 2)
        self.geometry(f"{width}x{height}+{left}+{top}")

    def set_window_icon(self):
        icon_path = resource_path(APP_ICON_FILE)

        if not os.path.isfile(icon_path):
            return

        try:
            self.iconbitmap(icon_path)
        except Exception:
            pass

    def apply_theme(self):
        style = ttk.Style(self)

        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            ".",
            background=GRAPHITE_BG,
            foreground=GRAPHITE_TEXT,
            fieldbackground=GRAPHITE_FIELD,
            font=("Segoe UI", 10),
        )
        style.configure("TFrame", background=GRAPHITE_BG)
        style.configure(
            "TLabelframe",
            background=GRAPHITE_CARD,
            foreground=GRAPHITE_TEXT,
            bordercolor=GRAPHITE_BORDER,
            relief="flat",
            padding=12,
        )
        style.configure(
            "TLabelframe.Label",
            background=GRAPHITE_CARD,
            foreground=GRAPHITE_TEXT,
            font=("Segoe UI", 10, "bold"),
        )
        style.configure("Card.TLabel", background=GRAPHITE_CARD, foreground=GRAPHITE_TEXT)
        style.configure("Muted.TLabel", background=GRAPHITE_BG, foreground=GRAPHITE_MUTED)
        style.configure(
            "TEntry",
            fieldbackground=GRAPHITE_FIELD,
            foreground=GRAPHITE_TEXT,
            insertcolor=GRAPHITE_TEXT,
            bordercolor=GRAPHITE_BORDER,
            padding=7,
        )
        style.configure(
            "TCombobox",
            fieldbackground=GRAPHITE_FIELD,
            background=GRAPHITE_FIELD,
            foreground=GRAPHITE_TEXT,
            arrowcolor=GRAPHITE_MUTED,
            padding=6,
        )
        style.map("TCombobox", fieldbackground=[("readonly", GRAPHITE_FIELD)])
        style.configure(
            "TButton",
            background=GRAPHITE_CARD_SOFT,
            foreground=GRAPHITE_TEXT,
            bordercolor=GRAPHITE_CARD_SOFT,
            padding=(12, 8),
        )
        style.map("TButton", background=[("active", GRAPHITE_BORDER)], foreground=[("disabled", GRAPHITE_MUTED)])
        style.configure("TCheckbutton", background=GRAPHITE_CARD, foreground=GRAPHITE_TEXT, padding=3)
        style.map("TCheckbutton", background=[("active", GRAPHITE_CARD)])
        style.configure(
            "Horizontal.TProgressbar",
            background=GRAPHITE_ACCENT,
            troughcolor=GRAPHITE_FIELD,
            bordercolor=GRAPHITE_FIELD,
        )

    def make_button(self, parent, text, command, primary=False):
        if not primary:
            return ttk.Button(parent, text=text, command=command)

        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=GRAPHITE_ACCENT_DARK,
            fg="#ffffff",
            activebackground=GRAPHITE_ACCENT,
            activeforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=16,
            pady=8,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        )

    def make_update_button(self, parent):
        button = tk.Button(
            parent,
            text="Check for Update",
            command=lambda: launch_updater(self, APP_TITLE),
            bg=GRAPHITE_SUCCESS_DARK,
            fg="#ffffff",
            activebackground=GRAPHITE_SUCCESS,
            activeforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=12,
            pady=6,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        )
        button.pack(side="right", padx=(0, 8))
        button.bind("<Enter>", lambda event: button.configure(bg=GRAPHITE_SUCCESS), add="+")
        button.bind("<Leave>", lambda event: button.configure(bg=GRAPHITE_SUCCESS_DARK), add="+")
        return button

    def build_ui(self):
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)
        header = tk.Frame(outer, bg=GRAPHITE_HEADER)
        header.pack(fill="x", pady=(0, 10), ipady=7)
        title_area = tk.Frame(header, bg=GRAPHITE_HEADER)
        title_area.pack(side="left", fill="x", expand=True, padx=14)
        tk.Label(
            title_area,
            text=APP_TITLE,
            bg=GRAPHITE_HEADER,
            fg=GRAPHITE_TEXT,
            font=("Segoe UI", 18, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_area,
            text="Extract complete official DayZ PBO data into correct virtual paths.",
            bg=GRAPHITE_HEADER,
            fg=GRAPHITE_MUTED,
            font=("Segoe UI", 9),
        ).pack(anchor="w")
        right = tk.Frame(header, bg=GRAPHITE_HEADER)
        right.pack(side="right", padx=14)
        tk.Label(
            right,
            text=f"v{APP_VERSION}",
            bg=GRAPHITE_HEADER,
            fg=GRAPHITE_MUTED,
            font=("Segoe UI", 9),
        ).pack(side="right")
        self.make_update_button(right)
        make_licence_button(right, self)

        paths = ttk.LabelFrame(outer, text="Game data")
        paths.pack(fill="x", pady=(0, 10))
        paths.columnconfigure(1, weight=1)
        ttk.Label(paths, text="Detected install", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        self.install_combo = ttk.Combobox(paths, textvariable=self.install_var, state="readonly")
        self.install_combo.grid(row=0, column=1, sticky="ew", padx=8, pady=4)
        self.install_combo.bind("<<ComboboxSelected>>", self.on_install_selected)
        self.detect_button = ttk.Button(paths, text="Detect", command=self.detect_installations)
        self.detect_button.grid(row=0, column=2, pady=4)
        ttk.Label(paths, text="DayZ folder", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        game_entry = ttk.Entry(paths, textvariable=self.game_path_var)
        game_entry.grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        game_entry.bind("<Return>", lambda event: self.request_estimate())
        game_entry.bind("<FocusOut>", lambda event: self.request_estimate())
        ttk.Button(paths, text="Browse", command=self.choose_game_path).grid(row=1, column=2, pady=4)
        ttk.Label(paths, text="Extract to", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(paths, textvariable=self.output_path_var).grid(row=2, column=1, sticky="ew", padx=8, pady=4)
        ttk.Button(paths, text="Browse", command=self.choose_output_path).grid(row=2, column=2, pady=4)

        options = ttk.LabelFrame(outer, text="Options")
        options.pack(fill="x", pady=(0, 10))
        options.columnconfigure(1, weight=1)
        ttk.Label(options, text="Profile", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        profile_combo = ttk.Combobox(
            options,
            textvariable=self.profile_var,
            values=PROFILES,
            state="readonly",
            width=24,
        )
        profile_combo.grid(row=0, column=1, sticky="w", padx=8, pady=4)
        profile_combo.bind("<<ComboboxSelected>>", self.on_profile_selected)
        ttk.Checkbutton(
            options,
            text="Include official DayZ-Misc source assets (ADPL-SA)",
            variable=self.include_dayz_misc_var,
            command=self.on_dayz_misc_changed,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Button(
            options,
            text="GitHub",
            command=lambda: webbrowser.open(DAYZ_MISC_REPO_URL),
        ).grid(row=1, column=2, sticky="e", pady=4)
        ttk.Label(options, text="CfgConvert.exe", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        cfg_frame = ttk.Frame(options)
        cfg_frame.grid(row=2, column=1, columnspan=2, sticky="ew", padx=8, pady=4)
        cfg_frame.columnconfigure(0, weight=1)
        ttk.Entry(cfg_frame, textvariable=self.cfgconvert_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(cfg_frame, text="Browse", command=self.choose_cfgconvert).grid(row=0, column=1, padx=(8, 0))

        estimate = tk.Frame(outer, bg=GRAPHITE_CARD, highlightbackground=GRAPHITE_BORDER, highlightthickness=1)
        estimate.pack(fill="x", pady=(0, 10))
        tk.Label(
            estimate,
            text="ESTIMATED OUTPUT",
            bg=GRAPHITE_CARD,
            fg=GRAPHITE_MUTED,
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", padx=12, pady=(9, 0))
        tk.Label(
            estimate,
            textvariable=self.summary_var,
            bg=GRAPHITE_CARD,
            fg=GRAPHITE_TEXT,
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w", padx=12, pady=(1, 9))

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(0, 10))
        self.extract_button = self.make_button(actions, "Extract Game Data", self.extract, primary=True)
        self.extract_button.pack(side="left", padx=(0, 8))
        self.extract_button.configure(state="disabled")
        self.stop_button = self.make_button(actions, "Stop", self.stop)
        self.stop_button.pack(side="left", padx=(0, 8))
        self.stop_button.configure(state="disabled")
        self.open_button = self.make_button(actions, "Open output", self.open_output)
        self.open_button.pack(side="left")

        progress_frame = ttk.Frame(outer)
        progress_frame.pack(fill="x", pady=(0, 10))
        self.progress = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress.pack(fill="x", ipady=5, pady=(0, 6))
        ttk.Label(progress_frame, textvariable=self.status_var, style="Muted.TLabel").pack(anchor="w")

        log_frame = ttk.LabelFrame(outer, text="Log")
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(
            log_frame,
            height=8,
            bg=GRAPHITE_FIELD,
            fg=GRAPHITE_TEXT,
            insertbackground=GRAPHITE_TEXT,
            relief="flat",
            borderwidth=0,
            wrap="word",
            font=("Consolas", 9),
            state="disabled",
        )
        self.log_text.pack(fill="both", expand=True)

    def detect_installations(self, schedule_estimate=True):
        installs = find_dayz_installations()
        self.installations = {f"{item.name} — {item.path}": item for item in installs}
        values = list(self.installations)
        self.install_combo.configure(values=values)

        if not values:
            self.install_var.set("")
            self.log("No Steam DayZ installation detected. Select folder manually.")
            return

        current = os.path.normcase(os.path.abspath(self.game_path_var.get())) if self.game_path_var.get() else ""
        selected = next(
            (label for label, item in self.installations.items() if os.path.normcase(os.path.abspath(item.path)) == current),
            values[0],
        )
        self.install_var.set(selected)

        if not self.game_path_var.get() or not Path(self.game_path_var.get()).is_dir():
            self.game_path_var.set(str(self.installations[selected].path))

        self.log(f"Detected {len(values)} DayZ installation(s).")

        if schedule_estimate:
            self.after(50, self.request_estimate)

    def on_install_selected(self, event=None):
        install = self.installations.get(self.install_var.get())

        if install:
            self.game_path_var.set(str(install.path))
            self.request_estimate()

    def on_profile_selected(self, event=None):
        self.request_estimate()

    def on_dayz_misc_changed(self):
        if self.plan:
            self.render_estimate(self.plan)

    def choose_game_path(self):
        path = filedialog.askdirectory(
            title="Select DayZ installation",
            initialdir=self.game_path_var.get() or str(Path.home()),
            parent=self,
        )

        if path:
            self.game_path_var.set(path)
            self.request_estimate()

    def choose_output_path(self):
        path = filedialog.askdirectory(
            title="Select extraction folder",
            initialdir=self.output_path_var.get() or get_default_output_path(),
            parent=self,
        )

        if path:
            self.output_path_var.set(path)

            if self.plan:
                self.render_estimate(self.plan)

    def choose_cfgconvert(self):
        path = filedialog.askopenfilename(
            title="Select CfgConvert.exe",
            initialdir=str(Path(self.cfgconvert_var.get()).parent) if self.cfgconvert_var.get() else str(Path.home()),
            filetypes=[("CfgConvert", "CfgConvert.exe"), ("Executables", "*.exe")],
            parent=self,
        )

        if path:
            self.cfgconvert_var.set(path)

    def current_plan_signature(self):
        game_path = os.path.normcase(os.path.abspath(self.game_path_var.get().strip()))
        return game_path, self.profile_var.get()

    def request_estimate(self):
        if self.busy:
            return

        game_path = self.game_path_var.get().strip()

        if not game_path:
            self.plan = None
            self.plan_signature = None
            self.extract_button.configure(state="disabled")
            self.summary_var.set("Select DayZ installation folder.")
            return

        signature = self.current_plan_signature()

        if self.plan is not None and self.plan_signature == signature:
            self.render_estimate(self.plan)
            return

        self.estimate_generation += 1
        generation = self.estimate_generation
        include_extensions = get_profile_extensions(self.profile_var.get())
        self.plan = None
        self.plan_signature = None
        self.extract_button.configure(state="disabled")
        self.progress_var.set(0)
        self.summary_var.set("Calculating...")
        self.status_var.set("Reading PBO headers...")

        def report(payload):
            payload["generation"] = generation
            self.events.put(payload)

        def worker():
            try:
                plan = build_extraction_plan(
                    game_path,
                    include_extensions=include_extensions,
                    progress=report,
                )
                self.events.put({
                    "type": "estimate_done",
                    "generation": generation,
                    "plan": plan,
                    "signature": signature,
                })
            except Exception as error:
                self.events.put({
                    "type": "estimate_error",
                    "generation": generation,
                    "message": str(error),
                })

        threading.Thread(target=worker, name="rag_estimate", daemon=True).start()

    def extract(self):
        if self.busy:
            return

        if self.plan is None or self.plan_signature != self.current_plan_signature():
            self.request_estimate()
            return

        output_path = self.output_path_var.get().strip()

        if not output_path:
            messagebox.showerror(APP_TITLE, "Select output folder.", parent=self)
            return

        cfgconvert_exe = self.cfgconvert_var.get().strip()

        if has_conversion_candidates(self.plan) and not Path(cfgconvert_exe).is_file():
            messagebox.showerror(
                APP_TITLE,
                "CfgConvert.exe is required to convert configs and rapified materials.",
                parent=self,
            )
            return

        plan = self.plan
        include_dayz_misc = self.include_dayz_misc_var.get()
        self.save_current_settings()
        self.set_busy(True)
        self.progress_var.set(0)
        self.status_var.set("Starting extraction...")
        self.log(f"Extracting to: {output_path}")
        self.log(f"Using all {get_recommended_workers()} available CPU cores.")

        def worker():
            try:
                result = extract_game_data(
                    plan,
                    output_path,
                    workers=None,
                    cfgconvert_exe=cfgconvert_exe,
                    include_dayz_misc=include_dayz_misc,
                    cancel_event=self.cancel_event,
                    progress=self.events.put,
                )
                self.events.put({"type": "extract_done", "result": result})
            except ExtractionCancelled:
                self.events.put({"type": "cancelled"})
            except Exception as error:
                self.events.put({"type": "error", "message": str(error)})

        threading.Thread(target=worker, name="rag_extract_controller", daemon=True).start()

    def set_busy(self, busy):
        self.busy = busy
        self.detect_button.configure(state="disabled" if busy else "normal")
        self.install_combo.configure(state="disabled" if busy else "readonly")
        self.extract_button.configure(state="disabled" if busy or self.plan is None else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")

        if busy:
            self.cancel_event.clear()

    def stop(self):
        if self.busy:
            self.cancel_event.set()
            self.status_var.set("Stopping after current file...")
            self.log("Stop requested.")

    def render_estimate(self, plan):
        free = get_free_space(self.output_path_var.get())
        total_bytes = plan.total_bytes
        total_files = len(plan.entries)

        if self.include_dayz_misc_var.get():
            total_bytes += DAYZ_MISC_ESTIMATED_BYTES
            total_files += DAYZ_MISC_ESTIMATED_FILES

        self.summary_var.set(
            f"{format_byte_size(total_bytes)} · {total_files:,} files · "
            f"{format_byte_size(free)} free"
        )
        self.progress_var.set(0)
        self.status_var.set("Ready")
        self.extract_button.configure(state="normal")

        if plan.shadowed_archives:
            self.log(f"DLC overrides: {len(plan.shadowed_archives)} base archive(s) skipped.")

        if plan.protected_archives:
            self.log(f"Protected EBO skipped: {', '.join(plan.protected_archives)}")

    def poll_events(self):
        processed = 0

        while processed < 100:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break

            processed += 1
            event_type = event.get("type")

            if event_type == "scan":
                if event.get("generation") != self.estimate_generation:
                    continue

                total = max(1, event.get("total", 1))
                self.progress_var.set(event.get("current", 0) * 100 / total)
                self.status_var.set("Calculating estimated output...")
            elif event_type == "estimate_done":
                if event.get("generation") != self.estimate_generation:
                    continue

                self.plan = event["plan"]
                self.plan_signature = event["signature"]
                self.render_estimate(self.plan)
            elif event_type == "estimate_error":
                if event.get("generation") != self.estimate_generation:
                    continue

                self.summary_var.set("Estimate unavailable")
                self.status_var.set("Failed")
                self.extract_button.configure(state="disabled")
                self.log("ERROR: " + event.get("message", "Unknown error"))
            elif event_type == "archive":
                self.status_var.set(event.get("message", "Extracting..."))
            elif event_type == "file":
                total = max(1, event.get("total", 1))
                base = 5 if self.include_dayz_misc_var.get() else 0
                span = 85 if self.include_dayz_misc_var.get() else 90
                self.progress_var.set(base + event.get("current", 0) * span / total)
                self.status_var.set(
                    f"Extracted {event.get('current', 0):,}/{total:,} · "
                    f"{format_byte_size(event.get('bytes', 0))} / "
                    f"{format_byte_size(event.get('total_bytes', 0))}"
                )
            elif event_type == "misc_download":
                total_bytes = event.get("total_bytes", 0)
                downloaded = event.get("bytes", 0)
                self.progress_var.set(downloaded * 5 / total_bytes if total_bytes else 2)
                self.status_var.set(f"Downloading DayZ-Misc · {format_byte_size(downloaded)}")
            elif event_type == "phase":
                message = event.get("message", "Converting...")
                if message.startswith("Downloading"):
                    self.progress_var.set(0)
                elif message.startswith("Installing"):
                    self.progress_var.set(95)
                else:
                    self.progress_var.set(90)

                self.status_var.set(message)
            elif event_type == "convert":
                total = max(1, event.get("total", 1))
                self.progress_var.set(90 + event.get("current", 0) * 5 / total)
                message = event.get("message", "Converting...")
                self.status_var.set(message)
            elif event_type == "misc":
                total = max(1, event.get("total", 1))
                self.progress_var.set(95 + event.get("current", 0) * 4 / total)
                self.status_var.set(event.get("message", "Installing DayZ-Misc..."))
            elif event_type == "warning":
                self.log("WARNING: " + event.get("message", ""))
            elif event_type == "extract_done":
                result = event["result"]
                self.progress_var.set(100)
                self.status_var.set("Extraction complete")
                self.set_busy(False)
                self.log(
                    f"Complete: {result['files']:,} files / {format_byte_size(result['bytes'])}; "
                    f"{result['workers']} automatic workers."
                )

                if result["converted_configs"] or result["converted_materials"]:
                    self.log(
                        f"Converted: {result['converted_configs']:,} configs, "
                        f"{result['converted_materials']:,} materials."
                    )

                if result["conversion_errors"]:
                    self.log(f"Conversion warnings: {len(result['conversion_errors'])}")

                    for warning in result["conversion_errors"][:20]:
                        self.log("WARNING: " + warning)

                if result["dayz_misc_files"]:
                    self.log(f"DayZ-Misc: {result['dayz_misc_files']:,} source files overlaid.")

                messagebox.showinfo(
                    APP_TITLE,
                    f"Extracted {result['files']:,} files.\n\nOutput: {result['output_root']}",
                    parent=self,
                )
            elif event_type == "cancelled":
                self.status_var.set("Extraction cancelled")
                self.set_busy(False)
                self.log("Extraction cancelled.")
            elif event_type == "error":
                self.status_var.set("Failed")
                self.set_busy(False)
                self.log("ERROR: " + event.get("message", "Unknown error"))
                messagebox.showerror(APP_TITLE, event.get("message", "Unknown error"), parent=self)

        self.after(25 if not self.events.empty() else 100, self.poll_events)

    def log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def open_output(self):
        path = Path(self.output_path_var.get().strip())

        if not path.exists():
            messagebox.showerror(APP_TITLE, "Output folder does not exist.", parent=self)
            return

        if os.name == "nt":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def save_current_settings(self):
        save_settings({
            "game_path": self.game_path_var.get().strip(),
            "output_path": self.output_path_var.get().strip(),
            "profile": self.profile_var.get(),
            "cfgconvert_exe": self.cfgconvert_var.get().strip(),
            "include_dayz_misc": self.include_dayz_misc_var.get(),
        })

    def on_close(self):
        if self.busy:
            if not messagebox.askyesno(APP_TITLE, "Extraction still running. Stop and close?", parent=self):
                return

            self.cancel_event.set()

        self.save_current_settings()
        self.destroy()


def main():
    app = GameDataExtractorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
