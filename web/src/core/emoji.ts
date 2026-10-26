// Bundled Twemoji set: zip access, search and image references (spec.md §9.4).

import { unzipSync } from "fflate";
import { cmp } from "./py";

export const EMOJI_PREFIX = "emoji:";
export const EMOJI_CREDIT = "Emoji graphics: Twemoji, CC BY 4.0 (Twitter, Inc and other contributors)";
// Shown in the picker before anything is typed.
export const EMOJI_STARTERS = ("1f600 1f60e 1f60d 1f4a9 1f47e 1f916 1f47b 1f383 2764 1f499 1f49a 1f49b 2b50 1f31f " +
  "1f308 2600 1f319 2601 26a1 2744 1f525 1f30a 1f33b 1f337 1f339 1f335 1f332 1f344 " +
  "1f34e 1f353 1f349 1f34c 1f352 1f951 1f355 1f354 1f369 1f36a 1f382 1f366 2615 1f431 " +
  "1f436 1f98a 1f43b 1f43c 1f438 1f427 1f989 1f41d 1f98b 1f422 1f419 1f988 1f433 1f984 " +
  "1f409 1f680 1f697 2708 26bd 1f3c0 1f3b8 1f3ae 1f381 1f388 1f451 1f48e 1f3e0").split(" ");

export function isEmoji(image: string): boolean {
  return image.startsWith(EMOJI_PREFIX);
}

/** The code in an image setting like "emoji:1f33b sunflower". */
export function emojiCode(image: string): string {
  return image.slice(EMOJI_PREFIX.length).split(" ")[0];
}

export function emojiChar(code: string): string {
  return String.fromCodePoint(...code.split("-").map((h) => parseInt(h, 16)));
}

/** The unpacked bundle: names plus PNG bytes by code. */
export class EmojiSet {
  readonly index: Record<string, string>;
  private files: Record<string, Uint8Array>;

  constructor(zipBytes: Uint8Array) {
    this.files = unzipSync(zipBytes);
    this.index = JSON.parse(new TextDecoder().decode(this.files["index.json"]));
  }

  png(code: string): Uint8Array {
    const data = this.files[`72x72/${code}.png`];
    if (!data) throw new Error(`No bundled emoji ${code}`);
    return data;
  }

  name(code: string): string {
    return this.index[code] ?? "";
  }

  imageValue(code: string): string {
    return `${EMOJI_PREFIX}${code} ${this.name(code)}`.trim();
  }

  /** Codes matching `query`: pasted emoji characters, or words that must all
   * appear in the name (exact and whole-word matches first). */
  search(query: string, limit = 80): string[] {
    const index = this.index;
    const q = query.trim().toLowerCase();
    if (!q) return EMOJI_STARTERS.filter((c) => c in index).slice(0, limit);
    if ([...q].some((ch) => ch.codePointAt(0)! > 0x2000)) {
      const code = [...q.replace(/ /g, "")].map((ch) => ch.codePointAt(0)!.toString(16)).join("-");
      for (const cand of [code, code.split("-").filter((p) => p !== "fe0f").join("-")]) {
        if (cand in index) return [cand];
      }
      return [];
    }
    const words = q.split(/\s+/);
    const hits = Object.keys(index).filter((c) => words.every((w) => index[c].includes(w)));
    const rank = (c: string) => {
      const name = index[c];
      const whole = words.every((w) => name.replace(/\+/g, " ").split(/\s+/).includes(w));
      return [name !== q, !whole, name.includes("skin tone"), !name.startsWith(q), name.length, c] as const;
    };
    const keys = new Map(hits.map((c) => [c, rank(c)]));
    hits.sort((a, b) => cmp(keys.get(a)!, keys.get(b)!));
    return hits.slice(0, limit);
  }
}
