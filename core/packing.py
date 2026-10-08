# core/packing.py
"""
Piece packing algorithms.

auto_pack(pieces, slab_w, slab_h, margin, allow_rotate, slab_polygon)

  * slab_polygon is None  → original Shelf-FFFD (rectangular slab).
  * slab_polygon provided → grid-scan packer that only places pieces
    whose entire footprint fits inside the polygon.

Grid packer detail
------------------
  1. Build a boolean grid at `step` mm resolution (True = inside polygon).
     Uses vectorised numpy ray-casting.
  2. Build a summed-area table (SAT) over the grid for O(1) rectangle
     containment queries.
  3. Sort pieces by area descending.
  4. For each piece scan valid start positions (top→bottom, left→right)
     and pick the first one that doesn't collide with already-placed pieces.
"""
import numpy as np


# ── helpers ───────────────────────────────────────────────────────────────────

def expand_pieces(piece_defs):
    """
    Expand piece definitions (which may have a 'qty' field) into individual
    piece dicts, each with a unique id.
    """
    result = []
    for p in piece_defs:
        qty = max(1, int(p.get("qty", 1)))
        for i in range(qty):
            suffix = f"-{i + 1}" if qty > 1 else ""
            result.append({
                "id":     str(p["id"]) + suffix,
                "w_mm":   float(p["w_mm"]),
                "h_mm":   float(p["h_mm"]),
                "color":  p.get("color", "#88aacc"),
                "x_mm":   0.0,
                "y_mm":   0.0,
                "placed": False,
            })
    return result


# ── polygon grid packer ───────────────────────────────────────────────────────

def _build_grid(poly_mm, slab_w, slab_h, step):
    """
    Boolean grid[iy, ix] = True when point (ix*step, iy*step) is inside
    the polygon.  Uses vectorised ray-casting (numpy).
    """
    nx = int(slab_w / step) + 2
    ny = int(slab_h / step) + 2
    xs = np.arange(nx, dtype=np.float64) * step
    ys = np.arange(ny, dtype=np.float64) * step
    gx, gy = np.meshgrid(xs, ys)
    px, py = gx.ravel(), gy.ravel()

    poly = np.array(poly_mm, dtype=np.float64)
    n    = len(poly)
    inside = np.zeros(len(px), dtype=bool)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i, 0], poly[i, 1]
        xj, yj = poly[j, 0], poly[j, 1]
        dy = yj - yi
        if abs(dy) > 1e-9:
            cond = ((yi > py) != (yj > py)) & (px < (xj - xi) * (py - yi) / dy + xi)
            inside ^= cond
        j = i
    return inside.reshape(ny, nx)


def _build_sat(grid):
    """Summed-area table with a zero-padded top/left row for clean indexing."""
    padded = np.pad(grid.astype(np.int32), ((1, 0), (1, 0)))
    return np.cumsum(np.cumsum(padded, axis=0), axis=1)


def _valid_starts(sat, nw, nh):
    """
    Return boolean matrix vs[iy, ix] = True when every cell in the
    nw-wide × nh-tall grid rectangle starting at (ix, iy) is inside polygon.
    """
    ny = sat.shape[0] - 1
    nx = sat.shape[1] - 1
    out_ny = ny - nh + 1
    out_nx = nx - nw + 1
    if out_ny <= 0 or out_nx <= 0:
        return np.zeros((0, 0), dtype=bool)

    r2p = np.arange(nh, nh + out_ny)
    r1p = r2p - nh
    c2p = np.arange(nw, nw + out_nx)
    c1p = c2p - nw

    sums = (sat[np.ix_(r2p, c2p)]
            - sat[np.ix_(r1p, c2p)]
            - sat[np.ix_(r2p, c1p)]
            + sat[np.ix_(r1p, c1p)])
    return sums == nh * nw


def _collides_any(x, y, w, h, placed_rects, margin):
    for rx, ry, rw, rh in placed_rects:
        if (x + w + margin <= rx or rx + rw + margin <= x or
                y + h + margin <= ry or ry + rh + margin <= y):
            continue
        return True
    return False


def _polygon_pack(order, slab_w, slab_h, margin, allow_rotate, slab_polygon):
    step = max(margin, 5.0)   # mm — grid resolution

    grid = _build_grid(slab_polygon, slab_w, slab_h, step)
    sat  = _build_sat(grid)

    placed_rects = []   # (x, y, w, h) in mm
    placed   = []
    unplaced = []

    def _find_pos(w, h):
        nw = max(1, int(np.ceil(w / step)))
        nh = max(1, int(np.ceil(h / step)))
        vs = _valid_starts(sat, nw, nh)
        if vs.size == 0:
            return None
        rows, cols = np.where(vs)          # top-to-bottom, left-to-right order
        for iy, ix in zip(rows.tolist(), cols.tolist()):
            x = float(ix) * step
            y = float(iy) * step
            if x + w > slab_w or y + h > slab_h:
                continue
            if not _collides_any(x, y, w, h, placed_rects, margin):
                return x, y
        return None

    for p in order:
        w, h = p["w_mm"], p["h_mm"]
        pos = _find_pos(w, h)
        if pos is None and allow_rotate and abs(w - h) > 0.5:
            pos = _find_pos(h, w)
            if pos is not None:
                w, h = h, w

        if pos is not None:
            x, y = pos
            out = dict(p)
            out.update(x_mm=x, y_mm=y, w_mm=w, h_mm=h, placed=True)
            placed.append(out)
            placed_rects.append((x, y, w, h))
        else:
            up = dict(p)
            up["placed"] = False
            unplaced.append(up)

    return placed, unplaced


# ── shelf packer (rectangular slab, legacy) ───────────────────────────────────

def _shelf_pack(order, slab_w, slab_h, margin, allow_rotate):
    shelves  = []
    placed   = []
    unplaced = []

    def _shelf_y_top():
        if not shelves:
            return 0.0
        last = shelves[-1]
        return last["y"] + last["h"] + margin

    def _try_fit(p, w, h):
        for s in shelves:
            if s["x_next"] + w <= slab_w and h <= s["h"]:
                out = dict(p)
                out.update(x_mm=s["x_next"], y_mm=s["y"], w_mm=w, h_mm=h, placed=True)
                s["x_next"] += w + margin
                return out
        y_new = _shelf_y_top()
        if y_new + h <= slab_h and w <= slab_w:
            shelves.append({"y": y_new, "h": h, "x_next": w + margin})
            out = dict(p)
            out.update(x_mm=0.0, y_mm=y_new, w_mm=w, h_mm=h, placed=True)
            return out
        return None

    for p in order:
        w, h   = p["w_mm"], p["h_mm"]
        result = _try_fit(p, w, h)
        if result is None and allow_rotate and abs(w - h) > 0.5:
            result = _try_fit(p, h, w)
        if result:
            placed.append(result)
        else:
            up = dict(p)
            up["placed"] = False
            unplaced.append(up)

    return placed, unplaced


# ── public API ────────────────────────────────────────────────────────────────

def auto_pack(pieces, slab_w, slab_h, margin=2.0, allow_rotate=True,
              slab_polygon=None):
    """
    Pack pieces into the slab.

    Parameters
    ----------
    pieces       : list of dicts with at least "id", "w_mm", "h_mm"
    slab_w/h     : slab bounding-box dimensions in mm
    margin       : minimum gap between pieces
    allow_rotate : try 90° rotation when natural orientation doesn't fit
    slab_polygon : list of (x_mm, y_mm) polygon vertices, or None for
                   rectangular slab (uses faster shelf packer)
    """
    order = sorted(pieces, key=lambda p: p["w_mm"] * p["h_mm"], reverse=True)

    if slab_polygon is not None and len(slab_polygon) >= 3:
        return _polygon_pack(order, slab_w, slab_h, margin, allow_rotate,
                             slab_polygon)
    return _shelf_pack(order, slab_w, slab_h, margin, allow_rotate)
