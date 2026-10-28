// Pixel editor dialog: paint tiles with the mosaic's own colors.
// open() resolves with the edited grid, or null if cancelled.
import type { Pattern } from "../core/pattern";
import { hexcolor, textColor } from "../core/svg";

export class PixelEditor {
  private dialog = document.getElementById("pixel-dialog") as HTMLDialogElement;
  private canvas = document.getElementById("pixel-canvas") as HTMLCanvasElement;
  private swatches = document.getElementById("pixel-swatches") as HTMLDivElement;
  private counts = document.getElementById("pixel-counts") as HTMLSpanElement;
  private title = document.getElementById("pixel-title") as HTMLSpanElement;
  private grid: number[][] = [];
  private palette: [number, number, number][] = [];
  private color = 0;
  private cell = 20;
  private stroke: [number, number, number][] | null = null;
  private undoStack: [number, number, number][][] = [];
  private dirty = false;
  private resolve: ((grid: number[][] | null) => void) | null = null;

  constructor() {
    const c = this.canvas;
    c.addEventListener("contextmenu", (e) => e.preventDefault());
    c.addEventListener("pointerdown", (e) => {
      const pos = this.cellAt(e);
      if (!pos) return;
      if (e.button === 2 || e.ctrlKey || e.altKey) {
        this.choose(this.grid[pos[1]][pos[0]]);
        return;
      }
      if (e.button !== 0) return;
      c.setPointerCapture(e.pointerId);
      this.stroke = [];
      this.paint(pos);
    });
    c.addEventListener("pointermove", (e) => {
      if (!this.stroke) return;
      const pos = this.cellAt(e);
      if (pos) this.paint(pos);
    });
    const end = () => {
      if (this.stroke?.length) this.undoStack.push(this.stroke);
      this.stroke = null;
    };
    c.addEventListener("pointerup", end);
    c.addEventListener("pointercancel", end);
    this.dialog.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        this.undo();
      } else if (/^[1-9]$/.test(e.key) && Number(e.key) <= this.palette.length) {
        this.choose(Number(e.key) - 1);
      }
    });
    this.dialog.addEventListener("cancel", (e) => { // Escape
      e.preventDefault();
      this.cancel();
    });
    document.getElementById("pixel-undo")!.addEventListener("click", () => this.undo());
    document.getElementById("pixel-cancel")!.addEventListener("click", () => this.cancel());
    document.getElementById("pixel-save")!.addEventListener("click", () => this.finish(this.grid));
  }

  open(pattern: Pattern): Promise<number[][] | null> {
    this.grid = pattern.grid.map((row) => row.slice());
    this.palette = pattern.palette;
    this.undoStack = [];
    this.stroke = null;
    this.dirty = false;
    const rows = this.grid.length, cols = this.grid[0].length;
    const room = Math.min(window.innerWidth - 80, 720), roomH = Math.min(window.innerHeight - 260, 720);
    this.cell = Math.max(4, Math.min(28, Math.floor(Math.min(room / cols, roomH / rows))));
    this.canvas.width = cols * this.cell;
    this.canvas.height = rows * this.cell;
    this.title.textContent = `Edit pixels: ${cols} x ${rows}`;
    this.swatches.replaceChildren(...this.palette.map((rgb, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "swatch";
      b.style.background = hexcolor(rgb);
      b.style.color = textColor(rgb);
      b.textContent = String(i + 1);
      b.title = `Color ${i + 1} ${hexcolor(rgb)}`;
      b.addEventListener("click", () => this.choose(i));
      return b;
    }));
    this.choose(0);
    this.draw();
    this.dialog.showModal();
    return new Promise((r) => (this.resolve = r));
  }

  private finish(grid: number[][] | null): void {
    const r = this.resolve;
    this.resolve = null;
    this.dialog.close();
    r?.(grid);
  }

  private cancel(): void {
    if (this.dirty && !window.confirm("Discard your changes?")) return;
    this.finish(null);
  }

  private choose(i: number): void {
    this.color = i;
    this.swatches.querySelectorAll("button").forEach((b, k) => b.setAttribute("aria-pressed", String(k === i)));
  }

  private cellAt(e: PointerEvent): [number, number] | null {
    const r = this.canvas.getBoundingClientRect();
    const i = Math.floor(((e.clientX - r.left) / r.width) * this.grid[0].length);
    const j = Math.floor(((e.clientY - r.top) / r.height) * this.grid.length);
    return i >= 0 && j >= 0 && j < this.grid.length && i < this.grid[0].length ? [i, j] : null;
  }

  private paint([i, j]: [number, number]): void {
    const old = this.grid[j][i];
    if (old === this.color) return;
    this.stroke?.push([j, i, old]);
    this.grid[j][i] = this.color;
    this.dirty = true;
    this.drawCell(i, j);
    this.updateCounts();
  }

  private undo(): void {
    const stroke = this.undoStack.pop();
    if (!stroke) return;
    for (const [j, i, old] of stroke.reverse()) {
      this.grid[j][i] = old;
      this.drawCell(i, j);
    }
    this.updateCounts();
  }

  private drawCell(i: number, j: number): void {
    const ctx = this.canvas.getContext("2d")!;
    const c = this.cell;
    ctx.fillStyle = hexcolor(this.palette[this.grid[j][i]]);
    ctx.fillRect(i * c, j * c, c, c);
    if (c >= 8) {
      ctx.strokeStyle = "rgb(0 0 0 / 0.25)";
      ctx.lineWidth = 1;
      ctx.strokeRect(i * c + 0.5, j * c + 0.5, c - 1, c - 1);
    }
  }

  private draw(): void {
    this.grid.forEach((row, j) => row.forEach((_, i) => this.drawCell(i, j)));
    this.updateCounts();
  }

  private updateCounts(): void {
    const n = this.palette.map(() => 0);
    for (const row of this.grid) for (const idx of row) n[idx]++;
    this.counts.textContent = n.map((c, i) => `${i + 1}: ${c}`).join("   ");
  }
}
