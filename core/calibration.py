# core/calibration.py
"""
Two-phase calibration — no perspective warp needed:

  Phase 1 – Slab outline
    Click N points around the slab edge (irregular shapes OK).
    Press Enter or D when done (minimum 3 points).

  Phase 2 – Scale reference
    Click any 2 points on the slab whose real distance you know.
    A dialog asks for that distance in mm.
    px_per_mm = pixel_distance / real_distance_mm.

Output
------
  rectified_slab.png   – cropped to polygon bounding box, outside darkened
  calibration.json     – polygon, scale, bounding-box mm dimensions
"""
import os
import json
import cv2
import numpy as np
from tkinter import filedialog, messagebox, simpledialog

_INSTR_OUTLINE = (
    "Phase 1 — Slab outline  |  Click outline points  |"
    "  Enter/D = done (min 3)  |  R = reset  |  Q = quit"
)
_INSTR_REF = (
    "Phase 2 — Scale reference  |  Click 2 points whose distance you know"
    "  |  R = reset  |  Q = quit"
)


def _fit_to_screen(img, max_w=1600, max_h=900):
    h, w = img.shape[:2]
    s = min(max_w / w, max_h / h, 1.0)
    if s < 1.0:
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return img, s


# ── Phase 1: polygon outline ──────────────────────────────────────────────────

def _pick_outline(img_view):
    """Interactive polygon tracing. Returns list of (x,y) in view coords, or None."""
    pts = []
    done = [False]
    win = _INSTR_OUTLINE
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN and not done[0]:
            pts.append((x, y))

    cv2.setMouseCallback(win, on_mouse)

    while not done[0]:
        frame = img_view.copy()
        # header bar
        cv2.rectangle(frame, (0, 0), (frame.shape[1], 36), (20, 20, 20), -1)
        hint = f"   Points: {len(pts)}"
        if len(pts) >= 3:
            hint += "  —  Enter or D to finish"
        cv2.putText(frame, _INSTR_OUTLINE + hint, (8, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)

        # draw placed points + open polygon
        for i, (x, y) in enumerate(pts):
            cv2.circle(frame, (x, y), 5, (0, 255, 120), -1)
            cv2.putText(frame, str(i + 1), (x + 7, y - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 120), 1, cv2.LINE_AA)
        if len(pts) >= 2:
            arr = np.array(pts, dtype=np.int32).reshape(-1, 1, 2)
            cv2.polylines(frame, [arr], False, (0, 220, 255), 2)
        if len(pts) >= 3:
            # closing edge preview
            cv2.line(frame, pts[-1], pts[0], (0, 220, 255), 1)

        cv2.imshow(win, frame)
        k = cv2.waitKey(16) & 0xFF
        if k == ord('q'):
            cv2.destroyWindow(win)
            return None
        if k == ord('r'):
            pts.clear()
        if (k in (13, ord('d'))) and len(pts) >= 3:
            done[0] = True

    cv2.destroyWindow(win)
    return pts


# ── Phase 2: reference distance ───────────────────────────────────────────────

def _pick_reference(img_view, outline_pts_view):
    """Click 2 reference points. Returns list of 2 (x,y) in view coords, or None."""
    ref = []
    win = _INSTR_REF
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)

    # Pre-build the dimmed base image so we don't recompute every frame
    outline_arr = np.array(outline_pts_view, dtype=np.int32).reshape(-1, 1, 2)
    mask = np.zeros(img_view.shape[:2], dtype=np.uint8)
    cv2.fillPoly(mask, [outline_arr], 255)
    darkened = (img_view * 0.30).astype(np.uint8)
    base = np.where(mask[:, :, np.newaxis] > 0, img_view, darkened)
    cv2.polylines(base, [outline_arr], True, (0, 220, 255), 2)

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN and len(ref) < 2:
            ref.append((x, y))

    cv2.setMouseCallback(win, on_mouse)

    while True:
        frame = base.copy()
        cv2.rectangle(frame, (0, 0), (frame.shape[1], 36), (20, 20, 20), -1)
        hint = f"   Points: {len(ref)}/2"
        cv2.putText(frame, _INSTR_REF + hint, (8, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)

        for i, (x, y) in enumerate(ref):
            cv2.circle(frame, (x, y), 8, (255, 80, 80), -1)
            cv2.putText(frame, ["A", "B"][i], (x + 10, y - 7),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 80, 80), 2, cv2.LINE_AA)
        if len(ref) == 2:
            cv2.line(frame, ref[0], ref[1], (255, 80, 80), 2)
            # pixel distance label
            px_dist = np.linalg.norm(np.array(ref[0]) - np.array(ref[1]))
            mid = ((ref[0][0] + ref[1][0]) // 2, (ref[0][1] + ref[1][1]) // 2)
            cv2.putText(frame, f"{px_dist:.0f} px", (mid[0] + 6, mid[1] - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 200, 80), 1, cv2.LINE_AA)

        cv2.imshow(win, frame)
        k = cv2.waitKey(16) & 0xFF
        if k == ord('q'):
            cv2.destroyWindow(win)
            return None
        if k == ord('r'):
            ref.clear()
        if len(ref) == 2:
            break

    cv2.destroyWindow(win)
    return ref


# ── Main entry ────────────────────────────────────────────────────────────────

def run_calibration(project_path, parent=None):
    if not project_path or not os.path.isdir(project_path):
        messagebox.showerror("Calibration", "Project folder is missing.", parent=parent)
        return False

    # 1) Pick source image
    img_path = filedialog.askopenfilename(
        parent=parent,
        title="Select slab photo",
        filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff")]
    )
    if not img_path:
        return False

    img_full = cv2.imread(img_path, cv2.IMREAD_COLOR)
    if img_full is None:
        messagebox.showerror("Calibration", "Could not read the selected image.", parent=parent)
        return False

    img_view, view_scale = _fit_to_screen(img_full.copy())

    # 2) Phase 1 — slab outline
    outline_view = _pick_outline(img_view)
    if outline_view is None:
        return False

    # 3) Phase 2 — reference distance
    ref_view = _pick_reference(img_view, outline_view)
    if ref_view is None:
        return False

    # 4) Ask real distance
    ref_dist_mm = simpledialog.askfloat(
        "Reference Distance",
        "Enter the real distance between points A — B in millimeters:",
        minvalue=10.0, maxvalue=20000.0, initialvalue=1000.0,
        parent=parent
    )
    if not ref_dist_mm:
        return False

    # 5) Map everything to full-resolution coords
    inv = 1.0 / view_scale
    outline_full = [(int(round(x * inv)), int(round(y * inv))) for (x, y) in outline_view]
    a_full = (ref_view[0][0] * inv, ref_view[0][1] * inv)
    b_full = (ref_view[1][0] * inv, ref_view[1][1] * inv)

    # 6) Compute scale
    ref_dist_px = float(np.linalg.norm(np.array(a_full) - np.array(b_full)))
    px_per_mm   = ref_dist_px / ref_dist_mm

    # 7) Crop image to bounding box of polygon
    xs = [p[0] for p in outline_full]
    ys = [p[1] for p in outline_full]
    x0 = max(0, min(xs))
    y0 = max(0, min(ys))
    x1 = min(img_full.shape[1], max(xs))
    y1 = min(img_full.shape[0], max(ys))

    crop = img_full[y0:y1, x0:x1].copy()
    crop_h, crop_w = crop.shape[:2]

    # 8) Apply polygon mask — darken outside the slab
    outline_rel = [(x - x0, y - y0) for (x, y) in outline_full]
    poly_arr    = np.array(outline_rel, dtype=np.int32)
    mask        = np.zeros((crop_h, crop_w), dtype=np.uint8)
    cv2.fillPoly(mask, [poly_arr], 255)
    dark   = (crop * 0.15).astype(np.uint8)
    result = np.where(mask[:, :, np.newaxis] > 0, crop, dark)

    # 9) Real bounding-box dimensions
    slab_w_mm = crop_w / px_per_mm
    slab_h_mm = crop_h / px_per_mm

    # 10) Save outputs
    out_img  = os.path.join(project_path, "rectified_slab.png")
    out_json = os.path.join(project_path, "calibration.json")

    try:
        cv2.imwrite(out_img, result)
    except Exception as e:
        messagebox.showerror("Calibration", f"Failed to save image:\n{e}", parent=parent)
        return False

    # ref_points in full-res (for reference/display only)
    calib = {
        "source_image":    os.path.basename(img_path),
        "rectified_image": "rectified_slab.png",
        "method":          "polygon_scale",
        "polygon_px":      outline_full,          # N outline points (full-res)
        "crop_origin_px":  [x0, y0],              # top-left of crop in source image
        "ref_points_px":   [list(a_full), list(b_full)],
        "ref_distance_mm": float(ref_dist_mm),
        "out_pixels":      {"w": int(crop_w), "h": int(crop_h)},
        "real_mm":         {"w": float(slab_w_mm), "h": float(slab_h_mm)},
        "px_per_mm":       float(px_per_mm),
    }
    try:
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(calib, f, indent=2)
    except Exception as e:
        messagebox.showerror("Calibration", f"Failed to save JSON:\n{e}", parent=parent)
        return False

    # 11) Show preview
    preview, _ = _fit_to_screen(result.copy())
    # draw the outline on the preview too
    scale_p = preview.shape[1] / crop_w
    poly_prev = np.array(
        [(int(x * scale_p), int(y * scale_p)) for (x, y) in outline_rel],
        dtype=np.int32
    )
    cv2.polylines(preview, [poly_prev], True, (0, 220, 255), 2)
    cv2.imshow("Slab Preview — press any key to close", preview)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

    messagebox.showinfo(
        "Calibration Complete",
        f"Slab bounding box: {slab_w_mm:.0f} × {slab_h_mm:.0f} mm\n"
        f"Scale: {px_per_mm:.4f} px/mm\n"
        f"Outline: {len(outline_full)} points\n\n"
        f"Saved to:\n  {out_img}\n  {out_json}",
        parent=parent
    )
    return True
