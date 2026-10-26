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
from dataclasses import asdict, dataclass, fields
from pathlib import Path

CUT = "#ff0000"
ENGRAVE = "#0000ff"
STROKE = 0.1
SETTINGS_FILE = Path.home() / ".pixelkit.json"
TRAY_STYLES = ["box", "layered", "none"]
TILE_SHEETS = ["combined", "per_color"]
CHOICES = {"tray_style": TRAY_STYLES, "tile_sheets": TILE_SHEETS}


@dataclass
class Settings:
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


def kerf_test_values(s):
    """Kerf values to try, centred on the current setting."""
    mid = KERF_TEST_COUNT // 2
    vals = [round(s.kerf + (i - mid) * KERF_TEST_STEP, 3) for i in range(KERF_TEST_COUNT)]
    return [v + 0.0 for v in vals if v >= 0]  # + 0.0 turns -0.0 into 0.0


def make_kerf_test(s):
    """Pairs of test pieces in tray stock, one pair per kerf value: a plate
    with two slots, and a tab piece whose two tabs push into them, exactly as
    the box base and walls fit. Each piece is engraved with its kerf value;
    the pair that goes together snugly gives the kerf to use."""
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
        slots = [(a - q, margin - q, b + q, margin + t + q) for a, b in xs]
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


def load_pattern(path, cols, rows, max_colors, background="#ffffff"):
    """Reduce an image to a cols x rows grid of palette indices, most common
    color first.

    An image larger than the grid is scaled down to fit inside it, keeping
    its aspect ratio; one that already fits is used 1:1. Either way it is
    centred and any leftover rows/columns are filled with the most common
    border color. When scaling down, each tile takes the majority color of
    its area rather than an average, so pixel art stays crisp."""
    from PIL import Image

    im = _rgb_image(path, background)
    w, h = im.size
    note = f"Pattern: {path.split()[0] if is_emoji(path) else 'image'} {w} x {h} px"
    scale = min(cols / w, rows / h)
    nw, nh = w, h
    sub = 1  # samples per tile side used for the majority vote
    if scale < 1:
        nw = min(cols, max(1, round(w * scale)))
        nh = min(rows, max(1, round(h * scale)))
        sub = max(1, min(8, int(min(w / nw, h / nh))))
        im = im.resize((nw * sub, nh * sub), getattr(Image, "Resampling", Image).BOX)
        note += f" scaled to {nw} x {nh} (majority color per tile)"
    pixels = reduce_colors(list(im.getdata()), max_colors)
    iw = nw * sub
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
        ox, oy = (cols - nw) // 2, (rows - nh) // 2
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
    return {"palette": palette, "counts": [counts[c] for c in palette], "grid": grid, "note": note}


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
    legend_h = 8 + 7 * len(pattern["palette"]) + (8 if is_emoji(s.image) else 0)
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
    if is_emoji(s.image):
        out.append(f'<text x="{m}" y="{y + 8:.1f}" font-size="2.8" fill="#555">{EMOJI_CREDIT}</text>')
    out.append("</svg>")
    Path(path).write_text("\n".join(out) + "\n")


# --------------------------------------------------------------------------
# Kit generation
# --------------------------------------------------------------------------

def validate(s):
    problems = []
    if s.cols < 1 or s.rows < 1:
        problems.append("Columns and rows must be at least 1.")
    if s.cols > MAX_GRID or s.rows > MAX_GRID:
        problems.append(f"Columns and rows are limited to {MAX_GRID} tiles each.")
    for k in ("pixel", "tile_thickness", "tray_thickness", "sheet_width", "sheet_height"):
        if getattr(s, k) <= 0:
            problems.append(f"{k} must be positive.")
    if s.kerf < 0 or s.kerf >= s.pixel:
        problems.append("Kerf must be between 0 and the pixel size.")
    if s.tile_kerf < -1 or s.tile_kerf >= s.pixel:
        problems.append("Tile kerf must be between -1 mm and the pixel size.")
    for key, options in CHOICES.items():
        if getattr(s, key) not in options:
            problems.append(f"{key} must be one of {options}.")
    if s.tile_tabs and not 0 < s.tab_width < s.pixel / 2:
        problems.append("Tab width must be more than 0 and less than half the pixel size.")
    if s.tray_style == "box" and s.finger_width < 2 * s.kerf + 1:
        problems.append("Finger width is too small.")
    try:
        from PIL import ImageColor
        ImageColor.getrgb(s.background or "#ffffff")
    except ValueError:
        problems.append(f"Background {s.background!r} is not a color (use e.g. #1e8cdc).")
    except ImportError:
        pass
    if s.engrave_numbers and not s.image:
        problems.append("Engraving color numbers needs a pattern image.")
    return problems


def build(s):
    """Lay out every sheet. Returns (sheets, pattern, needed, notes) where
    sheets is a list of (file_stem, width, height, pieces)."""
    problems = validate(s)
    if problems:
        raise ValueError("\n".join(problems))
    pattern = load_pattern(s.image, s.cols, s.rows, s.colors, s.background) if s.image else None
    notes = [pattern["note"]] if pattern else []
    if is_emoji(s.image):
        notes.append(EMOJI_CREDIT)
    sheets = []

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
        f"kerf {vals[0]:.2f} to {vals[-1]:.2f} mm (joint play {s.joint_play:g} mm).",
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
    back as the image (same grid size) uses it 1:1."""
    from PIL import Image

    grid = pattern["grid"]
    im = Image.new("RGB", (len(grid[0]), len(grid)))
    im.putdata([pattern["palette"][idx] for row in grid for idx in row])
    im.save(path)


def generate(s):
    """Write all SVGs plus a notes file. Returns (files, notes, sheets, pattern)."""
    sheets, pattern, needed, notes = build(s)
    out = Path(os.path.expanduser(s.output_dir))
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for stem, w, h, items in sheets:
        path = out / f"{stem}.svg"
        write_svg(path, w, h, items)
        files.append(path)
    if pattern:
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
    text = [summary(s), ""] + notes + [
        "",
        "Red = cut, blue = engrave/score. Units are mm.",
        "Run engrave layers before cut layers.",
    ]
    if s.engrave_numbers:
        text.append("Base numbers are SVG <text>; convert to paths in your laser software if it does not import text.")
    path = out / f"{s.name}_notes.txt"
    path.write_text("\n".join(text) + "\n\nSettings:\n" + json.dumps(asdict(s), indent=2) + "\n")
    files.append(path)
    return files, notes, sheets, pattern


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

FORM = [
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
    ("Output", [("output_dir", "Folder"), ("name", "Name prefix")]),
]


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
        if found and max(found) <= MAX_GRID:
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

    for col_idx, sections in enumerate((FORM[:3], FORM[3:])):
        column = ttk.Frame(form)
        column.grid(row=0, column=col_idx, sticky="n", padx=4)
        for title, items in sections:
            box = ttk.LabelFrame(column, text=title, padding=6)
            box.pack(fill="x", pady=4)
            for r, (key, label) in enumerate(items):
                val = getattr(defaults, key)
                if types[key] is bool:
                    var = tk.BooleanVar(value=val)
                    ttk.Checkbutton(box, text=label, variable=var).grid(row=r, column=0, columnspan=3, sticky="w")
                else:
                    var = tk.StringVar(value=str(val))
                    ttk.Label(box, text=label).grid(row=r, column=0, sticky="w")
                    if key in CHOICES:
                        ttk.Combobox(box, textvariable=var, values=CHOICES[key], state="readonly",
                                     width=9).grid(row=r, column=1, sticky="w")
                    else:
                        width = 22 if key in ("image", "output_dir") else 9
                        ttk.Entry(box, textvariable=var, width=width).grid(row=r, column=1, sticky="w")
                    if key == "image":
                        ttk.Button(box, text="…", width=2, command=browse_image).grid(row=r, column=2)
                        ttk.Button(box, text="Emoji…", command=pick_emoji).grid(row=r, column=3)
                        ttk.Button(box, text="Set grid from image", command=grid_from_image).grid(
                            row=r + 10, column=0, columnspan=3, sticky="w", pady=(4, 0))
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
    state = {"sheets": [], "i": 0, "pattern": None}
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
        pattern = state["pattern"] if show_picture.get() else None
        for pc in items:
            if pattern and pc.picture:
                (x0, y0), (x1, y1), (x2, y2) = pc.picture
                nc, nr = len(pattern["grid"][0]), len(pattern["grid"])
                ux, uy = (x1 - x0) / nc, (y1 - y0) / nc
                vx, vy = (x2 - x0) / nr, (y2 - y0) / nr
                for j, row in enumerate(pattern["grid"]):
                    for i, idx in enumerate(row):
                        cx, cy = x0 + i * ux + j * vx, y0 + i * uy + j * vy
                        quad = [(cx, cy), (cx + ux, cy + uy), (cx + ux + vx, cy + uy + vy), (cx + vx, cy + vy)]
                        canvas.create_polygon(*[c for x, y in quad for c in (ox + x * k, oy + y * k)],
                                              fill=hexcolor(pattern["palette"][idx]), outline="")
            for color, polys in (("#2b4fd6", pc.engraves), ("#d62b2b", pc.cuts)):
                for pts, closed in polys:
                    flat = [c for x, y in (pts + pts[:1] if closed else pts) for c in (ox + x * k, oy + y * k)]
                    canvas.create_line(*flat, fill=color)
            if k * (pc.texts[0][2] if pc.texts else 0) >= 6:
                for x, y, size, text, ang, _ in pc.texts:
                    canvas.create_text(ox + x * k, oy + y * k, text=text, fill="#2b4fd6", anchor="s",
                                       angle=-ang, font=("TkDefaultFont", max(6, int(size * k * 0.8))))

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
        state["after"] = None
        try:
            s = read_settings()
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
        log.set("\n".join(notes))

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

    for v in vars_.values():
        v.trace_add("write", refresh_info)
    canvas.bind("<Configure>", lambda e: draw())
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
