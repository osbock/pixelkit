// Picture → tile grid: compositing, resampling, color reduction, majority
// vote, padding and pixel-art grid detection (spec.md §9.1–9.3).
// Images are plain arrays so this runs in the browser and in Node tests.

import { cmp, maxBy, pyRound } from "./py";
import { hexcolor } from "./svg";

export type RGB = [number, number, number];

/** Decoded image, 4 bytes (RGBA) per pixel. `source` is the pixelkit:source
 * text recorded in a PNG pixelkit wrote (e.g. "emoji:1f33b sunflower"). */
export interface RGBAImage { width: number; height: number; data: Uint8Array | Uint8ClampedArray; source?: string }

/** Opaque image, 3 bytes per pixel. */
export interface RGBImage { width: number; height: number; data: Uint8Array }

export interface Pattern {
  palette: RGB[];    // most common first
  counts: number[];
  grid: number[][];  // rows x cols palette indices
  note: string;
  source?: string;   // where the picture came from: "emoji:..." keeps the credit
}

const pack = (r: number, g: number, b: number) => (r << 16) | (g << 8) | b;
const unpack = (c: number): RGB => [(c >> 16) & 255, (c >> 8) & 255, c & 255];

/** Alpha-composite over an opaque background, with Pillow's integer math. */
export function composite(img: RGBAImage, bg: RGB): RGBImage {
  const n = img.width * img.height;
  const out = new Uint8Array(n * 3);
  const PB = 7;
  const div255 = (a: number) => ((a >>> 8) + a) >>> 8;
  for (let i = 0; i < n; i++) {
    const a = img.data[i * 4 + 3];
    for (let c = 0; c < 3; c++) {
      const src = img.data[i * 4 + c];
      if (a === 255) out[i * 3 + c] = src;
      else if (a === 0) out[i * 3 + c] = bg[c];
      else {
        const outa255 = a * 255 + 255 * (255 - a);
        const coef1 = Math.floor((a * 255 * 255 * (1 << PB)) / outa255);
        const coef2 = 255 * (1 << PB) - coef1;
        const tmp = src * coef1 + bg[c] * coef2;
        out[i * 3 + c] = div255(tmp + (0x80 << PB)) >>> PB;
      }
    }
  }
  return { width: img.width, height: img.height, data: out };
}

/** Pillow-style box-filter coefficients for resizing one axis. */
function boxCoeffs(inSize: number, outSize: number): { start: number; weights: number[] }[] {
  const scale = inSize / outSize;
  const support = Math.max(scale, 1) * 0.5;
  const ss = 1 / Math.max(scale, 1);
  const res: { start: number; weights: number[] }[] = [];
  for (let xx = 0; xx < outSize; xx++) {
    const center = (xx + 0.5) * scale;
    let xmin = Math.trunc(center - support + 0.5);
    if (xmin < 0) xmin = 0;
    let xmax = Math.trunc(center + support + 0.5);
    if (xmax > inSize) xmax = inSize;
    xmax -= xmin;
    const ws: number[] = [];
    let total = 0;
    for (let x = 0; x < xmax; x++) {
      const v = (x + xmin - center + 0.5) * ss;
      const w = v > -0.5 && v <= 0.5 ? 1 : 0; // Pillow's box_filter
      ws.push(w);
      total += w;
    }
    res.push({ start: xmin, weights: ws.map((w) => (total ? w / total : 0)) });
  }
  return res;
}

/** Box-filter resize (horizontal pass, then vertical), rounding to 8 bits
 * after each pass like Pillow. */
export function boxResize(img: RGBImage, w: number, h: number): RGBImage {
  const PB = 22;
  const toInt = (cs: { start: number; weights: number[] }[]) =>
    cs.map((c) => ({ start: c.start, weights: c.weights.map((x) => Math.floor(x * (1 << PB) + 0.5)) }));
  let cur = img;
  if (w !== cur.width) {
    const cs = toInt(boxCoeffs(cur.width, w));
    const out = new Uint8Array(w * cur.height * 3);
    for (let y = 0; y < cur.height; y++) {
      for (let x = 0; x < w; x++) {
        const { start, weights } = cs[x];
        for (let c = 0; c < 3; c++) {
          let ss = 1 << (PB - 1);
          for (let k = 0; k < weights.length; k++) ss += cur.data[(y * cur.width + start + k) * 3 + c] * weights[k];
          out[(y * w + x) * 3 + c] = Math.max(0, Math.min(255, Math.floor(ss / (1 << PB))));
        }
      }
    }
    cur = { width: w, height: cur.height, data: out };
  }
  if (h !== cur.height) {
    const cs = toInt(boxCoeffs(cur.height, h));
    const out = new Uint8Array(cur.width * h * 3);
    for (let y = 0; y < h; y++) {
      const { start, weights } = cs[y];
      for (let x = 0; x < cur.width; x++) {
        for (let c = 0; c < 3; c++) {
          let ss = 1 << (PB - 1);
          for (let k = 0; k < weights.length; k++) ss += cur.data[((start + k) * cur.width + x) * 3 + c] * weights[k];
          out[(y * cur.width + x) * 3 + c] = Math.max(0, Math.min(255, Math.floor(ss / (1 << PB))));
        }
      }
    }
    cur = { width: cur.width, height: h, data: out };
  }
  return cur;
}

/** Nearest-neighbour resize, as Pillow's ImagingScaleAffine: sample
 * positions start at scale / 2 and step by adding scale (a running sum, so
 * rounding matches Pillow exactly). */
export function nearestResize(img: RGBImage, w: number, h: number): RGBImage {
  const table = (inSize: number, outSize: number) => {
    const a = inSize / outSize;
    const out = new Int32Array(outSize);
    let pos = a * 0.5;
    for (let k = 0; k < outSize; k++) {
      out[k] = Math.min(inSize - 1, Math.trunc(pos));
      pos += a;
    }
    return out;
  };
  const xs = table(img.width, w), ys = table(img.height, h);
  const out = new Uint8Array(w * h * 3);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const src = (ys[y] * img.width + xs[x]) * 3, dst = (y * w + x) * 3;
      out[dst] = img.data[src];
      out[dst + 1] = img.data[src + 1];
      out[dst + 2] = img.data[src + 2];
    }
  }
  return { width: w, height: h, data: out };
}

/** Pillow's median-cut quantizer (libImaging/Quant.c, method 0), ported
 * step for step so palettes match pixelkit.py: boxes are split in order of
 * pixel count using Pillow's heap, along the luminance-weighted widest axis,
 * at the pixel-count median; each box's palette color is the rounded mean of
 * its pixels, and every color maps to the nearest palette entry.
 * Returns a map from input color to palette color. */
export function medianCut(counts: Map<number, number>, maxColors: number): Map<number, number> {
  const MAX_HASH_ENTRIES = 65536;
  const ch = (c: number, i: number) => (c >> (16 - 8 * i)) & 255;
  // Colors are hashed at a bit-shift scale small enough for <= 65536 entries.
  let scale = 0;
  const scaled = (c: number, sc: number) => pack(ch(c, 0) >> sc, ch(c, 1) >> sc, ch(c, 2) >> sc);
  let hist = new Map<number, number>();
  for (;;) {
    hist = new Map();
    for (const [c, n] of counts) {
      const k = scaled(c, scale);
      hist.set(k, (hist.get(k) ?? 0) + n);
    }
    if (hist.size <= MAX_HASH_ENTRIES) break;
    scale++;
  }

  type Entry = { c: number; v: [number, number, number]; count: number };
  type Box = { entries: Entry[]; pixelCount: number; l: Box | null; r: Box | null };
  const entries: Entry[] = [...hist].map(([c, count]) => ({ c, v: [ch(c, 0), ch(c, 1), ch(c, 2)], count }));
  const total = entries.reduce((s, e) => s + e.count, 0);
  const root: Box = { entries, pixelCount: total, l: null, r: null };

  const volume = (b: Box) => {
    if (!b.entries.length) return 0;
    let v = 1;
    for (let i = 0; i < 3; i++) {
      let lo = 255, hi = 0;
      for (const e of b.entries) {
        if (e.v[i] < lo) lo = e.v[i];
        if (e.v[i] > hi) hi = e.v[i];
      }
      v *= hi - lo + 1;
    }
    return v;
  };

  const split = (node: Box) => {
    const lo = [255, 255, 255], hi = [0, 0, 0];
    for (const e of node.entries) {
      for (let i = 0; i < 3; i++) {
        if (e.v[i] < lo[i]) lo[i] = e.v[i];
        if (e.v[i] > hi[i]) hi[i] = e.v[i];
      }
    }
    const f = [(hi[0] - lo[0]) * 77, (hi[1] - lo[1]) * 150, (hi[2] - lo[2]) * 29];
    let axis = 0;
    for (let i = 1; i < 3; i++) if (f[axis] < f[i]) axis = i;
    const sorted = node.entries.slice().sort((a, b) => b.v[axis] - a.v[axis]); // descending
    let left = 0, idx = 0;
    while (idx < sorted.length) {
      left += sorted[idx].count;
      idx++;
      if (left * 2 > node.pixelCount) break;
    }
    if (idx < sorted.length) {
      const splitVal = sorted[idx - 1].v[axis];
      while (idx < sorted.length && sorted[idx].v[axis] === splitVal) idx++;
    }
    let l = sorted.slice(0, idx), r = sorted.slice(idx);
    if (!r.length) {
      const minVal = sorted[sorted.length - 1].v[axis];
      r = sorted.filter((e) => e.v[axis] === minVal);
      l = sorted.filter((e) => e.v[axis] !== minVal);
    }
    const sum = (es: Entry[]) => es.reduce((s, e) => s + e.count, 0);
    node.l = { entries: l, pixelCount: sum(l), l: null, r: null };
    node.r = { entries: r, pixelCount: sum(r), l: null, r: null };
  };

  // Pillow's binary max-heap on pixelCount (1-based), for identical tie order.
  const heap: (Box | null)[] = [null];
  const cf = (a: Box, b: Box) => a.pixelCount - b.pixelCount;
  const heapAdd = (val: Box) => {
    heap.push(null);
    let k = heap.length - 1;
    while (k !== 1) {
      if (cf(val, heap[k >> 1]!) <= 0) break;
      heap[k] = heap[k >> 1];
      k >>= 1;
    }
    heap[k] = val;
  };
  const heapRemove = (): Box | null => {
    const count = heap.length - 1;
    if (!count) return null;
    const r = heap[1]!;
    const v = heap.pop()!;
    const n = count - 1;
    if (n === 0) return r;
    let k = 1;
    for (let l = 0; k * 2 <= n; k = l) {
      l = k * 2;
      if (l < n && cf(heap[l]!, heap[l + 1]!) < 0) l++;
      if (cf(v, heap[l]!) > 0) break;
      heap[k] = heap[l];
    }
    heap[k] = v;
    return r;
  };

  heapAdd(root);
  for (let n = maxColors; --n;) {
    let node: Box | null;
    do {
      node = heapRemove();
    } while (node && volume(node) === 1);
    if (!node) break;
    split(node);
    heapAdd(node.l!);
    heapAdd(node.r!);
  }

  // Leaves in tree order become palette entries.
  const boxOf = new Map<number, number>(); // scaled color -> palette index
  let nEntries = 0;
  const annotate = (b: Box) => {
    if (b.l && b.r) {
      annotate(b.l);
      annotate(b.r);
      return;
    }
    for (const e of b.entries) boxOf.set(e.c, nEntries);
    if (b.entries.length) nEntries++;
  };
  annotate(root);

  const sums = Array.from({ length: nEntries }, () => [0, 0, 0, 0]);
  for (const [c, n] of counts) {
    const s = sums[boxOf.get(scaled(c, scale))!];
    for (let i = 0; i < 3; i++) s[i] += ch(c, i) * n;
    s[3] += n;
  }
  const palette = sums.map((s) => [0, 1, 2].map((i) => Math.trunc(0.5 + s[i] / s[3])) as RGB);
  const d2 = (a: readonly number[], b: readonly number[]) =>
    (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2;
  // For each entry, the others sorted by distance (then index), as Pillow's tables.
  const sortKey = palette.map((p) =>
    palette.map((q, j) => ({ j, d: d2(p, q) })).sort((a, b) => a.d - b.d || a.j - b.j));

  const map = new Map<number, number>();
  for (const c of counts.keys()) {
    const px = [ch(c, 0), ch(c, 1), ch(c, 2)];
    const own = boxOf.get(scaled(c, scale))!;
    const initial = d2(palette[own], px);
    let best = initial, bestMatch = own;
    for (const { j, d } of sortKey[own]) {
      if (d > initial * 4) break;
      const dist = d2(palette[j], px);
      if (dist < best) {
        best = dist;
        bestMatch = j;
      }
    }
    map.set(c, pack(...palette[bestMatch]));
  }
  return map;
}

/** Map pixels onto at most `maxColors` colors, then merge colors closer than
 * `mergeDist` (JPEG noise otherwise splits one flat color into several
 * near-identical palette entries). Pixels are packed 0xRRGGBB ints. */
export function reduceColors(pixels: number[], maxColors: number, mergeDist = 24): number[] {
  const histogram = (ps: number[]) => {
    const m = new Map<number, number>();
    for (const p of ps) m.set(p, (m.get(p) ?? 0) + 1);
    return m;
  };
  let counts = histogram(pixels);
  if (counts.size > maxColors) {
    const map = medianCut(counts, maxColors);
    pixels = pixels.map((p) => map.get(p)!);
    counts = histogram(pixels);
  }
  // Greedy merge: fold each color into a more common one if it is close.
  const remap = new Map<number, number>();
  const kept: RGB[] = [];
  const order = [...counts.keys()].sort((a, b) => counts.get(b)! - counts.get(a)!);
  for (const c of order) {
    const rgb = unpack(c);
    const near = kept.find((k) => Math.hypot(rgb[0] - k[0], rgb[1] - k[1], rgb[2] - k[2]) < mergeDist);
    remap.set(c, near ? pack(...near) : c);
    if (!near) kept.push(rgb);
  }
  return pixels.map((p) => remap.get(p)!);
}

/** Give each detail zone its own colors: the small bright or dark spots that
 * the whole picture's palette has no entry for, like a highlight in an eye.
 * Repeatedly takes the color farthest from every color so far, if at least
 * minSamples pixels are near it (colors are pooled in 32-level bins). Only
 * pixels far from their assigned color then move to the nearest color, so
 * flat areas and the zone's edge don't change. `boxes` are [x0, y0, x1, y1]
 * sample ranges. Updates `pixels`; returns the number of colors added. */
export function addZoneColors(raw: number[], pixels: number[], width: number, boxes: number[][], maxColors: number,
  minSamples: number, mergeDist = 24): number {
  const d2 = (a: readonly number[], b: readonly number[]) =>
    (a[0] - b[0]) * (a[0] - b[0]) + (a[1] - b[1]) * (a[1] - b[1]) + (a[2] - b[2]) * (a[2] - b[2]);
  const near2 = mergeDist * mergeDist;
  const known: RGB[] = [...new Set(pixels)].sort((a, b) => a - b).map(unpack);
  let added = 0;
  for (const [x0, y0, x1, y1] of boxes) {
    const spots: number[] = [];
    for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) spots.push(y * width + x);
    const bins = new Map<number, [number, number, number, number]>();
    for (const k of spots) {
      const [r, g, b] = unpack(raw[k]);
      const key = pack(r >> 3, g >> 3, b >> 3);
      let sums = bins.get(key);
      if (!sums) bins.set(key, sums = [0, 0, 0, 0]);
      sums[0] += r;
      sums[1] += g;
      sums[2] += b;
      sums[3] += 1;
    }
    const pool = [...bins.keys()].sort((a, b) => a - b).map((key) => {
      const s = bins.get(key)!;
      return [Math.floor(s[0] / s[3]), Math.floor(s[1] / s[3]), Math.floor(s[2] / s[3]), s[3]];
    });
    const spotted: { color: RGB; n: number }[] = []; // colors with the samples near them
    for (const c of pool) {
      if (known.some((k) => d2(c, k) < near2)) continue;
      const group = pool.filter((q) => d2(c, q) < near2);
      const n = group.reduce((sum, q) => sum + q[3], 0);
      const mean = (ch: number) => Math.floor(group.reduce((sum, q) => sum + q[ch] * q[3], 0) / n);
      spotted.push({ color: [mean(0), mean(1), mean(2)], n });
    }
    const fresh: RGB[] = [];
    while (fresh.length < maxColors) {
      let best: RGB | null = null, far = near2 - 1;
      for (const { color, n } of spotted) {
        let dist = Infinity;
        for (const k of [...known, ...fresh]) dist = Math.min(dist, d2(color, k));
        if (n >= minSamples && dist > far) {
          best = color;
          far = dist;
        }
      }
      if (!best) break;
      fresh.push(best);
    }
    known.push(...fresh);
    for (const k of spots) {
      const px = unpack(raw[k]);
      let best = pixels[k], near = d2(px, unpack(best));
      if (near < near2) continue; // only badly matched: a highlight lumped in with the dark around it
      for (const c of known) {
        const dist = d2(px, c);
        if (dist < near) {
          best = pack(...c);
          near = dist;
        }
      }
      pixels[k] = best;
    }
    added += fresh.length;
  }
  return added;
}

/** Reduce a picture to a cols x rows grid of palette indices, most common
 * color first. A picture larger than the grid is scaled down to fit inside it
 * keeping its aspect ratio; one that already fits is used 1:1. Leftover
 * rows/columns are filled with the most common border color. When scaling
 * down, each tile takes the majority color of its area. */
export function cropImage(img: RGBImage, x0: number, y0: number, w: number, h: number): RGBImage {
  const out = new Uint8Array(w * h * 3);
  for (let y = 0; y < h; y++) {
    out.set(img.data.subarray(((y0 + y) * img.width + x0) * 3, ((y0 + y) * img.width + x0 + w) * 3), y * w * 3);
  }
  return { width: w, height: h, data: out };
}

/** For canvases, fit="fit" also enlarges a small picture (nearest pixel, so
 * colors stay flat) until it fits, and fit="fill" first crops the picture,
 * centred, to the grid's shape so it covers the whole grid. `zones` are
 * [i0, j0, i1, j1] cell ranges that also get addZoneColors. */
export function loadPattern(img: RGBImage, cols: number, rows: number, maxColors: number, label: string,
  fit: "fit" | "fill" | null = null, zones: number[][] = [], zoneColors = 2, zoneSpot = 1): Pattern {
  let w = img.width, h = img.height;
  let note = `Pattern: ${label} ${w} x ${h} px`;
  let im = img;
  if (fit === "fill" && w * rows !== h * cols) {
    if (w * rows > h * cols) { // wider than the grid
      const cw = Math.max(1, pyRound((h * cols) / rows));
      im = cropImage(im, Math.floor((w - cw) / 2), 0, cw, h);
    } else {
      const ch = Math.max(1, pyRound((w * rows) / cols));
      im = cropImage(im, 0, Math.floor((h - ch) / 2), w, ch);
    }
    w = im.width;
    h = im.height;
    note += ` cropped to ${w} x ${h}`;
  }
  const scale = Math.min(cols / w, rows / h);
  let nw = w, nh = h, sub = 1; // sub = samples per tile side for the majority vote
  if (scale < 1) {
    nw = fit === "fill" ? cols : Math.min(cols, Math.max(1, pyRound(w * scale)));
    nh = fit === "fill" ? rows : Math.min(rows, Math.max(1, pyRound(h * scale)));
    sub = Math.max(1, Math.min(8, Math.floor(Math.min(w / nw, h / nh))));
    im = boxResize(im, nw * sub, nh * sub);
    note += ` scaled to ${nw} x ${nh} (majority color per tile)`;
  } else if (scale > 1 && fit) {
    nw = fit === "fill" ? cols : Math.min(cols, Math.max(1, pyRound(w * scale)));
    nh = fit === "fill" ? rows : Math.min(rows, Math.max(1, pyRound(h * scale)));
    im = nearestResize(im, nw, nh);
    note += ` enlarged to ${nw} x ${nh} (nearest pixel)`;
  }
  const raw: number[] = new Array(im.width * im.height);
  for (let i = 0; i < raw.length; i++) raw[i] = pack(im.data[i * 3], im.data[i * 3 + 1], im.data[i * 3 + 2]);
  const pixels = reduceColors(raw, maxColors);
  const iw = nw * sub;
  const ox = Math.floor((cols - nw) / 2), oy = Math.floor((rows - nh) / 2);
  if (zones.length) {
    const boxes = zones.map(([i0, j0, i1, j1]) =>
      [Math.max(0, i0 - ox) * sub, Math.max(0, j0 - oy) * sub, Math.min(nw, i1 - ox) * sub, Math.min(nh, j1 - oy) * sub]);
    const added = addZoneColors(raw, pixels, iw, boxes, zoneColors, zoneSpot * sub * sub);
    if (added) note += `, ${added} color(s) added in detail zones`;
  }
  let cells: number[][] = [];
  for (let j = 0; j < nh; j++) {
    const row: number[] = [];
    for (let i = 0; i < nw; i++) {
      const votes = new Map<number, number>();
      for (let y = j * sub; y < (j + 1) * sub; y++) {
        for (let x = i * sub; x < (i + 1) * sub; x++) {
          const p = pixels[y * iw + x];
          votes.set(p, (votes.get(p) ?? 0) + 1);
        }
      }
      row.push(maxBy(votes.keys(), (c) => votes.get(c)!));
    }
    cells.push(row);
  }
  if (nw !== cols || nh !== rows) {
    const border = [...cells[0], ...cells[cells.length - 1], ...cells.map((r) => r[0]), ...cells.map((r) => r[r.length - 1])];
    const tally = new Map<number, number>();
    for (const c of border) tally.set(c, (tally.get(c) ?? 0) + 1);
    const bg = maxBy(tally.keys(), (c) => tally.get(c)!);
    cells = Array.from({ length: rows }, (_, j) =>
      Array.from({ length: cols }, (_, i) =>
        j - oy >= 0 && j - oy < nh && i - ox >= 0 && i - ox < nw ? cells[j - oy][i - ox] : bg));
    note += `, centred and padded with ${hexcolor(unpack(bg))}`;
  }
  const counts = new Map<number, number>();
  for (const row of cells) for (const c of row) counts.set(c, (counts.get(c) ?? 0) + 1);
  const palette = [...counts.keys()].sort((a, b) => cmp([-counts.get(a)!, a], [-counts.get(b)!, b]));
  const index = new Map(palette.map((c, i) => [c, i]));
  return {
    palette: palette.map(unpack),
    counts: palette.map((c) => counts.get(c)!),
    grid: cells.map((row) => row.map((c) => index.get(c)!)),
    note,
  };
}

/** Guess the pixel-art grid (cols, rows) of an image, e.g. 40 x 50 for art
 * drawn on a 40 x 50 grid and saved at 1856 x 2304 px; null if no clear cell
 * size is found. Works from the spacing between neighbouring color edges, so
 * it copes with AI-generated art whose cells drift. */
export function detectGrid(img: RGBImage): [number, number] | null {
  const w = img.width, h = img.height;
  const k = Math.max(1, Math.max(w, h) / 1000); // analyse at most ~1000 px per side
  const im = k > 1 ? nearestResize(img, pyRound(w / k), pyRound(h / k)) : img;
  const sw = im.width, sh = im.height;
  const lum = (i: number, j: number) => {
    // ImageChops.difference then convert("L") with Pillow's integer weights.
    const d = [0, 1, 2].map((c) => Math.abs(im.data[i * 3 + c] - im.data[j * 3 + c]));
    return (d[0] * 19595 + d[1] * 38470 + d[2] * 7471 + 0x8000) >> 16;
  };
  const gaps: number[] = [];
  const PB = 22; // the profile is a Pillow box resize to one row/column
  for (const axis of [0, 1]) {
    const n = axis === 0 ? sw - 1 : sh - 1;
    const m = axis === 0 ? sh : sw;
    const coef = Math.floor((1 / m) * (1 << PB) + 0.5);
    const prof: number[] = [];
    for (let a = 0; a < n; a++) {
      let s = 0;
      for (let b = 0; b < m; b++) {
        const v = axis === 0 ? lum(b * sw + a + 1, b * sw + a) : lum((a + 1) * sw + b, a * sw + b);
        if (v > 16) s += 255;
      }
      prof.push(Math.min(255, Math.floor((s * coef + (1 << (PB - 1))) / (1 << PB))));
    }
    const top = Math.max(0, ...prof);
    if (top === 0) continue;
    let peaks: number[] = [];
    for (let i = 0; i < n; i++) {
      if (prof[i] > 0.1 * top && prof[i] === Math.max(...prof.slice(Math.max(0, i - 2), i + 3))) peaks.push(i);
    }
    peaks = peaks.filter((p, i) => i === 0 || p - peaks[i - 1] > 2);
    for (let i = 1; i < peaks.length; i++) gaps.push(peaks[i] - peaks[i - 1]);
  }
  if (gaps.length < 6) return null;

  const score = (gc: number) => { // gaps that are whole multiples of gc, single cells counting most
    let s = 0;
    for (const gap of gaps) {
      const mult = pyRound(gap / gc);
      if (mult >= 1 && Math.abs(gap - mult * gc) <= Math.max(1.5, 0.04 * gap)) s += 1 / mult;
    }
    return s;
  };
  const candidates = [...new Set(gaps)].sort((a, b) => a - b);
  const best = maxBy(candidates, (gc) => [score(gc), gc]);
  const singles = gaps.filter((gap) => Math.abs(gap - best) <= Math.max(1.5, 0.04 * best));
  const cell = (singles.reduce((a, b) => a + b, 0) / singles.length) * k;
  if (cell < 1.5) return [w, h];
  return [Math.max(1, pyRound(w / cell)), Math.max(1, pyRound(h / cell))];
}

/** One pixel per tile in the final palette, as RGBA (for PNG export). */
export function scaledImage(pattern: Pattern): RGBAImage {
  const rows = pattern.grid.length, cols = pattern.grid[0].length;
  const data = new Uint8Array(cols * rows * 4);
  pattern.grid.forEach((row, j) => row.forEach((idx, i) => {
    const [r, g, b] = pattern.palette[idx];
    data.set([r, g, b, 255], (j * cols + i) * 4);
  }));
  return { width: cols, height: rows, data, source: pattern.source };
}
