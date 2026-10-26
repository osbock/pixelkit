// Emoji picker dialog: live search over the bundled Twemoji set, thumbnails
// from the zip, click to select, double-click / Enter / Use to choose.
import type { EmojiSet } from "../core/emoji";

export class EmojiPicker {
  private dialog = document.getElementById("emoji-dialog") as HTMLDialogElement;
  private query = document.getElementById("emoji-query") as HTMLInputElement;
  private grid = document.getElementById("emoji-grid") as HTMLDivElement;
  private status = document.getElementById("emoji-status") as HTMLSpanElement;
  private urls = new Map<string, string>();
  private chosen: string | null = null;
  private timer = 0;
  private resolve: ((code: string | null) => void) | null = null;

  constructor(private set: EmojiSet) {
    this.query.addEventListener("input", () => {
      clearTimeout(this.timer);
      this.timer = window.setTimeout(() => this.refresh(), 150);
    });
    this.query.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        if (this.chosen) this.finish(this.chosen);
      }
    });
    this.dialog.addEventListener("close", () => {
      this.finish(this.dialog.returnValue === "use" ? this.chosen : null);
    });
  }

  /** Open the picker; resolves with the chosen code, or null if cancelled. */
  open(): Promise<string | null> {
    this.chosen = null;
    this.query.value = "";
    this.dialog.returnValue = "";
    this.refresh();
    this.dialog.showModal();
    this.query.focus();
    return new Promise((r) => (this.resolve = r));
  }

  private finish(code: string | null): void {
    const r = this.resolve;
    this.resolve = null;
    if (this.dialog.open) this.dialog.close();
    r?.(code);
  }

  private thumb(code: string): string {
    let url = this.urls.get(code);
    if (!url) {
      url = URL.createObjectURL(new Blob([this.set.png(code) as BlobPart], { type: "image/png" }));
      this.urls.set(code, url);
    }
    return url;
  }

  private select(code: string): void {
    this.chosen = code;
    for (const b of this.grid.querySelectorAll("button")) b.setAttribute("aria-selected", String(b.dataset.code === code));
    this.status.textContent = `${this.set.name(code) || "(no name)"}   U+${code.toUpperCase().replace(/-/g, " U+")}`;
  }

  private refresh(): void {
    const codes = this.set.search(this.query.value);
    this.grid.replaceChildren(...codes.map((code) => {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.code = code;
      b.title = this.set.name(code);
      b.setAttribute("role", "option");
      b.setAttribute("aria-selected", "false");
      const img = document.createElement("img");
      img.src = this.thumb(code);
      img.alt = this.set.name(code);
      b.append(img);
      b.addEventListener("click", () => this.select(code));
      b.addEventListener("dblclick", () => this.finish(code));
      return b;
    }));
    this.chosen = null;
    if (codes.length === 1) this.select(codes[0]);
    else if (!codes.length) this.status.textContent = "No matches";
    else this.status.textContent = `${codes.length} shown, double-click to use` +
      (codes.length === 80 ? " (refine the search to see more)" : "");
  }
}
