// spec.md §14 acceptance checks, run against the TypeScript port.
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { Piece } from "../src/core/geometry";
import { build, generate, scaledImageName } from "../src/core/kit";
import { makeBox, makeKerfTest } from "../src/core/parts";
import { composite, detectGrid, loadPattern, scaledImage, type RGBAImage } from "../src/core/pattern";
import { DEFAULTS, type Settings, validate } from "../src/core/settings";
import { decode, decodePng, emoji, ROOT, sunflower } from "./helpers";

const S = (p: Partial<Settings> = {}): Settings => ({ ...DEFAULTS, ...p });
const WHITE: [number, number, number] = [255, 255, 255];
const rgb = (img: RGBAImage) => composite(img, WHITE);
const widths = (pts: [number, number][], horizontalAt: (y: number) => boolean) => {
  const out = new Set<number>();
  pts.forEach((a, i) => {
    const b = pts[(i + 1) % pts.length];
    if (Math.abs(a[1] - b[1]) < 1e-9 && horizontalAt(a[1])) out.add(Math.round(Math.abs(a[0] - b[0]) * 1000) / 1000);
  });
  return [...out];
};

describe("spec §14 acceptance", () => {
  it("1. default box: 207 x 257 x 10, 3 corner segments, kerf-compensated fingers", () => {
    const [parts, info] = makeBox(S(), null);
    expect(info.notes[0]).toBe(
      "Box: outer 207.0 x 257.0 mm, wall height 10.0 mm (3 corner segment(s), 16/20 base fingers per long/short edge)");
    const seg = 201 / 33;
    const base = parts[0].cuts[0].pts;
    expect(widths(base, (y) => Math.abs(y + 0.075) < 1e-6)).toEqual([Math.round((seg + 0.15) * 1000) / 1000]);
    const front = parts[1].cuts[0].pts;
    const z = 10;
    expect(widths(front, (y) => Math.abs(y - (z - 3 + 0.075)) < 1e-6)).toEqual([Math.round((seg - 0.15) * 1000) / 1000]);
  });

  it("2. kerf test 0.15: slots 5.85 x 2.85, tabs 6.15", () => {
    const pieces = makeKerfTest(S());
    const plate = pieces.find((p) => p.name === "kerf_0.15_plate")!;
    for (const hole of plate.cuts.slice(0, 2)) {
      const xs = hole.pts.map((p) => p[0]), ys = hole.pts.map((p) => p[1]);
      expect(+(Math.max(...xs) - Math.min(...xs)).toFixed(3)).toBe(5.85);
      expect(+(Math.max(...ys) - Math.min(...ys)).toFixed(3)).toBe(2.85);
    }
    const tab = pieces.find((p) => p.name === "kerf_0.15_tab")!;
    expect(widths(tab.cuts[0].pts, (y) => y > 12)).toEqual([6.15]);
  });

  it("3. 20 x 25 on 304 mm: one tray sheet, one tile sheet of 529", () => {
    const b = build(S({ sheet_width: 304, sheet_height: 304 }), null);
    expect(b.sheets.map((s) => s.stem)).toEqual(["pixelart_tray_1", "pixelart_tiles_all_1"]);
    expect(b.notes).toContain("Tiles all: 500 in picture, 529 cut (29 spare) on 1 sheet(s) of 3 mm stock");
  });

  it("4. spares trimmed only when they'd need another sheet", () => {
    const n = (p: Partial<Settings>) =>
      build(S({ ...p, sheet_width: 304, sheet_height: 304, tray_style: "none" }), null).notes.find((x) => x.startsWith("Tiles"));
    expect(n({ cols: 27, rows: 28, spare_percent: 10 })).toBe(
      "Tiles all: 756 in picture, 784 cut (28 spare, trimmed from 10% to stay on 1 sheet(s)) on 1 sheet(s) of 3 mm stock");
    expect(n({ cols: 30, rows: 30 })).toBe("Tiles all: 900 in picture, 953 cut (53 spare) on 2 sheet(s) of 3 mm stock");
  });

  it("5. 10x upscaled pixel art reduces to the exact original counts", () => {
    const a = loadPattern(rgb(sunflower(10)), 20, 25, 8, "image");
    const b = loadPattern(rgb(sunflower(1)), 20, 25, 8, "image");
    expect(a.counts).toEqual(b.counts);
    expect(a.grid).toEqual(b.grid);
  });

  it("6. fit and pad without stretching or enlarging", () => {
    const wide: RGBAImage = { width: 800, height: 600, data: new Uint8Array(800 * 600 * 4).fill(200) };
    expect(loadPattern(rgb(wide), 20, 25, 8, "image").note).toMatch(/scaled to 20 x 15 .*padded/);
    const small: RGBAImage = { width: 16, height: 16, data: new Uint8Array(16 * 16 * 4).fill(255) };
    const p = loadPattern(rgb(small), 20, 25, 8, "image");
    expect(p.note).toBe("Pattern: image 16 x 16 px, centred and padded with #ffffff");
    expect(p.grid.length).toBe(25);
  });

  it("7. sample JPEG detects as 40 x 50 and its background merges to one color", () => {
    const img = rgb(decode(resolve(ROOT, "sample-images/poop-emoji.jpeg")));
    expect(detectGrid(img)).toEqual([40, 50]);
    const p = loadPattern(img, 40, 50, 8, "image");
    const light = p.palette.filter(([r, g, b]) => r > 230 && g > 230 && b > 220);
    expect(light.length).toBe(1);
    expect(p.palette.length).toBeLessThanOrEqual(6);
  });

  it("8. emoji search", () => {
    expect(emoji().search("poo")[0]).toBe("1f4a9");
    expect(emoji().search("🌻")).toEqual(["1f33b"]);
    expect(emoji().search("❤️")).toEqual(["2764"]);
    expect(emoji().search("").length).toBeGreaterThan(60);
  });

  it("9. scaled image round trip and naming", () => {
    const p = loadPattern(rgb(decode(resolve(ROOT, "sample-images/poop-emoji.jpeg"))), 20, 25, 8, "image");
    const back = loadPattern(rgb(scaledImage(p)), 20, 25, 8, "image");
    expect(back.grid).toEqual(p.grid);
    expect(back.palette).toEqual(p.palette);
    expect(scaledImageName(S({ image: "poop-emoji.jpeg" }))).toBe("poop-emoji_scaled.png");
    expect(scaledImageName(S({ image: "poop-emoji_scaled.png" }))).toBe("poop-emoji_scaled.png");
    expect(scaledImageName(S({ image: "emoji:1f60e smiling face with sunglasses" }))).toBe("smiling-face-with-sunglasses_scaled.png");
  });

  it("10. picture corners follow a rotated base", () => {
    const [parts] = makeBox(S(), null);
    const base: Piece = parts[0];
    const rot = base.rotated();
    const [tl, tr, bl] = rot.picture!;
    // Rotated clockwise: the grid's top edge now runs down the right side.
    expect(tr[0]).toBeCloseTo(tl[0]);
    expect(tr[1] - tl[1]).toBeCloseTo(200);
    expect(tl[0] - bl[0]).toBeCloseTo(250);
  });

  it("11. huge grids are rejected", () => {
    expect(validate(S({ cols: 1856, rows: 2304 }))).toContain("Columns and rows are limited to 300 tiles each.");
  });
});

describe("emoji kits", () => {
  it("builds a kit from an emoji with a background and credit", () => {
    const s = S({ image: "emoji:1f33b sunflower", cols: 24, rows: 24, colors: 6, background: "#1e8cdc" });
    const { files, build: b } = generate(s, decodePng(emoji().png("1f33b")));
    expect(b.pattern!.palette[0]).toEqual([30, 140, 220]);
    expect(b.notes).toContain("Emoji graphics: Twemoji, CC BY 4.0 (Twitter, Inc and other contributors)");
    const pattern = files.find((f) => f.name === "pixelart_pattern.svg")!;
    expect("text" in pattern && pattern.text).toContain("Twemoji, CC BY 4.0");
    expect(files.map((f) => f.name)).toContain("sunflower_scaled.png");
  });
});

describe("tile tabs", () => {
  it("leave a tab_width bridge at mid-height of every tile on each vertical cut", () => {
    const s = S({ tile_tabs: true, cols: 3, rows: 2, tray_style: "none", spare_percent: 0 });
    const b = build(s, null);
    const block = b.sheets[0].items[0].normalized();
    const p = s.pixel + s.kerf, bd = s.tile_border;
    const verticals = block.cuts.filter((c) => !c.closed && c.pts[0][0] === c.pts[1][0]);
    const horizontals = block.cuts.filter((c) => !c.closed && c.pts[0][1] === c.pts[1][1]);
    // 3 x 2 tiles: 4 vertical lines (including both edges) x 3 segments each.
    expect(verticals.length).toBe(4 * 3);
    // 1 interior + top + bottom horizontal cut, each unbroken across the block.
    expect(horizontals.length).toBe(3);
    for (const hc of horizontals) expect(Math.abs(hc.pts[0][0] - hc.pts[1][0])).toBeCloseTo(3 * p);
    // Gaps in the path are tab_width + kerf, centred on each tile.
    const x = bd; // left edge line
    const ys = verticals.filter((c) => c.pts[0][0] === x).flatMap((c) => c.pts.map((pt) => pt[1])).sort((a, b) => a - b);
    expect(ys[2] - ys[1]).toBeCloseTo(0.5 + 0.15);
    expect((ys[1] + ys[2]) / 2).toBeCloseTo(bd + 0.5 * p);
    expect(b.notes).toContain("Tile tabs: 2 per tile, 0.5 mm wide, on the vertical cuts; rows come apart and stay attached to the side frame.");
  });

  it("rejects tabs wider than half a tile", () => {
    expect(validate(S({ tile_tabs: true, tab_width: 6 }))).toContain(
      "Tab width must be more than 0 and less than half the pixel size.");
  });
});
