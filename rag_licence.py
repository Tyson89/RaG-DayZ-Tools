import tkinter as tk


LICENCE_NAME = "Freeware - Proprietary / All Rights Reserved"
LICENCE_TEXT = """RaG DayZ Tools License

Copyright (c) 2026 RaG Tyson

Freeware - Proprietary / All Rights Reserved

This software is freeware.
You may use it free of charge for personal and authorized DayZ modding purposes.

All rights reserved.

You may not sell, rent, sublicense, reupload, redistribute, modify, decompile,
reverse engineer, publish, or include this software or its source code in another
project without written permission from the author.

This software is provided "as is", without warranty of any kind, express or implied.

The author is not responsible for damaged files, lost data, invalid PBOs, failed
builds, server issues, broken signatures, leaked keys, or any other damage caused
by the use or misuse of this software.

Important:
Never share your .biprivatekey.
Only distribute the matching .bikey.
"""

GRAPHITE_BG = "#24262b"
GRAPHITE_CARD_SOFT = "#383c44"
GRAPHITE_FIELD = "#292c32"
GRAPHITE_BORDER = "#4a505b"
GRAPHITE_TEXT = "#f1f1f1"
GRAPHITE_MUTED = "#b8bec8"
GRAPHITE_ACCENT = "#a74747"
GRAPHITE_ACCENT_DARK = "#7f3434"


def show_licence(parent):
    window = tk.Toplevel(parent)
    window.title("Licence")
    window.geometry("720x560")
    window.minsize(600, 420)
    window.configure(bg=GRAPHITE_BG)
    window.transient(parent)
    window.grab_set()

    container = tk.Frame(window, bg=GRAPHITE_BG, padx=18, pady=18)
    container.pack(fill="both", expand=True)
    tk.Label(container, text="Licence", bg=GRAPHITE_BG, fg=GRAPHITE_TEXT, font=("Segoe UI", 20, "bold")).pack(anchor="w")
    tk.Label(container, text=LICENCE_NAME, bg=GRAPHITE_BG, fg=GRAPHITE_MUTED, font=("Segoe UI", 10)).pack(anchor="w", pady=(6, 14))

    body = tk.Frame(container, bg=GRAPHITE_BG)
    body.pack(fill="both", expand=True, pady=(0, 12))
    text = tk.Text(
        body,
        wrap="word",
        bg=GRAPHITE_FIELD,
        fg=GRAPHITE_TEXT,
        insertbackground=GRAPHITE_TEXT,
        selectbackground=GRAPHITE_ACCENT_DARK,
        selectforeground="#ffffff",
        relief="flat",
        borderwidth=0,
        highlightthickness=1,
        highlightbackground=GRAPHITE_BORDER,
        highlightcolor=GRAPHITE_ACCENT,
        font=("Segoe UI", 10),
        padx=10,
        pady=10,
    )
    text.pack(side="left", fill="both", expand=True)
    text.insert("1.0", LICENCE_TEXT)
    text.configure(state="disabled")
    scrollbar = tk.Scrollbar(body, command=text.yview)
    scrollbar.pack(side="right", fill="y")
    text.configure(yscrollcommand=scrollbar.set)

    close_button = tk.Button(
        container,
        text="Close",
        command=window.destroy,
        bg=GRAPHITE_CARD_SOFT,
        fg=GRAPHITE_TEXT,
        activebackground=GRAPHITE_BORDER,
        activeforeground=GRAPHITE_TEXT,
        relief="flat",
        borderwidth=0,
        padx=14,
        pady=8,
        font=("Segoe UI", 10),
        cursor="hand2",
    )
    close_button.pack(anchor="e")


def make_licence_button(parent, owner):
    button = tk.Button(
        parent,
        text="Licence",
        command=lambda: show_licence(owner),
        bg=GRAPHITE_CARD_SOFT,
        fg=GRAPHITE_TEXT,
        activebackground=GRAPHITE_BORDER,
        activeforeground=GRAPHITE_TEXT,
        relief="flat",
        borderwidth=0,
        padx=12,
        pady=6,
        font=("Segoe UI", 9),
        cursor="hand2",
    )
    button.pack(side="right", padx=(0, 8))
    button.bind("<Enter>", lambda event: button.configure(bg=GRAPHITE_BORDER), add="+")
    button.bind("<Leave>", lambda event: button.configure(bg=GRAPHITE_CARD_SOFT), add="+")
    return button
