// SVG writers for cut sheets and the printable pattern (spec.md §3, §10).

import type { Piece, Poly } from "./geometry";
import { fixed, g } from "./py";
import type { Pattern } from "./pattern";
import type { Settings } from "./settings";

export const CUT = "#ff0000";
export const ENGRAVE = "#0000ff";
export const STROKE = 0.1;

export function esc(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function d(p: Poly): string {
  const s = "M" + p.pts.map(([x, y]) => `${fixed(x, 3)},${fixed(y, 3)}`).join(" L");
  return p.closed ? s + " Z" : s;
}

/** Group markup for parts: engraving first, then inner cuts, then outlines
 * (pieces keep that order). */
export function piecesMarkup(items: Piece[]): string[] {
  const out: string[] = [];
  for (const pc of items) {
    out.push(`  <g id="${esc(pc.name)}">`);
    for (const p of pc.engraves) {
      out.push(`    <path d="${d(p)}" fill="none" stroke="${ENGRAVE}" stroke-width="${STROKE}"/>`);
    }
    for (const t of pc.texts) {
      const rot = t.angle ? ` transform="rotate(${g(t.angle)} ${fixed(t.x, 3)} ${fixed(t.y, 3)})"` : "";
      out.push(`    <text x="${fixed(t.x, 3)}" y="${fixed(t.y, 3)}" font-family="Arial, Helvetica, sans-serif" ` +
        `font-size="${fixed(t.size, 3)}" text-anchor="${t.anchor}" fill="${ENGRAVE}"${rot}>${esc(t.text)}</text>`);
    }
    for (const p of pc.cuts) {
      out.push(`    <path d="${d(p)}" fill="none" stroke="${CUT}" stroke-width="${STROKE}"/>`);
    }
    out.push("  </g>");
  }
  return out;
}

export function sheetSvg(w: number, h: number, items: Piece[]): string {
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    `<svg xmlns="http://www.w3.org/2000/svg" width="${fixed(w, 3)}mm" height="${fixed(h, 3)}mm" ` +
      `viewBox="0 0 ${fixed(w, 3)} ${fixed(h, 3)}">`,
    ...piecesMarkup(items),
    "</svg>",
  ].join("\n") + "\n";
}

export function hexcolor(rgb: readonly number[]): string {
  return "#" + rgb.map((v) => v.toString(16).padStart(2, "0")).join("");
}

export function textColor(rgb: readonly number[]): string {
  return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2] > 140 ? "#000000" : "#ffffff";
}

/** Python prints floats like 12.0 with a trailing ".0". */
const pf = (x: number) => (Number.isInteger(x) ? x.toFixed(1) : String(x));

/** Printable chart: numbered colored grid plus a legend with tile counts. */
export function patternSvg(s: Settings, pattern: Pattern, needed: number[], credit: string | null): string {
  const { cols, rows } = s;
  const cell = Math.min(8, 180 / cols, 230 / rows);
  const m = 12;
  const gw = cols * cell, gh = rows * cell;
  const legendH = 8 + 7 * pattern.palette.length + (credit ? 8 : 0);
  const w = Math.max(gw + 2 * m, 120), h = gh + 2 * m + legendH;
  const fs = cell * 0.45;
  const out = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    `<svg xmlns="http://www.w3.org/2000/svg" width="${fixed(w, 1)}mm" height="${fixed(h, 1)}mm" viewBox="0 0 ${fixed(w, 1)} ${fixed(h, 1)}" ` +
      'font-family="Arial, Helvetica, sans-serif">',
    `<rect width="${fixed(w, 1)}" height="${fixed(h, 1)}" fill="#ffffff"/>`,
    `<text x="${pf(m)}" y="${fixed(m * 0.55, 1)}" font-size="5">${esc(s.name)}: ${cols} x ${rows} @ ${g(s.pixel)} mm</text>`,
  ];
  pattern.grid.forEach((row, j) =>
    row.forEach((idx, i) => {
      const rgb = pattern.palette[idx];
      const x = m + i * cell, y = m + j * cell;
      out.push(`<rect x="${fixed(x, 2)}" y="${fixed(y, 2)}" width="${fixed(cell, 2)}" height="${fixed(cell, 2)}" fill="${hexcolor(rgb)}"/>`);
      out.push(`<text x="${fixed(x + cell / 2, 2)}" y="${fixed(y + cell / 2 + fs * 0.35, 2)}" font-size="${fixed(fs, 2)}" ` +
        `text-anchor="middle" fill="${textColor(rgb)}">${idx + 1}</text>`);
    }),
  );
  for (let i = 0; i <= cols; i++) {
    const sw = i % 5 === 0 ? 0.4 : 0.1;
    out.push(`<line x1="${fixed(m + i * cell, 2)}" y1="${pf(m)}" x2="${fixed(m + i * cell, 2)}" y2="${fixed(m + gh, 2)}" ` +
      `stroke="#333" stroke-width="${sw}"/>`);
  }
  for (let j = 0; j <= rows; j++) {
    const sw = j % 5 === 0 ? 0.4 : 0.1;
    out.push(`<line x1="${pf(m)}" y1="${fixed(m + j * cell, 2)}" x2="${fixed(m + gw, 2)}" y2="${fixed(m + j * cell, 2)}" ` +
      `stroke="#333" stroke-width="${sw}"/>`);
  }
  const lf = Math.min(2.5, cell * 0.4);
  for (let i = 0; i < cols; i++) {
    out.push(`<text x="${fixed(m + (i + 0.5) * cell, 2)}" y="${fixed(m - 1.2, 2)}" font-size="${fixed(lf, 2)}" ` +
      `text-anchor="middle" fill="#555">${i + 1}</text>`);
  }
  for (let j = 0; j < rows; j++) {
    out.push(`<text x="${fixed(m - 1.2, 2)}" y="${fixed(m + (j + 0.5) * cell + lf * 0.35, 2)}" font-size="${fixed(lf, 2)}" ` +
      `text-anchor="end" fill="#555">${j + 1}</text>`);
  }
  let y = m + gh + 8;
  out.push(`<text x="${pf(m)}" y="${fixed(y, 1)}" font-size="3.5" font-weight="bold">#   color      in picture   with spares</text>`);
  pattern.palette.forEach((rgb, idx) => {
    y += 7;
    out.push(`<rect x="${pf(m)}" y="${fixed(y - 4.5, 1)}" width="5" height="5" fill="${hexcolor(rgb)}" stroke="#333" stroke-width="0.2"/>`);
    out.push(`<text x="${pf(m + 7)}" y="${fixed(y - 0.5, 1)}" font-size="3.5">${idx + 1}   ${hexcolor(rgb)}   ` +
      `${pattern.counts[idx]}   ${needed[idx]}</text>`);
  });
  if (credit) out.push(`<text x="${pf(m)}" y="${fixed(y + 8, 1)}" font-size="2.8" fill="#555">${esc(credit)}</text>`);
  out.push("</svg>");
  return out.join("\n") + "\n";
}
