// Paint by number: byte-for-byte parity with pixelkit.py, plus structural
// checks on regions, outlines and numbers.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { build, generate } from "../src/core/kit";
import type { RGBAImage } from "../src/core/pattern";
import {
  Board, labelRegions, mergeSmallRegions, numberSpots, outlineChains, PBN_LINE, PBN_NUMBER,
} from "../src/core/pbn";
import { canvasMm, DEFAULTS, effectiveSettings, type Settings, validate } from "../src/core/settings";
import { decode, decodePng, emoji, ROOT, sunflower, writePng } from "./helpers";

const S = (p: Partial<Settings> = {}): Settings => ({ ...DEFAULTS, kit_type: "paint_by_number", ...p });

function photoish(): RGBAImage {
  const w = 240, h = 240, data = new Uint8Array(w * h * 4);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      data.set([127 + 127 * Math.sin(x / 15 + y / 21), 127 + 127 * Math.sin(y / 12), 127 + 127 * Math.cos((x + y) / 17), 255],
        (y * w + x) * 4);
    }
  }
  return { width: w, height: h, data };
}

let python = true;
try { execFileSync("python3", ["-c", "import PIL"], { stdio: "ignore" }); } catch { python = false; }

function runPython(s: Partial<Settings>): string {
  const dir = mkdtempSync(join(tmpdir(), "pk-pbn-"));
  const args = [resolve(ROOT, "pixelkit.py"), "--cli", "--output-dir", dir];
  for (const [k, v] of Object.entries(s)) {
    const flag = "--" + k.replace(/_/g, "-");
    if (typeof v === "boolean") args.push(v ? flag : flag.replace("--", "--no-"));
    else args.push(flag, String(v));
  }
  execFileSync("python3", args, { stdio: "pipe" });
  return dir;
}

const notesBody = (t: string) => t.split("\n\nSettings:\n")[0];

/** Picture cases: settings plus the RGBA the web side decodes. File pictures
 * are handed to Python as PNGs of exactly those pixels. */
function cases(): [string, Partial<Settings>, RGBAImage][] {
  const png = (img: RGBAImage) => {
    const path = join(mkdtempSync(join(tmpdir(), "pk-pbnimg-")), "pic.png");
    writePng(path, img);
    return path;
  };
  const jpeg = decode(resolve(ROOT, "sample-images/poop-emoji.jpeg"));
  const photo = photoish();
  return [
    ["emoji, engrave, smooth", { image: "emoji:1f33b sunflower", cols: 24, rows: 24, colors: 6, background: "#1e8cdc" },
      decodePng(emoji().png("1f33b"))],
    ["pixel art, print, steps", { image: png(sunflower(10)), pbn_use: "print", pbn_smooth: false, pbn_min_region: 1,
      pbn_line_width: 0.5 }, sunflower(10)],
    ["sample JPEG 40 x 50", { image: png(jpeg), cols: 40, rows: 50, pixel: 5 }, jpeg],
    ["photo-like, many regions", { image: png(photo), cols: 80, rows: 80, pixel: 3, colors: 10, pbn_min_region: 6 }, photo],
    ["aggressive merging", { image: png(photo), cols: 60, rows: 60, pixel: 4, colors: 12, pbn_min_region: 40,
      pbn_text_min: 3, pbn_text_max: 9, pbn_margin: 0 }, photo],
    ["canvas 8x10in, fit, emoji enlarged", { image: "emoji:1f33b sunflower", canvas: "8x10in", pixel: 2.5,
      background: "#1e8cdc" }, decodePng(emoji().png("1f33b"))],
    ["canvas 12x16in landscape, fill", { image: png(jpeg), canvas: "12x16in", canvas_landscape: true,
      canvas_fit: "fill", pixel: 4 }, jpeg],
    ["canvas custom, fill, shrunk", { image: png(photo), canvas: "custom", canvas_width: 150, canvas_height: 100,
      canvas_fit: "fill", pixel: 2, colors: 9, pbn_min_region: 6 }, photo],
    ["canvas 30x40cm, fit, print", { image: png(jpeg), canvas: "30x40cm", pixel: 3, pbn_use: "print",
      pbn_margin: 15 }, jpeg],
  ];
}

describe.skipIf(!python)("paint by number parity with pixelkit.py", () => {
  for (const [label, s, img] of cases()) {
    it(label, () => {
      const settings = S(s);
      const dir = runPython({ kit_type: "paint_by_number", ...s });
      const { files } = generate(settings, img);
      expect(files.map((f) => f.name).sort()).toEqual(readdirSync(dir).sort());
      for (const f of files) {
        if (!("text" in f)) continue;
        const py = readFileSync(join(dir, f.name), "utf8");
        if (f.name.endsWith(".txt")) expect(notesBody(f.text)).toBe(notesBody(py));
        else expect(f.text, f.name).toBe(py);
      }
    });
  }
});

describe("paint by number structure", () => {
  const sample = () => {
    const img = decode(resolve(ROOT, "sample-images/poop-emoji.jpeg"));
    const b = build(S({ image: "poop.jpeg", cols: 40, rows: 50, pixel: 5 }), img);
    return { b, board: b.sheets[0].items[0] as Board, grid: b.pattern!.grid };
  };

  it("merges every region below the minimum", () => {
    const { grid } = sample();
    expect(Math.min(...labelRegions(grid).sizes)).toBeGreaterThanOrEqual(4);
    const tiny = [[0, 0, 0], [0, 1, 0], [0, 0, 0]];
    expect(mergeSmallRegions(tiny, 2)).toEqual({ grid: [[0, 0, 0], [0, 0, 0], [0, 0, 0]], merged: 1 });
  });

  it("puts exactly one number in each region, inside it", () => {
    const { board, grid } = sample();
    const { labels, sizes } = labelRegions(grid);
    expect(board.texts.length).toBe(sizes.length);
    const spots = numberSpots(grid);
    const hit = new Set(spots.map(([i, j]) => labels[j][i]));
    expect(hit.size).toBe(sizes.length);
    for (const [i, j, , color] of spots) expect(grid[j][i]).toBe(color);
    board.texts.forEach((t, k) => expect(t.text).toBe(String(spots[k][3] + 1)));
  });

  it("draws every boundary edge exactly once", () => {
    const { grid } = sample();
    const rows = grid.length, cols = grid[0].length;
    const count = new Map<string, number>();
    for (const { pts, closed } of outlineChains(grid)) {
      const ring = closed ? [...pts, pts[0]] : pts;
      for (let k = 0; k + 1 < ring.length; k++) {
        const [a, b] = [ring[k], ring[k + 1]];
        const key = [Math.min(a[0], b[0]), Math.min(a[1], b[1]), a[0] === b[0] ? "v" : "h"].join(",");
        count.set(key, (count.get(key) ?? 0) + 1);
      }
    }
    let expected = 2 * cols + 2 * rows;
    for (let j = 0; j < rows; j++) for (let i = 0; i < cols; i++) {
      if (i + 1 < cols && grid[j][i] !== grid[j][i + 1]) expected++;
      if (j + 1 < rows && grid[j][i] !== grid[j + 1][i]) expected++;
    }
    expect(count.size).toBe(expected);
    expect([...count.values()].every((n) => n === 1)).toBe(true);
  });

  it("gives every color closed fill outlines", () => {
    const { board, b } = sample();
    expect(board.fills.length).toBe(b.pattern!.palette.length);
    for (const loops of board.fills) expect(loops.length).toBeGreaterThan(0);
  });

  it("uses separate colors for outlines, numbers and the cut line", () => {
    const { files } = generate(S({ image: "emoji:1f33b sunflower", cols: 20, rows: 20 }), decodePng(emoji().png("1f33b")));
    const svg = files.find((f) => f.name === "pixelart_pbn.svg");
    const text = svg && "text" in svg ? svg.text : "";
    expect(text).toContain(`<g id="outlines" fill="none" stroke="${PBN_LINE}"`);
    expect(text).toContain(`<g id="numbers" fill="${PBN_NUMBER}"`);
    expect(text).toContain('<g id="cut" fill="none" stroke="#ff0000"');
    expect(new Set([PBN_LINE, PBN_NUMBER, "#ff0000"]).size).toBe(3);
  });

  it("makes no tray, tile or kerf files, and needs a picture", () => {
    const { files } = generate(S({ image: "emoji:1f33b sunflower", cols: 20, rows: 20 }), decodePng(emoji().png("1f33b")));
    expect(files.map((f) => f.name).sort()).toEqual(
      ["pixelart_notes.txt", "pixelart_pbn.svg", "pixelart_pbn_key.svg", "pixelart_pbn_preview.svg", "sunflower_scaled.png"]);
    expect(validate(S())).toContain("Paint by number needs a picture.");
  });
});

describe("paint by number canvases", () => {
  const sunflower = () => decodePng(emoji().png("1f33b"));

  it("fits as many cells as the canvas allows and sizes the board to the canvas", () => {
    const s = S({ image: "emoji:1f33b sunflower", canvas: "8x10in", pixel: 3 });
    const e = effectiveSettings(s);
    expect([e.cols, e.rows]).toEqual([61, 78]); // floor((203.2 - 20) / 3), floor((254 - 20) / 3)
    const sheet = build(s, sunflower()).sheets[0];
    expect([sheet.w, sheet.h]).toEqual([203.2, 254]);
    const [[x0, y0]] = sheet.items[0].picture!;
    expect(x0).toBeCloseTo((203.2 - 61 * 3) / 2);
    expect(y0).toBeCloseTo((254 - 78 * 3) / 2);
  });

  it("turns presets sideways and uses custom sizes as given", () => {
    expect(canvasMm(S({ canvas: "11x14in", canvas_landscape: true }))).toEqual([355.6, 279.4]);
    expect(canvasMm(S({ canvas: "custom", canvas_width: 150, canvas_height: 100, canvas_landscape: true }))).toEqual([150, 100]);
    expect(canvasMm(S({ canvas: "off" }))).toBeNull();
    expect(canvasMm(S({ kit_type: "mosaic", canvas: "8x10in" }))).toBeNull(); // mosaics ignore it
  });

  it("enlarges a small picture to fit, and fill covers the whole grid", () => {
    const fit = build(S({ image: "emoji:1f33b sunflower", canvas: "12x12in", pixel: 2 }), sunflower());
    expect(fit.pattern!.note).toContain("enlarged to 142 x 142 (nearest pixel)");
    const fill = build(S({ image: "emoji:1f33b sunflower", canvas: "8x10in", pixel: 3, canvas_fit: "fill" }), sunflower());
    expect(fill.pattern!.note).toContain("cropped to 56 x 72 enlarged to 61 x 78");
    expect(fill.pattern!.note).not.toContain("padded");
  });

  it("rejects canvases needing too many cells", () => {
    expect(validate(S({ image: "x.png", canvas: "18x24in", pixel: 0.5 }))).toEqual(
      ["The canvas needs 874 x 1179 cells of 0.5 mm; the limit is 300 per side, so use a larger pixel size."]);
  });
});
