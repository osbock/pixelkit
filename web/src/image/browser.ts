// Browser image I/O: decode any image file to RGBA, encode RGBA as PNG.
import type { RGBAImage } from "../core/pattern";
import { decodePngExact } from "./png";

export async function decodeImage(bytes: Uint8Array, type = ""): Promise<RGBAImage> {
  const exact = decodePngExact(bytes);
  if (exact) return exact;
  const bitmap = await createImageBitmap(new Blob([bytes as BlobPart], { type }), {
    premultiplyAlpha: "none",
    colorSpaceConversion: "none",
  });
  const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
  const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(bitmap, 0, 0);
  const data = ctx.getImageData(0, 0, bitmap.width, bitmap.height).data;
  bitmap.close();
  return { width: canvas.width, height: canvas.height, data };
}

export async function encodePng(img: RGBAImage): Promise<Uint8Array> {
  const canvas = new OffscreenCanvas(img.width, img.height);
  const ctx = canvas.getContext("2d")!;
  ctx.putImageData(new ImageData(new Uint8ClampedArray(img.data), img.width, img.height), 0, 0);
  const blob = await canvas.convertToBlob({ type: "image/png" });
  return new Uint8Array(await blob.arrayBuffer());
}

export function download(name: string, data: Blob): void {
  const url = URL.createObjectURL(data);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
