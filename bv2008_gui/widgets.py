from __future__ import annotations

from typing import Any

import tkinter as tk
from tkinter import ttk


def _px(widget: tk.Misc, value: int | float) -> int:
    scale = float(getattr(widget.winfo_toplevel(), "ui_scale", 1.0))
    return max(1, int(round(float(value) * scale)))


class StatusBar(ttk.Frame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, style="Status.TFrame", padding=(_px(master, 18), _px(master, 8)))
        self.var = tk.StringVar(value="就绪")
        self.dot = ttk.Label(self, text="●", style="StatusDot.Ready.TLabel")
        self.dot.pack(side="left", padx=(0, _px(self, 8)))
        ttk.Label(self, textvariable=self.var, anchor="w", style="Status.TLabel").pack(side="left", fill="x", expand=True)

    def set(self, text: str, state: str | None = None) -> None:
        self.var.set(text)
        self.dot.configure(style=f"StatusDot.{(state or 'ready').title()}.TLabel")


class ScrolledText(ttk.Frame):
    def __init__(self, master: tk.Misc, height: int = 10) -> None:
        super().__init__(master, style="Panel.TFrame")
        self.text = tk.Text(
            self,
            height=height,
            wrap="word",
            relief="flat",
            borderwidth=0,
            padx=_px(self, 14),
            pady=_px(self, 12),
            font=("Consolas", 9),
            background="#111827",
            foreground="#E5E7EB",
            insertbackground="#E5E7EB",
            selectbackground="#374151",
            selectforeground="#FFFFFF",
        )
        ybar = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=ybar.set)
        self.text.pack(side="left", fill="both", expand=True)
        ybar.pack(side="right", fill="y")
        self.text.configure(state="disabled")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def write_line(self, message: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", message + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")


class TreeFrame(ttk.Frame):
    def __init__(self, master: tk.Misc, columns: tuple[str, ...], height: int = 14) -> None:
        super().__init__(master, style="Table.TFrame", padding=1)
        self._row_count = 0
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=height, style="Data.Treeview")
        ybar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        xbar = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=max(_px(self, 92), _px(self, len(col) * 22)), minwidth=_px(self, 72), stretch=True, anchor="w")
        self.tree.tag_configure("odd", background="#FFFFFF")
        self.tree.tag_configure("even", background="#F7FAFD")
        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

    def clear(self) -> None:
        self.tree.delete(*self.tree.get_children(""))
        self._row_count = 0

    def add_row(self, values: tuple[Any, ...], iid: str | None = None) -> str:
        tag = "even" if self._row_count % 2 else "odd"
        self._row_count += 1
        return self.tree.insert("", "end", iid=iid if iid else None, values=values, tags=(tag,))

    def update_cell(self, iid: str, column: str, value: Any) -> None:
        columns = list(self.tree["columns"])
        if column not in columns:
            return
        values = list(self.tree.item(iid, "values"))
        while len(values) < len(columns):
            values.append("")
        values[columns.index(column)] = value
        self.tree.item(iid, values=values)
