// Parts, rectilinear outlines and kerf offsets (spec.md §5.1–5.4).

export type Pt = [number, number];
export type Rect = [number, number, number, number]; // x0, y0, x1, y1
export type Poly = { pts: Pt[]; closed: boolean };
export type Text = { x: number; y: number; size: number; text: string; angle: number; anchor: "start" | "middle" | "end" };

/** A part: closed/open polylines to cut and engrave, plus engraved text. */
export class Piece {
  cuts: Poly[] = [];
  engraves: Poly[] = [];
  texts: Text[] = [];
  /** Tile area corners [top-left, top-right, bottom-left], for previews. */
  picture: Pt[] | null = null;

  constructor(public name: string) {}

  bbox(): Rect {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const pl of [...this.cuts, ...this.engraves]) {
      for (const [x, y] of pl.pts) {
        if (x < x0) x0 = x;
        if (y < y0) y0 = y;
        if (x > x1) x1 = x;
        if (y > y1) y1 = y;
      }
    }
    return [x0, y0, x1, y1];
  }

  get size(): [number, number] {
    const [x0, y0, x1, y1] = this.bbox();
    return [x1 - x0, y1 - y0];
  }

  private mapped(f: (x: number, y: number) => Pt, dangle = 0): Piece {
    const out = new Piece(this.name);
    out.cuts = this.cuts.map((p) => ({ pts: p.pts.map(([x, y]) => f(x, y)), closed: p.closed }));
    out.engraves = this.engraves.map((p) => ({ pts: p.pts.map(([x, y]) => f(x, y)), closed: p.closed }));
    out.texts = this.texts.map((t) => {
      const [x, y] = f(t.x, t.y);
      return { ...t, x, y, angle: t.angle + dangle };
    });
    out.picture = this.picture ? this.picture.map(([x, y]) => f(x, y)) : null;
    return out;
  }

  moved(dx: number, dy: number): Piece {
    return this.mapped((x, y) => [x + dx, y + dy]);
  }

  normalized(): Piece {
    const [x0, y0] = this.bbox();
    return this.moved(-x0, -y0);
  }

  /** Rotate 90 degrees clockwise (as seen in SVG's y-down space). */
  rotated(): Piece {
    return this.mapped((x, y) => [-y, x], 90).normalized();
  }
}

export function rectPts(x0: number, y0: number, x1: number, y1: number): Pt[] {
  return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
}

const key = (p: Pt) => `${p[0]},${p[1]}`;

/** Outline of (union of `adds`) minus (union of `subs`), all axis-aligned
 * rectangles. Returns closed loops with the material on the left of each edge
 * (outer loops positive area, holes negative). */
export function traceRegion(adds: Rect[], subs: Rect[]): Pt[][] {
  const xs = [...new Set([...adds, ...subs].flatMap((r) => [r[0], r[2]]))].sort((a, b) => a - b);
  const ys = [...new Set([...adds, ...subs].flatMap((r) => [r[1], r[3]]))].sort((a, b) => a - b);
  const nx = xs.length - 1, ny = ys.length - 1;
  const inside = (r: Rect, x: number, y: number) => r[0] < x && x < r[2] && r[1] < y && y < r[3];

  const filled: boolean[][] = [];
  for (let i = 0; i < nx; i++) {
    const cx = (xs[i] + xs[i + 1]) / 2;
    filled.push([]);
    for (let j = 0; j < ny; j++) {
      const cy = (ys[j] + ys[j + 1]) / 2;
      filled[i].push(adds.some((r) => inside(r, cx, cy)) && !subs.some((r) => inside(r, cx, cy)));
    }
  }
  const f = (i: number, j: number) => i >= 0 && i < nx && j >= 0 && j < ny && filled[i][j];

  const out = new Map<string, { from: Pt; to: Pt[] }>();
  const add = (a: Pt, b: Pt) => {
    const k = key(a);
    if (!out.has(k)) out.set(k, { from: a, to: [] });
    out.get(k)!.to.push(b);
  };
  for (let i = 0; i < nx; i++) {
    for (let j = 0; j < ny; j++) {
      if (!filled[i][j]) continue;
      const x0 = xs[i], x1 = xs[i + 1], y0 = ys[j], y1 = ys[j + 1];
      if (!f(i, j - 1)) add([x0, y0], [x1, y0]);
      if (!f(i + 1, j)) add([x1, y0], [x1, y1]);
      if (!f(i, j + 1)) add([x1, y1], [x0, y1]);
      if (!f(i - 1, j)) add([x0, y1], [x0, y0]);
    }
  }
  const take = (a: Pt, b: Pt) => {
    const e = out.get(key(a))!;
    e.to.splice(e.to.findIndex((p) => p[0] === b[0] && p[1] === b[1]), 1);
    if (e.to.length === 0) out.delete(key(a));
  };

  const loops: Pt[][] = [];
  while (out.size) {
    const first = out.values().next().value!;
    const a = first.from;
    const b = first.to[0];
    take(a, b);
    const loop: Pt[] = [a];
    let prev = a, cur = b;
    while (!(cur[0] === a[0] && cur[1] === a[1])) {
      loop.push(cur);
      const dx = cur[0] - prev[0], dy = cur[1] - prev[1];
      // At a vertex shared by two loops, take the leftmost turn so
      // diagonally touching regions stay separate loops.
      const cands = out.get(key(cur))!.to;
      let nxt = cands[0];
      let best = -Infinity;
      for (const n of cands) {
        const c = dx * (n[1] - cur[1]) - dy * (n[0] - cur[0]);
        if (c > best) {
          best = c;
          nxt = n;
        }
      }
      take(cur, nxt);
      prev = cur;
      cur = nxt;
    }
    loops.push(simplify(loop));
  }
  return loops;
}

/** Drop collinear vertices from a closed loop. */
export function simplify(loop: Pt[]): Pt[] {
  const pts = loop.slice();
  let changed = true;
  while (changed && pts.length > 3) {
    changed = false;
    for (let i = 0; i < pts.length; i++) {
      const a = pts[(i - 1 + pts.length) % pts.length], b = pts[i], c = pts[(i + 1) % pts.length];
      if (Math.abs((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])) < 1e-9) {
        pts.splice(i, 1);
        changed = true;
        break;
      }
    }
  }
  return pts;
}

export function area(loop: Pt[]): number {
  let s = 0;
  for (let i = 0; i < loop.length; i++) {
    const a = loop[i], b = loop[(i + 1) % loop.length];
    s += a[0] * b[1] - b[0] * a[1];
  }
  return s / 2;
}

function normal(a: Pt, b: Pt): Pt {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const len = Math.hypot(dx, dy);
  return [dy / len, -dx / len];
}

/** Offset a loop (material on the left) away from the material by d, using
 * mitred corners. Positive d grows outer outlines and shrinks holes. */
export function offsetLoop(loop: Pt[], d: number): Pt[] {
  const n = loop.length;
  return loop.map((p1, i) => {
    const p0 = loop[(i - 1 + n) % n], p2 = loop[(i + 1) % n];
    const n1 = normal(p0, p1), n2 = normal(p1, p2);
    const k = d / (1 + n1[0] * n2[0] + n1[1] * n2[1]);
    return [p1[0] + k * (n1[0] + n2[0]), p1[1] + k * (n1[1] + n2[1])] as Pt;
  });
}

export function outlinePiece(name: string, adds: Rect[], subs: Rect[], kerf: number): Piece {
  const piece = new Piece(name);
  const loops = traceRegion(adds, subs);
  loops.sort((a, b) => area(a) - area(b)); // holes (negative area) first, outer outline last
  piece.cuts = loops.map((lp) => ({ pts: offsetLoop(lp, kerf / 2), closed: true }));
  return piece;
}

/** Split `length` into an odd number of equal segments near `target`. */
export function segments(length: number, target: number, minN: number): [number, number][] {
  const exact = length / Math.max(target, 1e-6);
  let n = Math.max(1, Math.floor(Math.floor(exact) / 2) * 2 + 1); // odd count just above or below exact
  if (n > exact && n - 2 >= 1 && length / (n - 2) - target < target - length / n) n -= 2;
  else if (n < exact && length / n - target > target - length / (n + 2)) n += 2;
  while (n < minN) n += 2;
  const s = length / n;
  return Array.from({ length: n }, (_, i) => [i * s, (i + 1) * s] as [number, number]);
}
