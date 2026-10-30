// pixelkit web app: settings form, live preview, emoji picker and downloads.
import { zipSync } from "fflate";
import { EmojiSet, emojiCode, isEmoji } from "../core/emoji";
import { build, generate, kerfTest, type SheetOut, patternFor, scaledImageName, summary } from "../core/kit";
import { composite, detectGrid, type Pattern, type RGBAImage, scaledImage } from "../core/pattern";
import {
  CANVAS_FITS, CANVASES, canvasMm, DEFAULTS, effectiveSettings, gridLimit, INT_FIELDS, KIT_TYPES, MAX_GRID_PBN, MOSAIC_ONLY, parseColor, parseZones, PBN_ONLY, PBN_USES, type Settings, TILE_SHEETS,
  TRAY_STYLES,
} from "../core/settings";
import { decodeImage, download, encodePng } from "../image/browser";
import { EmojiPicker } from "./emojiPicker";
import { PixelEditor } from "./pixelEditor";
import { Preview } from "./preview";
import { loadPicture, savePicture } from "./store";
import "./style.css";

type Key = keyof Settings;
type FieldSpec = [Key, string, { step?: number; min?: number; choices?: readonly string[] }?];

const FORM: [string, FieldSpec[]][] = [
  ["Kit", [["kit_type", "Kit type", { choices: KIT_TYPES }]]],
  ["Grid", [["cols", "Columns", { step: 1, min: 1 }], ["rows", "Rows", { step: 1, min: 1 }], ["pixel", "Pixel size (mm)", { step: 0.5 }]]],
  ["Material", [["tile_thickness", "Tile thickness (mm)", { step: 0.1 }], ["tray_thickness", "Tray thickness (mm)", { step: 0.1 }],
    ["kerf", "Tray kerf (mm)", { step: 0.01, min: 0 }], ["tile_kerf", "Tile kerf (mm)", { step: 0.01, min: -1 }]]],
  ["Tray", [["tray_style", "Style", { choices: TRAY_STYLES }], ["tile_clearance", "Tile clearance (mm)", { step: 0.1 }],
    ["lip", "Wall above tiles (mm)", { step: 0.5 }], ["finger_width", "Finger width (mm)", { step: 0.5 }],
    ["joint_play", "Joint play (mm)", { step: 0.05 }], ["frame_width", "Frame width (layered, mm)", { step: 1 }],
    ["engrave_grid", "Engrave grid on base"], ["engrave_numbers", "Engrave color numbers"]]],
  ["Sheets", [["sheet_width", "Sheet width (mm)", { step: 1 }], ["sheet_height", "Sheet height (mm)", { step: 1 }],
    ["spacing", "Part spacing (mm)", { step: 0.5 }], ["tile_border", "Tile sheet border (mm)", { step: 0.5, min: 0 }],
    ["spare_percent", "Spare tiles (%)", { step: 1, min: 0 }], ["tile_sheets", "Tile sheets", { choices: TILE_SHEETS }],
    ["tile_tabs", "Tabs between tiles"], ["tab_width", "Tab width (mm)", { step: 0.1, min: 0 }]]],
  ["Pattern (optional)", [["image", "Image"], ["background", "Background"], ["colors", "Max colors", { step: 1, min: 1 }]]],
  ["Paint by number", [["canvas", "Canvas", { choices: CANVASES }], ["canvas_landscape", "Landscape canvas"],
    ["canvas_width", "Custom width (mm)", { step: 1 }], ["canvas_height", "Custom height (mm)", { step: 1 }],
    ["canvas_fit", "Picture fit", { choices: CANVAS_FITS }],
    ["pbn_use", "Board use", { choices: PBN_USES }],
    ["pbn_min_area", "Min area (mm²)", { step: 1, min: 0 }], ["pbn_min_width", "Min width (mm)", { step: 0.25, min: 0 }],
    ["pbn_simplify", "Simplify (mm)", { step: 0.01, min: 0 }], ["pbn_smooth", "Smooth outlines"],
    ["pbn_margin", "Board margin (mm)", { step: 1, min: 0 }], ["pbn_line_width", "Print line width (mm)", { step: 0.05 }],
    ["pbn_text_min", "Min number (mm)", { step: 0.5 }], ["pbn_text_max", "Max number (mm)", { step: 0.5 }],
    ["pbn_zones", "Detail zones"], ["pbn_zone_area", "Zone min area (mm²)", { step: 0.05, min: 0 }],
    ["pbn_zone_width", "Zone min width (mm)", { step: 0.25, min: 0 }], ["pbn_zone_colors", "Zone extra colors", { step: 1, min: 1 }]]],
  ["Output", [["name", "Name prefix"]]],
];

const SETTINGS_KEY = "pixelkit-settings";
const CHOICE_LABELS: Record<string, string> = {
  mosaic: "Mosaic tiles", paint_by_number: "Paint by number", off: "Off (grid size)", custom: "Custom",
  "8x10in": "8 × 10 in", "11x14in": "11 × 14 in", "12x12in": "12 × 12 in", "12x16in": "12 × 16 in",
  "16x20in": "16 × 20 in", "18x24in": "18 × 24 in", "20x30cm": "20 × 30 cm", "30x40cm": "30 × 40 cm",
  "40x50cm": "40 × 50 cm", fit: "Fit (whole picture)", fill: "Fill (crop to cover)",
};
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

// ---------------------------------------------------------------- state

let settings: Settings = loadSettings();
let picture: { id: string; image: RGBAImage } | null = null; // decoded pixels for settings.image
let patternCache: { key: string; pattern: Pattern } | null = null;
let sheets: SheetOut[] = [];
let sheetIndex = 0;
let currentPattern: Pattern | null = null;
let emojiSet: Promise<EmojiSet> | null = null;
let rebuildTimer = 0;
let pending = ""; // message to show above the next rebuild's notes

const preview = new Preview($("preview"));
const inputs = new Map<Key, HTMLInputElement | HTMLSelectElement>();
const rowOf = new Map<Key, HTMLElement>(); // each setting's row, for hiding by kit type
const fieldsets: [HTMLFieldSetElement, Key[]][] = [];
const imageName = document.createElement("div");
const zoneStatus = document.createElement("span");
const markZone = document.createElement("button");

function loadSettings(): Settings {
  try {
    return sanitize(JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "{}"));
  } catch {
    return { ...DEFAULTS };
  }
}

/** Keep only known keys whose values have the right type. */
function sanitize(data: Record<string, unknown>): Settings {
  const s = { ...DEFAULTS } as Record<Key, unknown>;
  for (const k of Object.keys(DEFAULTS) as Key[]) {
    const v = data[k];
    if (v !== undefined && typeof v === typeof DEFAULTS[k]) s[k] = v;
  }
  return s as unknown as Settings;
}

function saveSettings(): void {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch { /* storage may be unavailable */ }
}

function log(text: string, error = false): void {
  const el = $("log");
  el.textContent = text;
  el.classList.toggle("error", error);
}

function loadEmoji(): Promise<EmojiSet> {
  emojiSet ??= fetch(new URL("emoji/twemoji-72.zip", document.baseURI))
    .then((r) => {
      if (!r.ok) throw new Error(`Couldn't load the emoji set (${r.status})`);
      return r.arrayBuffer();
    })
    .then((b) => new EmojiSet(new Uint8Array(b)));
  emojiSet.catch(() => (emojiSet = null));
  return emojiSet;
}

// ---------------------------------------------------------------- form

function buildForm(): void {
  const form = $("form");
  for (const [title, fields] of FORM) {
    const fs = document.createElement("fieldset");
    const legend = document.createElement("legend");
    legend.textContent = title;
    fs.append(legend);
    fieldsets.push([fs, fields.map(([key]) => key)]);
    for (const [key, label, opts = {}] of fields) {
      if (key === "image") {
        fs.append(imageControls());
        continue;
      }
      if (key === "pbn_zones") {
        const zones = zoneControls();
        fs.append(zones);
        rowOf.set(key, zones);
        continue;
      }
      if (typeof DEFAULTS[key] === "boolean") {
        const row = document.createElement("label");
        row.className = "check";
        const input = document.createElement("input");
        input.type = "checkbox";
        input.id = `f-${key}`;
        row.append(input, label);
        fs.append(row);
        inputs.set(key, input);
        rowOf.set(key, row);
        continue;
      }
      const row = document.createElement("div");
      row.className = "field";
      const lab = document.createElement("label");
      lab.textContent = label;
      lab.htmlFor = `f-${key}`;
      let input: HTMLInputElement | HTMLSelectElement;
      if (opts.choices) {
        input = document.createElement("select");
        for (const c of opts.choices) input.append(new Option(CHOICE_LABELS[c] ?? c, c));
        if (key === "kit_type") row.classList.add("wide");
        row.append(lab, input);
      } else if (key === "background") {
        input = document.createElement("input");
        input.type = "text";
        const swatch = document.createElement("input");
        swatch.type = "color";
        swatch.setAttribute("aria-label", "Pick background color");
        swatch.addEventListener("input", () => {
          input.value = swatch.value;
          changed();
        });
        input.addEventListener("input", () => {
          const c = parseColor(input.value);
          if (c) swatch.value = "#" + c.map((v) => v.toString(16).padStart(2, "0")).join("");
        });
        const wrap = document.createElement("div");
        wrap.className = "color-field";
        wrap.append(input, swatch);
        input.style.width = "100%";
        row.append(lab, wrap);
      } else {
        input = document.createElement("input");
        if (typeof DEFAULTS[key] === "number") {
          input.type = "number";
          input.step = String(opts.step ?? "any");
          if (opts.min !== undefined) input.min = String(opts.min);
          if (key === "cols" || key === "rows") input.max = String(MAX_GRID_PBN);
        } else input.type = "text";
        row.append(lab, input);
      }
      input.id = `f-${key}`;
      fs.append(row);
      inputs.set(key, input);
      rowOf.set(key, row);
    }
    form.append(fs);
  }
  form.addEventListener("input", changed);
  form.addEventListener("change", changed);
  form.addEventListener("submit", (e) => e.preventDefault());
  writeForm();
}

function imageControls(): HTMLElement {
  const wrap = document.createElement("div");
  imageName.className = "image-name";
  const file = document.createElement("input");
  file.type = "file";
  file.accept = "image/*";
  file.hidden = true;
  file.addEventListener("change", () => {
    if (file.files?.[0]) void useFile(file.files[0]);
    file.value = "";
  });
  const row = document.createElement("div");
  row.className = "image-row";
  const button = (text: string, fn: () => void) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = text;
    b.addEventListener("click", fn);
    return b;
  };
  row.append(
    button("Choose image…", () => file.click()),
    button("Emoji…", () => void pickEmoji()),
    button("Set grid from image", setGridFromImage),
    button("Edit pixels…", () => void editPixels()),
    button("Clear", () => {
      settings.image = "";
      settings.pbn_zones = "";
      picture = null;
      void savePicture(null);
      writeForm();
      changed();
    }),
  );
  const hint = document.createElement("div");
  hint.className = "hint";
  hint.textContent = "Or drop an image anywhere on the page.";
  wrap.append(imageName, row, file, hint);
  return wrap;
}

/** Detail zones: boxes marked on the preview where smaller regions are kept. */
function zoneControls(): HTMLElement {
  const wrap = document.createElement("div");
  const row = document.createElement("div");
  row.className = "image-row";
  markZone.type = "button";
  markZone.textContent = "Mark zones";
  markZone.setAttribute("aria-pressed", "false");
  markZone.addEventListener("click", () => setMarking(!preview.onMark));
  const button = (text: string, fn: () => void) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = text;
    b.addEventListener("click", fn);
    return b;
  };
  row.append(
    markZone,
    button("Undo zone", () => setZones(settings.pbn_zones.split(";").filter((z) => z.trim()).slice(0, -1).join(";"))),
    button("Clear zones", () => setZones("")),
  );
  const hint = document.createElement("div");
  hint.className = "hint";
  hint.textContent = "Drag a box on the preview over each eye or other detail; scroll to zoom in first.";
  const label = document.createElement("div");
  label.className = "hint";
  label.append(zoneStatus);
  wrap.append(label, row, hint);
  return wrap;
}

function setMarking(on: boolean): void {
  preview.onMark = on ? addZone : null;
  markZone.setAttribute("aria-pressed", String(on));
  $("preview").classList.toggle("marking", on);
}

function setZones(text: string): void {
  settings.pbn_zones = text;
  writeForm();
  changed();
}

function addZone(a: [number, number], b: [number, number]): void {
  const picture = sheets[sheetIndex]?.items[0]?.picture;
  if (!picture) return;
  const [[ox, oy], [qx], [, qy]] = picture;
  const u = (x: number) => Math.min(1, Math.max(0, (x - ox) / (qx - ox)));
  const v = (y: number) => Math.min(1, Math.max(0, (y - oy) / (qy - oy)));
  const x0 = Math.min(u(a[0]), u(b[0])), x1 = Math.max(u(a[0]), u(b[0]));
  const y0 = Math.min(v(a[1]), v(b[1])), y1 = Math.max(v(a[1]), v(b[1]));
  const box = [x0, y0, x1, y1].map((n) => n.toFixed(4));
  if (Number(box[2]) <= Number(box[0]) || Number(box[3]) <= Number(box[1])) return; // a click, not a drag
  setZones([settings.pbn_zones, box.join(",")].filter(Boolean).join(";"));
}

/** Show only the settings the current kit type uses. */
function updateVisibility(): void {
  const pbn = settings.kit_type === "paint_by_number";
  const canvas = pbn ? settings.canvas : "off";
  const hiddenKey = (k: Key) => (pbn && MOSAIC_ONLY.has(k)) || (!pbn && PBN_ONLY.has(k)) ||
    ((k === "canvas_width" || k === "canvas_height") && canvas !== "custom") ||
    (k === "canvas_landscape" && (canvas === "off" || canvas === "custom"));
  for (const [key, row] of rowOf) row.hidden = hiddenKey(key);
  for (const [fs, keys] of fieldsets) fs.hidden = keys.every(hiddenKey);
  $("kerf-test").hidden = pbn;
  // A canvas sets the grid: show its columns and rows, but don't let them be edited.
  const derived = canvas !== "off" ? effectiveSettings(settings) : null;
  for (const k of ["cols", "rows"] as const) {
    const input = inputs.get(k) as HTMLInputElement;
    if (derived) input.value = String(derived[k]);
    else if (input.disabled) input.value = String(settings[k]); // back to the user's own grid
    input.disabled = Boolean(derived);
  }
}

function writeForm(): void {
  for (const [key, input] of inputs) {
    const v = settings[key];
    if (input instanceof HTMLInputElement && input.type === "checkbox") input.checked = Boolean(v);
    else input.value = String(v);
    if (key === "background") {
      const swatch = input.parentElement?.querySelector<HTMLInputElement>('input[type="color"]');
      const c = parseColor(String(v));
      if (swatch && c) swatch.value = "#" + c.map((n) => n.toString(16).padStart(2, "0")).join("");
    }
  }
  imageName.textContent = settings.image
    ? (isEmoji(settings.image) ? `Emoji: ${settings.image.slice(settings.image.indexOf(" ") + 1)}` : settings.image)
    : "No picture (tray and blank tiles only)";
  imageName.classList.toggle("set", Boolean(settings.image));
  const zones = settings.pbn_zones.split(";").filter((z) => z.trim()).length;
  zoneStatus.textContent = zones ? `${zones} detail zone(s)` : "No detail zones";
  updateVisibility();
}

/** Read the form; returns the problem text for unparseable numbers. */
function readForm(): string | null {
  const next = { ...settings } as Record<Key, unknown>;
  for (const [key, input] of inputs) {
    if (input.disabled) continue; // shows a derived value (canvas grid); keep the user's own
    if (input instanceof HTMLInputElement && input.type === "checkbox") next[key] = input.checked;
    else if (typeof DEFAULTS[key] === "number") {
      const n = Number(input.value);
      if (input.value.trim() === "" || !Number.isFinite(n)) return `Enter a number for ${key}.`;
      next[key] = INT_FIELDS.has(key) ? Math.trunc(n) : n;
    } else next[key] = input.value;
  }
  settings = next as unknown as Settings;
  return null;
}

function changed(): void {
  const problem = readForm();
  updateVisibility();
  try {
    $("summary").textContent = summary(settings);
  } catch { /* summary needs numbers only */ }
  if (problem) {
    log(`Preview not updated: ${problem}`, true);
    return;
  }
  saveSettings();
  clearTimeout(rebuildTimer);
  rebuildTimer = window.setTimeout(() => void rebuild(), isLarge() ? 900 : 300);
}

// ---------------------------------------------------------------- build

async function ensurePicture(): Promise<RGBAImage | null> {
  if (!settings.image) return null;
  if (isEmoji(settings.image)) {
    const code = emojiCode(settings.image);
    if (picture?.id !== `emoji:${code}`) {
      const set = await loadEmoji();
      picture = { id: `emoji:${code}`, image: await decodeImage(set.png(code), "image/png") };
    }
    return picture.image;
  }
  return picture?.image ?? null;
}

async function currentPatternFor(): Promise<Pattern | null> {
  const img = await ensurePicture();
  if (!img || !picture) return null;
  const s = settings;
  const e = effectiveSettings(s);
  const key = [picture.id, e.cols, e.rows, s.colors, s.background, canvasMm(s) ? s.canvas_fit : "", s.kit_type,
    s.pbn_zones, s.pbn_zone_colors, s.pbn_zone_area, s.pixel].join("|");
  if (patternCache?.key !== key) patternCache = { key, pattern: patternFor(s, img) };
  return patternCache.pattern;
}

const LARGE_BOARD = 250_000; // cells; bigger paint-by-number boards wait longer before rebuilding

function isLarge(): boolean {
  const e = effectiveSettings(settings);
  return e.kit_type === "paint_by_number" && e.cols * e.rows > LARGE_BOARD;
}

async function rebuild(): Promise<void> {
  try {
    if (isLarge()) {
      const e = effectiveSettings(settings);
      log(`Building the ${e.cols} x ${e.rows} board…`);
      await new Promise((r) => setTimeout(r, 30)); // let the message show before the work starts
    }
    const pattern = await currentPatternFor();
    if (settings.image && !pattern) {
      log("The picture isn't loaded any more; choose it again.", true);
    }
    const b = build(settings, null, pattern);
    sheets = b.sheets;
    currentPattern = b.pattern;
    if (sheetIndex >= sheets.length) sheetIndex = 0;
    if ($<HTMLInputElement>("show-picture").checked) showTraySheet();
    render();
    if (!(settings.image && !pattern)) log((pending ? pending + "\n\n" : "") + b.notes.join("\n"));
    pending = "";
  } catch (e) {
    log(`Preview not updated: ${(e as Error).message}`, true);
  }
}

function showTraySheet(): void {
  if (sheets[sheetIndex]?.items.some((p) => p.picture)) return;
  const i = sheets.findIndex((sh) => sh.items.some((p) => p.picture));
  if (i >= 0) sheetIndex = i;
}

function render(): void {
  const sheet = sheets[sheetIndex] ?? null;
  const show = $<HTMLInputElement>("show-picture").checked;
  preview.show(sheet, show ? currentPattern : null, zoneBoxes(sheet));
  $("preview").classList.toggle("picture", show && Boolean(currentPattern));
  $("sheet-label").textContent = sheet
    ? `${sheetIndex + 1}/${sheets.length}  ${sheet.stem}  (${sheet.w.toFixed(0)} x ${sheet.h.toFixed(0)} mm)`
    : "";
}

/** Detail zones in sheet mm, for the preview. */
function zoneBoxes(sheet: SheetOut | null): number[][] {
  const picture = sheet?.items[0]?.picture;
  if (!picture || settings.kit_type !== "paint_by_number") return [];
  const [[ox, oy], [qx], [, qy]] = picture;
  try {
    return parseZones(settings.pbn_zones).map(([x0, y0, x1, y1]) =>
      [ox + x0 * (qx - ox), oy + y0 * (qy - oy), (x1 - x0) * (qx - ox), (y1 - y0) * (qy - oy)]);
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------- pictures

async function useFile(file: File): Promise<void> {
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    const image = await decodeImage(bytes, file.type);
    picture = { id: `file:${file.name}:${file.size}:${file.lastModified}`, image };
    settings.image = file.name;
    settings.pbn_zones = ""; // zones belong to the old picture
    $<HTMLInputElement>("show-picture").checked = true;
    writeForm();
    changed();
    void savePicture({ name: file.name, id: picture.id, type: file.type, bytes });
    pending = `Loaded ${file.name} (${image.width} x ${image.height} px).`;
  } catch (e) {
    log(`Couldn't read that image: ${(e as Error).message}`, true);
  }
}

async function pickEmoji(): Promise<void> {
  let set: EmojiSet;
  try {
    set = await loadEmoji();
  } catch (e) {
    log((e as Error).message, true);
    return;
  }
  const code = await new EmojiPicker(set).open();
  if (!code) return;
  settings.image = set.imageValue(code);
  settings.pbn_zones = ""; // zones belong to the old picture
  settings.rows = settings.cols; // emoji are square
  $<HTMLInputElement>("show-picture").checked = true;
  pending = `Emoji: ${set.name(code) || code}, grid set to ${settings.cols} x ${settings.cols}.`;
  writeForm();
  changed();
}

let pixelEditor: PixelEditor | null = null;

/** Edit the mosaic tile by tile; saving makes <picture>_scaled.png the image. */
async function editPixels(): Promise<void> {
  if (readForm()) return;
  const pattern = await currentPatternFor();
  if (!pattern) {
    log("Choose an image or an emoji first.", true);
    return;
  }
  pixelEditor ??= new PixelEditor();
  const grid = await pixelEditor.open(pattern);
  if (!grid) return;
  const edited: Pattern = { ...pattern, grid };
  const image = scaledImage(edited);
  const name = scaledImageName(settings);
  const bytes = await encodePng(image);
  picture = { id: `edited:${name}:${Date.now()}`, image };
  settings.image = name;
  $<HTMLInputElement>("show-picture").checked = true;
  void savePicture({ name, id: picture.id, type: "image/png", bytes });
  pending = `Saved the edited picture as ${name}; it is now the image (and goes in the kit download).`;
  writeForm();
  changed();
}

async function setGridFromImage(): Promise<void> {
  const img = await ensurePicture();
  if (!img) {
    log("Choose an image or an emoji first.", true);
    return;
  }
  const found = detectGrid(composite(img, [255, 255, 255]));
  let msg: string;
  if (found && Math.max(...found) <= gridLimit(settings)) {
    [settings.cols, settings.rows] = found;
    msg = `Detected a ${found[0]} x ${found[1]} pixel-art grid in the ${img.width} x ${img.height} px image.`;
  } else {
    settings.rows = Math.max(1, Math.round((settings.cols * img.height) / img.width));
    msg = `No pixel-art grid found in the ${img.width} x ${img.height} px image; kept ${settings.cols} columns ` +
      `and set ${settings.rows} rows to match its shape.`;
  }
  pending = msg;
  writeForm();
  changed();
}

// ---------------------------------------------------------------- downloads

function showFiles(files: [string, Blob][]): void {
  const list = $("files");
  for (const a of list.querySelectorAll("a")) URL.revokeObjectURL(a.href);
  list.replaceChildren(...files.map(([name, blob]) => {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.textContent = name;
    li.append(a);
    return li;
  }));
}

async function downloadKit(): Promise<void> {
  const problem = readForm();
  if (problem) return log(problem, true);
  try {
    const pattern = await currentPatternFor();
    const { files, build: b } = generate(settings, null, pattern);
    const entries: Record<string, Uint8Array> = {};
    const blobs: [string, Blob][] = [];
    for (const f of files) {
      if ("text" in f) {
        entries[f.name] = new TextEncoder().encode(f.text);
        blobs.push([f.name, new Blob([f.text], { type: f.name.endsWith(".svg") ? "image/svg+xml" : "text/plain" })]);
      } else {
        const png = await encodePng(f.image);
        entries[f.name] = png;
        blobs.push([f.name, new Blob([png as BlobPart], { type: "image/png" })]);
      }
    }
    const zipName = `${settings.name || "pixelart"}_kit.zip`;
    download(zipName, new Blob([zipSync(entries, { level: 6 }) as BlobPart], { type: "application/zip" }));
    showFiles(blobs);
    log(`${b.notes.join("\n")}\n\nDownloaded ${zipName} (${files.length} files). Individual files are listed below.`);
  } catch (e) {
    log(`Couldn't make the kit: ${(e as Error).message}`, true);
  }
}

function downloadKerfTest(): void {
  const problem = readForm();
  if (problem) return log(problem, true);
  try {
    const { file, sheet, notes } = kerfTest(settings);
    if (!("text" in file)) return;
    const blob = new Blob([file.text], { type: "image/svg+xml" });
    download(file.name, blob);
    showFiles([[file.name, blob]]);
    sheets = [sheet];
    sheetIndex = 0;
    $<HTMLInputElement>("show-picture").checked = false;
    render();
    log(`${notes.join("\n")}\n\nDownloaded ${file.name}. Change any setting to return to the kit preview.`);
  } catch (e) {
    log((e as Error).message, true);
  }
}

// ---------------------------------------------------------------- startup

function wire(): void {
  $("prev").addEventListener("click", () => {
    if (!sheets.length) return;
    sheetIndex = (sheetIndex - 1 + sheets.length) % sheets.length;
    render();
  });
  $("next").addEventListener("click", () => {
    if (!sheets.length) return;
    sheetIndex = (sheetIndex + 1) % sheets.length;
    render();
  });
  $("show-picture").addEventListener("change", () => {
    if ($<HTMLInputElement>("show-picture").checked) {
      if (!currentPattern) log("Choose an image or an emoji to see the picture in the tray.");
      showTraySheet();
    }
    render();
  });
  $("download").addEventListener("click", () => void downloadKit());
  $("kerf-test").addEventListener("click", downloadKerfTest);
  $("export-settings").addEventListener("click", () => {
    download(`${settings.name || "pixelart"}_settings.json`,
      new Blob([JSON.stringify(settings, null, 2)], { type: "application/json" }));
  });
  $<HTMLInputElement>("import-settings").addEventListener("change", async (e) => {
    const input = e.target as HTMLInputElement;
    const f = input.files?.[0];
    input.value = "";
    if (!f) return;
    try {
      const next = sanitize(JSON.parse(await f.text()));
      if (next.image && !isEmoji(next.image) && next.image !== settings.image) {
        picture = null;
        log(`Settings imported. Choose ${next.image} again to use it as the picture.`);
      }
      settings = next;
      writeForm();
      changed();
    } catch {
      log("That file isn't a pixelkit settings file.", true);
    }
  });
  $("reset-settings").addEventListener("click", () => {
    settings = { ...DEFAULTS };
    picture = null;
    void savePicture(null);
    writeForm();
    changed();
  });

  // Drag and drop an image anywhere.
  let depth = 0;
  window.addEventListener("dragenter", (e) => {
    if (!e.dataTransfer?.types.includes("Files")) return;
    depth++;
    document.body.classList.add("drop-target");
  });
  window.addEventListener("dragleave", () => {
    if (--depth <= 0) {
      depth = 0;
      document.body.classList.remove("drop-target");
    }
  });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    depth = 0;
    document.body.classList.remove("drop-target");
    const f = e.dataTransfer?.files[0];
    if (f && f.type.startsWith("image/")) void useFile(f);
  });
}

async function restorePicture(): Promise<void> {
  if (!settings.image || isEmoji(settings.image)) return;
  const saved = await loadPicture();
  if (saved && saved.name === settings.image) {
    try {
      picture = { id: saved.id, image: await decodeImage(saved.bytes, saved.type) };
    } catch { /* fall through: the user picks it again */ }
  }
}

buildForm();
wire();
$("summary").textContent = summary(settings);
if (settings.image && isEmoji(settings.image)) $<HTMLInputElement>("show-picture").checked = true;
void restorePicture().then(() => {
  if (picture) $<HTMLInputElement>("show-picture").checked = true;
  void rebuild();
});

if (import.meta.env.PROD && "serviceWorker" in navigator) {
  navigator.serviceWorker.register(new URL("sw.js", document.baseURI)).catch(() => { /* offline support is optional */ });
}
