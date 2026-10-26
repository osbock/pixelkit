// Minimal PNG decoder (1/2/4/8/16-bit gray, RGB, palette, gray+alpha, RGBA;
// plain or Adam7-interlaced). Browsers decode through a premultiplied canvas,
// which rounds semi-transparent pixels; decoding here keeps emoji edges exact,
// so results match pixelkit.py. Returns null for anything unsupported.
import { unzlibSync } from "fflate";
import type { RGBAImage } from "../core/pattern";

const ADAM7: [number, number, number, number][] = [ // x0, y0, dx, dy
  [0, 0, 8, 8], [4, 0, 8, 8], [0, 4, 4, 8], [2, 0, 4, 4], [0, 2, 2, 4], [1, 0, 2, 2], [0, 1, 1, 2],
];

export function decodePngExact(bytes: Uint8Array): RGBAImage | null {
  const sig = [137, 80, 78, 71, 13, 10, 26, 10];
  if (bytes.length < 8 || sig.some((b, i) => bytes[i] !== b)) return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let pos = 8, width = 0, height = 0, depth = 0, type = 0, interlace = 0;
  let palette: Uint8Array | null = null, trns: Uint8Array | null = null;
  const idat: Uint8Array[] = [];
  while (pos + 8 <= bytes.length) {
    const len = view.getUint32(pos);
    const name = String.fromCharCode(...bytes.subarray(pos + 4, pos + 8));
    const data = bytes.subarray(pos + 8, pos + 8 + len);
    if (name === "IHDR") {
      width = view.getUint32(pos + 8);
      height = view.getUint32(pos + 12);
      depth = data[8];
      type = data[9];
      interlace = data[12];
    } else if (name === "PLTE") palette = data;
    else if (name === "tRNS") trns = data;
    else if (name === "IDAT") idat.push(data);
    else if (name === "IEND") break;
    pos += 12 + len;
  }
  const channels = ({ 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 } as Record<number, number>)[type];
  if (!channels || interlace > 1 || !(depth === 8 || depth === 16 || ((type === 0 || type === 3) && depth < 8))) return null;
  if (type === 3 && !palette) return null;

  const joined = new Uint8Array(idat.reduce((n, d) => n + d.length, 0));
  let o = 0;
  for (const d of idat) {
    joined.set(d, o);
    o += d.length;
  }
  const raw = unzlibSync(joined);
  const bitsPerPixel = channels * depth;
  const bpp = Math.max(1, bitsPerPixel >> 3); // bytes per pixel for filtering
  const out = new Uint8Array(width * height * 4);
  let offset = 0;

  /** Unfilter one (sub)image of w x h pixels and write it at the given grid. */
  const pass = (w: number, h: number, x0: number, y0: number, dx: number, dy: number) => {
    if (!w || !h) return true;
    const stride = Math.ceil((w * bitsPerPixel) / 8);
    if (offset + h * (stride + 1) > raw.length) return false;
    const pixels = new Uint8Array(stride * h);
    let prev = new Uint8Array(stride);
    for (let y = 0; y < h; y++) {
      const filter = raw[offset + y * (stride + 1)];
      const line = raw.subarray(offset + y * (stride + 1) + 1, offset + (y + 1) * (stride + 1));
      const cur = pixels.subarray(y * stride, (y + 1) * stride);
      for (let x = 0; x < stride; x++) {
        const a = x >= bpp ? cur[x - bpp] : 0, b = prev[x], c = x >= bpp ? prev[x - bpp] : 0;
        let v = line[x];
        if (filter === 1) v += a;
        else if (filter === 2) v += b;
        else if (filter === 3) v += (a + b) >> 1;
        else if (filter === 4) {
          const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
          v += pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
        }
        cur[x] = v & 255;
      }
      prev = cur;
    }
    offset += h * (stride + 1);

    const sample = (row: number, i: number) => { // i-th sample of a row, scaled to 8 bits
      const base = row * stride;
      if (depth === 16) return pixels[base + i * 2];
      if (depth === 8) return pixels[base + i];
      const bit = i * depth, v = (pixels[base + (bit >> 3)] >> (8 - depth - (bit & 7))) & ((1 << depth) - 1);
      return type === 3 ? v : Math.round((v * 255) / ((1 << depth) - 1)); // gray is scaled up, indices aren't
    };
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const q = ((y0 + y * dy) * width + (x0 + x * dx)) * 4;
        if (type === 3) {
          const idx = sample(y, x);
          if (idx * 3 + 2 >= palette!.length) return false;
          out[q] = palette![idx * 3];
          out[q + 1] = palette![idx * 3 + 1];
          out[q + 2] = palette![idx * 3 + 2];
          out[q + 3] = trns && idx < trns.length ? trns[idx] : 255;
        } else if (type === 0 || type === 4) {
          const v = sample(y, x * channels);
          out[q] = out[q + 1] = out[q + 2] = v;
          out[q + 3] = type === 4 ? sample(y, x * channels + 1) : 255;
        } else {
          out[q] = sample(y, x * channels);
          out[q + 1] = sample(y, x * channels + 1);
          out[q + 2] = sample(y, x * channels + 2);
          out[q + 3] = type === 6 ? sample(y, x * channels + 3) : 255;
        }
      }
    }
    return true;
  };

  if (interlace) {
    for (const [x0, y0, dx, dy] of ADAM7) {
      const w = Math.ceil((width - x0) / dx), h = Math.ceil((height - y0) / dy);
      if (!pass(Math.max(0, w), Math.max(0, h), x0, y0, dx, dy)) return null;
    }
  } else if (!pass(width, height, 0, 0, 1, 1)) return null;
  return { width, height, data: out };
}
