#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import traceback
import sys


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def show_startup_error(message: str) -> None:
    log_path = HERE / "bv_gui_error.log"
    log_path.write_text(message, encoding="utf-8")
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "bv2008 启动失败",
            f"窗口未能启动，错误已写入：\n{log_path}\n\n{message[-1200:]}",
        )
        root.destroy()
    except Exception:
        print(message)


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1].lower() == "cli":
        from bv2008_gui.cli import main as cli_main

        return cli_main(sys.argv[2:])
    try:
        from bv2008_gui.app import run

        run()
        return 0
    except Exception:
        show_startup_error(traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
