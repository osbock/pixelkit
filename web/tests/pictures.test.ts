// Pictures that need quantizing (emoji, photos/JPEG) must give exactly the
// palette and grid pixelkit.py gives for the same pixels. JPEG decoders
// differ slightly (jpeg-js here, libjpeg in Pillow and browsers), so the test
// hands both sides the same decoded pixels as a PNG.
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { composite, loadPattern, type RGBAImage } from "../src/core/pattern";
import { parseColor } from "../src/core/settings";
import { decode, decodePng, emoji, ROOT, writePng } from "./helpers";

let python = true;
try { execFileSync("python3", ["-c", "import PIL"], { stdio: "ignore" }); } catch { python = false; }

function pythonPattern(path: string, cols: number, rows: number, colors: number, bg: string) {
  const code = `import json,sys; sys.path.insert(0, ${JSON.stringify(ROOT)}); import pixelkit as pk
p = pk.load_pattern(${JSON.stringify(path)}, ${cols}, ${rows}, ${colors}, ${JSON.stringify(bg)})
print(json.dumps({"palette": p["palette"], "counts": p["counts"], "grid": p["grid"], "note": p["note"]}))`;
  return JSON.parse(execFileSync("python3", ["-c", code]).toString());
}

function expectSame(img: RGBAImage, cols: number, rows: number, colors: number, bg: string) {
  const path = join(mkdtempSync(join(tmpdir(), "pk-pic-")), "pic.png");
  writePng(path, img);
  const ts = loadPattern(composite(img, parseColor(bg)!), cols, rows, colors, "image");
  const py = pythonPattern(path, cols, rows, colors, bg);
  expect(ts.palette).toEqual(py.palette);
  expect(ts.counts).toEqual(py.counts);
  expect(ts.grid).toEqual(py.grid);
  expect(ts.note).toBe(py.note);
}

describe.skipIf(!python)("pictures match pixelkit.py exactly", () => {
  it("emoji 1f33b at 24 x 24, 6 colors, blue background", () => {
    expectSame(decodePng(emoji().png("1f33b")), 24, 24, 6, "#1e8cdc");
  });

  it("emoji 1f60e at 20 x 20, 8 colors, green background", () => {
    expectSame(decodePng(emoji().png("1f60e")), 20, 20, 8, "#00ff1c");
  });

  it("sample JPEG at 40 x 50 and 20 x 25", () => {
    const img = decode(resolve(ROOT, "sample-images/poop-emoji.jpeg"));
    expectSame(img, 40, 50, 8, "#ffffff");
    expectSame(img, 20, 25, 4, "#ffffff");
  });

  it("a photo-like gradient needing heavy quantizing", () => {
    const w = 300, h = 200, data = new Uint8Array(w * h * 4);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        data.set([(x * 255) / w, (y * 255) / h, ((x + y) * 97) % 256, 255], (y * w + x) * 4);
      }
    }
    expectSame({ width: w, height: h, data }, 30, 20, 8, "#ffffff");
  });
});

