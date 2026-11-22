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
import time
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
    get_cleanup_preview,
    get_required_space,
    get_recommended_workers,
    has_conversion_candidates,
    is_plan_current,
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
PROFILE_EXTENSIONS = {
    "Everything": "",
    "Scripts only": ".c",
    "Configs only": ".bin .cpp .hpp .cfg .xml .json",
    "Textures only": ".paa .pac .tga .png .edds",
    "Models only": ".p3d .rtm",
    "Materials only": ".rvmat",
}
PROFILES = tuple(PROFILE_EXTENSIONS)


class ToolTip:
    def __init__(self, widget, text, delay_ms=500):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.after_id = None
        self.window = None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<FocusIn>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<FocusOut>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def schedule(self, event=None):
        self.cancel()
        self.after_id = self.widget.after(self.delay_ms, self.show)

    def cancel(self):
        if self.after_id:
            try:
                self.widget.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None

    def show(self):
        self.after_id = None
        if self.window or not self.text or not self.widget.winfo_exists():
            return
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.configure(bg=GRAPHITE_BORDER)
        label = tk.Label(self.window, text=self.text, justify="left", bg=GRAPHITE_FIELD, fg=GRAPHITE_TEXT, relief="flat", borderwidth=0, padx=8, pady=5, font=("Segoe UI", 9), wraplength=520)
        label.pack(ipadx=1, ipady=1)
        self.window.update_idletasks()
        x = min(self.widget.winfo_rootx() + 20, self.widget.winfo_screenwidth() - self.window.winfo_reqwidth() - 8)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        if y + self.window.winfo_reqheight() > self.widget.winfo_screenheight():
            y = self.widget.winfo_rooty() - self.window.winfo_reqheight() - 8
        self.window.wm_geometry(f"+{max(0, x)}+{max(0, y)}")

    def hide(self, event=None):
        self.cancel()
        if self.window:
            self.window.destroy()
            self.window = None


def add_tooltip(widget, text):
    widget.tooltip = ToolTip(widget, text) if text else None
    return widget.tooltip


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
    return PROFILE_EXTENSIONS.get(profile, "")


def format_duration(seconds):
    if seconds is None:
        return "—"
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:d}:{seconds:02d}"


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
        self.option_checkbuttons = []
        self.input_controls = []
        self.closing = False
        self.started_at = None
        self.last_rate = None
        self.last_eta = None
        self.conversion_errors = []
        self.selected_archives = self.settings.get("selected_archives")
        self.selected_folders = self.settings.get("selected_folders")
        if not isinstance(self.selected_archives, list):
            self.selected_archives = None
        if not isinstance(self.selected_folders, list):
            self.selected_folders = None
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
        self.create_dayz_script_folder_var = tk.BooleanVar(value=self.settings.get("create_dayz_script_folder", False))
        self.clean_base_game_folders_var = tk.BooleanVar(value=self.settings.get("clean_base_game_folders", True))
        self.incremental_var = tk.BooleanVar(value=self.settings.get("incremental", False))
        self.summary_var = tk.StringVar(value="Calculating estimated output...")
        self.metrics_var = tk.StringVar()
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
        height = min(800, max(680, screen_height - 100))
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
        self.option_add("*TCombobox*Listbox.background", GRAPHITE_FIELD)
        self.option_add("*TCombobox*Listbox.foreground", GRAPHITE_TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", GRAPHITE_ACCENT_DARK)
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
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
        style.configure("Card.TFrame", background=GRAPHITE_CARD)
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
        style.configure("CardMuted.TLabel", background=GRAPHITE_CARD, foreground=GRAPHITE_MUTED)
        style.configure("Muted.TLabel", background=GRAPHITE_BG, foreground=GRAPHITE_MUTED)
        style.configure(
            "TEntry",
            fieldbackground=GRAPHITE_FIELD,
            foreground=GRAPHITE_TEXT,
            insertcolor=GRAPHITE_TEXT,
            bordercolor=GRAPHITE_BORDER,
            lightcolor=GRAPHITE_FIELD,
            darkcolor=GRAPHITE_FIELD,
            relief="flat",
            padding=5,
        )
        style.configure(
            "TCombobox",
            fieldbackground=GRAPHITE_FIELD,
            background=GRAPHITE_FIELD,
            foreground=GRAPHITE_TEXT,
            selectbackground=GRAPHITE_FIELD,
            selectforeground=GRAPHITE_TEXT,
            arrowcolor=GRAPHITE_MUTED,
            bordercolor=GRAPHITE_BORDER,
            lightcolor=GRAPHITE_FIELD,
            darkcolor=GRAPHITE_FIELD,
            focuscolor=GRAPHITE_FIELD,
            focusthickness=0,
            relief="flat",
            padding=5,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", GRAPHITE_FIELD), ("disabled", GRAPHITE_CARD), ("!disabled", GRAPHITE_FIELD)],
            background=[("readonly", GRAPHITE_FIELD), ("active", GRAPHITE_CARD_SOFT), ("!disabled", GRAPHITE_FIELD)],
            foreground=[("readonly", GRAPHITE_TEXT), ("disabled", GRAPHITE_MUTED), ("!disabled", GRAPHITE_TEXT)],
            selectbackground=[("readonly", GRAPHITE_FIELD), ("!disabled", GRAPHITE_FIELD)],
            selectforeground=[("readonly", GRAPHITE_TEXT), ("!disabled", GRAPHITE_TEXT)],
            bordercolor=[("focus", GRAPHITE_ACCENT), ("!focus", GRAPHITE_BORDER)],
            arrowcolor=[("active", GRAPHITE_TEXT), ("!disabled", GRAPHITE_MUTED)],
        )
        style.configure(
            "TButton",
            background=GRAPHITE_CARD_SOFT,
            foreground=GRAPHITE_TEXT,
            bordercolor=GRAPHITE_CARD_SOFT,
            lightcolor=GRAPHITE_CARD_SOFT,
            darkcolor=GRAPHITE_CARD_SOFT,
            focuscolor=GRAPHITE_CARD_SOFT,
            focusthickness=0,
            relief="flat",
            padding=(12, 8),
        )
        style.map("TButton", background=[("active", GRAPHITE_BORDER)], foreground=[("disabled", GRAPHITE_MUTED)])
        style.configure("Path.TButton", padding=(12, 4))
        style.configure("TCheckbutton", background=GRAPHITE_CARD, foreground=GRAPHITE_TEXT, padding=3)
        style.map("TCheckbutton", background=[("active", GRAPHITE_CARD)])
        style.configure(
            "Horizontal.TProgressbar",
            background=GRAPHITE_ACCENT,
            troughcolor=GRAPHITE_FIELD,
            bordercolor=GRAPHITE_FIELD,
        )

    def make_button(self, parent, text, command, primary=False, tooltip="", variant=""):
        bg = GRAPHITE_ACCENT_DARK if primary or variant == "stop" else GRAPHITE_CARD_SOFT
        active_bg = GRAPHITE_ACCENT if primary or variant == "stop" else GRAPHITE_BORDER
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg="#ffffff" if primary else GRAPHITE_TEXT,
            activebackground=active_bg,
            activeforeground=GRAPHITE_TEXT,
            disabledforeground=GRAPHITE_MUTED,
            relief="flat",
            borderwidth=0,
            padx=16,
            pady=6,
            font=("Segoe UI", 10, "bold" if primary else "normal"),
            cursor="hand2",
        )
        button.bind("<Enter>", lambda event: button.configure(bg=active_bg) if str(button.cget("state")) != "disabled" else None, add="+")
        button.bind("<Leave>", lambda event: button.configure(bg=bg), add="+")
        add_tooltip(button, tooltip)
        return button

    def _add_checkbutton(self, parent, text, variable, row, column, tooltip, command=None, columnspan=1):
        def refresh(*_args):
            selected = variable.get()
            checkbox.configure(
                text=("✓ " if selected else "  ") + text,
                bg=GRAPHITE_CARD_SOFT if selected else GRAPHITE_FIELD,
                fg=GRAPHITE_TEXT if selected else GRAPHITE_MUTED,
                activebackground=GRAPHITE_BORDER if selected else GRAPHITE_CARD_SOFT,
                activeforeground=GRAPHITE_TEXT,
            )

        def on_toggle():
            self.save_current_settings()
            if command:
                command()

        checkbox = tk.Checkbutton(parent, variable=variable, command=on_toggle, indicatoron=False, selectcolor=GRAPHITE_CARD_SOFT, relief="flat", borderwidth=0, padx=12, pady=7, font=("Segoe UI", 10), cursor="hand2", anchor="w", justify="left", width=max(22, len(text) + 3), takefocus=True, disabledforeground=GRAPHITE_MUTED)
        checkbox.grid(row=row, column=column, columnspan=columnspan, sticky="w", pady=(0, 6), padx=(0, 8))
        trace_id = variable.trace_add("write", refresh)
        checkbox.bind("<Destroy>", lambda event: variable.trace_remove("write", trace_id), add="+")
        refresh()
        add_tooltip(checkbox, tooltip)
        self.option_checkbuttons.append(checkbox)
        return checkbox

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
        add_tooltip(button, "Open RaG Tools Updater to check for suite updates.")
        return button

    def build_ui(self):
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        header = tk.Frame(outer, bg=GRAPHITE_HEADER)
        header.pack(fill="x", pady=(0, 8), ipady=4)
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
        add_tooltip(make_licence_button(right, self), "View the RaG DayZ Tools licence.")

        paths = ttk.LabelFrame(outer, text="Game data", padding=(12, 8))
        paths.pack(fill="x", pady=(0, 8))
        paths.columnconfigure(1, weight=1)
        ttk.Label(paths, text="Detected install", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=2)
        self.install_combo = ttk.Combobox(paths, textvariable=self.install_var, state="readonly")
        self.install_combo.grid(row=0, column=1, sticky="ew", padx=8, pady=2)
        self.install_combo.bind("<<ComboboxSelected>>", self.on_install_selected)
        add_tooltip(self.install_combo, "Choose a detected DayZ Stable or Experimental installation. Use Detect to refresh Steam installations, or browse for the DayZ folder manually.")
        self.detect_button = ttk.Button(paths, text="Detect", command=self.detect_installations, style="Path.TButton")
        self.detect_button.grid(row=0, column=2, pady=2)
        add_tooltip(self.detect_button, "Search Steam libraries for DayZ Stable and Experimental installations.")
        ttk.Label(paths, text="DayZ folder", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=2)
        game_entry = ttk.Entry(paths, textvariable=self.game_path_var)
        game_entry.grid(row=1, column=1, sticky="ew", padx=8, pady=2)
        game_entry.bind("<Return>", lambda event: self.request_estimate())
        game_entry.bind("<FocusOut>", lambda event: self.request_estimate())
        add_tooltip(game_entry, "DayZ installation containing Addons and dta. Official DLC archives are included when installed; protected EBO archives are skipped.")
        game_browse = ttk.Button(paths, text="Browse", command=self.choose_game_path, style="Path.TButton")
        game_browse.grid(row=1, column=2, pady=2)
        add_tooltip(game_browse, "Choose the DayZ installation folder to extract.")
        ttk.Label(paths, text="Extract to", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=2)
        output_entry = ttk.Entry(paths, textvariable=self.output_path_var)
        output_entry.grid(row=2, column=1, sticky="ew", padx=8, pady=2)
        output_entry.bind("<FocusOut>", lambda event: self.render_estimate(self.plan) if self.plan and not self.busy else None)
        add_tooltip(output_entry, "Destination work drive or folder, usually P:\\. Data is placed into its virtual paths, such as DZ and scripts. Existing matching files are overwritten. Cleanup can also remove existing official folders and all their contents.")
        output_browse = ttk.Button(paths, text="Browse", command=self.choose_output_path, style="Path.TButton")
        output_browse.grid(row=2, column=2, pady=2)
        add_tooltip(output_browse, "Choose the destination work drive or extraction folder.")
        ttk.Label(paths, text="CfgConvert.exe", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=2)
        cfg_entry = ttk.Entry(paths, textvariable=self.cfgconvert_var)
        cfg_entry.grid(row=3, column=1, sticky="ew", padx=8, pady=2)
        add_tooltip(cfg_entry, "DayZ Tools CfgConvert.exe converts extracted binary configs and supported rapified materials into editable text. Required when the extraction includes conversion candidates.")
        cfg_browse = ttk.Button(paths, text="Browse", command=self.choose_cfgconvert, style="Path.TButton")
        cfg_browse.grid(row=3, column=2, pady=2)
        add_tooltip(cfg_browse, "Locate CfgConvert.exe in the DayZ Tools installation.")

        options = ttk.LabelFrame(outer, text="Extraction options", padding=(12, 8))
        options.pack(fill="x", pady=(0, 8))
        for column in range(3):
            options.columnconfigure(column, weight=1)
        profile_frame = ttk.Frame(options, style="Card.TFrame")
        profile_frame.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(profile_frame, text="Profile", style="Card.TLabel").pack(side="left", padx=(0, 12))
        profile_combo = ttk.Combobox(
            profile_frame,
            textvariable=self.profile_var,
            values=PROFILES,
            state="readonly",
            width=24,
        )
        profile_combo.pack(side="left")
        profile_combo.bind("<<ComboboxSelected>>", self.on_profile_selected)
        add_tooltip(profile_combo, "Choose everything, scripts, configs, textures, models or materials. Filtered profiles preserve unrelated files. Scripts only can replace the scripts root when no archive/folder restriction is active. DayZ-Misc overlays source assets under DZ independently of the profile.")
        self._add_checkbutton(options, "DayZ-Misc sources", self.include_dayz_misc_var, 1, 0, "Download official Bohemia Interactive source assets from DayZ-Misc and overlay them after extraction. Includes road parts for Chernarus, Livonia, Sakhal and Nasdara, powerlines, water objects, body parts, proxies and animations. Assets use ADPL-SA; the upstream README is included.", self.on_dayz_misc_changed)
        self._add_checkbutton(options, "DayZ script folders", self.create_dayz_script_folder_var, 1, 1, "Create a DayZ subfolder inside each official script module, such as scripts\\4_World\\DayZ. Disabled by default to preserve the archive layout. Existing DayZ nesting is not duplicated.", self.request_estimate)
        github_button = ttk.Button(
            options,
            text="GitHub",
            command=lambda: webbrowser.open(DAYZ_MISC_REPO_URL),
        )
        github_button.grid(row=0, column=2, sticky="e", pady=(0, 6))
        add_tooltip(github_button, "Open the official Bohemia Interactive DayZ-Misc repository and its ADPL-SA licence.")
        self._add_checkbutton(options, "Clean base-game folders", self.clean_base_game_folders_var, 1, 2, "Replace selected official roots after successful extraction. Previous data is restored on failure or cancellation. Scripts only cleans scripts; other filtered profiles and archive/folder selections preserve unrelated files. Disabled by update-only mode. Use Preview cleanup to inspect exact folders and sizes.", lambda: self.render_estimate(self.plan) if self.plan else None)
        ttk.Label(
            options,
            text="Cleanup replaces whole official folders after success. Filtered selections and update-only mode preserve unrelated files.",
            style="CardMuted.TLabel",
            wraplength=760,
        ).grid(row=2, column=0, columnspan=3, sticky="w")
        self._add_checkbutton(options, "Update changed files only", self.incremental_var, 3, 0, "Skip verified unchanged files using a local manifest. Changed, missing or edited outputs are regenerated. Unrelated files are preserved; whole-folder cleanup is disabled. DayZ-Misc source assets are refreshed when enabled.", lambda: self.render_estimate(self.plan) if self.plan else None)
        selection_button = ttk.Button(options, text="Select archives / folders…", command=self.choose_selection)
        selection_button.grid(row=3, column=1, sticky="w", pady=(6, 0))
        rescan_button = ttk.Button(options, text="Rescan", command=lambda: self.request_estimate(force=True))
        rescan_button.grid(row=3, column=2, sticky="e", pady=(6, 0))
        self.input_controls.extend((widget, "normal") for widget in (game_entry, game_browse, output_entry, output_browse, cfg_entry, cfg_browse, selection_button, rescan_button))
        self.input_controls.append((profile_combo, "readonly"))

        estimate = tk.Frame(outer, bg=GRAPHITE_CARD, highlightbackground=GRAPHITE_BORDER, highlightthickness=1)
        estimate.pack(fill="x", pady=(0, 8))
        tk.Label(
            estimate,
            text="ESTIMATED OUTPUT",
            bg=GRAPHITE_CARD,
            fg=GRAPHITE_MUTED,
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left", padx=12, pady=9)
        tk.Label(
            estimate,
            textvariable=self.summary_var,
            bg=GRAPHITE_CARD,
            fg=GRAPHITE_TEXT,
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left", padx=(4, 12), pady=9)

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(0, 6))
        self.extract_button = self.make_button(actions, "Extract Game Data", self.extract, primary=True, tooltip="Review cleanup, extract and convert into staging, then install. Failures and cancellation restore previous output.")
        self.extract_button.pack(side="left", padx=(0, 8))
        self.extract_button.configure(state="disabled")
        self.stop_button = self.make_button(actions, "Stop", self.stop, variant="stop", tooltip="Cancel after the current file or conversion. Existing output is retained or restored before stopping.")
        self.stop_button.pack(side="left", padx=(0, 8))
        self.stop_button.configure(state="disabled")
        self.open_button = self.make_button(actions, "Open output", self.open_output, tooltip="Open the extraction destination in Explorer.")
        self.open_button.pack(side="left")
        preview_button = self.make_button(actions, "Preview cleanup", self.preview_cleanup, tooltip="List exact folders, existing file counts and sizes affected by cleanup.")
        preview_button.pack(side="left", padx=(8, 0))
        self.input_controls.append((preview_button, "normal"))
        self.make_button(actions, "Export log", self.export_log, tooltip="Save the complete log, including conversion failures, as text.").pack(side="left", padx=(8, 0))

        progress_frame = ttk.Frame(outer)
        progress_frame.pack(fill="x", pady=(0, 6))
        self.progress = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress.pack(fill="x", ipady=3, pady=(0, 4))
        ttk.Label(progress_frame, textvariable=self.status_var, style="Muted.TLabel").pack(anchor="w")
        ttk.Label(progress_frame, textvariable=self.metrics_var, style="Muted.TLabel").pack(anchor="w")

        log_frame = ttk.LabelFrame(outer, text="Log", padding=(10, 6))
        log_frame.pack(fill="x")
        self.log_text = tk.Text(
            log_frame,
            height=5,
            bg=GRAPHITE_FIELD,
            fg=GRAPHITE_TEXT,
            insertbackground=GRAPHITE_TEXT,
            relief="flat",
            borderwidth=0,
            wrap="word",
            font=("Consolas", 9),
            state="disabled",
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        log_scrollbar.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=log_scrollbar.set)
        add_tooltip(self.log_text, "Extraction progress, cleaned folders, DLC overrides, protected archives and conversion warnings appear here.")

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

    def choose_selection(self):
        if self.busy or not self.game_path_var.get().strip():
            return
        game_path = self.game_path_var.get().strip()
        self.set_busy(True)
        self.status_var.set("Reading archive and folder choices...")

        def worker():
            try:
                plan = build_extraction_plan(game_path)
                self.events.put({"type": "selection_ready", "plan": plan})
            except Exception as error:
                self.events.put({"type": "error", "message": str(error)})

        threading.Thread(target=worker, name="rag_selection", daemon=True).start()

    def show_selection(self, plan):
        window = tk.Toplevel(self)
        window.title("Select archives and folders")
        window.transient(self)
        left = max(0, self.winfo_rootx() + (self.winfo_width() - 900) // 2)
        top = max(0, self.winfo_rooty() + (self.winfo_height() - 540) // 2)
        window.geometry(f"900x540+{left}+{top}")
        window.minsize(650, 400)
        window.grab_set()
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(2, weight=1)
        ttk.Label(frame, text="Select items to restrict extraction; Ctrl/Shift selects multiple. Archive and folder restrictions combine. Selected folders include subfolders.", wraplength=820).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        archives = sorted(name for name, _size, _mtime in plan.source_signature if name.endswith(".pbo"))
        folders = sorted({
            "\\".join(item.relative_path.split("\\")[:index])
            for item in plan.entries for index in range(1, len(item.relative_path.split("\\")))
        }, key=str.casefold)
        restrict_archives = tk.BooleanVar(value=self.selected_archives is not None)
        restrict_folders = tk.BooleanVar(value=self.selected_folders is not None)
        lists = []
        for column, (label, variable, values, selected) in enumerate((
            ("Restrict archives", restrict_archives, archives, self.selected_archives),
            ("Restrict folders", restrict_folders, folders, self.selected_folders),
        )):
            ttk.Checkbutton(frame, text=label, variable=variable).grid(row=1, column=column, sticky="w")
            container = ttk.Frame(frame)
            container.grid(row=2, column=column, sticky="nsew", padx=6, pady=6)
            box = tk.Listbox(container, selectmode="extended", exportselection=False, bg=GRAPHITE_FIELD, fg=GRAPHITE_TEXT, selectbackground=GRAPHITE_ACCENT_DARK, width=35)
            vertical = ttk.Scrollbar(container, command=box.yview)
            horizontal = ttk.Scrollbar(container, orient="horizontal", command=box.xview)
            container.columnconfigure(0, weight=1)
            container.rowconfigure(0, weight=1)
            box.grid(row=0, column=0, sticky="nsew")
            vertical.grid(row=0, column=1, sticky="ns")
            horizontal.grid(row=1, column=0, sticky="ew")
            box.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            selected_keys = {value.casefold() for value in selected or []}
            for index, value in enumerate(values):
                box.insert("end", value)
                if value.casefold() in selected_keys:
                    box.selection_set(index)
            lists.append((box, values))
            box.bind("<<ListboxSelect>>", lambda event, variable=variable, box=box: variable.set(bool(box.curselection())))
        actions = ttk.Frame(frame)
        actions.grid(row=3, column=0, columnspan=2, sticky="e", pady=(8, 0))

        def apply(all_data=False):
            selections = []
            for variable, (box, values) in zip((restrict_archives, restrict_folders), lists):
                selected = [values[index] for index in box.curselection()] if variable.get() and not all_data else None
                if selected == []:
                    messagebox.showerror(APP_TITLE, "Select at least one item in each enabled restriction.", parent=window)
                    return
                selections.append(selected)
            self.selected_archives, self.selected_folders = selections
            self.log(f"Selection: {len(self.selected_archives) if self.selected_archives is not None else 'all'} archives; {len(self.selected_folders) if self.selected_folders is not None else 'all'} folders.")
            window.destroy()
            self.request_estimate(force=True)

        ttk.Button(actions, text="Reset to all", command=lambda: apply(True)).pack(side="left", padx=6)
        ttk.Button(actions, text="Cancel", command=window.destroy).pack(side="left", padx=6)
        ttk.Button(actions, text="Apply", command=apply).pack(side="left", padx=6)

    def preview_cleanup(self, start_extraction=False):
        if self.busy or self.plan is None:
            return
        plan = self.plan
        output_path = self.output_path_var.get().strip()
        if not output_path:
            messagebox.showerror(APP_TITLE, "Select output folder.", parent=self)
            return
        clean = self.clean_base_game_folders_var.get()
        incremental = self.incremental_var.get()
        self.set_busy(True)
        self.status_var.set("Calculating cleanup preview...")

        def worker():
            try:
                preview = get_cleanup_preview(plan, output_path, clean, incremental, self.cancel_event)
                self.events.put({"type": "cleanup_preview", "preview": preview, "start_extraction": start_extraction})
            except ExtractionCancelled:
                self.events.put({"type": "cancelled"})
            except Exception as error:
                self.events.put({"type": "error", "message": str(error)})

        threading.Thread(target=worker, name="rag_cleanup_preview", daemon=True).start()

    def export_log(self):
        path = filedialog.asksaveasfilename(parent=self, title="Export extraction log", defaultextension=".txt", initialfile="RaG_Game_Data_Extractor.log.txt", filetypes=[("Text log", "*.txt"), ("All files", "*.*")])
        if path:
            try:
                Path(path).write_text(self.log_text.get("1.0", "end-1c"), encoding="utf-8")
            except OSError as error:
                messagebox.showerror(APP_TITLE, str(error), parent=self)

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
        return game_path, self.profile_var.get(), self.create_dayz_script_folder_var.get(), (
            tuple(self.selected_archives) if self.selected_archives is not None else None,
            tuple(self.selected_folders) if self.selected_folders is not None else None,
        )

    def request_estimate(self, force=False, extract_when_ready=False):
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

        if not force and self.plan is not None and self.plan_signature == signature and is_plan_current(self.plan):
            self.render_estimate(self.plan)
            return

        self.estimate_generation += 1
        generation = self.estimate_generation
        include_extensions = get_profile_extensions(self.profile_var.get())
        create_dayz_script_folder = self.create_dayz_script_folder_var.get()
        selected_archives = self.selected_archives
        selected_folders = self.selected_folders
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
                    create_dayz_script_folder=create_dayz_script_folder,
                    selected_archives=selected_archives,
                    selected_folders=selected_folders,
                )
                self.events.put({
                    "type": "estimate_done",
                    "generation": generation,
                    "plan": plan,
                    "signature": signature,
                    "extract_when_ready": extract_when_ready,
                })
            except Exception as error:
                self.events.put({
                    "type": "estimate_error",
                    "generation": generation,
                    "message": str(error),
                })

        threading.Thread(target=worker, name="rag_estimate", daemon=True).start()

    def extract(self, reviewed_cleanup=False):
        if self.busy:
            return

        if self.plan is None or self.plan_signature != self.current_plan_signature():
            self.request_estimate(extract_when_ready=True)
            return
        if not is_plan_current(self.plan):
            self.log("Source archives changed; rescanning automatically.")
            self.request_estimate(force=True, extract_when_ready=True)
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

        if not reviewed_cleanup:
            self.preview_cleanup(start_extraction=True)
            return

        plan = self.plan
        include_dayz_misc = self.include_dayz_misc_var.get()
        clean_base_game_folders = self.clean_base_game_folders_var.get()
        incremental = self.incremental_var.get()
        self.save_current_settings()
        self.set_busy(True)
        self.started_at = time.monotonic()
        self.last_rate = None
        self.last_eta = None
        self.progress_var.set(0)
        self.status_var.set("Starting extraction...")
        self.log(f"Extracting to: {output_path}")
        self.log(f"Using all {get_recommended_workers()} available CPU cores.")
        self.log(f"Profile: {self.profile_var.get()}; update-only: {incremental}; cleanup: {clean_base_game_folders and not incremental}.")

        def worker():
            try:
                result = extract_game_data(
                    plan,
                    output_path,
                    workers=None,
                    cfgconvert_exe=cfgconvert_exe,
                    include_dayz_misc=include_dayz_misc,
                    clean_base_game_folders=clean_base_game_folders,
                    incremental=incremental,
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
        for checkbox in self.option_checkbuttons:
            checkbox.configure(state="disabled" if busy else "normal")
        for widget, state in self.input_controls:
            widget.configure(state="disabled" if busy else state)

        if busy:
            self.cancel_event.clear()
            self.started_at = None
            self.last_rate = None
            self.last_eta = None

    def stop(self):
        if self.busy:
            self.cancel_event.set()
            self.status_var.set("Stopping after current file...")
            self.log("Stop requested.")

    def render_estimate(self, plan):
        if plan is None or self.busy:
            return
        free = get_free_space(self.output_path_var.get())
        total_bytes = plan.total_bytes
        total_files = len(plan.entries)

        if self.include_dayz_misc_var.get():
            total_bytes += DAYZ_MISC_ESTIMATED_BYTES
            total_files += DAYZ_MISC_ESTIMATED_FILES

        self.summary_var.set(
            f"{format_byte_size(total_bytes)} · {total_files:,} files · "
            f"{format_byte_size(free)} free · {format_byte_size(get_required_space(plan, self.include_dayz_misc_var.get()))} required free"
        )
        self.progress_var.set(0)
        self.status_var.set("Ready — update-only skips verified unchanged files" if self.incremental_var.get() else "Ready")
        self.extract_button.configure(state="normal")

        if plan.shadowed_archives:
            self.log(f"DLC overrides: {len(plan.shadowed_archives)} base archive(s) skipped.")

        overlays = sum(source.fs_shadowing for source in plan.archives)
        if overlays:
            self.log(f"PBO shadowing: {overlays} overlay archive(s) merged; {len(plan.conflicts)} entry path(s) overridden.")

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
                if event.get("extract_when_ready") and not self.closing:
                    self.extract()
            elif event_type == "selection_ready":
                self.set_busy(False)
                if not self.closing and not self.cancel_event.is_set():
                    self.status_var.set("Ready")
                    self.show_selection(event["plan"])
                elif not self.closing:
                    self.status_var.set("Selection scan cancelled")
            elif event_type == "cleanup_preview":
                self.set_busy(False)
                if self.closing:
                    continue
                preview = event["preview"]
                details = "\n".join(f"{item['path']} — {item['files']:,} files, {format_byte_size(item['bytes'])}" for item in preview)
                text = "Folders replaced after successful extraction:\n\n" + details if preview else "No whole folders will be cleared. Existing matching files may be replaced."
                self.status_var.set("Ready")
                if self.cancel_event.is_set():
                    self.status_var.set("Cleanup preview cancelled")
                    continue
                if event.get("start_extraction"):
                    if not preview or messagebox.askyesno("Review cleanup", text + "\n\nAll contents inside these folders will be replaced. Continue?", parent=self):
                        for item in preview:
                            self.log(f"Cleanup: {item['path']} — {item['files']:,} files / {format_byte_size(item['bytes'])}.")
                        self.extract(reviewed_cleanup=True)
                else:
                    messagebox.showinfo("Cleanup preview", text, parent=self)
            elif event_type == "estimate_error":
                if event.get("generation") != self.estimate_generation:
                    continue

                self.summary_var.set("Estimate unavailable")
                self.status_var.set("Failed")
                self.extract_button.configure(state="disabled")
                self.log("ERROR: " + event.get("message", "Unknown error"))
            elif event_type == "cleanup":
                self.progress_var.set(99)
                self.status_var.set(event["message"])
                self.log(event["message"])
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
                self.last_rate = event.get("bytes_per_second")
                self.last_eta = event.get("eta_seconds")
            elif event_type == "misc_download":
                total_bytes = event.get("total_bytes", 0)
                downloaded = event.get("bytes", 0)
                self.progress_var.set(downloaded * 5 / total_bytes if total_bytes else 2)
                self.status_var.set(f"Downloading DayZ-Misc · {format_byte_size(downloaded)}")
            elif event_type == "phase":
                message = event.get("message", "Converting...")
                self.last_eta = None
                if message.startswith("Downloading"):
                    self.progress_var.set(0)
                elif message.startswith("Checking"):
                    self.progress_var.set(0)
                elif message.startswith("Installing extracted"):
                    self.progress_var.set(99)
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
                self.conversion_errors = result["conversion_errors"]
                self.status_var.set("Complete with conversion warnings" if self.conversion_errors else "Extraction complete")
                self.set_busy(False)
                self.metrics_var.set(f"Elapsed {format_duration(result['elapsed_seconds'])} · {result['skipped_files']:,} unchanged files skipped")
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

                    for warning in result["conversion_errors"]:
                        self.log("WARNING: " + warning)

                if result["dayz_misc_files"]:
                    self.log(f"DayZ-Misc: {result['dayz_misc_files']:,} source files overlaid.")

                self.log(f"Elapsed: {format_duration(result['elapsed_seconds'])}; skipped unchanged: {result['skipped_files']:,}.")
                if not self.closing:
                    text = f"Extracted {result['files']:,} files; skipped {result['skipped_files']:,} unchanged.\nElapsed: {format_duration(result['elapsed_seconds'])}\n\nOutput: {result['output_root']}"
                    if self.conversion_errors:
                        messagebox.showwarning(APP_TITLE, text + f"\n\n{len(self.conversion_errors):,} conversion warning(s). Binary files retained.\n" + "\n".join(self.conversion_errors[:5]) + "\n\nFull details available in Export log.", parent=self)
                    else:
                        messagebox.showinfo(APP_TITLE, text, parent=self)
            elif event_type == "cancelled":
                self.status_var.set("Extraction cancelled")
                self.set_busy(False)
                self.log("Extraction cancelled. Previous output retained or restored.")
                if self.started_at is not None:
                    self.metrics_var.set(f"Elapsed {format_duration(time.monotonic() - self.started_at)} · Cancelled")
            elif event_type == "error":
                self.status_var.set("Failed")
                self.set_busy(False)
                self.log("ERROR: " + event.get("message", "Unknown error"))
                if self.started_at is not None:
                    self.metrics_var.set(f"Elapsed {format_duration(time.monotonic() - self.started_at)} · Failed")
                if not self.closing:
                    messagebox.showerror(APP_TITLE, event.get("message", "Unknown error"), parent=self)
                    if "rescan" in event.get("message", "").lower():
                        self.request_estimate(force=True)

        if self.closing and not self.busy:
            self.save_current_settings()
            self.destroy()
            return
        if self.busy and self.started_at is not None:
            rate = f" · {format_byte_size(self.last_rate)}/s" if self.last_rate is not None else ""
            eta = f" · Extraction ETA {format_duration(self.last_eta)}" if self.last_eta is not None else ""
            self.metrics_var.set(f"Elapsed {format_duration(time.monotonic() - self.started_at)}{rate}{eta}")
        self.after(25 if not self.events.empty() else 100, self.poll_events)

    def log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", time.strftime("[%Y-%m-%d %H:%M:%S] ") + message.rstrip() + "\n")
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
            "create_dayz_script_folder": self.create_dayz_script_folder_var.get(),
            "clean_base_game_folders": self.clean_base_game_folders_var.get(),
            "incremental": self.incremental_var.get(),
            "selected_archives": self.selected_archives,
            "selected_folders": self.selected_folders,
        })

    def on_close(self):
        if self.busy:
            if not messagebox.askyesno(APP_TITLE, "Extraction still running. Stop and close?", parent=self):
                return

            self.cancel_event.set()
            self.closing = True
            self.status_var.set("Cancelling and restoring output before closing...")
            return

        self.save_current_settings()
        self.destroy()


def main():
    app = GameDataExtractorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
