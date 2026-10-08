# core/pieces.py
"""
Piece management panel.

PiecesFrame  – a tk.Frame that contains a Treeview listing piece definitions
               (id, width, height, qty) with Add / Edit / Delete / Import buttons.
_PieceDialog – modal dialog for creating or editing one piece.
"""
import colorsys
import random
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import pandas as pd

# ── colour helpers ────────────────────────────────────────────────────────────

def _rand_color():
    """Return a bright, saturated hex colour suitable for labelling on dark backgrounds."""
    h = random.random()
    s = 0.55 + random.random() * 0.35   # 0.55 – 0.90
    v = 0.70 + random.random() * 0.25   # 0.70 – 0.95
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


def contrast_color(hex_color):
    """Return '#000000' or '#ffffff' for best readability on hex_color."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#000000" if lum > 0.55 else "#ffffff"


# ── PiecesFrame ───────────────────────────────────────────────────────────────

class PiecesFrame(tk.Frame):
    """
    Self-contained frame for managing piece definitions.

    on_change(pieces)  – optional callback invoked whenever the list changes.
    """

    def __init__(self, master, on_change=None, **kw):
        super().__init__(master, **kw)
        self._pieces: list[dict] = []
        self.on_change = on_change
        self._build()

    # ── build UI ──────────────────────────────────────────────────────────────

    def _build(self):
        BG  = self["bg"]
        BTN = dict(relief="flat", pady=5, padx=12, cursor="hand2",
                   font=("Segoe UI", 9))

        # Toolbar
        tb = tk.Frame(self, bg=BG)
        tb.pack(fill="x", pady=(0, 8))

        tk.Button(tb, text="＋ Add",         command=self._add,
                  bg="#4361ee", fg="white", **BTN).pack(side="left", padx=2)
        tk.Button(tb, text="✏ Edit",         command=self._edit,
                  bg="#4361ee", fg="white", **BTN).pack(side="left", padx=2)
        tk.Button(tb, text="✕ Delete",       command=self._delete,
                  bg="#4361ee", fg="white", **BTN).pack(side="left", padx=2)
        tk.Button(tb, text="📊 Import Excel", command=self._import_excel,
                  bg="#2d6a4f", fg="white", **BTN).pack(side="left", padx=(14, 2))
        tk.Button(tb, text="Clear All",      command=self._clear_all,
                  bg="#7f1d1d", fg="white", **BTN).pack(side="right", padx=2)

        # Treeview + scrollbar
        cols = ("id", "w_mm", "h_mm", "qty", "area")
        self.tree = ttk.Treeview(self, columns=cols, show="headings",
                                 selectmode="browse")

        headers = {
            "id":    ("ID",          80),
            "w_mm":  ("Width (mm)",  110),
            "h_mm":  ("Height (mm)", 110),
            "qty":   ("Qty",         60),
            "area":  ("Area (mm²)",  130),
        }
        for col, (label, width) in headers.items():
            self.tree.heading(col, text=label,
                              command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=width, anchor="center")

        scroll = ttk.Scrollbar(self, orient="vertical",
                               command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")

        self.tree.bind("<Double-1>", lambda _: self._edit())
        self.tree.bind("<Delete>",   lambda _: self._delete())

    # ── public API ────────────────────────────────────────────────────────────

    def get_pieces(self) -> list[dict]:
        return list(self._pieces)

    def set_pieces(self, pieces: list[dict]):
        self._pieces = []
        for p in pieces:
            entry = {
                "id":    str(p["id"]),
                "w_mm":  float(p["w_mm"]),
                "h_mm":  float(p["h_mm"]),
                "qty":   int(p.get("qty", 1)),
                "color": p.get("color") or _rand_color(),
            }
            self._pieces.append(entry)
        self._refresh()

    # ── internal helpers ──────────────────────────────────────────────────────

    def _refresh(self):
        self.tree.delete(*self.tree.get_children())
        for p in self._pieces:
            area = int(p["w_mm"] * p["h_mm"])
            self.tree.insert("", "end", iid=p["id"],
                             values=(p["id"],
                                     int(p["w_mm"]), int(p["h_mm"]),
                                     p["qty"],
                                     f"{area:,}"))
        if self.on_change:
            self.on_change(self._pieces)

    def _selected_piece(self):
        sel = self.tree.selection()
        if not sel:
            return None, None
        pid = sel[0]
        idx = next((i for i, p in enumerate(self._pieces) if p["id"] == pid), None)
        return pid, idx

    def _sort_by(self, col):
        reverse = getattr(self, f"_sort_{col}_rev", False)
        if col == "id":
            self._pieces.sort(key=lambda p: p["id"], reverse=reverse)
        elif col in ("w_mm", "h_mm", "qty"):
            self._pieces.sort(key=lambda p: p[col], reverse=reverse)
        elif col == "area":
            self._pieces.sort(key=lambda p: p["w_mm"] * p["h_mm"], reverse=reverse)
        setattr(self, f"_sort_{col}_rev", not reverse)
        self._refresh()

    def _id_exists(self, pid, exclude_idx=None):
        for i, p in enumerate(self._pieces):
            if p["id"] == pid and i != exclude_idx:
                return True
        return False

    # ── button commands ───────────────────────────────────────────────────────

    def _add(self):
        dlg = _PieceDialog(self, title="Add Piece")
        if not dlg.result:
            return
        d = dlg.result
        if self._id_exists(d["id"]):
            messagebox.showerror("Duplicate ID",
                                 f"A piece with ID '{d['id']}' already exists.")
            return
        d["color"] = _rand_color()
        self._pieces.append(d)
        self._refresh()

    def _edit(self):
        pid, idx = self._selected_piece()
        if idx is None:
            return
        dlg = _PieceDialog(self, title="Edit Piece", initial=self._pieces[idx])
        if not dlg.result:
            return
        d = dlg.result
        if self._id_exists(d["id"], exclude_idx=idx):
            messagebox.showerror("Duplicate ID",
                                 f"A piece with ID '{d['id']}' already exists.")
            return
        d["color"] = self._pieces[idx]["color"]   # keep existing colour
        self._pieces[idx] = d
        self._refresh()

    def _delete(self):
        pid, idx = self._selected_piece()
        if idx is None:
            return
        self._pieces.pop(idx)
        self._refresh()

    def _clear_all(self):
        if not self._pieces:
            return
        if messagebox.askyesno("Clear All", "Remove all piece definitions?"):
            self._pieces.clear()
            self._refresh()

    def _import_excel(self):
        path = filedialog.askopenfilename(
            title="Select Excel file",
            filetypes=[("Excel files", "*.xlsx *.xls")]
        )
        if not path:
            return
        try:
            df = pd.read_excel(path)
        except Exception as e:
            messagebox.showerror("Import Error", f"Could not read file:\n{e}")
            return

        df.columns = [str(c).strip().lower() for c in df.columns]
        required = {"id", "width_mm", "height_mm"}
        if not required.issubset(df.columns):
            messagebox.showerror("Import Error",
                                 f"File must contain columns: {sorted(required)}")
            return

        added = skipped = 0
        for _, row in df.iterrows():
            try:
                pid = str(row["id"]).strip()
                w   = float(row["width_mm"])
                h   = float(row["height_mm"])
                qty = int(row["qty"]) if "qty" in df.columns else 1
            except (ValueError, TypeError):
                skipped += 1
                continue

            if pid in ("", "nan") or w <= 0 or h <= 0 or qty < 1:
                skipped += 1
                continue

            existing_idx = next(
                (i for i, p in enumerate(self._pieces) if p["id"] == pid), None)
            entry = {"id": pid, "w_mm": w, "h_mm": h, "qty": qty,
                     "color": _rand_color()}
            if existing_idx is not None:
                entry["color"] = self._pieces[existing_idx]["color"]
                self._pieces[existing_idx] = entry
            else:
                self._pieces.append(entry)
            added += 1

        self._refresh()
        msg = f"Imported {added} piece(s)."
        if skipped:
            msg += f"\n{skipped} row(s) skipped (invalid data)."
        messagebox.showinfo("Import Complete", msg)


# ── _PieceDialog ──────────────────────────────────────────────────────────────

class _PieceDialog(tk.Toplevel):
    """Modal dialog for adding or editing a single piece definition."""

    def __init__(self, parent, title="Piece", initial=None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.grab_set()
        self.result = None

        BG  = "#1e1e2e"
        FG  = "#cdd6f4"
        ENT = dict(bg="#313244", fg=FG, insertbackground=FG,
                   relief="flat", width=18, font=("Segoe UI", 10))
        self.configure(bg=BG)

        def row(label, var, r):
            tk.Label(self, text=label, bg=BG, fg=FG,
                     font=("Segoe UI", 10), anchor="w"
                     ).grid(row=r, column=0, sticky="w", padx=16, pady=6)
            tk.Entry(self, textvariable=var, **ENT
                     ).grid(row=r, column=1, padx=16, pady=6)

        self._var_id  = tk.StringVar(value=initial["id"]           if initial else "")
        self._var_w   = tk.StringVar(value=f"{initial['w_mm']:.0f}" if initial else "")
        self._var_h   = tk.StringVar(value=f"{initial['h_mm']:.0f}" if initial else "")
        self._var_qty = tk.StringVar(value=str(initial.get("qty", 1)) if initial else "1")

        row("Piece ID:",     self._var_id,  0)
        row("Width (mm):",   self._var_w,   1)
        row("Height (mm):",  self._var_h,   2)
        row("Quantity:",     self._var_qty, 3)

        btns = tk.Frame(self, bg=BG)
        btns.grid(row=4, column=0, columnspan=2, pady=14)
        tk.Button(btns, text="OK",     command=self._ok,
                  bg="#4361ee", fg="white", relief="flat",
                  padx=24, pady=6).pack(side="left", padx=6)
        tk.Button(btns, text="Cancel", command=self.destroy,
                  bg="#555", fg="white", relief="flat",
                  padx=24, pady=6).pack(side="left", padx=6)

        self.bind("<Return>", lambda _: self._ok())
        self.bind("<Escape>", lambda _: self.destroy())
        self.wait_window()

    def _ok(self):
        pid = self._var_id.get().strip()
        if not pid:
            messagebox.showerror("Validation", "ID cannot be empty.", parent=self)
            return
        try:
            w   = float(self._var_w.get())
            h   = float(self._var_h.get())
            qty = int(self._var_qty.get())
            assert w > 0 and h > 0 and qty >= 1
        except Exception:
            messagebox.showerror(
                "Validation",
                "Width and height must be positive numbers.\nQty must be ≥ 1.",
                parent=self
            )
            return
        self.result = {"id": pid, "w_mm": w, "h_mm": h, "qty": qty}
        self.destroy()
