// Max-rects sheet packing with rotation (spec.md §7).

import { Piece } from "./geometry";
import { cmp } from "./py";

type Free = [number, number, number, number]; // x0, y0, x1, y1

/** Free-space tracker for one sheet. Every part is padded by `gap` on its
 * right and bottom; the free area starts `gap` in from the top-left, so parts
 * end up `gap` apart and `gap` from every edge. */
class Sheet {
  free: Free[];
  items: Piece[] = [];

  constructor(public w: number, public h: number, public gap: number) {
    this.free = [[gap, gap, w - gap, h - gap]];
  }

  /** Best-short-side-fit position for a w x h part, or null. */
  bestSpot(w: number, h: number): { score: [number, number]; x: number; y: number } | null {
    w += this.gap;
    h += this.gap;
    let best: { score: [number, number]; x: number; y: number } | null = null;
    for (const [x0, y0, x1, y1] of this.free) {
      const fw = x1 - x0, fh = y1 - y0;
      if (w <= fw + 1e-9 && h <= fh + 1e-9) {
        const score: [number, number] = [Math.min(fw - w, fh - h), Math.max(fw - w, fh - h)];
        if (best === null || cmp(score, best.score) < 0) best = { score, x: x0, y: y0 };
      }
    }
    return best;
  }

  place(pc: Piece, x: number, y: number): void {
    const [w, h] = pc.size;
    this.items.push(pc.moved(x, y));
    const px1 = x + w + this.gap, py1 = y + h + this.gap;
    const out: Free[] = [];
    for (const f of this.free) {
      const [x0, y0, x1, y1] = f;
      if (x >= x1 || px1 <= x0 || y >= y1 || py1 <= y0) {
        out.push(f);
        continue;
      }
      if (x > x0) out.push([x0, y0, x, y1]);
      if (px1 < x1) out.push([px1, y0, x1, y1]);
      if (y > y0) out.push([x0, y0, x1, y]);
      if (py1 < y1) out.push([x0, py1, x1, y1]);
    }
    const same = (a: Free, b: Free) => a[0] === b[0] && a[1] === b[1] && a[2] === b[2] && a[3] === b[3];
    this.free = out.filter((a) => !out.some((b) =>
      !same(a, b) && b[0] <= a[0] && b[1] <= a[1] && a[2] <= b[2] && a[3] <= b[3]));
  }
}

export type PackedSheet = { w: number; h: number; items: Piece[] };

/** Pack pieces onto as few sheets as possible, trying each part both upright
 * and rotated 90 degrees. A part too big for the sheet gets a sheet of its own. */
export function pack(pieces: Piece[], sw: number, sh: number, gap: number): PackedSheet[] {
  const parts = pieces.map((pc) => pc.normalized()).sort((a, b) => {
    const ka = [-Math.max(...a.size), -a.size[0] * a.size[1]];
    const kb = [-Math.max(...b.size), -b.size[0] * b.size[1]];
    return cmp(ka, kb);
  });
  const sheets: Sheet[] = [];
  for (let pc of parts) {
    const rot = pc.rotated();
    let best: { key: number[]; sheet: Sheet; cand: Piece; x: number; y: number } | null = null;
    for (const sheet of sheets) {
      for (const [cand, penalty] of [[pc, 0], [rot, 1e-6]] as [Piece, number][]) { // prefer upright on ties
        const spot = sheet.bestSpot(...cand.size);
        if (!spot) continue;
        const key = [...spot.score, penalty];
        if (best === null || cmp(key, best.key) < 0) best = { key, sheet, cand, x: spot.x, y: spot.y };
      }
      if (best) break; // fill earlier sheets first
    }
    if (best) {
      best.sheet.place(best.cand, best.x, best.y);
      continue;
    }
    let [w, h] = pc.size;
    if (w + 2 * gap > sw || h + 2 * gap > sh) {
      const [rw, rh] = rot.size;
      if (rw + 2 * gap <= sw && rh + 2 * gap <= sh) {
        pc = rot;
        w = rw;
        h = rh;
      }
    }
    const sheet = new Sheet(Math.max(sw, w + 2 * gap), Math.max(sh, h + 2 * gap), gap);
    sheet.place(pc, gap, gap);
    sheets.push(sheet);
  }
  return sheets.map((s) => ({ w: s.w, h: s.h, items: s.items }));
}
