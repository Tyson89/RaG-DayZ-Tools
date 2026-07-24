from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

from rag_licence import make_licence_button
from rag_update_check import UpdateError, check_for_update, download_update
from rag_version import APP_VERSION


APP_TITLE = "RaG Tools Updater"
APP_ICON_FILE = os.path.join("assets", "installer.ico")
GRAPHITE_BG = "#24262b"
GRAPHITE_HEADER = "#1f2126"
GRAPHITE_CARD = "#2f3238"
GRAPHITE_CARD_SOFT = "#383c44"
GRAPHITE_FIELD = "#292c32"
GRAPHITE_BORDER = "#4a505b"
GRAPHITE_TEXT = "#f1f1f1"
GRAPHITE_MUTED = "#b8bec8"
GRAPHITE_SUCCESS = "#7fb087"
GRAPHITE_SUCCESS_DARK = "#41684a"
GRAPHITE_ERROR = "#df7777"
GRAPHITE_WARNING = "#d6aa5f"


def resource_path(relative_path):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative_path


class RaGToolsUpdaterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.events = queue.Queue()
        self.available_update = None
        self.busy = False

        self.status_var = tk.StringVar(value="Ready")
        self.latest_var = tk.StringVar(value="Checking...")

        self.title(APP_TITLE)
        self.geometry("760x570")
        self.minsize(660, 480)
        self.configure(bg=GRAPHITE_BG)
        self.set_window_icon()
        self.apply_theme()
        self.build_ui()
        self.after(100, self.poll_events)
        self.after(150, self.start_check)

    def set_window_icon(self):
        icon_path = resource_path(APP_ICON_FILE)
        if not icon_path.is_file():
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
        style.configure(".", background=GRAPHITE_BG, foreground=GRAPHITE_TEXT, font=("Segoe UI", 10))
        style.configure("TFrame", background=GRAPHITE_BG)
        style.configure("Card.TFrame", background=GRAPHITE_CARD)
        style.configure("TLabel", background=GRAPHITE_BG, foreground=GRAPHITE_TEXT)
        style.configure("Card.TLabel", background=GRAPHITE_CARD, foreground=GRAPHITE_TEXT)
        style.configure("Muted.TLabel", background=GRAPHITE_CARD, foreground=GRAPHITE_MUTED)
        style.configure(
            "Horizontal.TProgressbar",
            background=GRAPHITE_SUCCESS,
            troughcolor=GRAPHITE_FIELD,
            bordercolor=GRAPHITE_FIELD,
        )

    def build_ui(self):
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill="both", expand=True)

        header = tk.Frame(outer, bg=GRAPHITE_HEADER, padx=16, pady=12)
        header.pack(fill="x", pady=(0, 12))
        left = tk.Frame(header, bg=GRAPHITE_HEADER)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text=APP_TITLE, bg=GRAPHITE_HEADER, fg=GRAPHITE_TEXT, font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(left, text="Update complete RaG PBO Tools installation.", bg=GRAPHITE_HEADER, fg=GRAPHITE_MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(header, text=f"v{APP_VERSION}", bg=GRAPHITE_HEADER, fg=GRAPHITE_MUTED, font=("Segoe UI", 9)).pack(side="right")
        make_licence_button(header, self)

        versions = ttk.Frame(outer, style="Card.TFrame", padding=14)
        versions.pack(fill="x", pady=(0, 12))
        ttk.Label(versions, text="Installed version", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(versions, text=APP_VERSION, style="Card.TLabel", font=("Segoe UI", 11, "bold")).grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Label(versions, text="Latest version", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(50, 0))
        ttk.Label(versions, textvariable=self.latest_var, style="Card.TLabel", font=("Segoe UI", 11, "bold")).grid(row=1, column=1, sticky="w", padx=(50, 0), pady=(2, 0))
        versions.columnconfigure(1, weight=1)

        notes_frame = tk.Frame(outer, bg=GRAPHITE_CARD)
        notes_frame.pack(fill="both", expand=True, pady=(0, 12))
        tk.Label(notes_frame, text="Release notes", bg=GRAPHITE_CARD, fg=GRAPHITE_TEXT, font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 6))
        self.notes = tk.Text(
            notes_frame,
            wrap="word",
            height=12,
            bg=GRAPHITE_FIELD,
            fg=GRAPHITE_TEXT,
            insertbackground=GRAPHITE_TEXT,
            selectbackground=GRAPHITE_SUCCESS_DARK,
            selectforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=8,
            font=("Segoe UI", 9),
            state="disabled",
        )
        self.notes.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.set_notes("Checking GitHub releases...")

        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 8))
        self.status_label = tk.Label(outer, textvariable=self.status_var, bg=GRAPHITE_BG, fg=GRAPHITE_MUTED, anchor="w")
        self.status_label.pack(fill="x", pady=(0, 10))

        actions = ttk.Frame(outer)
        actions.pack(fill="x")
        self.check_button = self.make_button(actions, "Check Again", self.start_check)
        self.release_button = self.make_button(actions, "View Release", self.open_release)
        self.release_button.configure(state="disabled")
        self.install_button = self.make_button(actions, "Download and Install", self.start_download, update=True)
        self.install_button.configure(state="disabled")
        self.make_button(actions, "Close", self.destroy, right=True)

    def make_button(self, parent, text, command, update=False, right=False):
        background = GRAPHITE_SUCCESS_DARK if update else GRAPHITE_CARD_SOFT
        hover = GRAPHITE_SUCCESS if update else GRAPHITE_BORDER
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=background,
            fg="#ffffff" if update else GRAPHITE_TEXT,
            activebackground=hover,
            activeforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=14,
            pady=7,
            font=("Segoe UI", 9, "bold" if update else "normal"),
            cursor="hand2",
        )
        button.pack(side="right" if right else "left", padx=(8, 0) if right else (0, 8))
        button.bind("<Enter>", lambda event: button.configure(bg=hover) if str(button.cget("state")) != "disabled" else None, add="+")
        button.bind("<Leave>", lambda event: button.configure(bg=background), add="+")
        return button

    def set_notes(self, text):
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", str(text or "No release notes provided."))
        self.notes.configure(state="disabled")

    def set_busy(self, busy, status):
        self.busy = busy
        self.status_var.set(status)
        self.check_button.configure(state="disabled" if busy else "normal")
        self.install_button.configure(state="disabled" if busy or not self.available_update else "normal")
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def start_check(self):
        if self.busy:
            return
        self.available_update = None
        self.release_button.configure(state="disabled")
        self.latest_var.set("Checking...")
        self.set_notes("Checking GitHub releases...")
        self.set_busy(True, "Checking for updates...")
        threading.Thread(target=self.check_worker, daemon=True).start()

    def check_worker(self):
        try:
            self.events.put(("check_done", check_for_update(APP_VERSION)))
        except (UpdateError, OSError, ValueError) as exc:
            self.events.put(("error", f"Update check failed:\n\n{exc}"))

    def handle_check_done(self, update):
        self.available_update = update
        if update is None:
            self.latest_var.set(APP_VERSION)
            self.set_notes("Installed RaG PBO Tools version is current.")
            self.set_busy(False, "No update available.")
            return
        self.latest_var.set(update["version"])
        self.set_notes(update.get("notes") or "No release notes provided.")
        self.release_button.configure(state="normal" if update.get("release_url") else "disabled")
        self.set_busy(False, f"{update['name']} available.")

    def start_download(self):
        if self.busy or not self.available_update:
            return
        self.set_busy(True, f"Downloading {self.available_update['name']}...")
        threading.Thread(target=self.download_worker, args=(self.available_update,), daemon=True).start()

    def download_worker(self, update):
        try:
            self.events.put(("download_done", download_update(update)))
        except (UpdateError, OSError, ValueError) as exc:
            self.events.put(("error", f"Update download failed:\n\n{exc}"))

    def launch_installer(self, installer_path):
        self.set_busy(False, "Installer verified. Starting setup...")
        try:
            subprocess.Popen([str(installer_path), "/SP-", "/CLOSEAPPLICATIONS", "/NORESTART"])
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Could not start update installer:\n\n{exc}", parent=self)
            self.status_var.set("Could not start installer.")
            return
        self.after_idle(self.destroy)

    def open_release(self):
        if self.available_update and self.available_update.get("release_url"):
            webbrowser.open(self.available_update["release_url"])

    def poll_events(self):
        try:
            while True:
                event_type, payload = self.events.get_nowait()
                if event_type == "check_done":
                    self.handle_check_done(payload)
                elif event_type == "download_done":
                    self.launch_installer(payload)
                elif event_type == "error":
                    self.set_busy(False, "Update failed.")
                    messagebox.showerror(APP_TITLE, payload, parent=self)
        except queue.Empty:
            pass
        self.after(100, self.poll_events)


def main():
    RaGToolsUpdaterApp().mainloop()


if __name__ == "__main__":
    main()
