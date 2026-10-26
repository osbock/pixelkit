// The in-browser PNG decoder must match pngjs exactly (it keeps emoji edge
// pixels exact, which a premultiplied canvas would round).
import { describe, expect, it } from "vitest";
import { decodePngExact } from "../src/image/png";
import { decodePng, emoji, sunflower, writePng } from "./helpers";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

describe("PNG decoder", () => {
  it("decodes every bundled emoji identically to pngjs", () => {
    const set = emoji();
    let n = 0;
    for (const code of Object.keys(set.index)) {
      const bytes = set.png(code);
      const mine = decodePngExact(bytes);
      expect(mine, code).not.toBeNull();
      const ref = decodePng(bytes);
      expect(mine!.width).toBe(ref.width);
      expect(Buffer.compare(Buffer.from(mine!.data), Buffer.from(ref.data)), code).toBe(0);
      n++;
    }
    expect(n).toBe(4009);
  });

  it("decodes an RGBA PNG written by pngjs", () => {
    const path = join(mkdtempSync(join(tmpdir(), "pk-png-")), "s.png");
    const img = sunflower(3);
    writePng(path, img);
    const mine = decodePngExact(readFileSync(path))!;
    expect(Buffer.from(mine.data).equals(Buffer.from(img.data))).toBe(true);
  });

  it("rejects non-PNG data", () => {
    expect(decodePngExact(new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9]))).toBeNull();
  });
});
