// Kit parts: trays, kerf test and tile blocks (spec.md §5.5–5.7, §6, §8).

import { Piece, Rect, outlinePiece, rectPts, segments } from "./geometry";
import { fixed, pyRoundTo } from "./py";
import type { Pattern } from "./pattern";
import type { Settings } from "./settings";

export interface TrayInfo {
  outer: [number, number, number];
  notes: string[];
}

export function trayInner(s: Settings): [number, number] {
  return [s.cols * s.pixel + s.tile_clearance, s.rows * s.pixel + s.tile_clearance];
}

/** Engrave the tile grid (and optionally color numbers) on the base, with
 * the tile area's top-left corner at (ox, oy). */
export function engraveBase(s: Settings, piece: Piece, ox: number, oy: number, pattern: Pattern | null): void {
  const p = s.pixel;
  ox += s.tile_clearance / 2;
  oy += s.tile_clearance / 2;
  piece.picture = [[ox, oy], [ox + s.cols * p, oy], [ox, oy + s.rows * p]];
  if (s.engrave_grid) {
    for (let i = 0; i <= s.cols; i++) {
      const x = ox + i * p;
      const pts: [number, number][] = [[x, oy], [x, oy + s.rows * p]];
      piece.engraves.push({ pts: i % 2 ? pts : pts.reverse(), closed: false });
    }
    for (let j = 0; j <= s.rows; j++) {
      const y = oy + j * p;
      const pts: [number, number][] = [[ox, y], [ox + s.cols * p, y]];
      piece.engraves.push({ pts: j % 2 ? pts : pts.reverse(), closed: false });
    }
  }
  if (s.engrave_numbers && pattern) {
    const size = p * 0.42;
    pattern.grid.forEach((row, j) =>
      row.forEach((idx, i) => {
        const cx = ox + (i + 0.5) * p, cy = oy + (j + 0.5) * p;
        piece.texts.push({ x: cx, y: cy + size * 0.35, size, text: String(idx + 1), angle: 0, anchor: "middle" });
      }),
    );
  }
}

/** Open-top box: base with fingers on all edges, four walls with finger
 * joints at the corners. Walls rise tile_thickness + lip above the base. */
export function makeBox(s: Settings, pattern: Pattern | null): [Piece[], TrayInfo] {
  const t = s.tray_thickness;
  const [win, hin] = trayInner(s);
  const z = t + s.tile_thickness + s.lip;
  const q = s.joint_play / 4; // half the play goes to each side of a joint
  const xseg = segments(win, s.finger_width, 3);
  const yseg = segments(hin, s.finger_width, 3);
  const zseg = segments(z, s.finger_width, 1);
  const odd = <T>(a: T[]) => a.filter((_, i) => i % 2 === 1);
  const even = <T>(a: T[]) => a.filter((_, i) => i % 2 === 0);

  const adds: Rect[] = [[t, t, t + win, t + hin]];
  for (const [a, b] of odd(xseg)) {
    adds.push([t + a + q, 0, t + b - q, t], [t + a + q, t + hin, t + b - q, 2 * t + hin]);
  }
  for (const [a, b] of odd(yseg)) {
    adds.push([0, t + a + q, t, t + b - q], [t + win, t + a + q, 2 * t + win, t + b - q]);
  }
  const base = outlinePiece("base", adds, [], s.kerf);
  engraveBase(s, base, t, t, pattern);

  const wall = (name: string, innerLen: number, segs: [number, number][], ownsEvenCorners: boolean) => {
    const length = innerLen + 2 * t;
    const subs: Rect[] = [];
    // v measured up from the wall's bottom edge
    const notch = (u0: number, v0: number, u1: number, v1: number) => subs.push([u0, z - v1, u1, z - v0]);
    for (const [a, b] of odd(segs)) notch(t + a - q, 0, t + b + q, t); // slots for the base's fingers
    for (const [a, b] of ownsEvenCorners ? odd(zseg) : even(zseg)) {
      notch(0, a - q, t, b + q);
      notch(length - t, a - q, length, b + q);
    }
    return outlinePiece(name, [[0, 0, length, z]], subs, s.kerf);
  };

  const parts = [base];
  for (const side of ["front", "back"]) parts.push(wall(`wall_${side}`, win, xseg, true));
  for (const side of ["left", "right"]) parts.push(wall(`wall_${side}`, hin, yseg, false));
  const notes = [
    `Box: outer ${fixed(win + 2 * t, 1)} x ${fixed(hin + 2 * t, 1)} mm, ` +
      `wall height ${fixed(z, 1)} mm (${zseg.length} corner segment(s), ` +
      `${Math.floor(xseg.length / 2)}/${Math.floor(yseg.length / 2)} base fingers per long/short edge)`,
  ];
  if (zseg.length === 1) notes.push("Walls are too low for corner fingers: corners are butt joints, glue them.");
  return [parts, { outer: [win + 2 * t, hin + 2 * t, z], notes }];
}

export function layerCount(s: Settings): number {
  return Math.max(1, Math.ceil((s.tile_thickness + s.lip) / s.tray_thickness - 1e-6));
}

/** Flat base plus a stack of frame rings, glued together. */
export function makeLayered(s: Settings, pattern: Pattern | null): [Piece[], TrayInfo] {
  const t = s.tray_thickness;
  const b = s.frame_width;
  const [win, hin] = trayInner(s);
  const wo = win + 2 * b, ho = hin + 2 * b;
  const layers = layerCount(s);
  const base = outlinePiece("base", [[0, 0, wo, ho]], [], s.kerf);
  engraveBase(s, base, b, b, pattern);
  const parts = [base];
  for (let n = 0; n < layers; n++) {
    parts.push(outlinePiece(`frame_${n + 1}`, [[0, 0, wo, ho]], [[b, b, b + win, b + hin]], s.kerf));
  }
  const rim = t * layers - s.tile_thickness;
  return [parts, {
    outer: [wo, ho, t * (layers + 1)],
    notes: [`Layered tray: outer ${fixed(wo, 1)} x ${fixed(ho, 1)} mm, base + ${layers} frame layer(s), ` +
      `rim ${rim >= 0 ? "+" : ""}${fixed(rim, 1)} mm relative to the tiles`],
  }];
}

export const KERF_TEST_STEP = 0.05;
export const KERF_TEST_COUNT = 7;

/** Kerf values to try, centred on the current setting. */
export function kerfTestValues(s: Settings): number[] {
  const mid = Math.floor(KERF_TEST_COUNT / 2);
  const vals = Array.from({ length: KERF_TEST_COUNT }, (_, i) => pyRoundTo(s.kerf + (i - mid) * KERF_TEST_STEP, 3));
  return vals.filter((v) => v >= 0).map((v) => v + 0); // + 0 turns -0 into 0
}

/** Pairs of test pieces in tray stock, one pair per kerf value: a plate with
 * two slots, and a tab piece whose two tabs push into them, exactly as the
 * box base and walls fit. Each piece is engraved with its kerf value. */
export function makeKerfTest(s: Settings): Piece[] {
  const t = s.tray_thickness, f = s.finger_width;
  const q = s.joint_play / 4; // same play per side as the box joints
  const margin = 4;
  const w = 2 * f + 3 * margin;
  const plateH = margin + t + 9;
  const tabH = 10;
  const xs: [number, number][] = [[margin, margin + f], [w - margin - f, w - margin]];
  const size = 3.5;
  const pieces: Piece[] = [];
  for (const k of kerfTestValues(s)) {
    const label = fixed(k, 2);
    const slots: Rect[] = xs.map(([a, b]) => [a - q, margin - q, b + q, margin + t + q]);
    const plate = outlinePiece(`kerf_${label}_plate`, [[0, 0, w, plateH]], slots, k);
    plate.texts.push({ x: w / 2, y: plateH - 2.5, size, text: label, angle: 0, anchor: "middle" });
    const tabs: Rect[] = xs.map(([a, b]) => [a + q, tabH, b - q, tabH + t]);
    const tab = outlinePiece(`kerf_${label}_tab`, [[0, 0, w, tabH], ...tabs], [], k);
    tab.texts.push({ x: w / 2, y: tabH / 2 + size * 0.35, size, text: label, angle: 0, anchor: "middle" });
    pieces.push(plate, tab);
  }
  return pieces;
}

/** c x r tiles on a shared cut grid, optionally inside a waste border.
 *
 * With tile_tabs, every vertical cut (including the tile area's left and
 * right edges) stops short around the middle of each tile, leaving a
 * tab_width bridge: each tile hangs on its left and right neighbours, rows
 * come apart along the horizontal cuts, and row ends hold on to the side
 * frame. The gap in the path is tab_width + kerf, because the beam reaches
 * kerf/2 past each end of a cut. That uses the real beam width (the tray
 * kerf), not tile_kerf, so bridges stay tab_width however loose the tiles
 * are made. */
export function tileBlock(name: string, c: number, r: number, s: Settings, label = ""): Piece {
  const p = s.pixel + s.tile_kerf;
  const bd = s.tile_border;
  const w = c * p, h = r * p;
  const piece = new Piece(name);
  if (label && bd >= 3) {
    piece.texts.push({ x: bd, y: bd * 0.8, size: bd * 0.6, text: label, angle: 0, anchor: "start" });
  }
  type Seg = [number, number][];
  const vertical = (i: number): Seg[] => {
    const x = bd + i * p;
    if (!s.tile_tabs) {
      const pts: Seg = [[x, bd], [x, bd + h]];
      return [i % 2 ? pts : pts.reverse()];
    }
    const half = (s.tab_width + s.kerf) / 2;
    const segs: Seg[] = [];
    let y = bd;
    for (let j = 0; j < r; j++) {
      const mid = bd + (j + 0.5) * p;
      segs.push([[x, y], [x, mid - half]]);
      y = mid + half;
    }
    segs.push([[x, y], [x, bd + h]]);
    return i % 2 ? segs : segs.reverse().map((seg) => seg.reverse());
  };
  const open = (pts: Seg) => ({ pts, closed: false });

  for (let i = 1; i < c; i++) piece.cuts.push(...vertical(i).map(open));
  for (let j = 1; j < r; j++) {
    const y = bd + j * p;
    const pts: Seg = [[bd, y], [bd + w, y]];
    piece.cuts.push(open(j % 2 ? pts : pts.reverse()));
  }
  if (s.tile_tabs) {
    piece.cuts.push(open([[bd, bd], [bd + w, bd]]));
    piece.cuts.push(...vertical(c).map(open));
    piece.cuts.push(open([[bd + w, bd + h], [bd, bd + h]]));
    piece.cuts.push(...vertical(0).map(open));
  } else {
    piece.cuts.push({ pts: rectPts(bd, bd, bd + w, bd + h), closed: true });
  }
  if (bd > 0) piece.cuts.push({ pts: rectPts(0, 0, w + 2 * bd, h + 2 * bd), closed: true });
  return piece;
}

/** Most tile columns and rows one block can have on a sheet. */
export function tileCapacity(s: Settings): [number, number] {
  const p = s.pixel + s.tile_kerf;
  const maxc = Math.floor((s.sheet_width - 2 * s.spacing - 2 * s.tile_border) / p);
  const maxr = Math.floor((s.sheet_height - 2 * s.spacing - 2 * s.tile_border) / p);
  if (maxc < 1 || maxr < 1) throw new Error("Sheet is too small for even one tile");
  return [maxc, maxr];
}

export function wantedTiles(s: Settings, exact: number): number {
  return Math.ceil(exact * (1 + s.spare_percent / 100) - 1e-9);
}

/** Picture count plus spares, trimming spares that would only spill onto an
 * extra sheet. Never fewer than `exact`. */
export function tilesToCut(s: Settings, exact: number): number {
  const [maxc, maxr] = tileCapacity(s);
  const cap = maxc * maxr;
  return Math.max(exact, Math.min(wantedTiles(s, exact), Math.ceil(exact / cap) * cap));
}

/** Split `total` tiles over colors: each gets its count, one spare if there
 * are enough to go round, and a share of the rest proportional to its count
 * (largest remainder). */
export function shareSpares(counts: number[], total: number): number[] {
  let spares = total - counts.reduce((a, b) => a + b, 0);
  const base = spares >= counts.length ? 1 : 0;
  spares -= base * counts.length;
  const sum = counts.reduce((a, b) => a + b, 0);
  const exact = counts.map((c) => (spares * c) / sum);
  const extra = exact.map((e) => Math.floor(e));
  const order = counts.map((_, i) => i).sort((a, b) => (exact[b] - extra[b]) - (exact[a] - extra[a]));
  const left = spares - extra.reduce((a, b) => a + b, 0);
  for (const i of order.slice(0, Math.max(0, left))) extra[i] += 1;
  return counts.map((c, i) => c + base + extra[i]);
}

export function tileBlocks(s: Settings, count: number, label: string, key: string): Piece[] {
  const [maxc, maxr] = tileCapacity(s);
  const blocks: Piece[] = [];
  let n = count;
  while (n > 0) {
    const m = Math.min(n, maxc * maxr);
    let c = Math.min(maxc, Math.ceil(Math.sqrt(m)));
    let r = Math.ceil(m / c);
    if (r > maxr) {
      c = maxc;
      r = Math.ceil(m / c);
    }
    blocks.push(tileBlock(`tiles_${key}_${blocks.length + 1}`, c, r, s, label));
    n -= c * r;
  }
  return blocks;
}

