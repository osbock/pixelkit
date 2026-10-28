// Pixel editing support: edited grids survive the PNG round trip exactly,
// and an edited emoji keeps its Twemoji credit via the pixelkit:source chunk.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { generate, patternFor } from "../src/core/kit";
import { scaledImage, type Pattern } from "../src/core/pattern";
import { DEFAULTS, type Settings } from "../src/core/settings";
import { addPngText, decodePngExact, readPngText, SOURCE_KEY } from "../src/image/png";
import { decodePng, emoji, ROOT, writePng } from "./helpers";

const S = (p: Partial<Settings> = {}): Settings => ({ ...DEFAULTS, ...p });
const sunflower = () => S({ image: "emoji:1f33b sunflower", cols: 20, rows: 20 });

function editedPng(): { pattern: Pattern; edited: Pattern; bytes: Uint8Array } {
  const s = sunflower();
  const pattern = patternFor(s, decodePng(emoji().png("1f33b")));
  const grid = pattern.grid.map((r) => r.slice());
  for (let i = 0; i < 5; i++) grid[0][i] = 1; // paint a stroke in color 2
  const edited = { ...pattern, grid };
  const path = join(mkdtempSync(join(tmpdir(), "pk-ed-")), "e.png");
  writePng(path, scaledImage(edited));
  const bytes = addPngText(readFileSync(path), SOURCE_KEY, edited.source!);
  return { pattern, edited, bytes };
}

describe("pixel editor support", () => {
  it("round-trips an edited grid exactly through the scaled PNG", () => {
    const { edited, bytes } = editedPng();
    const img = decodePngExact(bytes)!;
    const back = patternFor(S({ image: "sunflower_scaled.png", cols: 20, rows: 20 }), img);
    expect(back.grid).toEqual(edited.grid);
    expect(back.palette).toEqual(edited.palette);
  });

  it("keeps the Twemoji credit when the edited PNG becomes the image", () => {
    const { bytes } = editedPng();
    const img = decodePngExact(bytes)!;
    expect(img.source).toBe("emoji:1f33b sunflower");
    const { files, build: b } = generate(S({ image: "sunflower_scaled.png", cols: 20, rows: 20 }), img);
    expect(b.notes).toContain("Emoji graphics: Twemoji, CC BY 4.0 (Twitter, Inc and other contributors)");
    const scaled = files.find((f) => f.name === "sunflower_scaled.png");
    expect(scaled && "image" in scaled && scaled.image.source).toBe("emoji:1f33b sunflower");
  });

  it("gives no credit to an ordinary picture", () => {
    const img = decodePng(emoji().png("1f33b")); // no pixelkit:source chunk
    const { build: b } = generate(S({ image: "plain.png", cols: 20, rows: 20 }), img);
    expect(b.notes.some((n) => n.includes("Twemoji"))).toBe(false);
  });

  it("writes and reads tEXt chunks", () => {
    const path = join(mkdtempSync(join(tmpdir(), "pk-txt-")), "t.png");
    writePng(path, scaledImage(editedPng().edited));
    const tagged = addPngText(readFileSync(path), "pixelkit:source", "emoji:1f600 grinning face");
    expect(readPngText(tagged)).toEqual({ "pixelkit:source": "emoji:1f600 grinning face" });
    expect(decodePngExact(tagged)).not.toBeNull(); // CRC and layout stay valid for decoders
    expect(decodePng(tagged).width).toBe(20);
  });
});

let python = true;
try { execFileSync("python3", ["-c", "import PIL"], { stdio: "ignore" }); } catch { python = false; }

describe.skipIf(!python)("pixel editor support across versions", () => {
  it("reads the source chunk from a scaled PNG written by pixelkit.py", () => {
    const dir = mkdtempSync(join(tmpdir(), "pk-pysrc-"));
    execFileSync("python3", [join(ROOT, "pixelkit.py"), "--cli", "--emoji", "🌻", "--output-dir", dir], { stdio: "ignore" });
    const img = decodePngExact(readFileSync(join(dir, "sunflower_scaled.png")))!;
    expect(img.source).toBe("emoji:1f33b sunflower");
  });

  it("Python reads the source chunk the web version writes", () => {
    const path = join(mkdtempSync(join(tmpdir(), "pk-websrc-")), "sunflower_scaled.png");
    writeFileSync(path, editedPng().bytes);
    const out = execFileSync("python3", ["-c",
      `import sys; sys.path.insert(0, ${JSON.stringify(ROOT)}); import pixelkit as pk; print(pk.image_source(${JSON.stringify(path)}))`]);
    expect(out.toString().trim()).toBe("emoji:1f33b sunflower");
  });
});
