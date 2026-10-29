// Paint by number: regions, small-region merging, outline chains, smoothing,
// number placement and the board / preview / key SVGs (spec.md §15).
// A line-for-line port of pixelkit.py, so both write identical files.

import { Piece, type Pt, rectPts } from "./geometry";
import type { Pattern } from "./pattern";
import { cmp, fixed, g } from "./py";
import type { Settings } from "./settings";
import { CUT, esc, hexcolor, STROKE, textColor } from "./svg";

export const PBN_LINE = "#0000ff";   // region outlines (LightBurn layer 01)
export const PBN_NUMBER = "#00e000"; // numbers (LightBurn layer 03), a separate layer
export const PBN_PRINT_LINE = "#000000";
export const PBN_PRINT_NUMBER = "#555555";

const N4: [number, number][] = [[0, -1], [0, 1], [-1, 0], [1, 0]]; // up, down, left, right
const N8: [number, number][] = [[-1, -1], [0, -1], [1, -1], [-1, 0], [1, 0], [-1, 1], [0, 1], [1, 1]];

type Grid = number[][];
type Chain = { pts: Pt[]; closed: boolean };

/** 4-connected same-color regions; ids in row-major discovery order. */
export function labelRegions(grid: Grid): { labels: Grid; sizes: number[] } {
  const rows = grid.length, cols = grid[0].length;
  const labels = grid.map((row) => row.map(() => -1));
  const sizes: number[] = [];
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      if (labels[j][i] >= 0) continue;
      const rid = sizes.length, color = grid[j][i];
      labels[j][i] = rid;
      const queue: [number, number][] = [[i, j]];
      for (let k = 0; k < queue.length; k++) {
        const [ci, cj] = queue[k];
        for (const [di, dj] of N4) {
          const ni = ci + di, nj = cj + dj;
          if (ni >= 0 && ni < cols && nj >= 0 && nj < rows && labels[nj][ni] < 0 && grid[nj][ni] === color) {
            labels[nj][ni] = rid;
            queue.push([ni, nj]);
          }
        }
      }
      sizes.push(queue.length);
    }
  }
  return { labels, sizes };
}

/** Min-heap of [size, id] pairs, popped in the same order as Python's heapq
 * (keys are unique, so any correct heap agrees). */
class Heap {
  private a: [number, number][] = [];
  private less = (x: [number, number], y: [number, number]) => x[0] < y[0] || (x[0] === y[0] && x[1] < y[1]);
  get size() { return this.a.length; }
  push(v: [number, number]): void {
    const a = this.a;
    a.push(v);
    for (let k = a.length - 1; k > 0;) {
      const p = (k - 1) >> 1;
      if (!this.less(a[k], a[p])) break;
      [a[k], a[p]] = [a[p], a[k]];
      k = p;
    }
  }
  pop(): [number, number] {
    const a = this.a, top = a[0], last = a.pop()!;
    if (a.length) {
      a[0] = last;
      for (let k = 0; ;) {
        const l = 2 * k + 1, r = l + 1;
        let m = k;
        if (l < a.length && this.less(a[l], a[m])) m = l;
        if (r < a.length && this.less(a[r], a[m])) m = r;
        if (m === k) break;
        [a[k], a[m]] = [a[m], a[k]];
        k = m;
      }
    }
    return top;
  }
}

/** Repaint regions smaller than minSize cells with the color of the
 * neighbour they share the longest border with (ties: larger region, then
 * lower id), smallest first. */
export function mergeSmallRegions(grid: Grid, minSize: number): { grid: Grid; merged: number } {
  const rows = grid.length, cols = grid[0].length;
  const { labels, sizes } = labelRegions(grid);
  const colors = new Array<number>(sizes.length).fill(0);
  const cells: [number, number][][] = sizes.map(() => []);
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      colors[labels[j][i]] = grid[j][i];
      cells[labels[j][i]].push([i, j]);
    }
  }
  const alive = sizes.map(() => true);
  const heap = new Heap();
  sizes.forEach((n, rid) => heap.push([n, rid]));
  let merged = 0;
  while (heap.size) {
    const [n, rid] = heap.pop();
    if (!alive[rid] || n !== sizes[rid]) continue; // stale entry
    if (n >= minSize) break;
    const border = new Map<number, number>();
    for (const [i, j] of cells[rid]) {
      for (const [di, dj] of N4) {
        const ni = i + di, nj = j + dj;
        if (ni >= 0 && ni < cols && nj >= 0 && nj < rows && labels[nj][ni] !== rid) {
          border.set(labels[nj][ni], (border.get(labels[nj][ni]) ?? 0) + 1);
        }
      }
    }
    if (!border.size) continue; // the whole picture is one region
    let target = -1;
    for (const t of border.keys()) {
      if (target < 0 || cmp([border.get(t)!, sizes[t], -t], [border.get(target)!, sizes[target], -target]) > 0) target = t;
    }
    for (const [i, j] of cells[rid]) labels[j][i] = target;
    cells[target].push(...cells[rid]);
    sizes[target] += sizes[rid];
    alive[rid] = false;
    merged++;
    heap.push([sizes[target], target]);
  }
  return { grid: labels.map((row) => row.map((rid) => colors[rid])), merged };
}

/** Region boundaries as polylines in grid units, each shared border once.
 * Chains run between junctions (branch points, plus the picture's corners);
 * boundaries with no junction are closed loops. */
export function outlineChains(grid: Grid): Chain[] {
  const rows = grid.length, cols = grid[0].length;
  const hEdge = (x: number, y: number) => x >= 0 && x < cols &&
    (y === 0 || y === rows || (y > 0 && y < rows && grid[y - 1][x] !== grid[y][x]));
  const vEdge = (x: number, y: number) => y >= 0 && y < rows &&
    (x === 0 || x === cols || (x > 0 && x < cols && grid[y][x - 1] !== grid[y][x]));
  type Link = [string, Pt];
  const links = (x: number, y: number): Link[] => { // right, down, left, up
    const out: Link[] = [];
    if (hEdge(x, y)) out.push([`h${x},${y}`, [x + 1, y]]);
    if (vEdge(x, y)) out.push([`v${x},${y}`, [x, y + 1]]);
    if (hEdge(x - 1, y)) out.push([`h${x - 1},${y}`, [x - 1, y]]);
    if (vEdge(x, y - 1)) out.push([`v${x},${y - 1}`, [x, y - 1]]);
    return out;
  };
  const isCorner = (x: number, y: number) => (x === 0 || x === cols) && (y === 0 || y === rows);
  const junction = (x: number, y: number) => {
    const n = links(x, y).length;
    return isCorner(x, y) || (n !== 0 && n !== 2);
  };
  const seen = new Set<string>();
  const chains: Chain[] = [];
  const walk = (start: Pt, edge: string, next: Pt): Pt[] => {
    const pts: Pt[] = [start];
    seen.add(edge);
    let cur = next;
    for (;;) {
      pts.push(cur);
      if (junction(cur[0], cur[1]) || (cur[0] === start[0] && cur[1] === start[1])) return pts;
      const follow = links(cur[0], cur[1]).filter(([e]) => !seen.has(e));
      if (!follow.length) return pts;
      const [e, n] = follow[0];
      seen.add(e);
      cur = n;
    }
  };
  for (let y = 0; y <= rows; y++) {
    for (let x = 0; x <= cols; x++) {
      if (!junction(x, y)) continue;
      for (const [e, n] of links(x, y)) if (!seen.has(e)) chains.push({ pts: walk([x, y], e, n), closed: false });
    }
  }
  for (let y = 0; y <= rows; y++) {
    for (let x = 0; x <= cols; x++) {
      for (const [e, n] of links(x, y)) {
        if (!seen.has(e)) chains.push({ pts: walk([x, y], e, n).slice(0, -1), closed: true });
      }
    }
  }
  return chains;
}

/** Round off pixel steps: edge midpoints, then Chaikin corner cutting. Open
 * chains keep their end points, so neighbouring chains still meet. */
export function smoothChain(pts: Pt[], closed: boolean, passes = 2): Pt[] {
  let q: Pt[];
  const n0 = pts.length;
  if (closed) {
    q = pts.map((p, k) => [(p[0] + pts[(k + 1) % n0][0]) / 2, (p[1] + pts[(k + 1) % n0][1]) / 2]);
  } else {
    if (n0 < 3) return pts.map(([x, y]) => [x, y]);
    q = [[pts[0][0], pts[0][1]]];
    for (let k = 0; k + 1 < n0; k++) q.push([(pts[k][0] + pts[k + 1][0]) / 2, (pts[k][1] + pts[k + 1][1]) / 2]);
    q.push([pts[n0 - 1][0], pts[n0 - 1][1]]);
  }
  for (let pass = 0; pass < passes; pass++) {
    const r: Pt[] = closed ? [] : [q[0]];
    const n = closed ? q.length : q.length - 1;
    for (let k = 0; k < n; k++) {
      const a = q[k], b = q[(k + 1) % q.length];
      r.push([0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]]);
      r.push([0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]]);
    }
    if (!closed) r.push(q[q.length - 1]);
    q = r;
  }
  return q;
}

const collinear = (a: Pt, b: Pt, c: Pt) => Math.abs((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])) < 1e-9;

/** Remove points on the straight line through their neighbours (one pass,
 * comparing against the last point kept). */
export function dropCollinear(pts: Pt[], closed: boolean): Pt[] {
  if (closed) {
    const n = pts.length;
    let start = -1;
    for (let k = 0; k < n; k++) {
      if (!collinear(pts[(k - 1 + n) % n], pts[k], pts[(k + 1) % n])) {
        start = k;
        break;
      }
    }
    if (start < 0) return pts.slice();
    const ring = [...pts.slice(start), ...pts.slice(0, start), pts[start]];
    return dropCollinear(ring, false).slice(0, -1);
  }
  if (pts.length < 3) return pts.slice();
  const out: Pt[] = [pts[0]];
  for (let k = 1; k < pts.length - 1; k++) {
    if (!collinear(out[out.length - 1], pts[k], pts[k + 1])) out.push(pts[k]);
  }
  out.push(pts[pts.length - 1]);
  return out;
}

/** Region ids on the two sides of a chain's first edge (-1 off the grid):
 * side A along the normal (dy, -dx) of travel, side B opposite. */
export function chainSides(chain: Pt[], labels: Grid): [number, number] {
  const rows = labels.length, cols = labels[0].length;
  const [[x0, y0], [x1, y1]] = chain;
  const dx = x1 - x0, dy = y1 - y0;
  const mx = x0 + dx / 2, my = y0 + dy / 2;
  const region = (px: number, py: number) => {
    const i = Math.floor(px), j = Math.floor(py);
    return i >= 0 && i < cols && j >= 0 && j < rows ? labels[j][i] : -1;
  };
  return [region(mx + dy / 2, my - dx / 2), region(mx - dy / 2, my + dx / 2)];
}

/** Closed outlines for each region, built from the (smoothed) chains so
 * fills meet the drawn lines exactly. */
export function regionLoops(chains: Chain[], sides: [number, number][], nregions: number): Map<number, Pt[][]> {
  const pieces = new Map<number, Pt[][]>(); // region -> rings, each with the region on side A
  const add = (rid: number, ring: Pt[]) => {
    if (!pieces.has(rid)) pieces.set(rid, []);
    pieces.get(rid)!.push(ring);
  };
  chains.forEach(({ pts, closed }, k) => {
    const ring = closed ? [...pts, pts[0]] : pts;
    const [a, b] = sides[k];
    if (a >= 0) add(a, ring.slice());
    if (b >= 0) add(b, ring.slice().reverse());
  });
  const same = (p: Pt, q: Pt) => p[0] === q[0] && p[1] === q[1];
  const loops = new Map<number, Pt[][]>();
  for (let rid = 0; rid < nregions; rid++) {
    const todo = pieces.get(rid) ?? [];
    const used = todo.map(() => false);
    const out: Pt[][] = [];
    for (let k = 0; k < todo.length; k++) {
      if (used[k]) continue;
      used[k] = true;
      let loop = todo[k].slice();
      while (!same(loop[loop.length - 1], loop[0])) {
        const nxt = todo.findIndex((piece, m) => !used[m] && same(piece[0], loop[loop.length - 1]));
        if (nxt < 0) break;
        used[nxt] = true;
        loop = [...loop, ...todo[nxt].slice(1)];
      }
      out.push(same(loop[loop.length - 1], loop[0]) ? loop.slice(0, -1) : loop);
    }
    loops.set(rid, out);
  }
  return loops;
}

/** One label spot per region: the cell farthest from the region's edge
 * (8-neighbour steps), ties broken by nearness to the centroid, then
 * row-major order. Returns [i, j, depth, color] in region order. */
export function numberSpots(grid: Grid): [number, number, number, number][] {
  const rows = grid.length, cols = grid[0].length;
  const { labels, sizes } = labelRegions(grid);
  const depth = grid.map((row) => row.map(() => 0));
  const queue: [number, number][] = [];
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      for (const [di, dj] of N8) {
        const ni = i + di, nj = j + dj;
        if (!(ni >= 0 && ni < cols && nj >= 0 && nj < rows) || labels[nj][ni] !== labels[j][i]) {
          depth[j][i] = 1;
          queue.push([i, j]);
          break;
        }
      }
    }
  }
  for (let k = 0; k < queue.length; k++) {
    const [i, j] = queue[k];
    for (const [di, dj] of N8) {
      const ni = i + di, nj = j + dj;
      if (ni >= 0 && ni < cols && nj >= 0 && nj < rows && depth[nj][ni] === 0) {
        depth[nj][ni] = depth[j][i] + 1;
        queue.push([ni, nj]);
      }
    }
  }
  const sx = sizes.map(() => 0), sy = sizes.map(() => 0);
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      sx[labels[j][i]] += i + 0.5;
      sy[labels[j][i]] += j + 0.5;
    }
  }
  const best: ({ key: [number, number]; i: number; j: number } | null)[] = sizes.map(() => null);
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      const rid = labels[j][i];
      const cx = sx[rid] / sizes[rid], cy = sy[rid] / sizes[rid];
      const dx = i + 0.5 - cx, dy = j + 0.5 - cy;
      const key: [number, number] = [depth[j][i], -(dx * dx + dy * dy)];
      const b = best[rid];
      if (!b || cmp(key, b.key) > 0) best[rid] = { key, i, j };
    }
  }
  return best.map((b) => [b!.i, b!.j, b!.key[0], grid[b!.j][b!.i]]);
}

/** A paint-by-number board piece: outlines, numbers, optional cut line, plus
 * per-color fills (for the preview) and the layer styling. */
export class Board extends Piece {
  lineColor = PBN_LINE;
  textColor = PBN_NUMBER;
  lineWidth = STROKE;
  fills: Pt[][][] = [];
}

export interface PbnResult { board: Board; w: number; h: number; pattern: Pattern; regions: number[]; notes: string[] }

export function buildPbn(s: Settings, pattern: Pattern): PbnResult {
  const { grid: mergedGrid, merged } = mergeSmallRegions(pattern.grid, Math.max(1, s.pbn_min_region));
  // Renumber colors by tile count, as for mosaics (merging changes counts).
  const counts = new Map<number, number>();
  for (const row of mergedGrid) for (const idx of row) counts.set(idx, (counts.get(idx) ?? 0) + 1);
  const order = [...counts.keys()].sort((a, b) =>
    cmp([-counts.get(a)!, ...pattern.palette[a]], [-counts.get(b)!, ...pattern.palette[b]]));
  const renum = new Map(order.map((old, i) => [old, i]));
  const grid = mergedGrid.map((row) => row.map((idx) => renum.get(idx)!));
  const mergedPattern: Pattern = {
    ...pattern, palette: order.map((i) => pattern.palette[i]), counts: order.map((i) => counts.get(i)!), grid,
  };

  const rows = grid.length, cols = grid[0].length;
  const p = s.pixel, mg = s.pbn_margin;
  const w = cols * p + 2 * mg, h = rows * p + 2 * mg;
  const engrave = s.pbn_use === "engrave";
  const board = new Board("paint_by_number");
  board.lineColor = engrave ? PBN_LINE : PBN_PRINT_LINE;
  board.textColor = engrave ? PBN_NUMBER : PBN_PRINT_NUMBER;
  board.lineWidth = engrave ? STROKE : s.pbn_line_width;
  board.picture = [[mg, mg], [mg + cols * p, mg], [mg, mg + rows * p]];
  const { labels, sizes } = labelRegions(grid);
  const chains = outlineChains(grid);
  const sides = chains.map((c) => chainSides(c.pts, labels));
  const shaped: Chain[] = chains.map(({ pts, closed }) =>
    ({ pts: dropCollinear(s.pbn_smooth ? smoothChain(pts, closed) : pts, closed), closed }));
  const toMm = (pts: Pt[]): Pt[] => pts.map(([x, y]) => [mg + x * p, mg + y * p]);
  board.engraves = shaped.map(({ pts, closed }) => ({ pts: toMm(pts), closed }));
  // Fills for the color preview: each color's region outlines as one path.
  const regionColor = sizes.map(() => 0);
  for (let j = 0; j < rows; j++) for (let i = 0; i < cols; i++) regionColor[labels[j][i]] = grid[j][i];
  board.fills = order.map(() => []);
  for (const [rid, loops] of regionLoops(shaped, sides, sizes.length)) {
    board.fills[regionColor[rid]].push(...loops.map(toMm));
  }

  const regions = order.map(() => 0);
  let tight = 0;
  for (const [i, j, d, idx] of numberSpots(grid)) {
    regions[idx]++;
    const text = String(idx + 1);
    const room = (2 * d - 1) * p;
    let size = Math.min(s.pbn_text_max, 0.8 * room, (0.8 * room) / (0.6 * text.length));
    if (size < s.pbn_text_min) {
      size = s.pbn_text_min;
      tight++;
    }
    const cx = mg + (i + 0.5) * p, cy = mg + (j + 0.5) * p;
    board.texts.push({ x: cx, y: cy + size * 0.35, size, text, angle: 0, anchor: "middle" });
  }
  if (engrave) board.cuts.push({ pts: rectPts(0, 0, w, h), closed: true });

  const total = regions.reduce((a, b) => a + b, 0);
  const notes = [
    `Paint by number: ${cols} x ${rows} cells at ${g(s.pixel)} mm, board ${fixed(w, 0)} x ${fixed(h, 0)} mm ` +
      `(${s.pbn_use}); ${total} regions in ${order.length} colors` +
      (merged ? `, ${merged} region(s) under ${s.pbn_min_region} cells merged into a neighbour` : ""),
  ];
  if (tight) {
    notes.push(`${tight} region(s) are too small for a ${g(s.pbn_text_min)} mm number; ` +
      "their numbers overlap the outlines. Raise Min region or use more cells.");
  }
  if (engrave) notes.push(`Layers: ${PBN_LINE} = region outlines, ${PBN_NUMBER} = numbers, ${CUT} = board cut line.`);
  else notes.push(`Print: outlines ${PBN_PRINT_LINE} at ${g(s.pbn_line_width)} mm, numbers ${PBN_PRINT_NUMBER}.`);
  if (w > s.sheet_width || h > s.sheet_height) {
    notes.push(`WARNING: the ${fixed(w, 0)} x ${fixed(h, 0)} mm board is larger than the ${g(s.sheet_width)} x ` +
      `${g(s.sheet_height)} mm sheet.`);
  }
  return { board, w, h, pattern: mergedPattern, regions, notes };
}

function d(pts: Pt[], closed: boolean): string {
  const s = "M" + pts.map(([x, y]) => `${fixed(x, 3)},${fixed(y, 3)}`).join(" L");
  return closed ? s + " Z" : s;
}

const head = (w: number, h: number) => [
  '<?xml version="1.0" encoding="UTF-8"?>',
  `<svg xmlns="http://www.w3.org/2000/svg" width="${fixed(w, 3)}mm" height="${fixed(h, 3)}mm" ` +
    `viewBox="0 0 ${fixed(w, 3)} ${fixed(h, 3)}">`,
];

/** Board markup: outlines, numbers and (engrave) cut line in separate groups
 * and colors, so each can get its own laser settings. */
export function pbnBody(board: Board): string[] {
  const cap = board.lineColor !== PBN_LINE ? ' stroke-linejoin="round" stroke-linecap="round"' : "";
  const out = [`  <g id="outlines" fill="none" stroke="${board.lineColor}" stroke-width="${g(board.lineWidth)}"${cap}>`];
  for (const e of board.engraves) out.push(`    <path d="${d(e.pts, e.closed)}"/>`);
  out.push("  </g>");
  out.push(`  <g id="numbers" fill="${board.textColor}" font-family="Arial, Helvetica, sans-serif" text-anchor="middle">`);
  for (const t of board.texts) {
    out.push(`    <text x="${fixed(t.x, 3)}" y="${fixed(t.y, 3)}" font-size="${fixed(t.size, 3)}">${t.text}</text>`);
  }
  out.push("  </g>");
  if (board.cuts.length) {
    out.push(`  <g id="cut" fill="none" stroke="${CUT}" stroke-width="${STROKE}">`);
    for (const c of board.cuts) out.push(`    <path d="${d(c.pts, c.closed)}"/>`);
    out.push("  </g>");
  }
  return out;
}

export function pbnSvg(w: number, h: number, board: Board): string {
  return [...head(w, h), ...pbnBody(board), "</svg>"].join("\n") + "\n";
}

/** The finished picture in color, with the outlines, for approval. */
export function pbnPreviewSvg(w: number, h: number, board: Board, pattern: Pattern): string {
  const out = [...head(w, h), `  <rect width="${fixed(w, 3)}" height="${fixed(h, 3)}" fill="#ffffff"/>`];
  board.fills.forEach((loops, idx) => { // one even-odd path per color
    if (loops.length) {
      out.push(`  <path d="${loops.map((l) => d(l, true)).join(" ")}" fill="${hexcolor(pattern.palette[idx])}" fill-rule="evenodd"/>`);
    }
  });
  out.push('  <g fill="none" stroke="#333333" stroke-width="0.15">');
  for (const e of board.engraves) out.push(`    <path d="${d(e.pts, e.closed)}"/>`);
  out.push("  </g>");
  out.push("</svg>");
  return out.join("\n") + "\n";
}

/** Color key: swatch, number, hex, regions and cells per color. */
export function pbnKeySvg(s: Settings, pattern: Pattern, regions: number[], credit: string | null): string {
  const n = pattern.palette.length;
  const w = 120, h = 20 + 9 * n + (credit ? 8 : 0);
  const out = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    `<svg xmlns="http://www.w3.org/2000/svg" width="${fixed(w, 1)}mm" height="${fixed(h, 1)}mm" viewBox="0 0 ${fixed(w, 1)} ${fixed(h, 1)}" ` +
      'font-family="Arial, Helvetica, sans-serif">',
    `<rect width="${fixed(w, 1)}" height="${fixed(h, 1)}" fill="#ffffff"/>`,
    `<text x="10.0" y="10.0" font-size="5">${esc(s.name)}: paint by number key</text>`,
  ];
  let y = 14;
  pattern.palette.forEach((rgb, idx) => {
    out.push(`<rect x="10.0" y="${fixed(y, 1)}" width="7" height="7" fill="${hexcolor(rgb)}" stroke="#333" stroke-width="0.2"/>`);
    out.push(`<text x="13.5" y="${fixed(y + 5, 1)}" font-size="4" text-anchor="middle" fill="${textColor(rgb)}">${idx + 1}</text>`);
    out.push(`<text x="21.0" y="${fixed(y + 5, 1)}" font-size="3.5">${hexcolor(rgb)}   ` +
      `${regions[idx]} region(s), ${pattern.counts[idx]} cells</text>`);
    y += 9;
  });
  if (credit) out.push(`<text x="10.0" y="${fixed(y + 4, 1)}" font-size="2.8" fill="#555">${esc(credit)}</text>`);
  out.push("</svg>");
  return out.join("\n") + "\n";
}
