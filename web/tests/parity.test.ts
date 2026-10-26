// Byte-for-byte parity with pixelkit.py: run the Python CLI and the TypeScript
// port on the same settings and compare every output file. Skipped when
// python3 with Pillow isn't available.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { generate, kerfTest } from "../src/core/kit";
import { DEFAULTS, type Settings } from "../src/core/settings";
import { decode, ROOT, sunflower, writePng } from "./helpers";

function hasPython(): boolean {
  try {
    execFileSync("python3", ["-c", "import PIL"], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

function runPython(s: Partial<Settings>, extra: string[] = []): string {
  const dir = mkdtempSync(join(tmpdir(), "pk-py-"));
  const args = [resolve(ROOT, "pixelkit.py"), "--cli", "--output-dir", dir, ...extra];
  for (const [k, v] of Object.entries(s)) {
    const flag = "--" + k.replace(/_/g, "-");
    if (typeof v === "boolean") args.push(v ? flag : flag.replace("--", "--no-"));
    else args.push(flag, String(v));
  }
  execFileSync("python3", args, { stdio: "pipe" });
  return dir;
}

/** Notes up to the settings dump (which differs: the web has no output_dir). */
const notesBody = (t: string) => t.split("\n\nSettings:\n")[0];

const CASES: [string, Partial<Settings>][] = [
  ["defaults", {}],
  ["304 mm sheet", { sheet_width: 304, sheet_height: 304 }],
  ["layered", { tray_style: "layered", lip: 3, sheet_width: 300, sheet_height: 300 }],
  ["corner fingers + play", { lip: 12, finger_width: 5, kerf: 0.25, joint_play: 0.2, cols: 8, rows: 6 }],
  ["no tray, no border", { tray_style: "none", tile_border: 0, spare_percent: 50, cols: 30, rows: 30 }],
  ["oversize base", { cols: 40, rows: 50, pixel: 6, sheet_width: 304, sheet_height: 304 }],
  ["tile tabs", { tile_tabs: true, cols: 7, rows: 5 }],
  ["looser tiles: tile kerf below tray kerf", { tile_kerf: 0.05, kerf: 0.2, tile_tabs: true }],
  ["old 600 x 400 sheet", { sheet_width: 600, sheet_height: 400 }],
  ["tile tabs, wide, no border", { tile_tabs: true, tab_width: 1.2, tile_border: 0, kerf: 0.2, tray_style: "none" }],
];

describe.skipIf(!hasPython())("parity with pixelkit.py", () => {
  for (const [label, s] of CASES) {
    it(label, () => {
      const dir = runPython(s);
      const { files } = generate({ ...DEFAULTS, ...s }, null);
      expect(files.map((f) => f.name).sort()).toEqual(readdirSync(dir).sort());
      for (const f of files) {
        const py = readFileSync(join(dir, f.name), "utf8");
        if ("text" in f) expect(f.name.endsWith(".txt") ? notesBody(f.text) : f.text).toBe(f.name.endsWith(".txt") ? notesBody(py) : py);
      }
    });
  }

  it("pixel-art picture, per-color sheets, engraved numbers", () => {
    const pic = join(mkdtempSync(join(tmpdir(), "pk-img-")), "sunflower.png");
    writePng(pic, sunflower(10));
    const s: Partial<Settings> = { image: pic, tile_sheets: "per_color", engrave_numbers: true, spare_percent: 20 };
    const dir = runPython(s);
    const { files } = generate({ ...DEFAULTS, ...s }, decode(pic));
    expect(files.map((f) => f.name).sort()).toEqual(readdirSync(dir).sort());
    for (const f of files) {
      if (!("text" in f)) continue;
      const py = readFileSync(join(dir, f.name), "utf8");
      expect(f.name.endsWith(".txt") ? notesBody(f.text) : f.text).toBe(f.name.endsWith(".txt") ? notesBody(py) : py);
    }
  });

  it("kerf test sheet", () => {
    const s: Partial<Settings> = { kerf: 0.2, joint_play: 0.1, finger_width: 7 };
    const dir = runPython(s, ["--kerf-test"]);
    const { file } = kerfTest({ ...DEFAULTS, ...s });
    expect("text" in file && file.text).toBe(readFileSync(join(dir, file.name), "utf8"));
  });
});
