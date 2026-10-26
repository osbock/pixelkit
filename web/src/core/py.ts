// Helpers that reproduce Python's rounding and number formatting, so the web
// version writes the same numbers (and SVG bytes) as pixelkit.py.

/** Python's round(x): halves go to the even neighbour. */
export function pyRound(x: number): number {
  const f = Math.floor(x);
  const diff = x - f;
  if (diff > 0.5) return f + 1;
  if (diff < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}

/** Python's round(x, nd) for the small positive values used here. */
export function pyRoundTo(x: number, nd: number): number {
  const m = 10 ** nd;
  return pyRound(x * m) / m;
}

/** Python's f"{x:.{d}f}": exact decimal ties round half to even. */
export function fixed(x: number, d: number): string {
  const s = x.toFixed(d);
  const exact = x.toFixed(Math.min(100, d + 30)); // exact decimal expansion
  const tail = exact.slice(exact.length - 30);
  if (tail === "5" + "0".repeat(29)) {
    // Exact tie: toFixed rounded away from zero; Python rounds to even.
    const lastDigit = Number(s[s.length - 1]);
    if (lastDigit % 2 === 1) {
      const step = 10 ** -d;
      return (x < 0 ? x + step / 2 : x - step / 2).toFixed(d);
    }
  }
  return s;
}

/** Python's f"{x:g}" for ordinary magnitudes. */
export function g(x: number): string {
  if (Number.isInteger(x)) return String(x);
  return String(Number(x.toPrecision(6)));
}

/** Compare tuples element by element, like Python. */
export function cmp(a: readonly (number | string | boolean)[], b: readonly (number | string | boolean)[]): number {
  for (let i = 0; i < Math.min(a.length, b.length); i++) {
    const x = a[i], y = b[i];
    if (x === y) continue;
    return x < y ? -1 : 1;
  }
  return a.length - b.length;
}

/** First element with the largest key (Python's max(seq, key=...)). */
export function maxBy<T>(items: Iterable<T>, key: (t: T) => readonly (number | string | boolean)[] | number): T {
  let best: T | undefined;
  let bestKey: readonly (number | string | boolean)[] | undefined;
  for (const it of items) {
    const raw = key(it);
    const k = typeof raw === "number" ? [raw] : raw;
    if (bestKey === undefined || cmp(k, bestKey) > 0) {
      best = it;
      bestKey = k;
    }
  }
  if (bestKey === undefined) throw new Error("maxBy of empty sequence");
  return best as T;
}
