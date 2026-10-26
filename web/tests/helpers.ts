// Test helpers: decode images in Node and build synthetic pixel art.
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import jpeg from "jpeg-js";
import { PNG } from "pngjs";
import { EmojiSet } from "../src/core/emoji";
import type { RGBAImage } from "../src/core/pattern";

export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

export function decode(path: string): RGBAImage {
  const buf = readFileSync(path);
  if (/\.jpe?g$/i.test(path)) {
    const j = jpeg.decode(buf, { useTArray: true, formatAsRGBA: true });
    return { width: j.width, height: j.height, data: j.data };
  }
  return decodePng(buf);
}

export function decodePng(buf: Uint8Array): RGBAImage {
  const p = PNG.sync.read(Buffer.from(buf));
  return { width: p.width, height: p.height, data: new Uint8Array(p.data) };
}

export function writePng(path: string, img: RGBAImage): void {
  const p = new PNG({ width: img.width, height: img.height });
  p.data = Buffer.from(img.data);
  writeFileSync(path, PNG.sync.write(p));
}

let emojiSet: EmojiSet | null = null;
export function emoji(): EmojiSet {
  emojiSet ??= new EmojiSet(readFileSync(resolve(ROOT, "emoji/twemoji-72.zip")));
  return emojiSet;
}

/** A 20 x 25 sunflower in 5 flat colors, each cell `scale` px. */
export function sunflower(scale = 1): RGBAImage {
  const cols = 20, rows = 25;
  const sky = [30, 140, 220], petal = [245, 190, 40], seed = [100, 60, 40], leaf = [70, 180, 80], stem = [40, 150, 60];
  const cell = (i: number, j: number) => {
    const d = (cx: number, cy: number) => Math.hypot(i + 0.5 - cx, j + 0.5 - cy);
    if (d(10, 10) < 3.5) return seed;
    if (d(10, 10) < 6.5) return petal;
    if ((i === 9 || i === 10) && j >= 16 && j <= 23) return stem;
    if (d(6, 19) < 3 || d(14, 19) < 3) return leaf;
    return sky;
  };
  const w = cols * scale, h = rows * scale;
  const data = new Uint8Array(w * h * 4);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) data.set([...cell(Math.floor(x / scale), Math.floor(y / scale)), 255], (y * w + x) * 4);
  }
  return { width: w, height: h, data };
}
