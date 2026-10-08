# core/layout.py
"""
Interactive layout canvas.

LayoutCanvas  – a tk.Frame with:
  • a piece palette sidebar (unplaced pieces)
  • a canvas showing the rectified slab image
  • ghost-preview placement on mouse move
  • drag-to-reposition placed pieces
  • AABB collision detection with configurable margin
  • ⚡ Auto Layout  – calls packing.auto_pack
  • 💾 Save Layout  – writes layout.json + layout_preview.png
"""
import json
import math
import os
import tkinter as tk
from tkinter import messagebox

from PIL import Image, ImageDraw, ImageTk

from .packing import auto_pack
from .pieces import contrast_color

# Canvas item tags
_TAG_PIECE  = "piece"
_TAG_PREFIX = "pid:"
_TAG_BG     = "bg"

_ROT_STEP_DEG = 2.0   # degrees rotated per tick while R is held
_ROT_INTERVAL = 40    # ms between ticks (~25°/s)


class LayoutCanvas(tk.Frame):

    def __init__(self, master, project_path, pieces, px_per_mm,
                 slab_w_mm, slab_h_mm, margin_mm=2.0,
                 slab_polygon_mm=None, on_stats=None, **kw):
        """
        Parameters
        ----------
        pieces          : list of individual piece dicts (already expanded)
        px_per_mm       : calibration scale
        slab_w/h_mm     : real slab bounding-box dimensions
        margin_mm       : min gap between pieces (saw blade kerf)
        slab_polygon_mm : list of (x_mm, y_mm) tuples tracing the slab outline,
                          or None for a full-rectangle slab
        on_stats        : callback(placed, total, coverage_pct)
        """
        super().__init__(master, **kw)
        self.project_path    = project_path
        self.true_px_per_mm  = float(px_per_mm)
        self.slab_w_mm       = float(slab_w_mm)
        self.slab_h_mm       = float(slab_h_mm)
        self.margin_mm       = float(margin_mm)
        self.slab_polygon_mm = slab_polygon_mm
        self.on_stats        = on_stats

        # Pieces are stored with CENTER position (cx_mm, cy_mm) + angle.
        # Incoming dicts may use old top-left x_mm/y_mm format.
        self.pieces = []
        for p in pieces:
            w = float(p["w_mm"])
            h = float(p["h_mm"])
            if "cx_mm" in p:
                cx = float(p["cx_mm"])
                cy = float(p["cy_mm"])
            else:
                cx = float(p.get("x_mm", 0.0)) + w / 2
                cy = float(p.get("y_mm", 0.0)) + h / 2
            self.pieces.append({
                "id":     str(p["id"]),
                "w_mm":   w,
                "h_mm":   h,
                "cx_mm":  cx,
                "cy_mm":  cy,
                "angle":  float(p.get("angle", 0.0)),
                "color":  p.get("color", "#4cc9f0"),
                "placed": bool(p.get("placed", False)),
            })

        # Interaction state
        self.selected_piece = None
        self.drag_offset    = (0, 0)   # (dx, dy) from click to piece center
        self.armed_piece    = None
        self.ghost_item     = None
        self._mouse_x       = 0
        self._mouse_y       = 0
        self._rotate_job    = None     # after() id for continuous rotation

        # Image refs (kept to prevent GC)
        self.bg_img_full = None
        self._tk_bg      = None

        self._build_ui()
        self._load_bg()

    # ─────────────────────────────────────────────────────────────────────────
    # UI construction
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        SIDE = "#13131f"
        BTN  = dict(relief="flat", pady=5, cursor="hand2",
                    font=("Segoe UI", 9))

        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # ── Left sidebar ──────────────────────────────────────────────────────
        sidebar = tk.Frame(self, bg=SIDE, width=190)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        tk.Label(sidebar, text="Unplaced Pieces", bg=SIDE, fg="#cdd6f4",
                 font=("Segoe UI", 10, "bold")).pack(fill="x", padx=10,
                                                      pady=(12, 4))

        self.listbox = tk.Listbox(
            sidebar, bg="#1e1e2e", fg="#cdd6f4",
            selectbackground="#4361ee", selectforeground="white",
            highlightthickness=0, relief="flat",
            font=("Consolas", 9), activestyle="none"
        )
        self.listbox.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        tk.Button(sidebar, text="▶  Place Selected",
                  command=self._arm_selected,
                  bg="#4361ee", fg="white", **BTN).pack(fill="x", padx=8, pady=2)
        tk.Button(sidebar, text="⚡  Auto Layout",
                  command=self.do_auto_layout,
                  bg="#06d6a0", fg="#13131f", **BTN).pack(fill="x", padx=8, pady=2)
        tk.Button(sidebar, text="🔄  Clear Layout",
                  command=self.clear_layout,
                  bg="#555566", fg="white", **BTN).pack(fill="x", padx=8, pady=2)
        tk.Button(sidebar, text="💾  Save Layout",
                  command=self.save_layout,
                  bg="#2d6a4f", fg="white", **BTN).pack(fill="x", padx=8, pady=(8, 4))

        tk.Label(sidebar, text="Hold R — rotate  |  Esc — cancel",
                 bg=SIDE, fg="#555566",
                 font=("Segoe UI", 8)).pack(padx=8, pady=(4, 10))

        # ── Canvas area ───────────────────────────────────────────────────────
        canvas_frame = tk.Frame(self, bg=self["bg"])
        canvas_frame.grid(row=0, column=1, sticky="nsew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(canvas_frame, bg="#1a1a2e",
                                highlightthickness=0, cursor="crosshair")
        self.canvas.grid(row=0, column=0, sticky="nsew")

        # Bindings
        self.canvas.bind("<Button-1>",        self._on_click)
        self.canvas.bind("<B1-Motion>",       self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>",          self._on_motion)
        self.canvas.bind("<Enter>",           lambda _: self.canvas.focus_set())
        self.canvas.bind("<Escape>",          lambda _: self._disarm())
        self.canvas.bind("<KeyPress-r>",      lambda _: self._start_rotate())
        self.canvas.bind("<KeyPress-R>",      lambda _: self._start_rotate())
        self.canvas.bind("<KeyRelease-r>",    lambda _: self._stop_rotate())
        self.canvas.bind("<KeyRelease-R>",    lambda _: self._stop_rotate())
        self.listbox.bind("<Double-1>",       lambda _: self._arm_selected())

    # ─────────────────────────────────────────────────────────────────────────
    # Background image
    # ─────────────────────────────────────────────────────────────────────────

    def _load_bg(self):
        img_path = os.path.join(self.project_path, "rectified_slab.png")
        try:
            self.bg_img_full = Image.open(img_path).copy()
        except Exception as e:
            messagebox.showerror("Layout", f"Cannot open slab image:\n{e}")
            return
        self._apply_bg()

    def _apply_bg(self):
        full_w, full_h = self.bg_img_full.size
        scale  = min(1020 / full_w, 720 / full_h, 1.0)
        disp_w = int(full_w * scale)
        disp_h = int(full_h * scale)

        bg_disp     = self.bg_img_full.resize((disp_w, disp_h), Image.LANCZOS)
        self._tk_bg = ImageTk.PhotoImage(bg_disp)
        self.canvas.configure(width=disp_w, height=disp_h)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw",
                                 image=self._tk_bg, tags=(_TAG_BG,))

        self.display_px_per_mm = self.true_px_per_mm * scale

        self._draw_polygon_outline()
        self._refresh_palette()
        self._draw_all()
        self.canvas.update_idletasks()

    # ─────────────────────────────────────────────────────────────────────────
    # Geometry helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _mm(self, v_px):  return v_px / self.display_px_per_mm
    def _px(self, v_mm):  return v_mm * self.display_px_per_mm

    def _corners_px(self, p, cx_mm=None, cy_mm=None):
        """
        4 rotated corners of piece p in canvas pixels.
        Optionally override center (used for ghost preview).
        Returns flat list [x0,y0, x1,y1, x2,y2, x3,y3].
        """
        cx = self._px(cx_mm if cx_mm is not None else p["cx_mm"])
        cy = self._px(cy_mm if cy_mm is not None else p["cy_mm"])
        hw = self._px(p["w_mm"] / 2)
        hh = self._px(p["h_mm"] / 2)
        θ  = math.radians(p.get("angle", 0.0))
        c, s = math.cos(θ), math.sin(θ)
        pts = []
        for dx, dy in [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]:
            pts.append(cx + dx * c - dy * s)
            pts.append(cy + dx * s + dy * c)
        return pts

    def _aabb_half_mm(self, p):
        """Half-sizes of the AABB of the rotated piece, in mm."""
        hw = p["w_mm"] / 2
        hh = p["h_mm"] / 2
        θ  = math.radians(p.get("angle", 0.0))
        c, s = abs(math.cos(θ)), abs(math.sin(θ))
        return hw * c + hh * s, hw * s + hh * c

    def _aabb_px(self, p):
        """AABB of piece in canvas px: (x1, y1, x2, y2)."""
        cx    = self._px(p["cx_mm"])
        cy    = self._px(p["cy_mm"])
        hw, hh = self._aabb_half_mm(p)
        return (cx - self._px(hw), cy - self._px(hh),
                cx + self._px(hw), cy + self._px(hh))

    def _draw_polygon_outline(self):
        if not self.slab_polygon_mm or len(self.slab_polygon_mm) < 3:
            return
        pts = []
        for x_mm, y_mm in self.slab_polygon_mm:
            pts.append(self._px(x_mm))
            pts.append(self._px(y_mm))
        self.canvas.create_polygon(pts, outline="#00e5ff", fill="", width=2,
                                   dash=(10, 4), tags=(_TAG_BG,))

    # ─────────────────────────────────────────────────────────────────────────
    # Collision detection  (AABB of rotated rects + margin)
    # ─────────────────────────────────────────────────────────────────────────

    def _collides(self, test):
        m  = self.margin_mm
        tw, th = self._aabb_half_mm(test)
        for p in self.pieces:
            if p is test or not p["placed"]:
                continue
            pw, ph = self._aabb_half_mm(p)
            if (test["cx_mm"] + tw + m <= p["cx_mm"] - pw or
                    p["cx_mm"] + pw + m <= test["cx_mm"] - tw or
                    test["cy_mm"] + th + m <= p["cy_mm"] - ph or
                    p["cy_mm"] + ph + m <= test["cy_mm"] - th):
                continue
            return True
        return False

    # ─────────────────────────────────────────────────────────────────────────
    # Palette
    # ─────────────────────────────────────────────────────────────────────────

    def _refresh_palette(self):
        self.listbox.delete(0, tk.END)
        for p in self.pieces:
            if not p["placed"]:
                self.listbox.insert(tk.END,
                    f"  {p['id']}  {int(p['w_mm'])}×{int(p['h_mm'])} mm")
        self._emit_stats()

    def _emit_stats(self):
        if not self.on_stats:
            return
        placed    = [p for p in self.pieces if p["placed"]]
        area_used = sum(p["w_mm"] * p["h_mm"] for p in placed)
        slab_area = self.slab_w_mm * self.slab_h_mm
        pct       = (area_used / slab_area * 100) if slab_area else 0.0
        self.on_stats(len(placed), len(self.pieces), pct)

    # ─────────────────────────────────────────────────────────────────────────
    # Drawing
    # ─────────────────────────────────────────────────────────────────────────

    def _draw_all(self):
        for item in self.canvas.find_withtag(_TAG_PIECE):
            self.canvas.delete(item)
        for p in self.pieces:
            if p["placed"]:
                self._draw_piece(p)

    def _draw_piece(self, p, highlight=False):
        tag_id  = _TAG_PREFIX + p["id"]
        outline = "#f9e2af" if highlight else "#000000"
        width   = 2 if highlight else 1
        pts     = self._corners_px(p)

        self.canvas.create_polygon(
            pts, fill=p["color"], outline=outline, width=width,
            tags=(_TAG_PIECE, tag_id)
        )

        # Label — skip if piece is tiny on screen
        x1, y1, x2, y2 = self._aabb_px(p)
        if (x2 - x1) > 18 and (y2 - y1) > 12:
            fg = contrast_color(p["color"])
            self.canvas.create_text(
                self._px(p["cx_mm"]), self._px(p["cy_mm"]),
                text=p["id"], fill=fg,
                font=("Segoe UI", 9, "bold"),
                tags=(_TAG_PIECE, tag_id)
            )

    def _update_ghost(self, mx, my):
        if self.ghost_item:
            for item in self.ghost_item:
                self.canvas.delete(item)
            self.ghost_item = None

        if not self.armed_piece:
            return

        p  = self.armed_piece
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        hw_mm, hh_mm = self._aabb_half_mm(p)
        hw_px = self._px(hw_mm)
        hh_px = self._px(hh_mm)

        # Clamp center to canvas bounds
        cx_px = max(hw_px, min(mx, cw - hw_px))
        cy_px = max(hh_px, min(my, ch - hh_px))

        pts = self._corners_px(p, cx_mm=self._mm(cx_px), cy_mm=self._mm(cy_px))

        r = self.canvas.create_polygon(
            pts, outline="#4cc9f0", fill="", width=2,
            dash=(6, 3), tags=(_TAG_PIECE,)
        )
        t = self.canvas.create_text(
            cx_px, cy_px,
            text=p["id"], fill="#4cc9f0",
            font=("Segoe UI", 9, "bold"),
            tags=(_TAG_PIECE,)
        )
        self.ghost_item = (r, t)

    def _disarm(self):
        self._stop_rotate()
        if self.ghost_item:
            for item in self.ghost_item:
                self.canvas.delete(item)
            self.ghost_item = None
        self.armed_piece = None

    # ─────────────────────────────────────────────────────────────────────────
    # Continuous rotation (hold R)
    # ─────────────────────────────────────────────────────────────────────────

    def _start_rotate(self):
        if self._rotate_job is not None:
            return   # already running (key auto-repeat)
        self._do_rotate_step()

    def _do_rotate_step(self):
        p = self.armed_piece or self.selected_piece
        if p is None:
            self._rotate_job = None
            return
        p["angle"] = (p.get("angle", 0.0) + _ROT_STEP_DEG) % 360
        if self.armed_piece:
            self._update_ghost(self._mouse_x, self._mouse_y)
        else:
            self._draw_all()
            self._draw_piece(self.selected_piece, highlight=True)
        self._rotate_job = self.canvas.after(_ROT_INTERVAL, self._do_rotate_step)

    def _stop_rotate(self):
        if self._rotate_job is not None:
            self.canvas.after_cancel(self._rotate_job)
            self._rotate_job = None

    # ─────────────────────────────────────────────────────────────────────────
    # Events
    # ─────────────────────────────────────────────────────────────────────────

    def _arm_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        unplaced = [p for p in self.pieces if not p["placed"]]
        idx = sel[0]
        if idx >= len(unplaced):
            return
        self.armed_piece = unplaced[idx]
        self._update_ghost(-9999, -9999)

    def _on_motion(self, event):
        self._mouse_x = event.x
        self._mouse_y = event.y
        if self.armed_piece:
            self._update_ghost(event.x, event.y)

    def _on_click(self, event):
        self.canvas.focus_set()
        if self.armed_piece:
            self._place_armed(event.x, event.y)
            return
        self._try_select(event.x, event.y)

    def _place_armed(self, mx, my):
        p  = self.armed_piece
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        hw_mm, hh_mm = self._aabb_half_mm(p)
        hw_px = self._px(hw_mm)
        hh_px = self._px(hh_mm)

        p["cx_mm"]  = self._mm(max(hw_px, min(mx, cw - hw_px)))
        p["cy_mm"]  = self._mm(max(hh_px, min(my, ch - hh_px)))
        p["placed"] = True

        if self._collides(p):
            messagebox.showwarning(
                "Overlap",
                f"Piece '{p['id']}' would overlap another piece.\n"
                "Try a different spot.",
                parent=self
            )
            p["placed"] = False
            return

        self._disarm()
        self._refresh_palette()
        self._draw_all()

    def _try_select(self, x, y):
        items = self.canvas.find_overlapping(x, y, x, y)
        target = None
        for item in reversed(items):
            for tag in self.canvas.gettags(item):
                if tag.startswith(_TAG_PREFIX):
                    target = self._get_piece(tag[len(_TAG_PREFIX):])
                    break
            if target:
                break

        self.selected_piece = target
        if target:
            self.drag_offset = (x - self._px(target["cx_mm"]),
                                y - self._px(target["cy_mm"]))
            self._draw_all()
            self._draw_piece(target, highlight=True)

    def _on_drag(self, event):
        if not self.selected_piece:
            return
        p  = self.selected_piece
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        hw_mm, hh_mm = self._aabb_half_mm(p)
        hw_px = self._px(hw_mm)
        hh_px = self._px(hh_mm)

        old_cx, old_cy = p["cx_mm"], p["cy_mm"]
        was_colliding  = self._collides(p)

        ncx = max(hw_px, min(event.x - self.drag_offset[0], cw - hw_px))
        ncy = max(hh_px, min(event.y - self.drag_offset[1], ch - hh_px))

        p["cx_mm"] = self._mm(ncx)
        p["cy_mm"] = self._mm(ncy)

        # Only block move if it *introduces* a new collision (allow escaping overlap)
        if not was_colliding and self._collides(p):
            p["cx_mm"], p["cy_mm"] = old_cx, old_cy

        self._draw_all()
        self._draw_piece(p, highlight=True)

    def _on_release(self, _event):
        self.selected_piece = None

    def _get_piece(self, pid):
        return next((p for p in self.pieces if p["id"] == pid), None)

    # ─────────────────────────────────────────────────────────────────────────
    # Auto layout
    # ─────────────────────────────────────────────────────────────────────────

    def do_auto_layout(self):
        for p in self.pieces:
            p["placed"] = False
            p["cx_mm"]  = 0.0
            p["cy_mm"]  = 0.0
            p["angle"]  = 0.0

        # auto_pack expects / returns x_mm/y_mm as top-left
        pack_input = [{**p, "x_mm": 0.0, "y_mm": 0.0} for p in self.pieces]
        placed, unplaced = auto_pack(
            pack_input,
            slab_w=self.slab_w_mm,
            slab_h=self.slab_h_mm,
            margin=self.margin_mm,
            allow_rotate=True,
            slab_polygon=self.slab_polygon_mm,
        )

        result_by_id = {r["id"]: r for r in placed}
        for p in self.pieces:
            if p["id"] in result_by_id:
                r = result_by_id[p["id"]]
                p["w_mm"]   = r["w_mm"]
                p["h_mm"]   = r["h_mm"]
                p["cx_mm"]  = r["x_mm"] + r["w_mm"] / 2
                p["cy_mm"]  = r["y_mm"] + r["h_mm"] / 2
                p["angle"]  = 0.0
                p["placed"] = True

        self._draw_all()
        self._refresh_palette()

        if unplaced:
            names = ", ".join(p["id"] for p in unplaced)
            messagebox.showinfo(
                "Auto Layout",
                f"Placed {len(placed)} of {len(self.pieces)} pieces.\n\n"
                f"Could not fit:\n{names}",
                parent=self
            )

    def clear_layout(self):
        for p in self.pieces:
            p["placed"] = False
            p["cx_mm"]  = 0.0
            p["cy_mm"]  = 0.0
            p["angle"]  = 0.0
        self._draw_all()
        self._refresh_palette()

    # ─────────────────────────────────────────────────────────────────────────
    # Save
    # ─────────────────────────────────────────────────────────────────────────

    def save_layout(self):
        if not self.bg_img_full:
            messagebox.showwarning("Save", "No slab image loaded.", parent=self)
            return

        out_json = os.path.join(self.project_path, "layout.json")
        out_img  = os.path.join(self.project_path, "layout_preview.png")

        pieces_out = [
            {**p,
             "cx_mm": round(p["cx_mm"], 2),
             "cy_mm": round(p["cy_mm"], 2),
             "angle": round(p.get("angle", 0.0), 2)}
            for p in self.pieces
        ]
        try:
            with open(out_json, "w", encoding="utf-8") as f:
                json.dump({
                    "pieces":    pieces_out,
                    "px_per_mm": self.true_px_per_mm,
                    "slab_mm":   {"w": self.slab_w_mm, "h": self.slab_h_mm},
                    "margin_mm": self.margin_mm,
                }, f, indent=2)
        except Exception as e:
            messagebox.showerror("Save Error",
                                 f"Could not save layout.json:\n{e}", parent=self)
            return

        try:
            img  = self.bg_img_full.copy()
            draw = ImageDraw.Draw(img, "RGBA")
            s    = self.true_px_per_mm

            for p in self.pieces:
                if not p["placed"]:
                    continue
                cx   = p["cx_mm"] * s
                cy   = p["cy_mm"] * s
                hw   = p["w_mm"] / 2 * s
                hh   = p["h_mm"] / 2 * s
                θ    = math.radians(p.get("angle", 0.0))
                c, sn = math.cos(θ), math.sin(θ)
                pts = [
                    (cx + dx * c - dy * sn, cy + dx * sn + dy * c)
                    for dx, dy in [(-hw,-hh),(hw,-hh),(hw,hh),(-hw,hh)]
                ]
                hx  = p["color"].lstrip("#")
                rgb = tuple(int(hx[i:i+2], 16) for i in (0, 2, 4))
                draw.polygon(pts, fill=(*rgb, 120), outline=(*rgb, 255))
                fg = (0,0,0,255) if contrast_color(p["color"]) == "#000000" \
                     else (255,255,255,255)
                draw.text((cx, cy), p["id"], fill=fg, anchor="mm")

            img.save(out_img)
        except Exception as e:
            messagebox.showerror("Save Error",
                                 f"Could not save preview image:\n{e}", parent=self)
            return

        messagebox.showinfo("Saved",
                            f"Layout saved:\n  {out_json}\n  {out_img}",
                            parent=self)
