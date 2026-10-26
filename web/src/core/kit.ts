// Whole-kit build: validation, trays, tiles, pattern and output files
// (spec.md §6, §10). Platform-neutral: callers decode images and encode PNGs.

import { EMOJI_CREDIT, emojiCode, isEmoji } from "./emoji";
import type { Piece } from "./geometry";
import { pack } from "./pack";
import {
  KERF_TEST_ROOM, kerfTestValues, layerCount, makeBox, makeKerfTest, makeLayered, shareSpares, tileBlocks, tilesToCut, trayInner,
  wantedTiles,
} from "./parts";
import { composite, loadPattern, type Pattern, type RGBAImage, scaledImage } from "./pattern";
import { fixed, g } from "./py";
import { parseColor, type Settings, validate } from "./settings";
import { hexcolor, patternSvg, sheetSvg } from "./svg";

export interface SheetOut { stem: string; w: number; h: number; items: Piece[] }

export interface Build {
  sheets: SheetOut[];
  pattern: Pattern | null;
  needed: number[];
  notes: string[];
}

export class SettingsError extends Error {}

/** The tile grid for s.image, given its decoded pixels. */
export function patternFor(s: Settings, picture: RGBAImage): Pattern {
  const label = isEmoji(s.image) ? `emoji:${emojiCode(s.image)}` : "image";
  return loadPattern(composite(picture, parseColor(s.background)!), s.cols, s.rows, s.colors, label);
}

/** Lay out every sheet. `picture` is the decoded image named by s.image;
 * pass `pattern` instead to reuse one already computed by patternFor. */
export function build(s: Settings, picture: RGBAImage | null, pattern: Pattern | null = null): Build {
  const problems = validate(s);
  if (problems.length) throw new SettingsError(problems.join("\n"));
  if (!s.image) pattern = null;
  else if (!pattern && picture) pattern = patternFor(s, picture);
  const notes: string[] = pattern ? [pattern.note] : [];
  if (isEmoji(s.image) && pattern) notes.push(EMOJI_CREDIT);
  const sheets: SheetOut[] = [];

  if (s.tray_style !== "none") {
    const [parts, info] = (s.tray_style === "box" ? makeBox : makeLayered)(s, pattern);
    notes.push(...info.notes);
    const packed = pack(parts, s.sheet_width, s.sheet_height, s.spacing);
    packed.forEach((p, n) => sheets.push({ stem: `${s.name}_tray_${n + 1}`, w: p.w, h: p.h, items: p.items }));
    const big = packed.find((p) => p.w > s.sheet_width || p.h > s.sheet_height);
    if (big) notes.push(`WARNING: a tray part needs a ${fixed(big.w, 0)} x ${fixed(big.h, 0)} mm sheet, larger than the bed.`);
  }

  const groups: [string, string, number][] = pattern && s.tile_sheets === "per_color"
    ? pattern.palette.map((rgb, i) => [`c${String(i + 1).padStart(2, "0")}`, `C${i + 1} ${hexcolor(rgb)}`, pattern!.counts[i]])
    : [["all", "", s.cols * s.rows]];
  let needed: number[] = [];
  for (const [key, label, count] of groups) {
    const blocks = tileBlocks(s, tilesToCut(s, count), label, key);
    const packed = pack(blocks, s.sheet_width, s.sheet_height, s.spacing);
    const cut = blocks.reduce((sum, b) => sum + tileCount(b, s), 0);
    needed.push(cut);
    packed.forEach((p, n) => sheets.push({ stem: `${s.name}_tiles_${key}_${n + 1}`, w: p.w, h: p.h, items: p.items }));
    let spare = `${cut - count} spare`;
    if (cut < wantedTiles(s, count)) spare += `, trimmed from ${g(s.spare_percent)}% to stay on ${packed.length} sheet(s)`;
    notes.push(`Tiles ${label || "all"}: ${count} in picture, ${cut} cut (${spare}) ` +
      `on ${packed.length} sheet(s) of ${g(s.tile_thickness)} mm stock`);
  }
  if (s.tile_tabs) {
    notes.push(`Tile tabs: 2 per tile, ${g(s.tab_width)} mm wide, on the vertical cuts; rows come apart ` +
      (s.tile_border > 0 ? "and stay attached to the side frame."
        : "(no tile border, so row ends are held only by the surrounding stock)."));
  }
  if (pattern && groups.length === 1) {
    needed = shareSpares(pattern.counts, needed[0]);
    notes.push("Color the tiles after cutting: " +
      pattern.palette.map((rgb, i) => `C${i + 1} ${hexcolor(rgb)} x${needed[i]}`).join(", "));
  }
  return { sheets, pattern, needed, notes };
}

function tileCount(block: Piece, s: Settings): number {
  const p = s.pixel + s.tile_kerf;
  const [w, h] = block.size;
  return Math.round((w - 2 * s.tile_border) / p) * Math.round((h - 2 * s.tile_border) / p);
}

export function summary(s: Settings): string {
  const [win, hin] = trayInner(s);
  const t = s.tray_thickness;
  const lines = [
    `Picture: ${s.cols} x ${s.rows} = ${s.cols * s.rows} tiles, ${g(s.cols * s.pixel)} x ${g(s.rows * s.pixel)} mm`,
    `Tile pitch on sheet: ${fixed(s.pixel + s.tile_kerf, 3)} mm (pixel + tile kerf)`,
    `Tray inside: ${fixed(win, 1)} x ${fixed(hin, 1)} mm`,
  ];
  if (s.tray_style === "box") {
    lines.push(`Box outside: ${fixed(win + 2 * t, 1)} x ${fixed(hin + 2 * t, 1)} x ${fixed(t + s.tile_thickness + s.lip, 1)} mm`);
  } else if (s.tray_style === "layered") {
    lines.push(`Layered outside: ${fixed(win + 2 * s.frame_width, 1)} x ${fixed(hin + 2 * s.frame_width, 1)} mm, ` +
      `${layerCount(s)} frame layer(s)`);
  }
  return lines.join("\n");
}

/** One output file. Text files are strings; the scaled image is RGBA pixels
 * for the caller to encode as PNG. */
export type OutFile = { name: string; text: string } | { name: string; image: RGBAImage };

/** <original name>_scaled.png, or <emoji name>_scaled.png for an emoji. */
export function scaledImageName(s: Settings): string {
  let stem: string;
  if (isEmoji(s.image)) {
    const rest = s.image.slice("emoji:".length);
    const sp = rest.indexOf(" ");
    const name = sp >= 0 ? rest.slice(sp + 1) : "";
    stem = [...(name || emojiCode(s.image))].map((ch) => (/[\p{L}\p{N}]/u.test(ch) ? ch : "-")).join("")
      .replace(/^-+|-+$/g, "");
  } else {
    const base = s.image.split(/[\\/]/).pop() ?? s.image;
    const dot = base.lastIndexOf(".");
    stem = dot > 0 ? base.slice(0, dot) : base;
  }
  if (!stem.endsWith("_scaled")) stem += "_scaled";
  return `${stem}.png`;
}

/** Every output file of a kit, plus the build (for previews and notes). */
export function generate(s: Settings, picture: RGBAImage | null, pattern: Pattern | null = null): { files: OutFile[]; build: Build } {
  const b = build(s, picture, pattern);
  const files: OutFile[] = b.sheets.map((sh) => ({ name: `${sh.stem}.svg`, text: sheetSvg(sh.w, sh.h, sh.items) }));
  if (b.pattern) {
    files.push({ name: `${s.name}_pattern.svg`, text: patternSvg(s, b.pattern, b.needed, isEmoji(s.image) ? EMOJI_CREDIT : null) });
    const name = scaledImageName(s);
    files.push({ name, image: scaledImage(b.pattern) });
    b.notes.push(`Scaled image for editing: ${name} (${s.cols} x ${s.rows} px, one pixel per tile)`);
  }
  const text = [summary(s), "", ...b.notes, "",
    "Red = cut, blue = engrave/score. Units are mm.",
    "Run engrave layers before cut layers."];
  if (s.engrave_numbers) {
    text.push("Base numbers are SVG <text>; convert to paths in your laser software if it does not import text.");
  }
  files.push({ name: `${s.name}_notes.txt`, text: text.join("\n") + "\n\nSettings:\n" + JSON.stringify(s, null, 2) + "\n" });
  return { files, build: b };
}

/** The kerf test sheet: one column per kerf value, plate above its tab piece,
 * in a small file sized to the pieces so it fits on scrap. */
export function kerfTest(s: Settings): { file: OutFile; sheet: SheetOut; notes: string[] } {
  const vals = kerfTestValues(s);
  const pieces = makeKerfTest(s);
  const gap = Math.max(s.spacing, 2);
  const items: Piece[] = [];
  let x = gap, h = 0;
  for (let i = 0; i < pieces.length; i += 2) {
    const plate = pieces[i].normalized(), tab = pieces[i + 1].normalized();
    items.push(plate.moved(x, gap), tab.moved(x, gap + plate.size[1] + gap));
    x += Math.max(plate.size[0], tab.size[0]) + gap;
    h = Math.max(h, gap + plate.size[1] + gap + tab.size[1] + gap);
  }
  const w = x;
  const stem = `${s.name}_kerf_test`;
  const notes = [
    `Kerf test: ${vals.length} plate + tab pairs in ${g(s.tray_thickness)} mm stock, ` +
      `kerf ${fixed(vals[0], 2)} to ${fixed(vals[vals.length - 1], 2)} mm (joint play ${g(s.joint_play)} mm). Slots have ` +
      `${g(KERF_TEST_ROOM)} mm extra room across the stock, so only the finger-width fit is tested.`,
    `Sheet is ${fixed(w, 0)} x ${fixed(h, 0)} mm. Push each tab piece into the plate above it. Use the kerf of the pair ` +
      "that fits snugly by hand as the tray kerf; if none do, adjust Tray kerf and cut the test again.",
  ];
  return { file: { name: `${stem}.svg`, text: sheetSvg(w, h, items) }, sheet: { stem, w, h, items }, notes };
}
