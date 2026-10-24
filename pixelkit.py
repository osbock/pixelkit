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


@dataclass
class Settings:
    cols: int = 20
    rows: int = 25
    pixel: float = 10.0            # finished tile size, mm
    tile_thickness: float = 3.0
    tray_thickness: float = 3.0
    kerf: float = 0.15             # width of the laser cut, mm
    tray_style: str = "box"        # box | layered | none
    tile_clearance: float = 1.0    # total slack between tiles and tray wall
    lip: float = 4.0               # how far the wall stands above the tiles (3+3+4 = 10 mm wall)
    finger_width: float = 6.0      # target finger (tab) length for box joints
    joint_play: float = 0.0        # extra gap per finger joint (+ looser, - tighter)
    frame_width: float = 10.0      # frame border for the layered style
    engrave_grid: bool = True
    engrave_numbers: bool = False  # needs a pattern image
    sheet_width: float = 600.0     # laser bed / stock size
    sheet_height: float = 400.0
    spacing: float = 3.0           # gap between parts and from sheet edge
    tile_border: float = 5.0       # waste frame around each tile block (0 = none)
    spare_percent: float = 5.0
    image: str = ""
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


def tile_block(name, c, r, s, label=""):
    """c x r tiles on a shared cut grid, optionally inside a waste border."""
    p = s.pixel + s.kerf
    bd = s.tile_border
    w, h = c * p, r * p
    piece = Piece(name)
    if label and bd >= 3:
        size = bd * 0.6
        piece.texts.append((bd, bd * 0.8, size, label, 0.0, "start"))
    for i in range(1, c):
        x = bd + i * p
        pts = [(x, bd), (x, bd + h)]
        piece.cuts.append((pts if i % 2 else pts[::-1], False))
    for j in range(1, r):
        y = bd + j * p
        pts = [(bd, y), (bd + w, y)]
        piece.cuts.append((pts if j % 2 else pts[::-1], False))
    piece.cuts.append((rect_pts(bd, bd, bd + w, bd + h), True))
    if bd > 0:
        piece.cuts.append((rect_pts(0, 0, w + 2 * bd, h + 2 * bd), True))
    return piece


def tile_blocks(s, count, label, key):
    p = s.pixel + s.kerf
    room_w = s.sheet_width - 2 * s.spacing - 2 * s.tile_border
    room_h = s.sheet_height - 2 * s.spacing - 2 * s.tile_border
    maxc, maxr = int(room_w // p), int(room_h // p)
    if maxc < 1 or maxr < 1:
        raise ValueError("Sheet is too small for even one tile")
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

def pack(pieces, sw, sh, gap):
    """Shelf-pack pieces onto as many sheets as needed. Returns a list of
    (width, height, [placed pieces]); oversize parts get a sheet of their own."""
    oriented = []
    for pc in pieces:
        pc = pc.normalized()
        w, h = pc.size
        fits = w <= sw - 2 * gap and h <= sh - 2 * gap
        fits_rot = h <= sw - 2 * gap and w <= sh - 2 * gap
        # Keep parts upright unless they only fit rotated, or are tall slivers
        # (walls) that shelf-pack far better lying down.
        if fits_rot and (not fits or h > 2 * w):
            pc = pc.rotated()
        oriented.append(pc)
    oriented.sort(key=lambda pc: -pc.size[1])

    sheets = []  # each: {"items": [...], "shelves": [[y, h, x]], "w":, "h":}
    for pc in oriented:
        w, h = pc.size
        placed = False
        for sheet in sheets:
            for shelf in sheet["shelves"]:
                if h <= shelf[1] and shelf[2] + w <= sheet["w"] - gap:
                    sheet["items"].append(pc.moved(shelf[2], shelf[0]))
                    shelf[2] += w + gap
                    placed = True
                    break
            if placed:
                break
            last = sheet["shelves"][-1]
            y = last[0] + last[1] + gap
            if y + h <= sheet["h"] - gap and gap + w <= sheet["w"] - gap:
                sheet["shelves"].append([y, h, gap + w + gap])
                sheet["items"].append(pc.moved(gap, y))
                placed = True
                break
        if not placed:
            sheet = {"w": max(sw, w + 2 * gap), "h": max(sh, h + 2 * gap), "items": [],
                     "shelves": [[gap, h, gap + w + gap]]}
            sheet["items"].append(pc.moved(gap, gap))
            sheets.append(sheet)
    return [(sh_["w"], sh_["h"], sh_["items"]) for sh_ in sheets]


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

def load_pattern(path, cols, rows, max_colors):
    """Reduce an image to a cols x rows grid of palette indices, most common
    color first."""
    from PIL import Image

    im = Image.open(os.path.expanduser(path)).convert("RGBA")
    white = Image.new("RGBA", im.size, (255, 255, 255, 255))
    im = Image.alpha_composite(white, im).convert("RGB")
    if im.size != (cols, rows):
        box = getattr(Image, "Resampling", Image).BOX
        im = im.resize((cols, rows), box)
    if len(im.getcolors(cols * rows)) > max_colors:
        method = getattr(Image, "Quantize", Image).MEDIANCUT
        im = im.quantize(colors=max_colors, method=method).convert("RGB")
    pixels = list(im.getdata())
    counts = defaultdict(int)
    for px in pixels:
        counts[px] += 1
    palette = sorted(counts, key=lambda c: (-counts[c], c))
    index = {c: i for i, c in enumerate(palette)}
    grid = [[index[pixels[j * cols + i]] for i in range(cols)] for j in range(rows)]
    return {"palette": palette, "counts": [counts[c] for c in palette], "grid": grid}


def image_size(path):
    from PIL import Image
    with Image.open(os.path.expanduser(path)) as im:
        return im.size


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
    legend_h = 8 + 7 * len(pattern["palette"])
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
    out.append(f'<text x="{m}" y="{y:.1f}" font-size="3.5" font-weight="bold">#   color      in picture   tiles to cut</text>')
    for idx, rgb in enumerate(pattern["palette"]):
        y += 7
        out.append(f'<rect x="{m}" y="{y - 4.5:.1f}" width="5" height="5" fill="{hexcolor(rgb)}" stroke="#333" stroke-width="0.2"/>')
        out.append(f'<text x="{m + 7}" y="{y - 0.5:.1f}" font-size="3.5">{idx + 1}   {hexcolor(rgb)}   '
                   f'{pattern["counts"][idx]}   {needed[idx]}</text>')
    out.append("</svg>")
    Path(path).write_text("\n".join(out) + "\n")


# --------------------------------------------------------------------------
# Kit generation
# --------------------------------------------------------------------------

def validate(s):
    problems = []
    if s.cols < 1 or s.rows < 1:
        problems.append("Columns and rows must be at least 1.")
    for k in ("pixel", "tile_thickness", "tray_thickness", "sheet_width", "sheet_height"):
        if getattr(s, k) <= 0:
            problems.append(f"{k} must be positive.")
    if s.kerf < 0 or s.kerf >= s.pixel:
        problems.append("Kerf must be between 0 and the pixel size.")
    if s.tray_style not in TRAY_STYLES:
        problems.append(f"Tray style must be one of {TRAY_STYLES}.")
    if s.tray_style == "box" and s.finger_width < 2 * s.kerf + 1:
        problems.append("Finger width is too small.")
    if s.engrave_numbers and not s.image:
        problems.append("Engraving color numbers needs a pattern image.")
    return problems


def build(s):
    """Lay out every sheet. Returns (sheets, pattern, needed, notes) where
    sheets is a list of (file_stem, width, height, pieces)."""
    problems = validate(s)
    if problems:
        raise ValueError("\n".join(problems))
    pattern = load_pattern(s.image, s.cols, s.rows, s.colors) if s.image else None
    notes = []
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

    if pattern:
        groups = [(f"c{i + 1:02d}", f"C{i + 1} {hexcolor(rgb)}", n)
                  for i, (rgb, n) in enumerate(zip(pattern["palette"], pattern["counts"]))]
    else:
        groups = [("all", "", s.cols * s.rows)]
    needed = []
    for key, label, count in groups:
        want = math.ceil(count * (1 + s.spare_percent / 100) - 1e-9)
        blocks = tile_blocks(s, want, label, key)
        packed = pack(blocks, s.sheet_width, s.sheet_height, s.spacing)
        cut = sum(_tile_count(b, s) for b in blocks)
        needed.append(cut)
        for n, (w, h, items) in enumerate(packed, 1):
            sheets.append((f"{s.name}_tiles_{key}_{n}", w, h, items))
        notes.append(f"Tiles {label or 'all'}: {count} in picture, {cut} cut "
                     f"on {len(packed)} sheet(s) of {s.tile_thickness:g} mm stock")
    return sheets, pattern, needed, notes


def _tile_count(block, s):
    # Interior lines = (c-1) + (r-1); recover c and r from the block size.
    p = s.pixel + s.kerf
    w, h = block.size
    return round((w - 2 * s.tile_border) / p) * round((h - 2 * s.tile_border) / p)


def summary(s):
    win, hin = tray_inner(s)
    t = s.tray_thickness
    lines = [
        f"Picture: {s.cols} x {s.rows} = {s.cols * s.rows} tiles, "
        f"{s.cols * s.pixel:g} x {s.rows * s.pixel:g} mm",
        f"Tile pitch on sheet: {s.pixel + s.kerf:.3f} mm (pixel + kerf)",
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


def generate(s):
    """Write all SVGs plus a notes file. Returns (files, notes, sheets)."""
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
    return files, notes, sheets


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

FORM = [
    ("Grid", [("cols", "Columns"), ("rows", "Rows"), ("pixel", "Pixel size (mm)")]),
    ("Material", [("tile_thickness", "Tile thickness (mm)"), ("tray_thickness", "Tray thickness (mm)"),
                  ("kerf", "Kerf (mm)")]),
    ("Tray", [("tray_style", "Style"), ("tile_clearance", "Tile clearance (mm)"),
              ("lip", "Wall above tiles (mm)"), ("finger_width", "Finger width (mm)"),
              ("joint_play", "Joint play (mm)"), ("frame_width", "Frame width (layered, mm)"),
              ("engrave_grid", "Engrave grid on base"), ("engrave_numbers", "Engrave color numbers")]),
    ("Sheets", [("sheet_width", "Sheet width (mm)"), ("sheet_height", "Sheet height (mm)"),
                ("spacing", "Part spacing (mm)"), ("tile_border", "Tile sheet border (mm)"),
                ("spare_percent", "Spare tiles (%)")]),
    ("Pattern (optional)", [("image", "Image"), ("colors", "Max colors")]),
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
    from tkinter import filedialog, messagebox, ttk

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

    def grid_from_image():
        try:
            w, h = image_size(vars_["image"].get())
        except Exception as e:
            messagebox.showerror("Image", str(e))
            return
        if w > 200 or h > 200:
            if not messagebox.askyesno("Image", f"Image is {w} x {h} px. Use that many tiles anyway?"):
                return
        vars_["cols"].set(str(w))
        vars_["rows"].set(str(h))

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
                    if key == "tray_style":
                        ttk.Combobox(box, textvariable=var, values=TRAY_STYLES, state="readonly",
                                     width=9).grid(row=r, column=1, sticky="w")
                    else:
                        width = 22 if key in ("image", "output_dir") else 9
                        ttk.Entry(box, textvariable=var, width=width).grid(row=r, column=1, sticky="w")
                    if key == "image":
                        ttk.Button(box, text="…", width=2, command=browse_image).grid(row=r, column=2)
                        ttk.Button(box, text="Set grid from image", command=grid_from_image).grid(
                            row=r + 10, column=0, columnspan=3, sticky="w", pady=(4, 0))
                    elif key == "output_dir":
                        ttk.Button(box, text="…", width=2, command=browse_dir).grid(row=r, column=2)
                vars_[key] = var

    info = tk.StringVar()
    ttk.Label(side, textvariable=info, justify="left", font=("TkFixedFont", 11)).pack(anchor="w")
    canvas = tk.Canvas(side, width=460, height=360, background="#f4f1ea", highlightthickness=1,
                       highlightbackground="#bbb")
    canvas.pack(fill="both", expand=True, pady=6)
    nav = ttk.Frame(side)
    nav.pack(fill="x")
    sheet_label = tk.StringVar(value="Press Preview to lay out the sheets")
    state = {"sheets": [], "i": 0}

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
        for pc in items:
            for color, polys in (("#2b4fd6", pc.engraves), ("#d62b2b", pc.cuts)):
                for pts, closed in polys:
                    flat = [c for x, y in (pts + pts[:1] if closed else pts) for c in (ox + x * k, oy + y * k)]
                    canvas.create_line(*flat, fill=color)
            if k * (pc.texts[0][2] if pc.texts else 0) >= 6:
                for x, y, size, text, ang, _ in pc.texts:
                    canvas.create_text(ox + x * k, oy + y * k, text=text, fill="#2b4fd6", anchor="s",
                                       angle=-ang, font=("TkDefaultFont", max(6, int(size * k * 0.8))))

    def step(d):
        if state["sheets"]:
            state["i"] = (state["i"] + d) % len(state["sheets"])
            draw()

    def refresh_info(*_):
        try:
            info.set(summary(read_settings()))
        except (ValueError, ZeroDivisionError):
            info.set("(enter valid numbers)")

    def preview():
        try:
            s = read_settings()
            sheets, _, _, notes = build(s)
        except Exception as e:
            messagebox.showerror("Preview", str(e))
            return
        state["sheets"], state["i"] = sheets, 0
        draw()
        log.set("\n".join(notes))

    def generate_clicked():
        try:
            s = read_settings()
            files, notes, sheets = generate(s)
            SETTINGS_FILE.write_text(json.dumps(asdict(s), indent=2))
        except Exception as e:
            messagebox.showerror("Generate", str(e))
            return
        state["sheets"], state["i"] = sheets, 0
        draw()
        log.set("\n".join(notes) + f"\n\nWrote {len(files)} files to {files[0].parent}")
        last_dir["path"] = files[0].parent

    last_dir = {"path": None}

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

    log = tk.StringVar()
    ttk.Label(side, textvariable=log, justify="left", wraplength=460).pack(anchor="w", pady=4)
    buttons = ttk.Frame(side)
    buttons.pack(fill="x", pady=(4, 0))
    ttk.Button(buttons, text="Preview", command=preview).pack(side="left")
    ttk.Button(buttons, text="Generate SVGs", command=generate_clicked).pack(side="left", padx=6)
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
        elif f.name == "tray_style":
            ap.add_argument(flag, choices=TRAY_STYLES, default=val)
        else:
            ap.add_argument(flag, type=type(val), default=val)
    args = ap.parse_args(argv)
    s = Settings(**{f.name: getattr(args, f.name) for f in fields(Settings)})
    try:
        files, notes, _ = generate(s)
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
