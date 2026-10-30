#!/usr/bin/env python3
"""
pixelkit.py - generate laser-cut pixel art kits as SVG files.

A kit is:
  * a tray (finger-jointed box, or a glued stack of frame layers) whose base
    can be engraved with the placement grid and color numbers,
  * sheets of square tiles cut on a shared grid (one cut line between
    neighbouring tiles), one set of sheets per color when a pattern image
    is given,
  * an optional printable pattern chart with a color legend.

The pattern comes from any image file, or from the bundled Twemoji set
(emoji/twemoji-72.zip, see fetch_emoji.py) via the GUI's Emoji… picker or
`--emoji`.

Kerf is compensated everywhere: tile pitch is pixel + kerf so each tile comes
out at the nominal pixel size, and tray outlines are offset by kerf/2 away
from the material so finger joints fit.

Run with no arguments for the GUI, or `--cli` plus options for headless use
(`python3 pixelkit.py --cli --help`).

SVG conventions: units are mm, cut lines are red (#ff0000), engraving is
blue (#0000ff), hairline 0.1 mm strokes.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import subprocess
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

CUT = "#ff0000"
ENGRAVE = "#0000ff"
STROKE = 0.1
SETTINGS_FILE = Path.home() / ".pixelkit.json"
TRAY_STYLES = ["box", "layered", "none"]
TILE_SHEETS = ["combined", "per_color"]
KIT_TYPES = ["mosaic", "paint_by_number"]
PBN_USES = ["engrave", "print"]
# Paint-by-number canvas presets, portrait (width, height) in mm.
CANVAS_SIZES = {
    "8x10in": (203.2, 254.0), "11x14in": (279.4, 355.6), "12x12in": (304.8, 304.8),
    "12x16in": (304.8, 406.4), "16x20in": (406.4, 508.0), "18x24in": (457.2, 609.6),
    "20x30cm": (200.0, 300.0), "30x40cm": (300.0, 400.0), "40x50cm": (400.0, 500.0),
}
CANVASES = ["off", "custom"] + list(CANVAS_SIZES)
CANVAS_FITS = ["fit", "fill"]
CHOICES = {"kit_type": KIT_TYPES, "tray_style": TRAY_STYLES, "tile_sheets": TILE_SHEETS, "pbn_use": PBN_USES,
           "canvas": CANVASES, "canvas_fit": CANVAS_FITS}


@dataclass
class Settings:
    kit_type: str = "mosaic"       # mosaic (tray + tiles) | paint_by_number (one numbered board)
    cols: int = 20
    rows: int = 25
    pixel: float = 10.0            # finished tile size, mm
    tile_thickness: float = 3.0
    tray_thickness: float = 3.0
    kerf: float = 0.15             # width of the laser cut in tray stock, mm
    tile_kerf: float = 0.15        # kerf compensation for tiles; lower (even negative) = smaller, looser tiles
    tray_style: str = "box"        # box | layered | none
    tile_clearance: float = 1.0    # total slack between tiles and tray wall
    lip: float = 4.0               # how far the wall stands above the tiles (3+3+4 = 10 mm wall)
    finger_width: float = 6.0      # target finger (tab) length for box joints
    joint_play: float = 0.0        # extra gap per finger joint (+ looser, - tighter)
    frame_width: float = 10.0      # frame border for the layered style
    engrave_grid: bool = True
    engrave_numbers: bool = False  # needs a pattern image
    sheet_width: float = 304.0     # laser bed / stock size (about 12 in)
    sheet_height: float = 304.0
    spacing: float = 3.0           # gap between parts and from sheet edge
    tile_border: float = 5.0       # waste frame around each tile block (0 = none)
    tile_tabs: bool = False        # leave bridges on the vertical tile cuts
    tab_width: float = 0.5         # width of each uncut bridge, mm
    spare_percent: float = 5.0     # trimmed if spares alone would need another sheet
    tile_sheets: str = "combined"  # combined (cut, then paint) | per_color (colored stock)
    image: str = ""                # image file, or "emoji:<code> <name>" from the bundled set
    background: str = "#ffffff"    # color behind transparent parts of the image
    colors: int = 8
    pbn_use: str = "engrave"       # paint by number: engrave (laser layers + cut line) | print
    pbn_min_area: float = 6.0      # regions smaller than this (mm^2) are absorbed by their neighbours
    pbn_min_width: float = 1.5     # ... and so are regions nowhere this wide (mm): slivers and fringes
    pbn_simplify: float = 0.05     # outline simplification tolerance, mm (0 = off)
    pbn_smooth: bool = True        # round off pixel steps in the outlines
    pbn_margin: float = 10.0       # board border around the picture, mm
    pbn_line_width: float = 0.3    # outline width when printing, mm
    pbn_text_min: float = 2.0      # smallest number, mm
    pbn_text_max: float = 6.0      # largest number, mm
    pbn_zones: str = ""            # detail zones: "x0,y0,x1,y1;..." as fractions of the grid
    pbn_zone_area: float = 0.25    # min area inside a zone, mm^2
    pbn_zone_width: float = 0.0    # min width inside a zone, mm
    pbn_zone_colors: int = 2       # most colors a zone may add to the palette
    canvas: str = "off"            # paint by number: board size preset, "custom", or "off" (size from the grid)
    canvas_landscape: bool = False # turn a preset canvas sideways
    canvas_width: float = 203.2    # custom canvas, mm
    canvas_height: float = 254.0
    canvas_fit: str = "fit"        # fit: whole picture, padded | fill: cover the canvas, cropping overflow
    output_dir: str = "~/pixelkit_output"
    name: str = "pixelart"


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

class Piece:
    """A part: closed/open polylines to cut and engrave, plus engraved text."""

    def __init__(self, name):
        self.name = name
        self.cuts = []      # [(points, closed)]
        self.engraves = []  # [(points, closed)]
        self.texts = []     # [(x, y, size, text, angle, anchor)]
        self.picture = None  # tile area corners [top-left, top-right, bottom-left], for previews

    def bbox(self):
        pts = [p for pl, _ in self.cuts + self.engraves for p in pl]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def size(self):
        x0, y0, x1, y1 = self.bbox()
        return x1 - x0, y1 - y0

    def _mapped(self, f, dangle=0.0):
        out = Piece(self.name)
        out.cuts = [([f(*p) for p in pl], c) for pl, c in self.cuts]
        out.engraves = [([f(*p) for p in pl], c) for pl, c in self.engraves]
        out.texts = [(*f(x, y), s, t, a + dangle, an) for x, y, s, t, a, an in self.texts]
        out.picture = [f(*pt) for pt in self.picture] if self.picture else None
        return out

    def moved(self, dx, dy):
        return self._mapped(lambda x, y: (x + dx, y + dy))

    def normalized(self):
        x0, y0, _, _ = self.bbox()
        return self.moved(-x0, -y0)

    def rotated(self):
        """Rotate 90 degrees clockwise (as seen in SVG's y-down space)."""
        return self._mapped(lambda x, y: (-y, x), 90.0).normalized()


def rect_pts(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def trace_region(adds, subs):
    """Outline of (union of `adds`) minus (union of `subs`), all axis-aligned
    rectangles (x0, y0, x1, y1). Returns closed loops with the material on
    the left of each edge (outer loops positive area, holes negative)."""
    xs = sorted({v for r in adds + subs for v in (r[0], r[2])})
    ys = sorted({v for r in adds + subs for v in (r[1], r[3])})
    nx, ny = len(xs) - 1, len(ys) - 1

    def inside(r, x, y):
        return r[0] < x < r[2] and r[1] < y < r[3]

    filled = [[False] * ny for _ in range(nx)]
    for i in range(nx):
        cx = (xs[i] + xs[i + 1]) / 2
        for j in range(ny):
            cy = (ys[j] + ys[j + 1]) / 2
            filled[i][j] = (any(inside(r, cx, cy) for r in adds)
                            and not any(inside(r, cx, cy) for r in subs))

    def f(i, j):
        return 0 <= i < nx and 0 <= j < ny and filled[i][j]

    out = defaultdict(list)
    for i in range(nx):
        for j in range(ny):
            if not filled[i][j]:
                continue
            x0, x1, y0, y1 = xs[i], xs[i + 1], ys[j], ys[j + 1]
            if not f(i, j - 1):
                out[(x0, y0)].append((x1, y0))
            if not f(i + 1, j):
                out[(x1, y0)].append((x1, y1))
            if not f(i, j + 1):
                out[(x1, y1)].append((x0, y1))
            if not f(i - 1, j):
                out[(x0, y1)].append((x0, y0))

    def take(a, b):
        out[a].remove(b)
        if not out[a]:
            del out[a]

    loops = []
    while out:
        a = next(iter(out))
        b = out[a][0]
        take(a, b)
        loop, prev, cur = [a], a, b
        while cur != a:
            loop.append(cur)
            dx, dy = cur[0] - prev[0], cur[1] - prev[1]
            # At a vertex shared by two loops, take the leftmost turn so
            # diagonally touching regions stay separate loops.
            nxt = max(out[cur], key=lambda n: dx * (n[1] - cur[1]) - dy * (n[0] - cur[0]))
            take(cur, nxt)
            prev, cur = cur, nxt
        loops.append(simplify(loop))
    return loops


def simplify(loop):
    """Drop collinear vertices from a closed loop."""
    pts = loop[:]
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            if abs((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])) < 1e-9:
                del pts[i]
                changed = True
                break
    return pts


def area(loop):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(loop, loop[1:] + loop[:1])) / 2


def offset_loop(loop, d):
    """Offset a loop (material on the left) away from the material by d,
    using mitred corners. Positive d grows outer outlines and shrinks holes."""
    n = len(loop)
    res = []
    for i in range(n):
        p0, p1, p2 = loop[i - 1], loop[i], loop[(i + 1) % n]
        n1 = _normal(p0, p1)
        n2 = _normal(p1, p2)
        k = d / (1 + n1[0] * n2[0] + n1[1] * n2[1])
        res.append((p1[0] + k * (n1[0] + n2[0]), p1[1] + k * (n1[1] + n2[1])))
    return res


def _normal(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    return dy / length, -dx / length


def outline_piece(name, adds, subs, kerf):
    piece = Piece(name)
    loops = trace_region(adds, subs)
    loops.sort(key=area)  # holes (negative area) first, outer outline last
    piece.cuts = [(offset_loop(lp, kerf / 2), True) for lp in loops]
    return piece


def segments(length, target, min_n):
    """Split `length` into an odd number of equal segments near `target`."""
    exact = length / max(target, 1e-6)
    n = max(1, int(exact) // 2 * 2 + 1)  # odd count just above or below exact
    if n > exact and n - 2 >= 1 and length / (n - 2) - target < target - length / n:
        n -= 2
    elif n < exact and length / n - target > target - length / (n + 2):
        n += 2
    while n < min_n:
        n += 2
    s = length / n
    return [(i * s, (i + 1) * s) for i in range(n)]


# --------------------------------------------------------------------------
# Kit parts
# --------------------------------------------------------------------------

def tray_inner(s):
    return s.cols * s.pixel + s.tile_clearance, s.rows * s.pixel + s.tile_clearance


def engrave_base(s, piece, ox, oy, pattern):
    """Engrave the tile grid (and optionally color numbers) on the base,
    with the tile area's top-left corner at (ox, oy)."""
    p = s.pixel
    ox += s.tile_clearance / 2
    oy += s.tile_clearance / 2
    piece.picture = [(ox, oy), (ox + s.cols * p, oy), (ox, oy + s.rows * p)]
    if s.engrave_grid:
        for i in range(s.cols + 1):
            x = ox + i * p
            pts = [(x, oy), (x, oy + s.rows * p)]
            piece.engraves.append((pts if i % 2 else pts[::-1], False))
        for j in range(s.rows + 1):
            y = oy + j * p
            pts = [(ox, y), (ox + s.cols * p, y)]
            piece.engraves.append((pts if j % 2 else pts[::-1], False))
    if s.engrave_numbers and pattern:
        size = p * 0.42
        for j, row in enumerate(pattern["grid"]):
            for i, idx in enumerate(row):
                cx, cy = ox + (i + 0.5) * p, oy + (j + 0.5) * p
                piece.texts.append((cx, cy + size * 0.35, size, str(idx + 1), 0.0, "middle"))


def make_box(s, pattern):
    """Open-top box: base with fingers on all edges, four walls with finger
    joints at the corners. Walls rise `tile_thickness + lip` above the base."""
    t = s.tray_thickness
    win, hin = tray_inner(s)
    z = t + s.tile_thickness + s.lip
    q = s.joint_play / 4  # half the play goes to each side of a joint
    xseg = segments(win, s.finger_width, 3)
    yseg = segments(hin, s.finger_width, 3)
    zseg = segments(z, s.finger_width, 1)

    adds = [(t, t, t + win, t + hin)]
    for a, b in xseg[1::2]:
        adds += [(t + a + q, 0, t + b - q, t), (t + a + q, t + hin, t + b - q, 2 * t + hin)]
    for a, b in yseg[1::2]:
        adds += [(0, t + a + q, t, t + b - q), (t + win, t + a + q, 2 * t + win, t + b - q)]
    base = outline_piece("base", adds, [], s.kerf)
    engrave_base(s, base, t, t, pattern)

    def wall(name, inner_len, segs, owns_even_corners):
        length = inner_len + 2 * t
        subs = []

        def notch(u0, v0, u1, v1):  # v measured up from the wall's bottom edge
            subs.append((u0, z - v1, u1, z - v0))

        for a, b in segs[1::2]:  # slots for the base's fingers
            notch(t + a - q, 0, t + b + q, t)
        for a, b in (zseg[1::2] if owns_even_corners else zseg[0::2]):
            notch(0, a - q, t, b + q)
            notch(length - t, a - q, length, b + q)
        return outline_piece(name, [(0, 0, length, z)], subs, s.kerf)

    parts = [base]
    for side in ("front", "back"):
        parts.append(wall(f"wall_{side}", win, xseg, True))
    for side in ("left", "right"):
        parts.append(wall(f"wall_{side}", hin, yseg, False))
    info = {
        "outer": (win + 2 * t, hin + 2 * t, z),
        "notes": [
            f"Box: outer {win + 2 * t:.1f} x {hin + 2 * t:.1f} mm, "
            f"wall height {z:.1f} mm ({len(zseg)} corner segment(s), "
            f"{len(xseg) // 2}/{len(yseg) // 2} base fingers per long/short edge)",
        ],
    }
    if len(zseg) == 1:
        info["notes"].append("Walls are too low for corner fingers: corners are butt joints, glue them.")
    return parts, info


def make_layered(s, pattern):
    """Flat base plus a stack of frame rings, glued together."""
    t = s.tray_thickness
    b = s.frame_width
    win, hin = tray_inner(s)
    wo, ho = win + 2 * b, hin + 2 * b
    layers = max(1, math.ceil((s.tile_thickness + s.lip) / t - 1e-6))
    base = outline_piece("base", [(0, 0, wo, ho)], [], s.kerf)
    engrave_base(s, base, b, b, pattern)
    parts = [base]
    for n in range(layers):
        parts.append(outline_piece(f"frame_{n + 1}", [(0, 0, wo, ho)], [(b, b, b + win, b + hin)], s.kerf))
    info = {
        "outer": (wo, ho, t * (layers + 1)),
        "notes": [f"Layered tray: outer {wo:.1f} x {ho:.1f} mm, base + {layers} frame layer(s), "
                  f"rim {t * layers - s.tile_thickness:+.1f} mm relative to the tiles"],
    }
    return parts, info


KERF_TEST_STEP = 0.05
KERF_TEST_COUNT = 7
KERF_TEST_ROOM = 0.3  # extra slot room across the stock thickness


def kerf_test_values(s):
    """Kerf values to try, centred on the current setting."""
    mid = KERF_TEST_COUNT // 2
    vals = [round(s.kerf + (i - mid) * KERF_TEST_STEP, 3) for i in range(KERF_TEST_COUNT)]
    return [v + 0.0 for v in vals if v >= 0]  # + 0.0 turns -0.0 into 0.0


def make_kerf_test(s):
    """Pairs of test pieces in tray stock, one pair per kerf value: a plate
    with two slots, and a tab piece whose two tabs push into them, exactly as
    the box base and walls fit. Each piece is engraved with its kerf value;
    the pair that goes together snugly gives the kerf to use.

    Slots get KERF_TEST_ROOM extra across the stock thickness: box fingers
    enter from an open edge, so only the finger-width fit matters, and a
    thickness setting that is slightly off mustn't skew the test."""
    t, f = s.tray_thickness, s.finger_width
    q = s.joint_play / 4  # same play per side as the box joints
    margin = 4.0
    w = 2 * f + 3 * margin
    plate_h = margin + t + 9.0
    tab_h = 10.0
    xs = [(margin, margin + f), (w - margin - f, w - margin)]
    size = 3.5
    pieces = []
    for k in kerf_test_values(s):
        label = f"{k:.2f}"
        slots = [(a - q, margin - q, b + q, margin + t + KERF_TEST_ROOM + q) for a, b in xs]
        plate = outline_piece(f"kerf_{label}_plate", [(0, 0, w, plate_h)], slots, k)
        plate.texts.append((w / 2, plate_h - 2.5, size, label, 0.0, "middle"))
        tabs = [(a + q, tab_h, b - q, tab_h + t) for a, b in xs]
        tab = outline_piece(f"kerf_{label}_tab", [(0, 0, w, tab_h)] + tabs, [], k)
        tab.texts.append((w / 2, tab_h / 2 + size * 0.35, size, label, 0.0, "middle"))
        pieces += [plate, tab]
    return pieces


def tile_block(name, c, r, s, label=""):
    """c x r tiles on a shared cut grid, optionally inside a waste border.

    With tile_tabs, every vertical cut (including the tile area's left and
    right edges) stops short around the middle of each tile, leaving a
    tab_width bridge: each tile hangs on its left and right neighbours, rows
    come apart along the horizontal cuts, and row ends hold on to the side
    frame. The gap in the path is tab_width + kerf, because the beam reaches
    kerf/2 past each end of a cut. That uses the real beam width (the tray
    kerf), not tile_kerf, so bridges stay tab_width however loose the tiles
    are made."""
    p = s.pixel + s.tile_kerf
    bd = s.tile_border
    w, h = c * p, r * p
    piece = Piece(name)
    if label and bd >= 3:
        size = bd * 0.6
        piece.texts.append((bd, bd * 0.8, size, label, 0.0, "start"))

    def vertical(i):
        x = bd + i * p
        if not s.tile_tabs:
            pts = [(x, bd), (x, bd + h)]
            return [pts if i % 2 else pts[::-1]]
        half = (s.tab_width + s.kerf) / 2
        segs, y = [], bd
        for j in range(r):
            mid = bd + (j + 0.5) * p
            segs.append([(x, y), (x, mid - half)])
            y = mid + half
        segs.append([(x, y), (x, bd + h)])
        return segs if i % 2 else [seg[::-1] for seg in reversed(segs)]

    for i in range(1, c):
        piece.cuts += [(seg, False) for seg in vertical(i)]
    for j in range(1, r):
        y = bd + j * p
        pts = [(bd, y), (bd + w, y)]
        piece.cuts.append((pts if j % 2 else pts[::-1], False))
    if s.tile_tabs:
        piece.cuts.append(([(bd, bd), (bd + w, bd)], False))
        piece.cuts += [(seg, False) for seg in vertical(c)]
        piece.cuts.append(([(bd + w, bd + h), (bd, bd + h)], False))
        piece.cuts += [(seg, False) for seg in vertical(0)]
    else:
        piece.cuts.append((rect_pts(bd, bd, bd + w, bd + h), True))
    if bd > 0:
        piece.cuts.append((rect_pts(0, 0, w + 2 * bd, h + 2 * bd), True))
    return piece


def tile_capacity(s):
    """Most tile columns and rows one block can have on a sheet."""
    p = s.pixel + s.tile_kerf
    room_w = s.sheet_width - 2 * s.spacing - 2 * s.tile_border
    room_h = s.sheet_height - 2 * s.spacing - 2 * s.tile_border
    maxc, maxr = int(room_w // p), int(room_h // p)
    if maxc < 1 or maxr < 1:
        raise ValueError("Sheet is too small for even one tile")
    return maxc, maxr


def tiles_to_cut(s, exact):
    """Picture count plus spares, trimming spares that would only spill onto
    an extra sheet. Never fewer than `exact`."""
    maxc, maxr = tile_capacity(s)
    cap = maxc * maxr
    want = math.ceil(exact * (1 + s.spare_percent / 100) - 1e-9)
    return max(exact, min(want, math.ceil(exact / cap) * cap))


def share_spares(counts, total):
    """Split `total` tiles over colors: each gets its count, one spare if
    there are enough to go round, and a share of the rest of the spares
    proportional to its count (largest remainder)."""
    spares = total - sum(counts)
    base = 1 if spares >= len(counts) else 0
    spares -= base * len(counts)
    exact = [spares * c / sum(counts) for c in counts]
    extra = [int(e) for e in exact]
    order = sorted(range(len(counts)), key=lambda i: -(exact[i] - extra[i]))
    for i in order[:spares - sum(extra)]:
        extra[i] += 1
    return [c + base + e for c, e in zip(counts, extra)]


def tile_blocks(s, count, label, key):
    maxc, maxr = tile_capacity(s)
    blocks = []
    n = count
    while n > 0:
        m = min(n, maxc * maxr)
        c = min(maxc, math.ceil(math.sqrt(m)))
        r = math.ceil(m / c)
        if r > maxr:
            c = maxc
            r = math.ceil(m / c)
        blocks.append(tile_block(f"tiles_{key}_{len(blocks) + 1}", c, r, s, label))
        n -= c * r
    return blocks


# --------------------------------------------------------------------------
# Layout and SVG output
# --------------------------------------------------------------------------

class _Sheet:
    """Max-rects free-space tracker for one sheet. Every part is padded by
    `gap` on its right and bottom; the free area starts `gap` in from the
    top-left, so parts end up `gap` apart and `gap` from every edge."""

    def __init__(self, w, h, gap):
        self.w, self.h, self.gap = w, h, gap
        self.free = [(gap, gap, w - gap, h - gap)]  # (x0, y0, x1, y1)
        self.items = []

    def best_spot(self, w, h):
        """Best-short-side-fit position for a w x h part, or None."""
        w, h = w + self.gap, h + self.gap
        best = None
        for x0, y0, x1, y1 in self.free:
            fw, fh = x1 - x0, y1 - y0
            if w <= fw + 1e-9 and h <= fh + 1e-9:
                score = (min(fw - w, fh - h), max(fw - w, fh - h))
                if best is None or score < best[0]:
                    best = (score, x0, y0)
        return best

    def place(self, pc, x, y):
        w, h = pc.size
        self.items.append(pc.moved(x, y))
        px1, py1 = x + w + self.gap, y + h + self.gap
        out = []
        for f in self.free:
            x0, y0, x1, y1 = f
            if x >= x1 or px1 <= x0 or y >= y1 or py1 <= y0:
                out.append(f)
                continue
            if x > x0:
                out.append((x0, y0, x, y1))
            if px1 < x1:
                out.append((px1, y0, x1, y1))
            if y > y0:
                out.append((x0, y0, x1, y))
            if py1 < y1:
                out.append((x0, py1, x1, y1))
        self.free = [a for a in out if not any(
            a != b and b[0] <= a[0] and b[1] <= a[1] and a[2] <= b[2] and a[3] <= b[3] for b in out)]


def pack(pieces, sw, sh, gap):
    """Pack pieces onto as few sheets as possible, trying each part both
    upright and rotated 90 degrees. Returns a list of (width, height,
    [placed pieces]); a part too big for the sheet gets a sheet of its own."""
    parts = sorted((pc.normalized() for pc in pieces),
                   key=lambda pc: (-max(pc.size), -pc.size[0] * pc.size[1]))
    sheets = []
    for pc in parts:
        rot = pc.rotated()
        best = None
        for sheet in sheets:
            for cand, penalty in ((pc, 0), (rot, 1e-6)):  # prefer upright on ties
                spot = sheet.best_spot(*cand.size)
                if spot and (best is None or (spot[0], penalty) < best[0]):
                    best = ((spot[0], penalty), sheet, cand, spot[1], spot[2])
            if best:
                break  # fill earlier sheets first
        if best:
            _, sheet, cand, x, y = best
            sheet.place(cand, x, y)
            continue
        w, h = pc.size
        if w + 2 * gap > sw or h + 2 * gap > sh:
            rw, rh = rot.size
            if rw + 2 * gap <= sw and rh + 2 * gap <= sh:
                pc, w, h = rot, rw, rh
        sheet = _Sheet(max(sw, w + 2 * gap), max(sh, h + 2 * gap), gap)
        sheet.place(pc, gap, gap)
        sheets.append(sheet)
    return [(s.w, s.h, s.items) for s in sheets]


def _d(pts, closed):
    d = "M" + " L".join(f"{x:.3f},{y:.3f}" for x, y in pts)
    return d + " Z" if closed else d


def write_svg(path, w, h, items):
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.3f}mm" height="{h:.3f}mm" '
        f'viewBox="0 0 {w:.3f} {h:.3f}">',
    ]
    # Engraving first, then inner cuts, then outlines (pieces keep that order).
    for pc in items:
        out.append(f'  <g id="{pc.name}">')
        for pts, closed in pc.engraves:
            out.append(f'    <path d="{_d(pts, closed)}" fill="none" stroke="{ENGRAVE}" stroke-width="{STROKE}"/>')
        for x, y, size, text, ang, anchor in pc.texts:
            rot = f' transform="rotate({ang:g} {x:.3f} {y:.3f})"' if ang else ""
            out.append(f'    <text x="{x:.3f}" y="{y:.3f}" font-family="Arial, Helvetica, sans-serif" '
                       f'font-size="{size:.3f}" text-anchor="{anchor}" fill="{ENGRAVE}"{rot}>{text}</text>')
        for pts, closed in pc.cuts:
            out.append(f'    <path d="{_d(pts, closed)}" fill="none" stroke="{CUT}" stroke-width="{STROKE}"/>')
        out.append("  </g>")
    out.append("</svg>")
    Path(path).write_text("\n".join(out) + "\n")


# --------------------------------------------------------------------------
# Pattern image
# --------------------------------------------------------------------------

MAX_GRID = 300  # tiles per side; beyond this the layout gets impractically slow
MAX_GRID_PBN = 1500  # paint-by-number cells per side (cells aren't cut pieces)


def grid_limit(s):
    return MAX_GRID_PBN if s.kit_type == "paint_by_number" else MAX_GRID


# Bundled emoji (see fetch_emoji.py). An image setting of "emoji:<code> <name>",
# e.g. "emoji:1f33b sunflower", reads the PNG straight from the zip.
EMOJI_ZIP = Path(__file__).resolve().parent / "emoji" / "twemoji-72.zip"
EMOJI_PREFIX = "emoji:"
EMOJI_CREDIT = "Emoji graphics: Twemoji, CC BY 4.0 (Twitter, Inc and other contributors)"
# Shown in the picker before anything is typed.
EMOJI_STARTERS = ("1f600 1f60e 1f60d 1f4a9 1f47e 1f916 1f47b 1f383 2764 1f499 1f49a 1f49b 2b50 1f31f "
                  "1f308 2600 1f319 2601 26a1 2744 1f525 1f30a 1f33b 1f337 1f339 1f335 1f332 1f344 "
                  "1f34e 1f353 1f349 1f34c 1f352 1f951 1f355 1f354 1f369 1f36a 1f382 1f366 2615 1f431 "
                  "1f436 1f98a 1f43b 1f43c 1f438 1f427 1f989 1f41d 1f98b 1f422 1f419 1f988 1f433 1f984 "
                  "1f409 1f680 1f697 2708 26bd 1f3c0 1f3b8 1f3ae 1f381 1f388 1f451 1f48e 1f3e0").split()


def is_emoji(image):
    return image.startswith(EMOJI_PREFIX)


def _emoji_zip():
    if not EMOJI_ZIP.exists():
        raise ValueError(f"Emoji set not found at {EMOJI_ZIP}; run fetch_emoji.py")
    import zipfile
    return zipfile.ZipFile(EMOJI_ZIP)


_emoji_names = {}


def emoji_index():
    """{code: name} for every bundled emoji, e.g. {"1f33b": "sunflower"}."""
    if not _emoji_names:
        with _emoji_zip() as z:
            _emoji_names.update(json.loads(z.read("index.json")))
    return _emoji_names


def emoji_png(code):
    with _emoji_zip() as z:
        return z.read(f"72x72/{code}.png")


def emoji_char(code):
    return "".join(chr(int(h, 16)) for h in code.split("-"))


def emoji_search(query, limit=80):
    """Codes matching `query`: pasted emoji characters, or words that must all
    appear in the name (exact and prefix matches first)."""
    index = emoji_index()
    q = query.strip().lower()
    if not q:
        return [c for c in EMOJI_STARTERS if c in index][:limit]
    if any(ord(ch) > 0x2000 for ch in q):
        code = "-".join(f"{ord(ch):x}" for ch in q.replace(" ", ""))
        for cand in (code, "-".join(p for p in code.split("-") if p != "fe0f")):
            if cand in index:
                return [cand]
        return []
    words = q.split()
    hits = [c for c, name in index.items() if all(w in name for w in words)]

    def rank(c):
        name = index[c]
        whole = all(w in name.replace("+", " ").split() for w in words)
        return name != q, not whole, "skin tone" in name, not name.startswith(q), len(name), c

    hits.sort(key=rank)
    return hits[:limit]


def emoji_image_value(code):
    return f"{EMOJI_PREFIX}{code} {emoji_index().get(code, '')}".strip()


SOURCE_KEY = "pixelkit:source"  # PNG text chunk naming what a scaled image came from


def image_source(path):
    """Where a picture came from: "emoji:<code> <name>" for a bundled emoji,
    the source recorded in a pixelkit-written PNG (so an edited emoji keeps
    its credit), else ""."""
    if is_emoji(path):
        return path
    from PIL import Image
    try:
        with Image.open(os.path.expanduser(path)) as im:
            return str(im.info.get(SOURCE_KEY, ""))
    except OSError:
        return ""


def _rgb_image(path, background="#ffffff"):
    import io
    from PIL import Image, ImageColor

    if is_emoji(path):
        src = io.BytesIO(emoji_png(path[len(EMOJI_PREFIX):].split()[0]))
    else:
        src = os.path.expanduser(path)
    im = Image.open(src).convert("RGBA")
    bg = Image.new("RGBA", im.size, ImageColor.getrgb(background or "#ffffff") + (255,))
    return Image.alpha_composite(bg, im).convert("RGB")


def reduce_colors(pixels, max_colors, merge_dist=24.0):
    """Map RGB pixels onto at most `max_colors` colors, then merge colors
    closer than `merge_dist` (JPEG noise otherwise splits one flat color
    into several near-identical palette entries)."""
    from PIL import Image

    counts = defaultdict(int)
    for px in pixels:
        counts[px] += 1
    if len(counts) > max_colors:
        im = Image.new("RGB", (len(pixels), 1))
        im.putdata(pixels)
        method = getattr(Image, "Quantize", Image).MEDIANCUT
        q = im.quantize(colors=max_colors, method=method)
        pal = q.getpalette()
        pixels = [tuple(pal[3 * i:3 * i + 3]) for i in q.getdata()]
        counts = defaultdict(int)
        for px in pixels:
            counts[px] += 1
    # Greedy merge: fold each color into a more common one if it is close.
    remap = {}
    kept = []
    for c in sorted(counts, key=lambda c: -counts[c]):
        near = [k for k in kept if math.dist(c, k) < merge_dist]
        remap[c] = near[0] if near else c
        if not near:
            kept.append(c)
    return [remap[px] for px in pixels]


def add_zone_colors(raw, pixels, width, boxes, max_colors, min_samples, merge_dist=24.0):
    """Give each detail zone its own colors: the small bright or dark spots
    that the whole picture's palette has no entry for, like a highlight in an
    eye. Repeatedly takes the color farthest from every color so far, if at
    least min_samples pixels are near it (colors are pooled in 32-level bins).
    Only pixels far from their assigned color then move to the nearest color,
    so flat areas and the zone's edge don't change. `boxes` are (x0, y0, x1,
    y1) sample ranges. Returns the number of colors added."""
    d2 = lambda a, b: (a[0] - b[0]) * (a[0] - b[0]) + (a[1] - b[1]) * (a[1] - b[1]) + (a[2] - b[2]) * (a[2] - b[2])
    near2 = merge_dist * merge_dist
    known = sorted(set(pixels))
    added = 0
    for x0, y0, x1, y1 in boxes:
        spots = [y * width + x for y in range(y0, y1) for x in range(x0, x1)]
        bins = {}
        for k in spots:
            r, g, b = raw[k]
            sums = bins.setdefault((r >> 3, g >> 3, b >> 3), [0, 0, 0, 0])
            sums[0] += r
            sums[1] += g
            sums[2] += b
            sums[3] += 1
        pool = [(s[0] // s[3], s[1] // s[3], s[2] // s[3], s[3]) for _, s in sorted(bins.items())]
        spotted = []  # (color, samples near it)
        for c in pool:
            if any(d2(c, k) < near2 for k in known):
                continue
            group = [q for q in pool if d2(c, q) < near2]
            n = sum(q[3] for q in group)
            spotted.append((tuple(sum(q[ch] * q[3] for q in group) // n for ch in range(3)), n))
        fresh = []
        while len(fresh) < max_colors:
            best, far = None, near2 - 1
            for c, n in spotted:
                d = min(d2(c, k) for k in known + fresh)
                if n >= min_samples and d > far:
                    best, far = c, d
            if best is None:
                break
            fresh.append(best)
        known += fresh
        for k in spots:
            best, near = pixels[k], d2(raw[k], pixels[k])
            if near >= near2:  # badly matched: a highlight lumped in with the dark around it
                for c in known:
                    d = d2(raw[k], c)
                    if d < near:
                        best, near = c, d
                pixels[k] = best
        added += len(fresh)
    return added


def detect_grid(path):
    """Guess the pixel-art grid (cols, rows) of an image, e.g. 40 x 50 for
    art drawn on a 40 x 50 grid and saved at 1856 x 2304 px. Returns None if
    no clear cell size is found. Works from the spacing between neighbouring
    color edges, so it copes with AI-generated art whose cells drift."""
    from PIL import Image, ImageChops

    im = _rgb_image(path)
    w, h = im.size
    k = max(1.0, max(w, h) / 1000)  # analyse at most ~1000 px per side
    if k > 1:
        im = im.resize((round(w / k), round(h / k)), getattr(Image, "Resampling", Image).NEAREST)
    sw, sh = im.size
    gaps = []
    for axis in (0, 1):
        if axis == 0:
            diff = ImageChops.difference(im.crop((1, 0, sw, sh)), im.crop((0, 0, sw - 1, sh)))
        else:
            diff = ImageChops.difference(im.crop((0, 1, sw, sh)), im.crop((0, 0, sw, sh - 1)))
        edges = diff.convert("L").point(lambda v: 255 if v > 16 else 0)
        n = edges.size[axis]
        prof = list(edges.resize((n, 1) if axis == 0 else (1, n), Image.BOX).getdata())
        top = max(prof)
        if top == 0:
            continue
        peaks = [i for i in range(n) if prof[i] > 0.1 * top
                 and prof[i] == max(prof[max(0, i - 2):i + 3])]
        peaks = [p for i, p in enumerate(peaks) if i == 0 or p - peaks[i - 1] > 2]
        gaps += [b - a for a, b in zip(peaks, peaks[1:])]
    if len(gaps) < 6:
        return None

    def score(g):  # gaps that are whole multiples of g, single cells counting most
        s = 0.0
        for gap in gaps:
            m = round(gap / g)
            if m >= 1 and abs(gap - m * g) <= max(1.5, 0.04 * gap):
                s += 1 / m
        return s

    cell = max(sorted(set(gaps)), key=lambda g: (score(g), g))
    singles = [gap for gap in gaps if abs(gap - cell) <= max(1.5, 0.04 * cell)]
    cell = sum(singles) / len(singles) * k
    if cell < 1.5:
        return w, h
    return max(1, round(w / cell)), max(1, round(h / cell))


def load_pattern(path, cols, rows, max_colors, background="#ffffff", fit=None, zones=(), zone_colors=2, zone_spot=1):
    """Reduce an image to a cols x rows grid of palette indices, most common
    color first.

    An image larger than the grid is scaled down to fit inside it, keeping
    its aspect ratio; one that already fits is used 1:1. Either way it is
    centred and any leftover rows/columns are filled with the most common
    border color. When scaling down, each tile takes the majority color of
    its area rather than an average, so pixel art stays crisp.

    For canvases, fit="fit" also enlarges a small picture (nearest pixel, so
    colors stay flat) until it fits, and fit="fill" first crops the picture,
    centred, to the grid's shape so it covers the whole grid.

    `zones` are (i0, j0, i1, j1) cell ranges that also get add_zone_colors."""
    from PIL import Image

    resampling = getattr(Image, "Resampling", Image)
    im = _rgb_image(path, background)
    w, h = im.size
    note = f"Pattern: {path.split()[0] if is_emoji(path) else 'image'} {w} x {h} px"
    if fit == "fill" and w * rows != h * cols:
        if w * rows > h * cols:  # wider than the grid
            cw = max(1, round(h * cols / rows))
            x0 = (w - cw) // 2
            im = im.crop((x0, 0, x0 + cw, h))
        else:
            ch = max(1, round(w * rows / cols))
            y0 = (h - ch) // 2
            im = im.crop((0, y0, w, y0 + ch))
        w, h = im.size
        note += f" cropped to {w} x {h}"
    scale = min(cols / w, rows / h)
    nw, nh = w, h
    sub = 1  # samples per tile side used for the majority vote
    if scale < 1:
        nw = cols if fit == "fill" else min(cols, max(1, round(w * scale)))
        nh = rows if fit == "fill" else min(rows, max(1, round(h * scale)))
        sub = max(1, min(8, int(min(w / nw, h / nh))))
        im = im.resize((nw * sub, nh * sub), resampling.BOX)
        note += f" scaled to {nw} x {nh} (majority color per tile)"
    elif scale > 1 and fit:
        nw = cols if fit == "fill" else min(cols, max(1, round(w * scale)))
        nh = rows if fit == "fill" else min(rows, max(1, round(h * scale)))
        im = im.resize((nw, nh), resampling.NEAREST)
        note += f" enlarged to {nw} x {nh} (nearest pixel)"
    raw = list(im.getdata())
    pixels = reduce_colors(raw, max_colors)
    iw = nw * sub
    ox, oy = (cols - nw) // 2, (rows - nh) // 2
    if zones:
        boxes = [(max(0, i0 - ox) * sub, max(0, j0 - oy) * sub, min(nw, i1 - ox) * sub, min(nh, j1 - oy) * sub)
                 for i0, j0, i1, j1 in zones]
        added = add_zone_colors(raw, pixels, iw, boxes, zone_colors, zone_spot * sub * sub)
        if added:
            note += f", {added} color(s) added in detail zones"
    cells = []
    for j in range(nh):
        row = []
        for i in range(nw):
            votes = defaultdict(int)
            for y in range(j * sub, (j + 1) * sub):
                for x in range(i * sub, (i + 1) * sub):
                    votes[pixels[y * iw + x]] += 1
            row.append(max(votes, key=votes.get))
        cells.append(row)
    if (nw, nh) != (cols, rows):
        border = cells[0] + cells[-1] + [r[0] for r in cells] + [r[-1] for r in cells]
        bg = max(set(border), key=border.count)
        cells = [[cells[j - oy][i - ox] if 0 <= j - oy < nh and 0 <= i - ox < nw else bg
                  for i in range(cols)] for j in range(rows)]
        note += f", centred and padded with {hexcolor(bg)}"
    counts = defaultdict(int)
    for row in cells:
        for c in row:
            counts[c] += 1
    palette = sorted(counts, key=lambda c: (-counts[c], c))
    index = {c: i for i, c in enumerate(palette)}
    grid = [[index[c] for c in row] for row in cells]
    return {"palette": palette, "counts": [counts[c] for c in palette], "grid": grid, "note": note,
            "source": image_source(path)}


def hexcolor(rgb):
    return "#%02x%02x%02x" % rgb


def text_color(rgb):
    return "#000000" if 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2] > 140 else "#ffffff"


def write_pattern_svg(path, s, pattern, needed):
    """Printable chart: numbered colored grid plus a legend with tile counts."""
    cols, rows = s.cols, s.rows
    cell = min(8.0, 180.0 / cols, 230.0 / rows)
    m = 12.0
    gw, gh = cols * cell, rows * cell
    credit = is_emoji(pattern.get("source", ""))
    legend_h = 8 + 7 * len(pattern["palette"]) + (8 if credit else 0)
    w, h = max(gw + 2 * m, 120), gh + 2 * m + legend_h
    fs = cell * 0.45
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.1f}mm" height="{h:.1f}mm" viewBox="0 0 {w:.1f} {h:.1f}" '
        'font-family="Arial, Helvetica, sans-serif">',
        f'<rect width="{w:.1f}" height="{h:.1f}" fill="#ffffff"/>',
        f'<text x="{m}" y="{m * 0.55:.1f}" font-size="5">{s.name}: {cols} x {rows} @ {s.pixel:g} mm</text>',
    ]
    for j, row in enumerate(pattern["grid"]):
        for i, idx in enumerate(row):
            rgb = pattern["palette"][idx]
            x, y = m + i * cell, m + j * cell
            out.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{cell:.2f}" height="{cell:.2f}" fill="{hexcolor(rgb)}"/>')
            out.append(f'<text x="{x + cell / 2:.2f}" y="{y + cell / 2 + fs * 0.35:.2f}" font-size="{fs:.2f}" '
                       f'text-anchor="middle" fill="{text_color(rgb)}">{idx + 1}</text>')
    for i in range(cols + 1):
        sw = 0.4 if i % 5 == 0 else 0.1
        out.append(f'<line x1="{m + i * cell:.2f}" y1="{m}" x2="{m + i * cell:.2f}" y2="{m + gh:.2f}" '
                   f'stroke="#333" stroke-width="{sw}"/>')
    for j in range(rows + 1):
        sw = 0.4 if j % 5 == 0 else 0.1
        out.append(f'<line x1="{m}" y1="{m + j * cell:.2f}" x2="{m + gw:.2f}" y2="{m + j * cell:.2f}" '
                   f'stroke="#333" stroke-width="{sw}"/>')
    lf = min(2.5, cell * 0.4)
    for i in range(cols):
        out.append(f'<text x="{m + (i + 0.5) * cell:.2f}" y="{m - 1.2:.2f}" font-size="{lf:.2f}" '
                   f'text-anchor="middle" fill="#555">{i + 1}</text>')
    for j in range(rows):
        out.append(f'<text x="{m - 1.2:.2f}" y="{m + (j + 0.5) * cell + lf * 0.35:.2f}" font-size="{lf:.2f}" '
                   f'text-anchor="end" fill="#555">{j + 1}</text>')
    y = m + gh + 8
    out.append(f'<text x="{m}" y="{y:.1f}" font-size="3.5" font-weight="bold">#   color      in picture   with spares</text>')
    for idx, rgb in enumerate(pattern["palette"]):
        y += 7
        out.append(f'<rect x="{m}" y="{y - 4.5:.1f}" width="5" height="5" fill="{hexcolor(rgb)}" stroke="#333" stroke-width="0.2"/>')
        out.append(f'<text x="{m + 7}" y="{y - 0.5:.1f}" font-size="3.5">{idx + 1}   {hexcolor(rgb)}   '
                   f'{pattern["counts"][idx]}   {needed[idx]}</text>')
    if credit:
        out.append(f'<text x="{m}" y="{y + 8:.1f}" font-size="2.8" fill="#555">{EMOJI_CREDIT}</text>')
    out.append("</svg>")
    Path(path).write_text("\n".join(out) + "\n")


# --------------------------------------------------------------------------
# Paint by number
# --------------------------------------------------------------------------

PBN_LINE = "#0000ff"         # region outlines (LightBurn layer 01)
PBN_NUMBER = "#00e000"       # numbers (LightBurn layer 03), a separate layer
PBN_PRINT_LINE = "#000000"
PBN_PRINT_NUMBER = "#555555"

_N4 = ((0, -1), (0, 1), (-1, 0), (1, 0))  # (di, dj): up, down, left, right
_N8 = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))


def label_regions(grid):
    """4-connected same-color regions. Returns (labels, sizes): labels[j][i]
    is a region id, ids numbered in row-major discovery order."""
    rows, cols = len(grid), len(grid[0])
    lab = [[-1] * cols for _ in range(rows)]
    sizes = []
    for j in range(rows):
        for i in range(cols):
            if lab[j][i] >= 0:
                continue
            rid, color = len(sizes), grid[j][i]
            lab[j][i] = rid
            queue, k = [(i, j)], 0
            while k < len(queue):
                ci, cj = queue[k]
                k += 1
                for di, dj in _N4:
                    ni, nj = ci + di, cj + dj
                    if 0 <= ni < cols and 0 <= nj < rows and lab[nj][ni] < 0 and grid[nj][ni] == color:
                        lab[nj][ni] = rid
                        queue.append((ni, nj))
            sizes.append(len(queue))
    return lab, sizes


def region_depths(grid, lab):
    """Per cell, the 8-neighbour steps to the edge of its region (1 = on the
    edge), by multi-source BFS from edge cells in row-major order."""
    rows, cols = len(grid), len(grid[0])
    depth = [[0] * cols for _ in range(rows)]
    queue = []
    for j in range(rows):
        for i in range(cols):
            for di, dj in _N8:
                ni, nj = i + di, j + dj
                if not (0 <= ni < cols and 0 <= nj < rows) or lab[nj][ni] != lab[j][i]:
                    depth[j][i] = 1
                    queue.append((i, j))
                    break
    k = 0
    while k < len(queue):
        i, j = queue[k]
        k += 1
        for di, dj in _N8:
            ni, nj = i + di, j + dj
            if 0 <= ni < cols and 0 <= nj < rows and depth[nj][ni] == 0:
                depth[nj][ni] = depth[j][i] + 1
                queue.append((ni, nj))
    return depth


def absorb_small_regions(grid, min_cells, min_depth, zones=(), zone_cells=1, zone_depth=1):
    """Remove regions too small (under min_cells) or too thin (no cell
    min_depth deep) to paint and number. Their cells go to the surrounding
    regions, which grow into them together (4-neighbour BFS from every kept
    cell, row-major): a speck fills in, and a fringe between two areas
    splits down the middle. A region at least half inside `zones` (cell
    ranges) has the zone limits instead. Returns (new grid, number of regions
    removed)."""
    rows, cols = len(grid), len(grid[0])
    lab, sizes = label_regions(grid)
    depth = region_depths(grid, lab)
    deepest = [0] * len(sizes)
    for j in range(rows):
        for i in range(cols):
            if depth[j][i] > deepest[lab[j][i]]:
                deepest[lab[j][i]] = depth[j][i]
    inside = [0] * len(sizes)
    for j, i in sorted({(j, i) for i0, j0, i1, j1 in zones for j in range(j0, j1) for i in range(i0, i1)}):
        inside[lab[j][i]] += 1
    keep = [sizes[r] >= (zone_cells if 2 * inside[r] >= sizes[r] else min_cells)
            and deepest[r] >= (zone_depth if 2 * inside[r] >= sizes[r] else min_depth) for r in range(len(sizes))]
    removed = keep.count(False)
    if removed == 0 or removed == len(sizes):
        return grid, 0
    out = [row[:] for row in grid]
    done = [[keep[lab[j][i]] for i in range(cols)] for j in range(rows)]
    queue = [(i, j) for j in range(rows) for i in range(cols) if done[j][i]]
    k = 0
    while k < len(queue):
        i, j = queue[k]
        k += 1
        for di, dj in _N4:
            ni, nj = i + di, j + dj
            if 0 <= ni < cols and 0 <= nj < rows and not done[nj][ni]:
                done[nj][ni] = True
                out[nj][ni] = out[j][i]
                queue.append((ni, nj))
    return out, removed


def outline_chains(grid):
    """Region boundaries as polylines in grid units, each shared border once.
    Chains run between junctions (vertices where the boundary branches, plus
    the picture's four corners); boundaries with no junction are closed
    loops. Returns [(points, closed)]."""
    rows, cols = len(grid), len(grid[0])
    # hor[y][x]: edge (x, y)-(x+1, y); ver[y][x]: edge (x, y)-(x, y+1)
    hor = [[y == 0 or y == rows or grid[y - 1][x] != grid[y][x] for x in range(cols)] for y in range(rows + 1)]
    ver = [[x == 0 or x == cols or grid[y][x - 1] != grid[y][x] for x in range(cols + 1)] for y in range(rows)]
    nh = (rows + 1) * cols
    seen = bytearray(nh + rows * (cols + 1))

    def links(x, y):  # incident edges (id, far end) in fixed order: right, down, left, up
        out = []
        if x < cols and hor[y][x]:
            out.append((y * cols + x, (x + 1, y)))
        if y < rows and ver[y][x]:
            out.append((nh + y * (cols + 1) + x, (x, y + 1)))
        if x > 0 and hor[y][x - 1]:
            out.append((y * cols + x - 1, (x - 1, y)))
        if y > 0 and ver[y - 1][x]:
            out.append((nh + (y - 1) * (cols + 1) + x, (x, y - 1)))
        return out

    def junction(x, y):
        return ((x == 0 or x == cols) and (y == 0 or y == rows)) or len(links(x, y)) not in (0, 2)

    chains = []

    def walk(start, edge, nxt):
        pts = [start]
        seen[edge] = 1
        cur = nxt
        while True:
            pts.append(cur)
            if junction(*cur) or cur == start:
                return pts
            follow = [(e, n) for e, n in links(*cur) if not seen[e]]
            if not follow:
                return pts
            e, n = follow[0]
            seen[e] = 1
            cur = n

    for y in range(rows + 1):
        for x in range(cols + 1):
            if junction(x, y):
                for e, n in links(x, y):
                    if not seen[e]:
                        chains.append((walk((x, y), e, n), False))
    for y in range(rows + 1):
        for x in range(cols + 1):
            for e, n in links(x, y):
                if not seen[e]:
                    pts = walk((x, y), e, n)
                    chains.append((pts[:-1], True))  # last point repeats the start
    return chains


def smooth_chain(pts, closed, passes=2):
    """Round off pixel steps: take edge midpoints, then Chaikin corner
    cutting. Open chains keep their end points (junctions stay put, so
    neighbouring chains still meet)."""
    if closed:
        q = [((pts[k][0] + pts[(k + 1) % len(pts)][0]) / 2, (pts[k][1] + pts[(k + 1) % len(pts)][1]) / 2)
             for k in range(len(pts))]
    else:
        if len(pts) < 3:
            return [(float(x), float(y)) for x, y in pts]
        q = ([(float(pts[0][0]), float(pts[0][1]))]
             + [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(pts, pts[1:])]
             + [(float(pts[-1][0]), float(pts[-1][1]))])
    for _ in range(passes):
        r = [] if closed else [q[0]]
        n = len(q) if closed else len(q) - 1
        for k in range(n):
            a, b = q[k], q[(k + 1) % len(q)]
            r.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            r.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
        if not closed:
            r.append(q[-1])
        q = r
    return q


def _collinear(a, b, c):
    return abs((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])) < 1e-9


def drop_collinear(pts, closed):
    """Remove points lying on the straight line through their neighbours
    (one pass, comparing against the last point kept)."""
    if closed:
        n = len(pts)
        start = next((k for k in range(n) if not _collinear(pts[k - 1], pts[k], pts[(k + 1) % n])), None)
        if start is None:
            return list(pts)
        ring = pts[start:] + pts[:start] + [pts[start]]
        return drop_collinear(ring, False)[:-1]
    if len(pts) < 3:
        return list(pts)
    out = [pts[0]]
    for k in range(1, len(pts) - 1):
        if not _collinear(out[-1], pts[k], pts[k + 1]):
            out.append(pts[k])
    out.append(pts[-1])
    return out


def _dp(pts, tol2):
    """Douglas-Peucker on an open polyline with squared tolerance tol2,
    keeping both ends (iterative, so long chains don't recurse deeply)."""
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        best, far = -1, -1.0
        for k in range(a + 1, b):
            px, py = pts[k]
            if l2 == 0:
                d = (px - ax) * (px - ax) + (py - ay) * (py - ay)
            else:
                c = dx * (ay - py) - (ax - px) * dy
                d = c * c / l2
            if d > far:
                far, best = d, k
        if far > tol2:
            keep[best] = True
            stack.append((a, best))
            stack.append((best, b))
    return [p for p, k in zip(pts, keep) if k]


def simplify_chain(pts, closed, tol):
    """Drop points within tol of the simplified line (Douglas-Peucker). A
    closed loop is split at its first point and the point farthest from it."""
    if tol <= 0 or len(pts) < 3:
        return list(pts)
    tol2 = tol * tol
    if not closed:
        return _dp(pts, tol2)
    ax, ay = pts[0]
    far, best = -1.0, 0
    for k in range(1, len(pts)):
        d = (pts[k][0] - ax) * (pts[k][0] - ax) + (pts[k][1] - ay) * (pts[k][1] - ay)
        if d > far:
            far, best = d, k
    first = _dp(pts[:best + 1], tol2)
    second = _dp(pts[best:] + [pts[0]], tol2)
    out = first[:-1] + second[:-1]
    return out if len(out) >= 3 else list(pts)


def chain_sides(chain, labels):
    """Region ids on the two sides of a chain's first edge (-1 off the grid):
    side A lies along the normal (dy, -dx) of travel, side B opposite."""
    rows, cols = len(labels), len(labels[0])
    (x0, y0), (x1, y1) = chain[0], chain[1]
    dx, dy = x1 - x0, y1 - y0
    mx, my = x0 + dx / 2, y0 + dy / 2

    def region(px, py):
        i, j = math.floor(px), math.floor(py)
        return labels[j][i] if 0 <= i < cols and 0 <= j < rows else -1

    return region(mx + dy / 2, my - dx / 2), region(mx - dy / 2, my + dx / 2)


def region_loops(chains, sides, nregions):
    """Closed outlines for each region, built from the (smoothed) chains so
    fills meet the drawn lines exactly. Returns {region: [loop, ...]}."""
    pieces = defaultdict(list)  # region -> [points], each oriented with the region on side A
    for (pts, closed), (a, b) in zip(chains, sides):
        ring = pts + [pts[0]] if closed else pts
        if a >= 0:
            pieces[a].append(list(ring))
        if b >= 0:
            pieces[b].append(list(reversed(ring)))
    loops = {}
    for rid in range(nregions):
        todo = pieces.get(rid, [])
        used = [False] * len(todo)
        out = []
        for k in range(len(todo)):
            if used[k]:
                continue
            used[k] = True
            loop = list(todo[k])
            while loop[-1] != loop[0]:
                nxt = next((m for m in range(len(todo)) if not used[m] and todo[m][0] == loop[-1]), None)
                if nxt is None:
                    break
                used[nxt] = True
                loop += todo[nxt][1:]
            out.append(loop[:-1] if loop[-1] == loop[0] else loop)
        loops[rid] = out
    return loops


def number_spots(grid):
    """One label spot per region: the cell farthest from the region's edge
    (8-neighbour steps), ties broken by nearness to the region's centroid,
    then row-major order. Returns [(i, j, depth, color)] in region order."""
    rows, cols = len(grid), len(grid[0])
    lab, sizes = label_regions(grid)
    depth = region_depths(grid, lab)
    sx = [0.0] * len(sizes)
    sy = [0.0] * len(sizes)
    for j in range(rows):
        for i in range(cols):
            sx[lab[j][i]] += i + 0.5
            sy[lab[j][i]] += j + 0.5
    best = [None] * len(sizes)
    for j in range(rows):
        for i in range(cols):
            rid = lab[j][i]
            cx, cy = sx[rid] / sizes[rid], sy[rid] / sizes[rid]
            dx, dy = i + 0.5 - cx, j + 0.5 - cy
            key = (depth[j][i], -(dx * dx + dy * dy))
            if best[rid] is None or key > best[rid][0]:
                best[rid] = (key, i, j)
    return [(i, j, key[0], grid[j][i]) for key, i, j in best]


def canvas_mm(s):
    """(width, height) of the paint-by-number canvas in mm, or None when the
    board is sized from the grid."""
    if s.kit_type != "paint_by_number" or s.canvas == "off":
        return None
    if s.canvas == "custom":
        return s.canvas_width, s.canvas_height
    w, h = CANVAS_SIZES.get(s.canvas, (0.0, 0.0))
    return (h, w) if s.canvas_landscape else (w, h)


def effective_settings(s):
    """Settings with columns and rows worked out from the canvas, when one is
    set: as many cells of the chosen size as fit inside the margin."""
    c = canvas_mm(s)
    if not c:
        return s
    cols = math.floor((c[0] - 2 * s.pbn_margin) / s.pixel) if s.pixel > 0 else 0
    rows = math.floor((c[1] - 2 * s.pbn_margin) / s.pixel) if s.pixel > 0 else 0
    return replace(s, cols=cols, rows=rows)


def pbn_layout(s, cols, rows):
    """Board size and picture offset: the canvas with the grid centred on
    it, or the grid plus the margin."""
    c = canvas_mm(s)
    p = s.pixel
    if c:
        return c[0], c[1], (c[0] - cols * p) / 2, (c[1] - rows * p) / 2
    return cols * p + 2 * s.pbn_margin, rows * p + 2 * s.pbn_margin, s.pbn_margin, s.pbn_margin


def parse_zones(text):
    """Detail zones from "x0,y0,x1,y1;...", fractions of the grid. Raises
    ValueError if the text isn't a list of boxes inside 0..1."""
    zones = []
    for part in text.split(";"):
        if part.strip():
            x0, y0, x1, y1 = [float(v) for v in part.split(",")]
            if not 0 <= x0 < x1 <= 1 or not 0 <= y0 < y1 <= 1:
                raise ValueError(part)
            zones.append((x0, y0, x1, y1))
    return zones


def zone_cells(s):
    """Detail zones as (i0, j0, i1, j1) cell ranges of the effective grid."""
    return [(math.floor(x0 * s.cols), math.floor(y0 * s.rows), math.ceil(x1 * s.cols), math.ceil(y1 * s.rows))
            for x0, y0, x1, y1 in parse_zones(s.pbn_zones)]


def cells_for(area, p):
    return max(1, math.ceil(area / (p * p) - 1e-9))


def depth_for(width, p):
    return max(1, math.ceil((width / p + 1) / 2 - 1e-9))  # widest square (2d - 1) cells


def build_pbn(s, pattern):
    """Paint-by-number board from a pattern. Returns (board piece, width,
    height, merged pattern, regions per color, notes)."""
    p = s.pixel
    min_cells, min_depth = cells_for(s.pbn_min_area, p), depth_for(s.pbn_min_width, p)
    zones = zone_cells(s)
    grid, merged = absorb_small_regions(pattern["grid"], min_cells, min_depth, zones,
                                        cells_for(s.pbn_zone_area, p), depth_for(s.pbn_zone_width, p))
    # Renumber colors by tile count, as for mosaics (merging changes counts).
    counts = defaultdict(int)
    for row in grid:
        for idx in row:
            counts[idx] += 1
    order = sorted(counts, key=lambda idx: (-counts[idx], pattern["palette"][idx]))
    renum = {old: new for new, old in enumerate(order)}
    grid = [[renum[idx] for idx in row] for row in grid]
    merged_pattern = {**pattern, "palette": [pattern["palette"][i] for i in order],
                      "counts": [counts[i] for i in order], "grid": grid}

    rows, cols = len(grid), len(grid[0])
    w, h, ox, oy = pbn_layout(s, cols, rows)
    engrave = s.pbn_use == "engrave"
    board = Piece("paint_by_number")
    board.line_color = PBN_LINE if engrave else PBN_PRINT_LINE
    board.text_color = PBN_NUMBER if engrave else PBN_PRINT_NUMBER
    board.line_width = STROKE if engrave else s.pbn_line_width
    board.picture = [(ox, oy), (ox + cols * p, oy), (ox, oy + rows * p)]
    labels, sizes = label_regions(grid)
    chains = outline_chains(grid)
    sides = [chain_sides(pts, labels) for pts, _ in chains]
    shaped = []
    for pts, closed in chains:
        if s.pbn_smooth:
            pts = smooth_chain(pts, closed)
        shaped.append((simplify_chain(drop_collinear(pts, closed), closed, s.pbn_simplify / p), closed))
    to_mm = lambda pts: [(ox + x * p, oy + y * p) for x, y in pts]
    board.engraves = [(to_mm(pts), closed) for pts, closed in shaped]
    # Fills for the color preview: each color's region outlines as one path.
    region_color = [0] * len(sizes)
    for j in range(rows):
        for i in range(cols):
            region_color[labels[j][i]] = grid[j][i]
    board.fills = [[] for _ in order]
    for rid, loops in region_loops(shaped, sides, len(sizes)).items():
        board.fills[region_color[rid]] += [to_mm(loop) for loop in loops]

    regions = [0] * len(order)
    tight = 0
    for i, j, d, idx in number_spots(grid):
        regions[idx] += 1
        text = str(idx + 1)
        room = (2 * d - 1) * p
        size = min(s.pbn_text_max, 0.8 * room, 0.8 * room / (0.6 * len(text)))
        # Zones hold more, smaller regions, so their numbers may go down to half the minimum.
        least = s.pbn_text_min / 2 if any(i0 <= i < i1 and j0 <= j < j1 for i0, j0, i1, j1 in zones) else s.pbn_text_min
        if size < least:
            size = least
            tight += 1
        cx, cy = ox + (i + 0.5) * p, oy + (j + 0.5) * p
        board.texts.append((cx, cy + size * 0.35, size, text, 0.0, "middle"))
    if engrave:
        board.cuts.append((rect_pts(0, 0, w, h), True))

    notes = [
        f"Paint by number: {cols} x {rows} cells at {s.pixel:g} mm, board {w:.0f} x {h:.0f} mm "
        f"({s.pbn_use}); {sum(regions)} regions in {len(order)} colors"
        + (f", {merged} region(s) smaller than {s.pbn_min_area:g} mm\u00b2 or narrower than {s.pbn_min_width:g} mm "
           "absorbed by their neighbours" if merged else ""),
    ]
    if zones:
        notes.append(f"{len(zones)} detail zone(s): regions down to {s.pbn_zone_area:g} mm\u00b2 and "
                     f"{s.pbn_zone_width:g} mm wide kept.")
    c = canvas_mm(s)
    if c:
        name = "custom" if s.canvas == "custom" else s.canvas + (" landscape" if s.canvas_landscape else "")
        notes.append(f"Canvas {name}: {c[0]:g} x {c[1]:g} mm, picture {s.canvas_fit}, grid centred "
                     f"with {ox:.1f} / {oy:.1f} mm side / top margins.")
    if tight:
        notes.append(f"{tight} region(s) are too small for a {s.pbn_text_min:g} mm number; "
                     "their numbers overlap the outlines. Raise Min area or Min width.")
    if engrave:
        notes.append(f"Layers: {PBN_LINE} = region outlines, {PBN_NUMBER} = numbers, {CUT} = board cut line.")
    else:
        notes.append(f"Print: outlines {PBN_PRINT_LINE} at {s.pbn_line_width:g} mm, numbers {PBN_PRINT_NUMBER}.")
    if w > s.sheet_width or h > s.sheet_height:
        notes.append(f"WARNING: the {w:.0f} x {h:.0f} mm board is larger than the {s.sheet_width:g} x "
                     f"{s.sheet_height:g} mm sheet.")
    return board, w, h, merged_pattern, regions, notes


def pbn_svg(w, h, board):
    """Board SVG: outlines, numbers and (engrave) cut line in separate groups
    and colors, so each can get its own laser settings."""
    print_style = board.line_color != PBN_LINE
    cap = ' stroke-linejoin="round" stroke-linecap="round"' if print_style else ""
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.3f}mm" height="{h:.3f}mm" '
        f'viewBox="0 0 {w:.3f} {h:.3f}">',
        f'  <g id="outlines" fill="none" stroke="{board.line_color}" stroke-width="{board.line_width:g}"{cap}>',
    ]
    out += [f'    <path d="{_d(pts, closed)}"/>' for pts, closed in board.engraves]
    out.append("  </g>")
    out.append(f'  <g id="numbers" fill="{board.text_color}" font-family="Arial, Helvetica, sans-serif" '
               'text-anchor="middle">')
    out += [f'    <text x="{x:.3f}" y="{y:.3f}" font-size="{size:.3f}">{text}</text>'
            for x, y, size, text, _, _ in board.texts]
    out.append("  </g>")
    if board.cuts:
        out.append(f'  <g id="cut" fill="none" stroke="{CUT}" stroke-width="{STROKE}">')
        out += [f'    <path d="{_d(pts, closed)}"/>' for pts, closed in board.cuts]
        out.append("  </g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def pbn_preview_svg(w, h, board, pattern):
    """The finished picture in color, with the outlines, for approval."""
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.3f}mm" height="{h:.3f}mm" '
        f'viewBox="0 0 {w:.3f} {h:.3f}">',
        f'  <rect width="{w:.3f}" height="{h:.3f}" fill="#ffffff"/>',
    ]
    for idx, loops in enumerate(board.fills):  # one even-odd path per color
        if loops:
            d = " ".join(_d(loop, True) for loop in loops)
            out.append(f'  <path d="{d}" fill="{hexcolor(pattern["palette"][idx])}" fill-rule="evenodd"/>')
    out.append('  <g fill="none" stroke="#333333" stroke-width="0.15">')
    out += [f'    <path d="{_d(pts, closed)}"/>' for pts, closed in board.engraves]
    out.append("  </g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def pbn_key_svg(s, pattern, regions, credit):
    """Color key: swatch, number, hex, regions and cells per color."""
    n = len(pattern["palette"])
    w, h = 120.0, 20.0 + 9.0 * n + (8.0 if credit else 0.0)
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.1f}mm" height="{h:.1f}mm" viewBox="0 0 {w:.1f} {h:.1f}" '
        'font-family="Arial, Helvetica, sans-serif">',
        f'<rect width="{w:.1f}" height="{h:.1f}" fill="#ffffff"/>',
        f'<text x="10.0" y="10.0" font-size="5">{s.name}: paint by number key</text>',
    ]
    y = 14.0
    for idx, rgb in enumerate(pattern["palette"]):
        out.append(f'<rect x="10.0" y="{y:.1f}" width="7" height="7" fill="{hexcolor(rgb)}" '
                   'stroke="#333" stroke-width="0.2"/>')
        out.append(f'<text x="13.5" y="{y + 5.0:.1f}" font-size="4" text-anchor="middle" '
                   f'fill="{text_color(rgb)}">{idx + 1}</text>')
        out.append(f'<text x="21.0" y="{y + 5.0:.1f}" font-size="3.5">{hexcolor(rgb)}   '
                   f'{regions[idx]} region(s), {pattern["counts"][idx]} cells</text>')
        y += 9.0
    if credit:
        out.append(f'<text x="10.0" y="{y + 4.0:.1f}" font-size="2.8" fill="#555">{EMOJI_CREDIT}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# Kit generation
# --------------------------------------------------------------------------

def validate(s):
    problems = []
    c = canvas_mm(s)
    if c:
        if s.canvas == "custom" and not (s.canvas_width > 0 and s.canvas_height > 0):
            problems.append("Custom canvas width and height must be positive.")
        s = effective_settings(s)
        if s.cols < 1 or s.rows < 1:
            problems.append("The canvas has no room for a cell inside the margin.")
        elif s.cols > grid_limit(s) or s.rows > grid_limit(s):
            problems.append(f"The canvas needs {s.cols} x {s.rows} cells of {s.pixel:g} mm; the limit is "
                            f"{grid_limit(s)} per side, so use a larger pixel size.")
    if not c and (s.cols < 1 or s.rows < 1):
        problems.append("Columns and rows must be at least 1.")
    if not c and (s.cols > grid_limit(s) or s.rows > grid_limit(s)):
        problems.append(f"Columns and rows are limited to {grid_limit(s)} "
                        f"{'cells' if s.kit_type == 'paint_by_number' else 'tiles'} each.")
    for k in ("pixel", "tile_thickness", "tray_thickness", "sheet_width", "sheet_height"):
        if getattr(s, k) <= 0:
            problems.append(f"{k} must be positive.")
    mosaic = s.kit_type != "paint_by_number"  # tray and tile rules don't apply to a board
    if mosaic and (s.kerf < 0 or s.kerf >= s.pixel):
        problems.append("Kerf must be between 0 and the pixel size.")
    if mosaic and (s.tile_kerf < -1 or s.tile_kerf >= s.pixel):
        problems.append("Tile kerf must be between -1 mm and the pixel size.")
    for key, options in CHOICES.items():
        if getattr(s, key) not in options:
            problems.append(f"{key} must be one of {options}.")
    if mosaic and s.tile_tabs and not 0 < s.tab_width < s.pixel / 2:
        problems.append("Tab width must be more than 0 and less than half the pixel size.")
    if mosaic and s.tray_style == "box" and s.finger_width < 2 * s.kerf + 1:
        problems.append("Finger width is too small.")
    try:
        from PIL import ImageColor
        ImageColor.getrgb(s.background or "#ffffff")
    except ValueError:
        problems.append(f"Background {s.background!r} is not a color (use e.g. #1e8cdc).")
    except ImportError:
        pass
    if s.kit_type == "paint_by_number":
        if not s.image:
            problems.append("Paint by number needs a picture.")
        if s.pbn_min_area < 0 or s.pbn_min_width < 0 or s.pbn_simplify < 0:
            problems.append("Min area, min width and simplify can't be negative.")
        if s.pbn_zone_area < 0 or s.pbn_zone_width < 0 or s.pbn_zone_colors < 1:
            problems.append("Zone area and width can't be negative, and zones need at least 1 color.")
        try:
            parse_zones(s.pbn_zones)
        except ValueError:
            problems.append("Detail zones must look like x0,y0,x1,y1;... with fractions from 0 to 1.")
        if not 0 < s.pbn_text_min <= s.pbn_text_max:
            problems.append("Number sizes must be positive, with the minimum no larger than the maximum.")
        if s.pbn_line_width <= 0 or s.pbn_margin < 0:
            problems.append("Line width must be positive and the board margin not negative.")
    if s.engrave_numbers and not s.image:
        problems.append("Engraving color numbers needs a pattern image.")
    return problems


def build(s):
    """Lay out every sheet. Returns (sheets, pattern, needed, notes) where
    sheets is a list of (file_stem, width, height, pieces)."""
    problems = validate(s)
    if problems:
        raise ValueError("\n".join(problems))
    fit = s.canvas_fit if canvas_mm(s) else None
    s = effective_settings(s)
    zones = zone_cells(s) if s.kit_type == "paint_by_number" else []
    spot = max(1, cells_for(s.pbn_zone_area, s.pixel) // 2)  # colors need only half a region's worth of cells
    pattern = (load_pattern(s.image, s.cols, s.rows, s.colors, s.background, fit, zones, s.pbn_zone_colors, spot)
               if s.image else None)
    notes = [pattern["note"]] if pattern else []
    if pattern and is_emoji(pattern["source"]):
        notes.append(EMOJI_CREDIT)
    sheets = []

    if s.kit_type == "paint_by_number":
        board, w, h, pattern, regions, pbn_notes = build_pbn(s, pattern)
        return [(f"{s.name}_pbn", w, h, [board])], pattern, regions, notes + pbn_notes

    if s.tray_style != "none":
        parts, info = (make_box if s.tray_style == "box" else make_layered)(s, pattern)
        notes += info["notes"]
        packed = pack(parts, s.sheet_width, s.sheet_height, s.spacing)
        for n, (w, h, items) in enumerate(packed, 1):
            sheets.append((f"{s.name}_tray_{n}", w, h, items))
        for w, h, _ in packed:
            if w > s.sheet_width or h > s.sheet_height:
                notes.append(f"WARNING: a tray part needs a {w:.0f} x {h:.0f} mm sheet, larger than the bed.")
                break

    if pattern and s.tile_sheets == "per_color":
        groups = [(f"c{i + 1:02d}", f"C{i + 1} {hexcolor(rgb)}", n)
                  for i, (rgb, n) in enumerate(zip(pattern["palette"], pattern["counts"]))]
    else:
        groups = [("all", "", s.cols * s.rows)]
    needed = []
    for key, label, count in groups:
        blocks = tile_blocks(s, tiles_to_cut(s, count), label, key)
        packed = pack(blocks, s.sheet_width, s.sheet_height, s.spacing)
        cut = sum(_tile_count(b, s) for b in blocks)
        needed.append(cut)
        for n, (w, h, items) in enumerate(packed, 1):
            sheets.append((f"{s.name}_tiles_{key}_{n}", w, h, items))
        spare = f"{cut - count} spare"
        if cut < math.ceil(count * (1 + s.spare_percent / 100) - 1e-9):
            spare += f", trimmed from {s.spare_percent:g}% to stay on {len(packed)} sheet(s)"
        notes.append(f"Tiles {label or 'all'}: {count} in picture, {cut} cut ({spare}) "
                     f"on {len(packed)} sheet(s) of {s.tile_thickness:g} mm stock")
    if s.tile_tabs:
        notes.append(f"Tile tabs: 2 per tile, {s.tab_width:g} mm wide, on the vertical cuts; rows come apart "
                     + ("and stay attached to the side frame." if s.tile_border > 0
                        else "(no tile border, so row ends are held only by the surrounding stock)."))
    if pattern and len(groups) == 1:
        needed = share_spares(pattern["counts"], needed[0])
        notes.append("Color the tiles after cutting: " + ", ".join(
            f"C{i + 1} {hexcolor(rgb)} x{n}" for i, (rgb, n) in enumerate(zip(pattern["palette"], needed))))
    return sheets, pattern, needed, notes


def _tile_count(block, s):
    # Interior lines = (c-1) + (r-1); recover c and r from the block size.
    p = s.pixel + s.tile_kerf
    w, h = block.size
    return round((w - 2 * s.tile_border) / p) * round((h - 2 * s.tile_border) / p)


def summary(s):
    s = effective_settings(s)
    if s.kit_type == "paint_by_number":
        mg = s.pbn_margin
        c = canvas_mm(s)
        if c:
            turned = " landscape" if s.canvas_landscape and s.canvas != "custom" else ""
            return "\n".join([
                f"Picture: {s.cols} x {s.rows} = {s.cols * s.rows} cells, "
                f"{s.cols * s.pixel:g} x {s.rows * s.pixel:g} mm ({s.canvas_fit})",
                f"Canvas {s.canvas}{turned}: {c[0]:.1f} x {c[1]:.1f} mm, {mg:g} mm minimum margin",
            ])
        return "\n".join([
            f"Picture: {s.cols} x {s.rows} = {s.cols * s.rows} cells, "
            f"{s.cols * s.pixel:g} x {s.rows * s.pixel:g} mm",
            f"Paint-by-number board ({s.pbn_use}): {s.cols * s.pixel + 2 * mg:.1f} x "
            f"{s.rows * s.pixel + 2 * mg:.1f} mm incl. {mg:g} mm margin",
        ])
    win, hin = tray_inner(s)
    t = s.tray_thickness
    lines = [
        f"Picture: {s.cols} x {s.rows} = {s.cols * s.rows} tiles, "
        f"{s.cols * s.pixel:g} x {s.rows * s.pixel:g} mm",
        f"Tile pitch on sheet: {s.pixel + s.tile_kerf:.3f} mm (pixel + tile kerf)",
        f"Tray inside: {win:.1f} x {hin:.1f} mm",
    ]
    if s.tray_style == "box":
        lines.append(f"Box outside: {win + 2 * t:.1f} x {hin + 2 * t:.1f} x "
                     f"{t + s.tile_thickness + s.lip:.1f} mm")
    elif s.tray_style == "layered":
        layers = max(1, math.ceil((s.tile_thickness + s.lip) / t - 1e-6))
        lines.append(f"Layered outside: {win + 2 * s.frame_width:.1f} x {hin + 2 * s.frame_width:.1f} mm, "
                     f"{layers} frame layer(s)")
    return "\n".join(lines)


def generate_kerf_test(s):
    """Write the kerf test sheet. Returns (path, sheets, notes)."""
    vals = kerf_test_values(s)
    pieces = make_kerf_test(s)
    # One column per kerf value, plate above its tab piece, in a small
    # file sized to the pieces so it can be placed on a scrap of stock.
    g = max(s.spacing, 2.0)
    items, x, h = [], g, 0.0
    for plate, tab in zip(pieces[::2], pieces[1::2]):
        plate, tab = plate.normalized(), tab.normalized()
        items.append(plate.moved(x, g))
        items.append(tab.moved(x, g + plate.size[1] + g))
        x += max(plate.size[0], tab.size[0]) + g
        h = max(h, g + plate.size[1] + g + tab.size[1] + g)
    w = x
    out = Path(os.path.expanduser(s.output_dir))
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{s.name}_kerf_test.svg"
    write_svg(path, w, h, items)
    notes = [
        f"Kerf test: {len(vals)} plate + tab pairs in {s.tray_thickness:g} mm stock, "
        f"kerf {vals[0]:.2f} to {vals[-1]:.2f} mm (joint play {s.joint_play:g} mm). Slots have "
        f"{KERF_TEST_ROOM:g} mm extra room across the stock, so only the finger-width fit is tested.",
        f"Sheet is {w:.0f} x {h:.0f} mm. Push each tab piece into the plate above it. Use the kerf of the pair "
        "that fits snugly by hand as the tray kerf; if none do, adjust Tray kerf and cut the test again.",
    ]
    return path, [(path.stem, w, h, items)], notes


def scaled_image_path(s, out):
    """Where to save the grid-sized pattern image: <original name>_scaled.png,
    or <emoji name>_scaled.png for an emoji."""
    if is_emoji(s.image):
        code, _, name = s.image[len(EMOJI_PREFIX):].partition(" ")
        stem = "".join(ch if ch.isalnum() else "-" for ch in (name or code)).strip("-")
    else:
        stem = Path(s.image).stem
    if not stem.endswith("_scaled"):
        stem += "_scaled"
    return out / f"{stem}.png"


def write_scaled_image(path, pattern):
    """One pixel per tile, in the final palette. Editing it and loading it
    back as the image (same grid size) uses it 1:1. Records the picture's
    source in a PNG text chunk, so an edited emoji keeps its credit."""
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    grid = pattern["grid"]
    im = Image.new("RGB", (len(grid[0]), len(grid)))
    im.putdata([pattern["palette"][idx] for row in grid for idx in row])
    info = PngInfo()
    if pattern.get("source"):
        info.add_text(SOURCE_KEY, pattern["source"])
    im.save(path, pnginfo=info)


def generate(s):
    """Write all SVGs plus a notes file. Returns (files, notes, sheets, pattern)."""
    sheets, pattern, needed, notes = build(s)
    s = effective_settings(s)
    out = Path(os.path.expanduser(s.output_dir))
    out.mkdir(parents=True, exist_ok=True)
    files = []
    pbn = s.kit_type == "paint_by_number"
    for stem, w, h, items in sheets:
        path = out / f"{stem}.svg"
        if pbn:
            path.write_text(pbn_svg(w, h, items[0]))
            files.append(path)
            path = out / f"{stem}_preview.svg"
            path.write_text(pbn_preview_svg(w, h, items[0], pattern))
        else:
            write_svg(path, w, h, items)
        files.append(path)
    if pattern:
        if pbn:
            path = out / f"{s.name}_pbn_key.svg"
            path.write_text(pbn_key_svg(s, pattern, needed, is_emoji(pattern.get("source", ""))))
        else:
            path = out / f"{s.name}_pattern.svg"
            write_pattern_svg(path, s, pattern, needed)
        files.append(path)
        path = scaled_image_path(s, out)
        source = None if is_emoji(s.image) else Path(os.path.expanduser(s.image)).resolve()
        if path.resolve() == source:
            notes.append(f"Scaled image not written: {path.name} is the image being used.")
        else:
            write_scaled_image(path, pattern)
            files.append(path)
            notes.append(f"Scaled image for editing: {path.name} ({s.cols} x {s.rows} px, one pixel per tile)")
    text = [summary(s), ""] + notes + ([] if pbn else [
        "",
        "Red = cut, blue = engrave/score. Units are mm.",
        "Run engrave layers before cut layers.",
    ])
    if s.engrave_numbers and not pbn:
        text.append("Base numbers are SVG <text>; convert to paths in your laser software if it does not import text.")
    path = out / f"{s.name}_notes.txt"
    path.write_text("\n".join(text) + "\n\nSettings:\n" + json.dumps(asdict(s), indent=2) + "\n")
    files.append(path)
    return files, notes, sheets, pattern


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

FORM = [
    ("Kit", [("kit_type", "Kit type")]),
    ("Grid", [("cols", "Columns"), ("rows", "Rows"), ("pixel", "Pixel size (mm)")]),
    ("Material", [("tile_thickness", "Tile thickness (mm)"), ("tray_thickness", "Tray thickness (mm)"),
                  ("kerf", "Tray kerf (mm)"), ("tile_kerf", "Tile kerf (mm)")]),
    ("Tray", [("tray_style", "Style"), ("tile_clearance", "Tile clearance (mm)"),
              ("lip", "Wall above tiles (mm)"), ("finger_width", "Finger width (mm)"),
              ("joint_play", "Joint play (mm)"), ("frame_width", "Frame width (layered, mm)"),
              ("engrave_grid", "Engrave grid on base"), ("engrave_numbers", "Engrave color numbers")]),
    ("Sheets", [("sheet_width", "Sheet width (mm)"), ("sheet_height", "Sheet height (mm)"),
                ("spacing", "Part spacing (mm)"), ("tile_border", "Tile sheet border (mm)"),
                ("spare_percent", "Spare tiles (%)"), ("tile_sheets", "Tile sheets"),
                ("tile_tabs", "Tabs between tiles"), ("tab_width", "Tab width (mm)")]),
    ("Pattern (optional)", [("image", "Image"), ("background", "Background"), ("colors", "Max colors")]),
    ("Paint by number", [("canvas", "Canvas"), ("canvas_landscape", "Landscape canvas"),
                         ("canvas_width", "Custom width (mm)"), ("canvas_height", "Custom height (mm)"),
                         ("canvas_fit", "Picture fit"),
                         ("pbn_use", "Board use"), ("pbn_min_area", "Min area (mm\u00b2)"),
                         ("pbn_min_width", "Min width (mm)"), ("pbn_simplify", "Simplify (mm)"),
                         ("pbn_smooth", "Smooth outlines"), ("pbn_margin", "Board margin (mm)"),
                         ("pbn_line_width", "Print line width (mm)"), ("pbn_text_min", "Min number (mm)"),
                         ("pbn_text_max", "Max number (mm)"), ("pbn_zones", "Detail zones"),
                         ("pbn_zone_area", "Zone min area (mm\u00b2)"), ("pbn_zone_width", "Zone min width (mm)"),
                         ("pbn_zone_colors", "Zone extra colors")]),
    ("Output", [("output_dir", "Folder"), ("name", "Name prefix")]),
]
FORM_COLUMNS = (FORM[:4], FORM[4:])
# Settings that only one kind of kit uses; the GUIs grey out (or hide) the others.
MOSAIC_ONLY = {"tile_thickness", "tray_thickness", "kerf", "tile_kerf", "tray_style", "tile_clearance", "lip",
               "finger_width", "joint_play", "frame_width", "engrave_grid", "engrave_numbers", "spacing",
               "tile_border", "spare_percent", "tile_sheets", "tile_tabs", "tab_width"}
PBN_ONLY = {f.name for f in fields(Settings) if f.name.startswith(("pbn_", "canvas"))}


def load_saved():
    s = Settings()
    try:
        data = json.loads(SETTINGS_FILE.read_text())
        for f in fields(Settings):
            if f.name in data:
                setattr(s, f.name, type(getattr(s, f.name))(data[f.name]))
    except Exception:
        pass
    return s


def run_gui():
    import tkinter as tk
    from tkinter import colorchooser, filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Pixel Art Kit Generator")
    defaults = load_saved()
    types = {f.name: type(getattr(defaults, f.name)) for f in fields(Settings)}
    vars_ = {}

    main = ttk.Frame(root, padding=8)
    main.pack(fill="both", expand=True)
    form = ttk.Frame(main)
    form.grid(row=0, column=0, sticky="nsew")
    side = ttk.Frame(main)
    side.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
    main.columnconfigure(1, weight=1)
    main.rowconfigure(0, weight=1)

    def read_settings():
        s = Settings()
        for k, v in vars_.items():
            val = v.get()
            if types[k] is int:
                val = int(float(val))
            elif types[k] is float:
                val = float(val)
            setattr(s, k, val)
        return s

    def browse_image():
        p = filedialog.askopenfilename(filetypes=[("Images", "*.png *.gif *.bmp *.jpg *.jpeg *.webp"), ("All", "*")])
        if p:
            vars_["pbn_zones"].set("")  # zones belong to the old picture
            vars_["image"].set(p)
            show_picture.set(True)

    def grid_from_image():
        path = vars_["image"].get()
        try:
            found = detect_grid(path)
            w, h = _rgb_image(path).size
        except Exception as e:
            messagebox.showerror("Image", str(e))
            return
        if found and max(found) <= grid_limit(read_settings()):
            cols, rows = found
            msg = f"Detected a {cols} x {rows} pixel-art grid in the {w} x {h} px image."
        else:
            cols = int(float(vars_["cols"].get() or 20))
            rows = max(1, round(cols * h / w))
            msg = (f"No pixel-art grid found in the {w} x {h} px image; kept {cols} columns "
                   f"and set {rows} rows to match its shape.")
        vars_["cols"].set(str(cols))
        vars_["rows"].set(str(rows))
        log.set(msg)

    def pick_background():
        try:
            rgb, hx = colorchooser.askcolor(color=vars_["background"].get() or "#ffffff", parent=root)
        except tk.TclError:
            rgb, hx = colorchooser.askcolor(parent=root)
        if hx:
            vars_["background"].set(hx)

    def pick_emoji():
        """Searchable grid of the bundled emoji; Use puts the choice in Image."""
        try:
            emoji_index()
        except ValueError as e:
            messagebox.showerror("Emoji", str(e))
            return
        win = tk.Toplevel(root)
        win.title("Choose emoji")
        win.transient(root)
        query = tk.StringVar()
        top = ttk.Frame(win, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Search").pack(side="left")
        entry = ttk.Entry(top, textvariable=query, width=28)
        entry.pack(side="left", padx=6)
        ttk.Label(top, text="a name, or paste an emoji", foreground="#777").pack(side="left")
        grid = tk.Frame(win, width=440, height=340, padx=8)
        grid.pack(fill="both", expand=True)
        grid.grid_propagate(False)
        bottom = ttk.Frame(win, padding=8)
        bottom.pack(fill="x")
        status = tk.StringVar()
        ttk.Label(bottom, textvariable=status).pack(side="left")
        chosen = {"code": None, "after": None}
        images = {}  # PhotoImages must stay referenced while shown
        cells = {}
        normal = grid.cget("background")

        def thumb(code):
            if code not in images:
                images[code] = tk.PhotoImage(data=base64.b64encode(emoji_png(code)).decode()).subsample(2)
            return images[code]

        def select(code):
            chosen["code"] = code
            for c, cell in cells.items():
                cell.configure(background="#9cc3f5" if c == code else normal)
            status.set(f"{emoji_index().get(code) or '(no name)'}   U+{code.upper().replace('-', ' U+')}")

        def use(code=None):
            code = code or chosen["code"]
            if not code:
                return
            vars_["pbn_zones"].set("")  # zones belong to the old picture
            vars_["image"].set(emoji_image_value(code))
            show_picture.set(True)
            vars_["rows"].set(vars_["cols"].get())
            log.set(f"Emoji: {emoji_index().get(code, code)}, grid set to "
                    f"{vars_['cols'].get()} x {vars_['cols'].get()}.\n{EMOJI_CREDIT}")
            win.destroy()

        def refresh():
            for cell in cells.values():
                cell.destroy()
            cells.clear()
            codes = emoji_search(query.get())
            for n, code in enumerate(codes):
                cell = tk.Label(grid, image=thumb(code), borderwidth=2, background=normal)
                cell.bind("<Button-1>", lambda e, c=code: select(c))
                cell.bind("<Double-Button-1>", lambda e, c=code: use(c))
                cell.grid(row=n // 10, column=n % 10, padx=1, pady=1)
                cells[code] = cell
            if len(codes) == 1:
                select(codes[0])
            elif not codes:
                status.set("No matches")
            else:
                more = "  (refine the search to see more)" if len(codes) == 80 else ""
                status.set(f"{len(codes)} shown, double-click to use{more}")

        def typed(*_):
            if chosen["after"]:
                win.after_cancel(chosen["after"])
            chosen["after"] = win.after(200, refresh)

        query.trace_add("write", typed)
        entry.bind("<Return>", lambda e: use())
        ttk.Button(bottom, text="Use", command=use).pack(side="right")
        ttk.Button(bottom, text="Cancel", command=win.destroy).pack(side="right", padx=6)
        refresh()
        entry.focus_set()

    def browse_dir():
        p = filedialog.askdirectory()
        if p:
            vars_["output_dir"].set(p)

    rows_of = defaultdict(list)  # setting -> its widgets, for greying out
    marking = tk.BooleanVar(value=False)  # dragging on the preview marks a detail zone

    for col_idx, sections in enumerate(FORM_COLUMNS):
        column = ttk.Frame(form)
        column.grid(row=0, column=col_idx, sticky="n", padx=4)
        for title, items in sections:
            box = ttk.LabelFrame(column, text=title, padding=6)
            box.pack(fill="x", pady=4)
            for r, (key, label) in enumerate(items):
                val = getattr(defaults, key)
                if types[key] is bool:
                    var = tk.BooleanVar(value=val)
                    w = ttk.Checkbutton(box, text=label, variable=var)
                    w.grid(row=r, column=0, columnspan=3, sticky="w")
                    rows_of[key].append(w)
                else:
                    var = tk.StringVar(value=str(val))
                    lab = ttk.Label(box, text=label)
                    lab.grid(row=r, column=0, sticky="w")
                    if key in CHOICES:
                        w = ttk.Combobox(box, textvariable=var, values=CHOICES[key], state="readonly",
                                         width=14 if key == "kit_type" else 9)
                    else:
                        width = 22 if key in ("image", "output_dir", "pbn_zones") else 9
                        w = ttk.Entry(box, textvariable=var, width=width)
                    w.grid(row=r, column=1, sticky="w")
                    rows_of[key] += [lab, w]
                    if key == "image":
                        ttk.Button(box, text="…", width=2, command=browse_image).grid(row=r, column=2)
                        ttk.Button(box, text="Emoji…", command=pick_emoji).grid(row=r, column=3)
                        ttk.Button(box, text="Set grid from image", command=grid_from_image).grid(
                            row=r + 10, column=0, columnspan=2, sticky="w", pady=(4, 0))
                        ttk.Button(box, text="Edit pixels…", command=lambda: edit_pixels()).grid(
                            row=r + 10, column=2, columnspan=2, sticky="w", pady=(4, 0))
                    elif key == "pbn_zones":
                        zone_row = ttk.Frame(box)
                        zone_row.grid(row=r + 20, column=0, columnspan=4, sticky="w", pady=(4, 0))
                        mark = ttk.Checkbutton(zone_row, text="Mark zones", variable=marking)
                        undo_zone = ttk.Button(zone_row, text="Undo zone", command=lambda: drop_zone(False))
                        clear_zones = ttk.Button(zone_row, text="Clear", command=lambda: drop_zone(True))
                        for z in (mark, undo_zone, clear_zones):
                            z.pack(side="left", padx=(0, 4))
                            rows_of[key].append(z)
                    elif key == "output_dir":
                        ttk.Button(box, text="…", width=2, command=browse_dir).grid(row=r, column=2)
                    elif key == "background":
                        ttk.Button(box, text="…", width=2, command=pick_background).grid(row=r, column=2)
                vars_[key] = var

    info = tk.StringVar()
    ttk.Label(side, textvariable=info, justify="left", font=("TkFixedFont", 11)).pack(anchor="w")
    canvas = tk.Canvas(side, width=460, height=360, background="#f4f1ea", highlightthickness=1,
                       highlightbackground="#bbb")
    canvas.pack(fill="both", expand=True, pady=6)
    nav = ttk.Frame(side)
    nav.pack(fill="x")
    sheet_label = tk.StringVar(value="Press Preview to lay out the sheets")
    state = {"sheets": [], "i": 0, "pattern": None, "pending": ""}
    show_picture = tk.BooleanVar(value=False)

    def draw():
        canvas.delete("all")
        if not state["sheets"]:
            return
        stem, w, h, items = state["sheets"][state["i"]]
        sheet_label.set(f"{state['i'] + 1}/{len(state['sheets'])}  {stem}  ({w:.0f} x {h:.0f} mm)")
        cw, ch = max(canvas.winfo_width(), 100), max(canvas.winfo_height(), 100)
        k = min((cw - 20) / w, (ch - 20) / h)
        ox, oy = (cw - w * k) / 2, (ch - h * k) / 2
        canvas.create_rectangle(ox, oy, ox + w * k, oy + h * k, fill="#e8d9b5", outline="#999")
        state["view"] = (ox, oy, k)
        pattern = state["pattern"] if show_picture.get() else None
        for pc in items:
            if pattern and pc.picture:
                (x0, y0), (x1, y1), (x2, y2) = pc.picture
                nc, nr = len(pattern["grid"][0]), len(pattern["grid"])
                ux, uy = (x1 - x0) / nc, (y1 - y0) / nc
                vx, vy = (x2 - x0) / nr, (y2 - y0) / nr
                for j, row in enumerate(pattern["grid"]):
                    i = 0
                    while i < nc:  # one polygon per run of equal color
                        e = i
                        while e + 1 < nc and row[e + 1] == row[i]:
                            e += 1
                        cx, cy = x0 + i * ux + j * vx, y0 + i * uy + j * vy
                        n = e - i + 1
                        quad = [(cx, cy), (cx + n * ux, cy + n * uy), (cx + n * ux + vx, cy + n * uy + vy),
                                (cx + vx, cy + vy)]
                        canvas.create_polygon(*[c for x, y in quad for c in (ox + x * k, oy + y * k)],
                                              fill=hexcolor(pattern["palette"][row[i]]), outline="")
                        i = e + 1
            line = "#2b4fd6" if getattr(pc, "line_color", ENGRAVE) == ENGRAVE else "#222222"
            for color, polys in ((line, pc.engraves), ("#d62b2b", pc.cuts)):
                for pts, closed in polys:
                    flat = [c for x, y in (pts + pts[:1] if closed else pts) for c in (ox + x * k, oy + y * k)]
                    canvas.create_line(*flat, fill=color)
            for x, y, size, text, ang, _ in pc.texts:
                if size * k >= 5:  # skip numbers too small to read at this zoom
                    canvas.create_text(ox + x * k, oy + y * k, text=text, anchor="s",
                                       fill={PBN_NUMBER: "#109a10", PBN_PRINT_NUMBER: "#555555"}.get(
                                           getattr(pc, "text_color", ""), "#2b4fd6"),
                                       angle=-ang, font=("TkDefaultFont", max(6, int(size * k * 0.8))))
            if is_board(pc):
                (px, py), (qx, _), (_, qy) = pc.picture
                try:
                    zones = parse_zones(vars_["pbn_zones"].get())
                except ValueError:
                    zones = []
                for x0, y0, x1, y1 in zones:
                    canvas.create_rectangle(ox + (px + x0 * (qx - px)) * k, oy + (py + y0 * (qy - py)) * k,
                                            ox + (px + x1 * (qx - px)) * k, oy + (py + y1 * (qy - py)) * k,
                                            outline="#e0218a", dash=(4, 3))

    def is_board(pc):
        return pc.name == "paint_by_number" and pc.picture

    def zone_fraction(e):
        """Where a mouse event falls on the board's picture, as fractions of it."""
        board = next((pc for pc in state["sheets"][state["i"]][3] if is_board(pc)), None) if state["sheets"] else None
        if not board or "view" not in state:
            return None
        ox, oy, k = state["view"]
        (px, py), (qx, _), (_, qy) = board.picture
        clamp = lambda v: min(1.0, max(0.0, v))
        return clamp(((e.x - ox) / k - px) / (qx - px)), clamp(((e.y - oy) / k - py) / (qy - py))

    def zone_press(e):
        if marking.get():
            state["zone_from"] = zone_fraction(e)

    def zone_drag(e):
        a, b = state.get("zone_from"), zone_fraction(e)
        canvas.delete("zone_drag")
        if marking.get() and a and b:
            ox, oy, k = state["view"]
            (px, py), (qx, _), (_, qy) = next(pc for pc in state["sheets"][state["i"]][3] if is_board(pc)).picture
            canvas.create_rectangle(ox + (px + a[0] * (qx - px)) * k, oy + (py + a[1] * (qy - py)) * k,
                                    ox + (px + b[0] * (qx - px)) * k, oy + (py + b[1] * (qy - py)) * k,
                                    outline="#e0218a", tags="zone_drag")

    def zone_release(e):
        a, b = state.pop("zone_from", None), zone_fraction(e)
        canvas.delete("zone_drag")
        if marking.get() and a and b:
            x0, x1, y0, y1 = min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1])
            if x1 > x0 and y1 > y0:
                old = vars_["pbn_zones"].get().strip()
                box = f"{x0:.4f},{y0:.4f},{x1:.4f},{y1:.4f}"
                vars_["pbn_zones"].set(f"{old};{box}" if old else box)
                draw()  # a large board's preview waits for the button, but its zones shouldn't

    def drop_zone(everything):
        parts = [z for z in vars_["pbn_zones"].get().split(";") if z.strip()]
        vars_["pbn_zones"].set("" if everything else ";".join(parts[:-1]))
        draw()

    def show_tray_sheet():
        """Jump to the sheet holding the tray base if we're looking elsewhere."""
        here = state["sheets"][state["i"]][3] if state["sheets"] else []
        if not any(pc.picture for pc in here):
            for n, (_, _, _, items) in enumerate(state["sheets"]):
                if any(pc.picture for pc in items):
                    state["i"] = n
                    break

    def toggle_picture():
        if show_picture.get() and not state["pattern"]:
            log.set("Choose an image or an emoji to see the picture in the tray.")
        elif show_picture.get():
            show_tray_sheet()
        draw()

    def step(d):
        if state["sheets"]:
            state["i"] = (state["i"] + d) % len(state["sheets"])
            draw()

    def refresh_info(*_):
        try:
            info.set(summary(read_settings()))
        except (ValueError, ZeroDivisionError):
            info.set("(enter valid numbers)")
        # Keep the preview in step with the settings, once typing pauses.
        if state.get("after"):
            root.after_cancel(state["after"])
        state["after"] = root.after(500, lambda: preview(quiet=True))

    def preview(quiet=False):
        if state.get("after"):  # an immediate preview replaces one still scheduled
            root.after_cancel(state["after"])
        state["after"] = None
        try:
            s = read_settings()
            e = effective_settings(s)
            if quiet and e.kit_type == "paint_by_number" and e.cols * e.rows > LARGE_BOARD:
                log.set(f"Large board ({e.cols} x {e.rows} cells): press Preview to update it.")
                return
            sheets, pattern, _, notes = build(s)
        except Exception as e:
            if quiet:
                log.set(f"Preview not updated: {e}")
            else:
                messagebox.showerror("Preview", str(e))
            return
        same_layout = [st[0] for st in sheets] == [st[0] for st in state["sheets"]]
        state["sheets"], state["pattern"] = sheets, pattern
        state["i"] = state["i"] if same_layout else 0
        if show_picture.get():
            show_tray_sheet()
        draw()
        log.set((state["pending"] + "\n\n" if state["pending"] else "") + "\n".join(notes))
        state["pending"] = ""

    def generate_clicked():
        try:
            s = read_settings()
            files, notes, sheets, pattern = generate(s)
            SETTINGS_FILE.write_text(json.dumps(asdict(s), indent=2))
        except Exception as e:
            messagebox.showerror("Generate", str(e))
            return
        state["sheets"], state["i"], state["pattern"] = sheets, 0, pattern
        draw()
        log.set("\n".join(notes) + f"\n\nWrote {len(files)} files to {files[0].parent}")
        last_dir["path"] = files[0].parent

    last_dir = {"path": None}

    LARGE_BOARD = 250_000  # cells; bigger paint-by-number boards preview only on request

    def edit_pixels():
        """Pop-out editor: paint tiles with the mosaic's own colors, then save
        over <picture>_scaled.png and make that the image."""
        preview(quiet=True)
        pattern = state["pattern"]
        if not pattern:
            messagebox.showinfo("Edit pixels", "Choose an image or an emoji first.")
            return
        s = read_settings()
        palette = pattern["palette"]
        grid = [row[:] for row in pattern["grid"]]
        rows, cols = len(grid), len(grid[0])
        if rows * cols > 120_000:
            messagebox.showinfo("Edit pixels", f"The {cols} x {rows} grid is too large to edit here. Generate the "
                                "kit and edit <picture>_scaled.png in an image editor, or use the web version.")
            return
        cell = max(4, min(28, 720 // max(cols, rows)))
        ed = {"color": 0, "stroke": None, "undo": [], "dirty": False}

        win = tk.Toplevel(root)
        win.title(f"Edit pixels: {cols} x {rows}")
        win.transient(root)
        top = ttk.Frame(win, padding=(8, 8, 8, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Color").pack(side="left", padx=(0, 6))
        swatches = []
        for idx, rgb in enumerate(palette):
            sw = tk.Canvas(top, width=30, height=30, highlightthickness=3, highlightbackground=win.cget("background"))
            sw.create_rectangle(2, 2, 29, 29, fill=hexcolor(rgb), outline="#333")
            sw.create_text(16, 16, text=str(idx + 1), fill=text_color(rgb))
            sw.bind("<Button-1>", lambda e, i=idx: choose(i))
            sw.pack(side="left", padx=1)
            swatches.append(sw)

        canvas = tk.Canvas(win, width=cols * cell, height=rows * cell, highlightthickness=0, background="#777")
        canvas.pack(padx=8, pady=4)
        outline = "#777" if cell >= 8 else ""
        rects = [[canvas.create_rectangle(i * cell, j * cell, (i + 1) * cell, (j + 1) * cell,
                                          fill=hexcolor(palette[grid[j][i]]), outline=outline)
                  for i in range(cols)] for j in range(rows)]
        bottom = ttk.Frame(win, padding=(8, 4, 8, 8))
        bottom.pack(fill="x")
        counts = tk.StringVar()
        ttk.Label(bottom, textvariable=counts).pack(side="left")
        ttk.Label(win, text="Click or drag to paint. Right-click (or Ctrl-click) a tile to pick its color. "
                            "Keys 1-9 choose a color; Ctrl/Cmd-Z undoes.",
                  foreground="#666", padding=(8, 0, 8, 6)).pack(anchor="w")

        def update_counts():
            n = [0] * len(palette)
            for row in grid:
                for idx in row:
                    n[idx] += 1
            counts.set("   ".join(f"{i + 1}: {c}" for i, c in enumerate(n)))

        def choose(i):
            ed["color"] = i
            for k, sw in enumerate(swatches):
                sw.configure(highlightbackground="#1e6fd9" if k == i else win.cget("background"))

        def cell_at(e):
            i, j = e.x // cell, e.y // cell
            return (i, j) if 0 <= i < cols and 0 <= j < rows else None

        def paint(e):
            pos = cell_at(e)
            if not pos or ed["stroke"] is None:
                return
            i, j = pos
            if grid[j][i] != ed["color"]:
                ed["stroke"].append((j, i, grid[j][i]))
                grid[j][i] = ed["color"]
                canvas.itemconfigure(rects[j][i], fill=hexcolor(palette[ed["color"]]))
                ed["dirty"] = True
                update_counts()

        def press(e):
            ed["stroke"] = []
            paint(e)

        def release(e):
            if ed["stroke"]:
                ed["undo"].append(ed["stroke"])
            ed["stroke"] = None

        def pick(e):
            pos = cell_at(e)
            if pos:
                choose(grid[pos[1]][pos[0]])

        def undo(e=None):
            if not ed["undo"]:
                return
            for j, i, old in reversed(ed["undo"].pop()):
                grid[j][i] = old
                canvas.itemconfigure(rects[j][i], fill=hexcolor(palette[old]))
            update_counts()

        def save():
            out = Path(os.path.expanduser(s.output_dir))
            try:
                out.mkdir(parents=True, exist_ok=True)
                path = scaled_image_path(s, out)
                write_scaled_image(path, {"palette": palette, "grid": grid, "source": pattern.get("source", "")})
            except Exception as e:
                messagebox.showerror("Edit pixels", str(e), parent=win)
                return
            state["pending"] = f"Saved the edited picture to {path}; it is now the image."
            show_picture.set(True)
            vars_["image"].set(str(path))  # the setting change refreshes the preview
            win.destroy()

        def cancel():
            if ed["dirty"] and not messagebox.askyesno("Edit pixels", "Discard your changes?", parent=win):
                return
            win.destroy()

        ttk.Button(bottom, text="Save", command=save).pack(side="right")
        ttk.Button(bottom, text="Cancel", command=cancel).pack(side="right", padx=6)
        ttk.Button(bottom, text="Undo", command=undo).pack(side="right")
        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<B1-Motion>", paint)
        canvas.bind("<ButtonRelease-1>", release)
        for seq in ("<Button-2>", "<Button-3>", "<Control-Button-1>"):
            canvas.bind(seq, pick)
        for seq in ("<Control-z>", "<Command-z>"):
            try:
                win.bind(seq, undo)
            except tk.TclError:
                pass  # <Command-...> exists only on macOS
        for k in range(min(9, len(palette))):
            win.bind(str(k + 1), lambda e, i=k: choose(i))
        win.protocol("WM_DELETE_WINDOW", cancel)
        choose(0)
        update_counts()
        win.focus_set()

    def kerf_test_clicked():
        try:
            path, sheets, notes = generate_kerf_test(read_settings())
        except Exception as e:
            messagebox.showerror("Kerf test", str(e))
            return
        state["sheets"], state["i"] = sheets, 0
        show_picture.set(False)
        draw()
        log.set("\n".join(notes) + f"\n\nWrote {path}")
        last_dir["path"] = path.parent

    def open_folder():
        p = last_dir["path"] or Path(os.path.expanduser(vars_["output_dir"].get()))
        if not Path(p).exists():
            return
        if sys.platform == "darwin":
            subprocess.run(["open", str(p)])
        elif os.name == "nt":
            os.startfile(str(p))
        else:
            subprocess.run(["xdg-open", str(p)])

    ttk.Button(nav, text="◀", width=3, command=lambda: step(-1)).pack(side="left")
    ttk.Button(nav, text="▶", width=3, command=lambda: step(1)).pack(side="left")
    ttk.Label(nav, textvariable=sheet_label).pack(side="left", padx=6)
    ttk.Checkbutton(nav, text="Show picture", variable=show_picture, command=toggle_picture).pack(side="right")

    log = tk.StringVar()
    ttk.Label(side, textvariable=log, justify="left", wraplength=460).pack(anchor="w", pady=4)
    buttons = ttk.Frame(side)
    buttons.pack(fill="x", pady=(4, 0))
    ttk.Button(buttons, text="Preview", command=preview).pack(side="left")
    ttk.Button(buttons, text="Generate SVGs", command=generate_clicked).pack(side="left", padx=6)
    ttk.Button(buttons, text="Kerf test", command=kerf_test_clicked).pack(side="left", padx=6)
    ttk.Button(buttons, text="Open folder", command=open_folder).pack(side="left")

    def update_enabled(*_):
        pbn = vars_["kit_type"].get() == "paint_by_number"
        canvas = vars_["canvas"].get()
        for key, widgets in rows_of.items():
            off = (pbn and key in MOSAIC_ONLY) or (not pbn and key in PBN_ONLY)
            if pbn and canvas != "off" and key in ("cols", "rows"):
                off = True  # worked out from the canvas
            if key in ("canvas_width", "canvas_height") and canvas != "custom":
                off = True
            if key == "canvas_landscape" and canvas in ("off", "custom"):
                off = True
            for w in widgets:
                w.state(["disabled"] if off else ["!disabled"])

    vars_["kit_type"].trace_add("write", update_enabled)
    vars_["canvas"].trace_add("write", update_enabled)
    update_enabled()
    for v in vars_.values():
        v.trace_add("write", refresh_info)
    canvas.bind("<Configure>", lambda e: draw())
    canvas.bind("<ButtonPress-1>", zone_press)
    canvas.bind("<B1-Motion>", zone_drag)
    canvas.bind("<ButtonRelease-1>", zone_release)
    refresh_info()
    root.mainloop()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def run_cli(argv):
    ap = argparse.ArgumentParser(description="Generate laser-cut pixel art kit SVGs.")
    ap.add_argument("--cli", action="store_true", help="run without the GUI")
    d = Settings()
    for f in fields(Settings):
        val = getattr(d, f.name)
        flag = "--" + f.name.replace("_", "-")
        if isinstance(val, bool):
            ap.add_argument(flag, action=argparse.BooleanOptionalAction, default=val)
        elif f.name in CHOICES:
            ap.add_argument(flag, choices=CHOICES[f.name], default=val)
        else:
            ap.add_argument(flag, type=type(val), default=val)
    ap.add_argument("--emoji", help="use a bundled emoji as the pattern: paste it or give its name; "
                                    "sets rows = cols unless --rows is given")
    ap.add_argument("--find-emoji", metavar="QUERY", help="list bundled emoji matching QUERY and exit")
    ap.add_argument("--kerf-test", action="store_true",
                    help="write only a kerf test sheet (slotted plates and tab pieces) and exit")
    args = ap.parse_args(argv)
    if args.kerf_test:
        s = Settings(**{f.name: getattr(args, f.name) for f in fields(Settings)})
        path, _, notes = generate_kerf_test(s)
        print("\n".join(notes))
        print("wrote", path)
        return
    if args.find_emoji is not None:
        for code in emoji_search(args.find_emoji, limit=40):
            print(f"{emoji_char(code)}  {code:<12} {emoji_index()[code]}")
        return
    if args.emoji:
        hits = emoji_search(args.emoji, limit=1)
        if not hits:
            sys.exit(f"error: no bundled emoji matches {args.emoji!r} (try --find-emoji)")
        args.image = emoji_image_value(hits[0])
        if not any(a == "--rows" or a.startswith("--rows=") for a in argv):
            args.rows = args.cols
    s = Settings(**{f.name: getattr(args, f.name) for f in fields(Settings)})
    try:
        files, notes, _, _ = generate(s)
    except ValueError as e:
        sys.exit(f"error: {e}")
    print(summary(s))
    print("\n".join(notes))
    for p in files:
        print("wrote", p)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_cli(sys.argv[1:])
    else:
        run_gui()
