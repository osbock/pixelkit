// Copy the shared emoji bundle (../emoji/twemoji-72.zip) into public/ so the
// web build serves it; the Python and web versions share one copy in git.
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, "../../emoji/twemoji-72.zip");
const dest = resolve(here, "../public/emoji/twemoji-72.zip");
mkdirSync(dirname(dest), { recursive: true });
copyFileSync(src, dest);
console.log("copied", src, "->", dest);
