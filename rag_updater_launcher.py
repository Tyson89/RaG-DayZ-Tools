from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from tkinter import messagebox


UPDATER_EXE = "RaG_Tools_Updater.exe"
UPDATER_FOLDER = "RaG_Tools_Updater"


def updater_commands(executable=None, module_file=None, frozen=None):
    executable_path = Path(executable or sys.executable).resolve()
    module_path = Path(module_file or __file__).resolve()
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    commands = []

    if is_frozen:
        tool_dir = executable_path.parent
        install_dir = tool_dir.parent
        commands.extend(
            [
                [str(install_dir / UPDATER_FOLDER / UPDATER_EXE)],
                [str(tool_dir / UPDATER_EXE)],
            ]
        )
    else:
        source_dir = module_path.parent
        commands.extend(
            [
                [str(source_dir / "dist" / UPDATER_FOLDER / UPDATER_EXE)],
                [str(executable_path), str(source_dir / "rag_tools_updater.py")],
            ]
        )

    return commands


def launch_updater(parent=None, app_title="RaG PBO Tools"):
    for command in updater_commands():
        target = Path(command[-1] if len(command) > 1 else command[0])
        if not target.is_file():
            continue
        try:
            return subprocess.Popen(command, cwd=str(target.parent))
        except OSError as exc:
            messagebox.showerror(app_title, f"Could not start RaG Tools Updater:\n\n{exc}", parent=parent)
            return None

    messagebox.showerror(
        app_title,
        "RaG Tools Updater is missing.\n\nReinstall RaG PBO Tools.",
        parent=parent,
    )
    return None
