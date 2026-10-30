// Kit settings: same names, defaults and rules as pixelkit.py (spec.md §4).

import { g } from "./py";

export const KIT_TYPES = ["mosaic", "paint_by_number"] as const;
export const PBN_USES = ["engrave", "print"] as const;
// Paint-by-number canvas presets, portrait [width, height] in mm.
export const CANVAS_SIZES: Record<string, [number, number]> = {
  "8x10in": [203.2, 254.0], "11x14in": [279.4, 355.6], "12x12in": [304.8, 304.8],
  "12x16in": [304.8, 406.4], "16x20in": [406.4, 508.0], "18x24in": [457.2, 609.6],
  "20x30cm": [200.0, 300.0], "30x40cm": [300.0, 400.0], "40x50cm": [400.0, 500.0],
};
export const CANVASES = ["off", "custom", ...Object.keys(CANVAS_SIZES)] as const;
export const CANVAS_FITS = ["fit", "fill"] as const;
export const TRAY_STYLES = ["box", "layered", "none"] as const;
export const TILE_SHEETS = ["combined", "per_color"] as const;
export const MAX_GRID = 300; // tiles per side; beyond this the layout gets impractically slow
export const MAX_GRID_PBN = 1500; // paint-by-number cells per side (cells aren't cut pieces)

export function gridLimit(s: Settings): number {
  return s.kit_type === "paint_by_number" ? MAX_GRID_PBN : MAX_GRID;
}

export interface Settings {
  kit_type: (typeof KIT_TYPES)[number]; // mosaic (tray + tiles) | paint_by_number (one numbered board)
  cols: number;
  rows: number;
  pixel: number;            // finished tile size, mm
  tile_thickness: number;
  tray_thickness: number;
  kerf: number;             // width of the laser cut in tray stock, mm
  tile_kerf: number;        // kerf compensation for tiles; lower (even negative) = smaller, looser tiles
  tray_style: (typeof TRAY_STYLES)[number];
  tile_clearance: number;   // total slack between tiles and tray wall
  lip: number;              // how far the wall stands above the tiles
  finger_width: number;     // target finger (tab) length for box joints
  joint_play: number;       // extra gap per finger joint (+ looser, - tighter)
  frame_width: number;      // frame border for the layered style
  engrave_grid: boolean;
  engrave_numbers: boolean; // needs a pattern image
  sheet_width: number;      // laser bed / stock size
  sheet_height: number;
  spacing: number;          // gap between parts and from sheet edge
  tile_border: number;      // waste frame around each tile block (0 = none)
  tile_tabs: boolean;       // leave bridges on the vertical tile cuts
  tab_width: number;        // width of each uncut bridge, mm
  spare_percent: number;    // trimmed if spares alone would need another sheet
  tile_sheets: (typeof TILE_SHEETS)[number];
  image: string;            // image file name, or "emoji:<code> <name>" from the bundled set
  background: string;       // color behind transparent parts of the image
  colors: number;
  pbn_use: (typeof PBN_USES)[number]; // paint by number: engrave (laser layers + cut line) | print
  pbn_min_area: number;     // regions smaller than this (mm^2) are absorbed by their neighbours
  pbn_min_width: number;    // ... and so are regions nowhere this wide (mm): slivers and fringes
  pbn_simplify: number;     // outline simplification tolerance, mm (0 = off)
  pbn_smooth: boolean;      // round off pixel steps in the outlines
  pbn_margin: number;       // board border around the picture, mm
  pbn_line_width: number;   // outline width when printing, mm
  pbn_text_min: number;     // smallest number, mm
  pbn_text_max: number;     // largest number, mm
  pbn_zones: string;        // detail zones: "x0,y0,x1,y1;..." as fractions of the grid
  pbn_zone_area: number;    // min area inside a zone, mm^2
  pbn_zone_width: number;   // min width inside a zone, mm
  pbn_zone_colors: number;  // most colors a zone may add to the palette
  canvas: string;           // paint by number: board size preset, "custom", or "off" (size from the grid)
  canvas_landscape: boolean; // turn a preset canvas sideways
  canvas_width: number;     // custom canvas, mm
  canvas_height: number;
  canvas_fit: (typeof CANVAS_FITS)[number]; // fit: whole picture, padded | fill: cover, cropping overflow
  name: string;             // prefix for output file names
}

export const DEFAULTS: Settings = {
  kit_type: "mosaic",
  cols: 20,
  rows: 25,
  pixel: 10,
  tile_thickness: 3,
  tray_thickness: 3,
  kerf: 0.15,
  tile_kerf: 0.15,
  tray_style: "box",
  tile_clearance: 1,
  lip: 4,
  finger_width: 6,
  joint_play: 0,
  frame_width: 10,
  engrave_grid: true,
  engrave_numbers: false,
  sheet_width: 304,  // about 12 in
  sheet_height: 304,
  spacing: 3,
  tile_border: 5,
  tile_tabs: false,
  tab_width: 0.5,
  spare_percent: 5,
  tile_sheets: "combined",
  image: "",
  background: "#ffffff",
  colors: 8,
  pbn_use: "engrave",
  pbn_min_area: 6,
  pbn_min_width: 1.5,
  pbn_simplify: 0.05,
  pbn_smooth: true,
  pbn_margin: 10,
  pbn_line_width: 0.3,
  pbn_text_min: 2,
  pbn_text_max: 6,
  pbn_zones: "",
  pbn_zone_area: 0.25,
  pbn_zone_width: 0,
  pbn_zone_colors: 2,
  canvas: "off",
  canvas_landscape: false,
  canvas_width: 203.2,
  canvas_height: 254.0,
  canvas_fit: "fit",
  name: "pixelart",
};

export const INT_FIELDS = new Set<keyof Settings>(["cols", "rows", "colors", "pbn_zone_colors"]);

/** Settings that only one kind of kit uses; the UI hides the others. */
export const MOSAIC_ONLY = new Set<keyof Settings>(["tile_thickness", "tray_thickness", "kerf", "tile_kerf",
  "tray_style", "tile_clearance", "lip", "finger_width", "joint_play", "frame_width", "engrave_grid",
  "engrave_numbers", "spacing", "tile_border", "spare_percent", "tile_sheets", "tile_tabs", "tab_width"]);
export const PBN_ONLY = new Set<keyof Settings>(["pbn_use", "pbn_min_area", "pbn_min_width", "pbn_simplify", "pbn_smooth", "pbn_margin",
  "pbn_line_width", "pbn_text_min", "pbn_text_max", "pbn_zones", "pbn_zone_area", "pbn_zone_width", "pbn_zone_colors", "canvas", "canvas_landscape", "canvas_width", "canvas_height",
  "canvas_fit"]);

/** [width, height] of the paint-by-number canvas in mm, or null when the
 * board is sized from the grid. */
export function canvasMm(s: Settings): [number, number] | null {
  if (s.kit_type !== "paint_by_number" || s.canvas === "off") return null;
  if (s.canvas === "custom") return [s.canvas_width, s.canvas_height];
  const [w, h] = CANVAS_SIZES[s.canvas] ?? [0, 0];
  return s.canvas_landscape ? [h, w] : [w, h];
}

/** Settings with columns and rows worked out from the canvas, when one is
 * set: as many cells of the chosen size as fit inside the margin. */
export function effectiveSettings(s: Settings): Settings {
  const c = canvasMm(s);
  if (!c) return s;
  const fit = (len: number) => (s.pixel > 0 ? Math.floor((len - 2 * s.pbn_margin) / s.pixel) : 0);
  return { ...s, cols: fit(c[0]), rows: fit(c[1]) };
}

/** Detail zones from "x0,y0,x1,y1;...", fractions of the grid. Throws if the
 * text isn't a list of boxes inside 0..1. */
export function parseZones(text: string): [number, number, number, number][] {
  const zones: [number, number, number, number][] = [];
  for (const part of text.split(";")) {
    if (!part.trim()) continue;
    const v = part.split(",").map((n) => (n.trim() ? Number(n) : NaN));
    const [x0, y0, x1, y1] = v;
    if (v.length !== 4 || !(x0 >= 0 && x0 < x1 && x1 <= 1 && y0 >= 0 && y0 < y1 && y1 <= 1)) throw new Error(part);
    zones.push([x0, y0, x1, y1]);
  }
  return zones;
}

/** Detail zones as [i0, j0, i1, j1] cell ranges of the effective grid. */
export function zoneCells(s: Settings): [number, number, number, number][] {
  return parseZones(s.pbn_zones).map(([x0, y0, x1, y1]) =>
    [Math.floor(x0 * s.cols), Math.floor(y0 * s.rows), Math.ceil(x1 * s.cols), Math.ceil(y1 * s.rows)]);
}

export const cellsFor = (area: number, p: number) => Math.max(1, Math.ceil(area / (p * p) - 1e-9));
export const depthFor = (width: number, p: number) => Math.max(1, Math.ceil((width / p + 1) / 2 - 1e-9)); // widest square (2d - 1) cells

export function withDefaults(partial: Partial<Settings>): Settings {
  return { ...DEFAULTS, ...partial };
}

/** Parse "#rgb", "#rrggbb" or "rgb(r, g, b)"; null if not a color. */
export function parseColor(text: string): [number, number, number] | null {
  const t = (text || "#ffffff").trim().toLowerCase();
  let m = /^#([0-9a-f]{3})$/.exec(t);
  if (m) return [0, 1, 2].map((i) => parseInt(m![1][i] + m![1][i], 16)) as [number, number, number];
  m = /^#([0-9a-f]{6})$/.exec(t);
  if (m) return [0, 2, 4].map((i) => parseInt(m![1].slice(i, i + 2), 16)) as [number, number, number];
  m = /^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$/.exec(t);
  if (m) {
    const v = [m[1], m[2], m[3]].map(Number);
    if (v.every((n) => n <= 255)) return v as [number, number, number];
  }
  if (t === "white") return [255, 255, 255];
  if (t === "black") return [0, 0, 0];
  return null;
}

/** Every problem with the settings; empty when they are usable. */
export function validate(s: Settings): string[] {
  const problems: string[] = [];
  const c = canvasMm(s);
  if (c) {
    if (s.canvas === "custom" && !(s.canvas_width > 0 && s.canvas_height > 0)) {
      problems.push("Custom canvas width and height must be positive.");
    }
    s = effectiveSettings(s);
    if (s.cols < 1 || s.rows < 1) problems.push("The canvas has no room for a cell inside the margin.");
    else if (s.cols > gridLimit(s) || s.rows > gridLimit(s)) {
      problems.push(`The canvas needs ${s.cols} x ${s.rows} cells of ${g(s.pixel)} mm; the limit is ` +
        `${gridLimit(s)} per side, so use a larger pixel size.`);
    }
  }
  const num = (k: keyof Settings) => s[k] as number;
  for (const k of Object.keys(DEFAULTS) as (keyof Settings)[]) {
    if (typeof DEFAULTS[k] === "number" && !Number.isFinite(num(k))) problems.push(`${k} must be a number.`);
  }
  if (!c && (s.cols < 1 || s.rows < 1)) problems.push("Columns and rows must be at least 1.");
  if (!c && (s.cols > gridLimit(s) || s.rows > gridLimit(s))) {
    problems.push(`Columns and rows are limited to ${gridLimit(s)} ${s.kit_type === "paint_by_number" ? "cells" : "tiles"} each.`);
  }
  for (const k of ["pixel", "tile_thickness", "tray_thickness", "sheet_width", "sheet_height"] as const) {
    if (!(s[k] > 0)) problems.push(`${k} must be positive.`);
  }
  const mosaic = s.kit_type !== "paint_by_number"; // tray and tile rules don't apply to a board
  if (mosaic && (s.kerf < 0 || s.kerf >= s.pixel)) problems.push("Kerf must be between 0 and the pixel size.");
  if (mosaic && (s.tile_kerf < -1 || s.tile_kerf >= s.pixel)) problems.push("Tile kerf must be between -1 mm and the pixel size.");
  if (!KIT_TYPES.includes(s.kit_type)) problems.push(`kit_type must be one of ${KIT_TYPES.join(", ")}.`);
  if (!PBN_USES.includes(s.pbn_use)) problems.push(`pbn_use must be one of ${PBN_USES.join(", ")}.`);
  if (!(CANVASES as readonly string[]).includes(s.canvas)) problems.push(`canvas must be one of ${CANVASES.join(", ")}.`);
  if (!CANVAS_FITS.includes(s.canvas_fit)) problems.push(`canvas_fit must be one of ${CANVAS_FITS.join(", ")}.`);
  if (!TRAY_STYLES.includes(s.tray_style)) problems.push(`tray_style must be one of ${TRAY_STYLES.join(", ")}.`);
  if (!TILE_SHEETS.includes(s.tile_sheets)) problems.push(`tile_sheets must be one of ${TILE_SHEETS.join(", ")}.`);
  if (mosaic && s.tile_tabs && !(s.tab_width > 0 && s.tab_width < s.pixel / 2)) {
    problems.push("Tab width must be more than 0 and less than half the pixel size.");
  }
  if (mosaic && s.tray_style === "box" && s.finger_width < 2 * s.kerf + 1) problems.push("Finger width is too small.");
  if (!parseColor(s.background)) problems.push(`Background '${s.background}' is not a color (use e.g. #1e8cdc).`);
  if (s.kit_type === "paint_by_number") {
    if (!s.image) problems.push("Paint by number needs a picture.");
    if (s.pbn_min_area < 0 || s.pbn_min_width < 0 || s.pbn_simplify < 0) {
      problems.push("Min area, min width and simplify can't be negative.");
    }
    if (s.pbn_zone_area < 0 || s.pbn_zone_width < 0 || s.pbn_zone_colors < 1) {
      problems.push("Zone area and width can't be negative, and zones need at least 1 color.");
    }
    try {
      parseZones(s.pbn_zones);
    } catch {
      problems.push("Detail zones must look like x0,y0,x1,y1;... with fractions from 0 to 1.");
    }
    if (!(s.pbn_text_min > 0 && s.pbn_text_min <= s.pbn_text_max)) {
      problems.push("Number sizes must be positive, with the minimum no larger than the maximum.");
    }
    if (s.pbn_line_width <= 0 || s.pbn_margin < 0) {
      problems.push("Line width must be positive and the board margin not negative.");
    }
  }
  if (s.engrave_numbers && !s.image) problems.push("Engraving color numbers needs a pattern image.");
  return problems;
}
