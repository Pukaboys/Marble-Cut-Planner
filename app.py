# app.py
"""
Marble Slab Planner — main entry point.

Three-tab workflow:
  ①  Slab    – load a photo and calibrate (perspective-correct it)
  ②  Pieces  – define marble pieces (id, width, height, quantity)
  ③  Layout  – auto-place pieces on the slab and fine-tune manually
"""
import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

from core import calibration
from core.packing import expand_pieces
from core.pieces import PiecesFrame
from core.layout import LayoutCanvas

# ── colour palette ─────────────────────────────────────────────────────────
C_BG   = "#1e1e2e"
C_BAR  = "#181825"
C_CARD = "#313244"
C_FG   = "#cdd6f4"
C_MUTE = "#6c7086"
C_BLUE = "#4361ee"
C_GRN  = "#2d6a4f"
C_RED  = "#7f1d1d"


# Default projects root — sibling "projects/" folder next to app.py
_APP_DIR      = os.path.dirname(os.path.abspath(__file__))
_PROJECTS_DIR = os.path.join(_APP_DIR, "projects")
_CONFIG_FILE  = os.path.join(_APP_DIR, ".last_project")


class MarblePlannerApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Marble Slab Planner")
        self.geometry("1340x840")
        self.minsize(960, 640)
        self.configure(bg=C_BG)

        self.project_path   = None
        self.calib_data     = None
        self._layout_canvas = None
        self._slab_photo    = None   # prevent GC

        os.makedirs(_PROJECTS_DIR, exist_ok=True)

        self._configure_styles()
        self._build_toolbar()
        self._build_notebook()
        self._build_statusbar()

        # Auto-load last project
        self._load_last_project()

    # ─────────────────────────────────────────────────────────────────────────
    # Styles
    # ─────────────────────────────────────────────────────────────────────────

    def _configure_styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")

        s.configure("TNotebook",      background=C_BG,   borderwidth=0)
        s.configure("TNotebook.Tab",  background=C_CARD, foreground=C_MUTE,
                    padding=[20, 8],  font=("Segoe UI", 10))
        s.map("TNotebook.Tab",
              background=[("selected", C_BLUE)],
              foreground=[("selected", "white")])

        s.configure("Treeview",
                    background=C_CARD, foreground=C_FG,
                    fieldbackground=C_CARD, borderwidth=0,
                    rowheight=28, font=("Consolas", 9))
        s.configure("Treeview.Heading",
                    background=C_BAR, foreground=C_FG,
                    font=("Segoe UI", 9, "bold"))
        s.map("Treeview",
              background=[("selected", C_BLUE)],
              foreground=[("selected", "white")])

        s.configure("Vertical.TScrollbar",
                    background=C_CARD, troughcolor=C_BAR,
                    borderwidth=0, arrowsize=12)

    # ─────────────────────────────────────────────────────────────────────────
    # Toolbar
    # ─────────────────────────────────────────────────────────────────────────

    def _build_toolbar(self):
        tb = tk.Frame(self, bg=C_BAR, height=52)
        tb.pack(side="top", fill="x")
        tb.pack_propagate(False)

        tk.Label(tb, text="🪨  Marble Slab Planner",
                 bg=C_BAR, fg=C_FG,
                 font=("Segoe UI", 13, "bold")).pack(side="left", padx=18)

        sep = tk.Frame(tb, bg="#333355", width=2)
        sep.pack(side="left", fill="y", pady=10, padx=8)

        BTN = dict(relief="flat", pady=7, padx=14, cursor="hand2",
                   font=("Segoe UI", 9))
        tk.Button(tb, text="New Project",  command=self._new_project,
                  bg=C_BLUE, fg="white", **BTN).pack(side="left", padx=3)
        tk.Button(tb, text="Open Project", command=self._open_project,
                  bg=C_BLUE, fg="white", **BTN).pack(side="left", padx=3)
        tk.Button(tb, text="Save Project", command=self._save_project,
                  bg=C_GRN,  fg="white", **BTN).pack(side="left", padx=3)
        tk.Button(tb, text="Exit",         command=self.quit,
                  bg=C_RED,  fg="white", **BTN).pack(side="right", padx=14)

        self._proj_label = tk.Label(tb, text="No project loaded",
                                    bg=C_BAR, fg=C_MUTE,
                                    font=("Segoe UI", 9))
        self._proj_label.pack(side="right", padx=10)

    # ─────────────────────────────────────────────────────────────────────────
    # Notebook (3 tabs)
    # ─────────────────────────────────────────────────────────────────────────

    def _build_notebook(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)

        # ── Tab 1: Slab ───────────────────────────────────────────────────────
        self._tab_slab = tk.Frame(self.nb, bg=C_BG)
        self.nb.add(self._tab_slab, text="①  Slab")
        self._build_slab_tab()

        # ── Tab 2: Pieces ─────────────────────────────────────────────────────
        self._tab_pieces = tk.Frame(self.nb, bg=C_BG)
        self.nb.add(self._tab_pieces, text="②  Pieces")
        self._build_pieces_tab()

        # ── Tab 3: Layout ─────────────────────────────────────────────────────
        self._tab_layout = tk.Frame(self.nb, bg=C_BG)
        self.nb.add(self._tab_layout, text="③  Layout")
        self._build_layout_tab()

        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_change)

    # ── Slab tab ──────────────────────────────────────────────────────────────

    def _build_slab_tab(self):
        ctrl = tk.Frame(self._tab_slab, bg=C_BAR)
        ctrl.pack(fill="x", padx=0, pady=0)

        BTN = dict(relief="flat", pady=9, padx=16, cursor="hand2",
                   font=("Segoe UI", 10))
        tk.Button(ctrl, text="📷  Load & Calibrate Slab",
                  command=self._calibrate,
                  bg=C_BLUE, fg="white", **BTN).pack(side="left", padx=(16, 8), pady=8)

        self._slab_info = tk.Label(ctrl, text="No slab calibrated yet.",
                                   bg=C_BAR, fg=C_MUTE, font=("Segoe UI", 9))
        self._slab_info.pack(side="left", padx=8)

        self._slab_canvas = tk.Canvas(self._tab_slab, bg="#0d0d1a",
                                      highlightthickness=0)
        self._slab_canvas.pack(fill="both", expand=True, padx=16, pady=16)

    # ── Pieces tab ────────────────────────────────────────────────────────────

    def _build_pieces_tab(self):
        tk.Label(self._tab_pieces,
                 text="Define the pieces you need to cut from the slab.",
                 bg=C_BG, fg=C_MUTE,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(12, 4))

        self._pieces_frame = PiecesFrame(
            self._tab_pieces,
            on_change=self._on_pieces_change,
            bg=C_BG
        )
        self._pieces_frame.pack(fill="both", expand=True, padx=16, pady=(0, 4))

        self._pieces_summary = tk.Label(self._tab_pieces, text="",
                                        bg=C_BG, fg=C_MUTE,
                                        font=("Segoe UI", 9), anchor="w")
        self._pieces_summary.pack(fill="x", padx=16, pady=(0, 10))

    # ── Layout tab ────────────────────────────────────────────────────────────

    def _build_layout_tab(self):
        # Toolbar strip at the top
        self._layout_bar = tk.Frame(self._tab_layout, bg=C_BAR)
        # (packed when canvas is built)

        self._layout_stats_lbl = tk.Label(self._layout_bar, text="",
                                          bg=C_BAR, fg=C_MUTE,
                                          font=("Segoe UI", 9))
        self._layout_stats_lbl.pack(side="left", padx=14, pady=6)

        BTN = dict(relief="flat", pady=6, padx=14, cursor="hand2",
                   font=("Segoe UI", 9))
        tk.Button(self._layout_bar, text="⟳  Rebuild Layout",
                  command=self._rebuild_layout,
                  bg="#555566", fg="white", **BTN).pack(side="right", padx=4, pady=6)

        self._layout_margin_var = tk.DoubleVar(value=2.0)
        tk.Label(self._layout_bar, text="Gap (mm):",
                 bg=C_BAR, fg=C_MUTE,
                 font=("Segoe UI", 9)).pack(side="right", padx=(8, 0), pady=6)
        tk.Spinbox(self._layout_bar,
                   textvariable=self._layout_margin_var,
                   from_=0.5, to=20.0, increment=0.5, width=5,
                   bg=C_CARD, fg=C_FG, insertbackground=C_FG,
                   buttonbackground=C_CARD, relief="flat",
                   font=("Segoe UI", 9)
                   ).pack(side="right", pady=6)

        # Placeholder shown before layout is built
        self._layout_placeholder = tk.Label(
            self._tab_layout,
            text="Switch to tab ①  to calibrate a slab,\n"
                 "then tab ②  to define pieces,\n"
                 "then come back here.",
            bg=C_BG, fg=C_MUTE,
            font=("Segoe UI", 12),
            justify="center"
        )
        self._layout_placeholder.pack(expand=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Status bar
    # ─────────────────────────────────────────────────────────────────────────

    def _build_statusbar(self):
        sb = tk.Frame(self, bg=C_BAR, height=26)
        sb.pack(side="bottom", fill="x")
        sb.pack_propagate(False)

        self._status = tk.Label(sb, text="Ready.",
                                bg=C_BAR, fg=C_MUTE,
                                font=("Segoe UI", 9), anchor="w")
        self._status.pack(side="left", padx=12)

    def _set_status(self, msg: str):
        self._status.config(text=msg)

    # ─────────────────────────────────────────────────────────────────────────
    # Project management
    # ─────────────────────────────────────────────────────────────────────────

    def _new_project(self):
        name = simpledialog.askstring(
            "New Project", "Project name:", parent=self
        )
        if not name:
            return
        name = name.strip()
        if not name:
            return
        path = os.path.join(_PROJECTS_DIR, name)
        if os.path.exists(path):
            if not messagebox.askyesno(
                "Exists", f"'{name}' already exists. Open it?", parent=self
            ):
                return
        else:
            os.makedirs(path, exist_ok=True)
        self._set_project(path)
        self._load_project_files(path)

    def _open_project(self):
        path = filedialog.askdirectory(
            title="Select project folder",
            initialdir=_PROJECTS_DIR
        )
        if not path:
            return
        self._set_project(path)
        self._load_project_files(path)

    def _save_project(self):
        if not self.project_path:
            messagebox.showwarning("No Project", "Create or open a project first.")
            return
        # Save piece definitions
        pieces_path = os.path.join(self.project_path, "pieces.json")
        try:
            with open(pieces_path, "w", encoding="utf-8") as f:
                json.dump(self._pieces_frame.get_pieces(), f, indent=2)
        except Exception as e:
            messagebox.showerror("Save Error", f"Could not save pieces:\n{e}")
            return
        # Save layout (if canvas exists)
        if self._layout_canvas:
            self._layout_canvas.save_layout()
        else:
            self._set_status("Project saved.")
            messagebox.showinfo("Saved", f"Project saved at:\n{self.project_path}")

    def _set_project(self, path: str):
        self.project_path = path
        name = os.path.basename(path)
        self._proj_label.config(text=f"📁  {name}")
        self._set_status(f"Project: {path}")
        try:
            with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
                f.write(path)
        except Exception:
            pass

    def _load_last_project(self):
        try:
            with open(_CONFIG_FILE, encoding="utf-8") as f:
                path = f.read().strip()
            if path and os.path.isdir(path):
                self._set_project(path)
                self._load_project_files(path)
        except Exception:
            pass

    def _load_project_files(self, path: str):
        # Calibration
        calib_path = os.path.join(path, "calibration.json")
        if os.path.exists(calib_path):
            try:
                with open(calib_path, encoding="utf-8") as f:
                    self.calib_data = json.load(f)
                self._refresh_slab_preview()
                self._set_status("Calibration data loaded.")
            except Exception as e:
                messagebox.showerror("Load Error",
                                     f"Could not load calibration:\n{e}")

        # Pieces
        pieces_path = os.path.join(path, "pieces.json")
        if os.path.exists(pieces_path):
            try:
                with open(pieces_path, encoding="utf-8") as f:
                    self._pieces_frame.set_pieces(json.load(f))
            except Exception:
                pass   # non-critical

        # If only layout.json exists (old format) try to load pieces from it
        elif os.path.exists(os.path.join(path, "layout.json")):
            try:
                with open(os.path.join(path, "layout.json"), encoding="utf-8") as f:
                    data = json.load(f)
                if "pieces" in data:
                    self._pieces_frame.set_pieces(data["pieces"])
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # Slab tab callbacks
    # ─────────────────────────────────────────────────────────────────────────

    def _calibrate(self):
        if not self.project_path:
            messagebox.showwarning("No Project",
                                   "Create or open a project first.")
            return
        ok = calibration.run_calibration(self.project_path, parent=self)
        if not ok:
            return
        calib_path = os.path.join(self.project_path, "calibration.json")
        try:
            with open(calib_path, encoding="utf-8") as f:
                self.calib_data = json.load(f)
        except Exception as e:
            messagebox.showerror("Error", f"Could not load calibration:\n{e}")
            return
        self._refresh_slab_preview()
        # Invalidate layout canvas so it's rebuilt with new calibration
        self._destroy_layout_canvas()
        self._set_status("Slab calibrated successfully.")

    def _refresh_slab_preview(self):
        if not self.project_path:
            return
        img_path = os.path.join(self.project_path, "rectified_slab.png")
        if not os.path.exists(img_path):
            return
        try:
            img = Image.open(img_path)
        except Exception:
            return

        self._slab_canvas.update_idletasks()
        cw = max(self._slab_canvas.winfo_width(),  900)
        ch = max(self._slab_canvas.winfo_height(), 560)
        img.thumbnail((cw - 32, ch - 32), Image.LANCZOS)
        self._slab_photo = ImageTk.PhotoImage(img)
        self._slab_canvas.delete("all")
        self._slab_canvas.create_image(cw // 2, ch // 2,
                                       image=self._slab_photo)

        if self.calib_data:
            rm  = self.calib_data.get("real_mm", {})
            w   = rm.get("w", 0)
            h   = rm.get("h", 0)
            px  = self.calib_data.get("px_per_mm", 0)
            self._slab_info.config(
                text=f"Slab: {w:.0f} × {h:.0f} mm   |   "
                     f"Scale: {px:.4f} px/mm"
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Pieces tab callbacks
    # ─────────────────────────────────────────────────────────────────────────

    def _on_pieces_change(self, pieces):
        qty_total  = sum(p.get("qty", 1) for p in pieces)
        area_total = sum(p["w_mm"] * p["h_mm"] * p.get("qty", 1) for p in pieces)
        self._pieces_summary.config(
            text=f"Total pieces: {qty_total}   |   "
                 f"Total area: {area_total:,.0f} mm²"
        )
        self._set_status(f"{qty_total} piece(s) defined.")
        # Invalidate layout so it reflects the updated piece list
        self._destroy_layout_canvas()

    # ─────────────────────────────────────────────────────────────────────────
    # Layout tab
    # ─────────────────────────────────────────────────────────────────────────

    def _on_tab_change(self, _event):
        if self.nb.index(self.nb.select()) == 2:
            self._ensure_layout_canvas()

    def _ensure_layout_canvas(self):
        if self._layout_canvas:
            return   # already built
        if not self._can_open_layout():
            return
        self._build_layout_canvas()

    def _rebuild_layout(self):
        """Destroy and recreate the layout canvas (e.g. after changing pieces)."""
        if not self._can_open_layout():
            return
        self._destroy_layout_canvas()
        self._build_layout_canvas()

    def _can_open_layout(self) -> bool:
        if not self.project_path:
            messagebox.showwarning("No Project",
                                   "Create or open a project first.")
            self.nb.select(0)
            return False
        if not self.calib_data:
            messagebox.showwarning("No Slab",
                                   "Calibrate the slab first (tab ①).")
            self.nb.select(0)
            return False
        img_path = os.path.join(self.project_path, "rectified_slab.png")
        if not os.path.exists(img_path):
            messagebox.showwarning("No Slab Image",
                                   "rectified_slab.png not found.\n"
                                   "Please calibrate the slab first.")
            self.nb.select(0)
            return False
        if not self._pieces_frame.get_pieces():
            messagebox.showwarning("No Pieces",
                                   "Define at least one piece in tab ②.")
            self.nb.select(1)
            return False
        return True

    def _build_layout_canvas(self):
        self._layout_placeholder.pack_forget()
        self._layout_bar.pack(fill="x")

        rm        = self.calib_data["real_mm"]
        pieces    = expand_pieces(self._pieces_frame.get_pieces())
        margin    = self._layout_margin_var.get()
        px_per_mm = self.calib_data["px_per_mm"]

        # Convert polygon from full-res pixel coords to mm (relative to crop)
        polygon_mm = None
        poly_px    = self.calib_data.get("polygon_px")
        crop_orig  = self.calib_data.get("crop_origin_px", [0, 0])
        if poly_px and len(poly_px) >= 3:
            polygon_mm = [
                ((px - crop_orig[0]) / px_per_mm,
                 (py - crop_orig[1]) / px_per_mm)
                for px, py in poly_px
            ]

        self._layout_canvas = LayoutCanvas(
            self._tab_layout,
            project_path=self.project_path,
            pieces=pieces,
            px_per_mm=px_per_mm,
            slab_w_mm=rm["w"],
            slab_h_mm=rm["h"],
            margin_mm=margin,
            slab_polygon_mm=polygon_mm,
            on_stats=self._on_layout_stats,
            bg=C_BG,
        )
        self._layout_canvas.pack(fill="both", expand=True)

    def _destroy_layout_canvas(self):
        if self._layout_canvas:
            self._layout_canvas.destroy()
            self._layout_canvas = None
            self._layout_bar.pack_forget()
            self._layout_placeholder.pack(expand=True)
            self._layout_stats_lbl.config(text="")

    def _on_layout_stats(self, placed: int, total: int, coverage: float):
        self._layout_stats_lbl.config(
            text=f"Placed: {placed} / {total}   |   "
                 f"Coverage: {coverage:.1f}%"
        )
        self._set_status(
            f"Layout: {placed}/{total} pieces placed — "
            f"{coverage:.1f}% slab coverage."
        )


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = MarblePlannerApp()
    app.mainloop()
