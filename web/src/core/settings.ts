// Kit settings: same names, defaults and rules as pixelkit.py (spec.md §4).

export const TRAY_STYLES = ["box", "layered", "none"] as const;
export const TILE_SHEETS = ["combined", "per_color"] as const;
export const MAX_GRID = 300; // tiles per side; beyond this the layout gets impractically slow

export interface Settings {
  cols: number;
  rows: number;
  pixel: number;            // finished tile size, mm
  tile_thickness: number;
  tray_thickness: number;
  kerf: number;             // width of the laser cut in tray stock, mm
  tile_kerf: number;        // kerf compensation for tiles; lower = smaller, looser tiles
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
  name: string;             // prefix for output file names
}

export const DEFAULTS: Settings = {
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
  name: "pixelart",
};

export const INT_FIELDS = new Set<keyof Settings>(["cols", "rows", "colors"]);

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
  const num = (k: keyof Settings) => s[k] as number;
  for (const k of Object.keys(DEFAULTS) as (keyof Settings)[]) {
    if (typeof DEFAULTS[k] === "number" && !Number.isFinite(num(k))) problems.push(`${k} must be a number.`);
  }
  if (s.cols < 1 || s.rows < 1) problems.push("Columns and rows must be at least 1.");
  if (s.cols > MAX_GRID || s.rows > MAX_GRID) problems.push(`Columns and rows are limited to ${MAX_GRID} tiles each.`);
  for (const k of ["pixel", "tile_thickness", "tray_thickness", "sheet_width", "sheet_height"] as const) {
    if (!(s[k] > 0)) problems.push(`${k} must be positive.`);
  }
  if (s.kerf < 0 || s.kerf >= s.pixel) problems.push("Kerf must be between 0 and the pixel size.");
  if (s.tile_kerf < 0 || s.tile_kerf >= s.pixel) problems.push("Tile kerf must be between 0 and the pixel size.");
  if (!TRAY_STYLES.includes(s.tray_style)) problems.push(`tray_style must be one of ${TRAY_STYLES.join(", ")}.`);
  if (!TILE_SHEETS.includes(s.tile_sheets)) problems.push(`tile_sheets must be one of ${TILE_SHEETS.join(", ")}.`);
  if (s.tile_tabs && !(s.tab_width > 0 && s.tab_width < s.pixel / 2)) {
    problems.push("Tab width must be more than 0 and less than half the pixel size.");
  }
  if (s.tray_style === "box" && s.finger_width < 2 * s.kerf + 1) problems.push("Finger width is too small.");
  if (!parseColor(s.background)) problems.push(`Background '${s.background}' is not a color (use e.g. #1e8cdc).`);
  if (s.engrave_numbers && !s.image) problems.push("Engraving color numbers needs a pattern image.");
  return problems;
}
