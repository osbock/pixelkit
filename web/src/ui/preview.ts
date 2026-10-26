// Live sheet preview: the real SVG markup, plus an optional picture overlay
// on the tray base, with wheel zoom, drag pan and double-click to fit.
import type { SheetOut } from "../core/kit";
import type { Pattern } from "../core/pattern";
import { hexcolor, piecesMarkup } from "../core/svg";

export class Preview {
  private view: [number, number, number, number] | null = null;
  private sheetSize: [number, number] = [0, 0];
  private svg: SVGSVGElement | null = null;

  constructor(private host: HTMLElement) {
    host.addEventListener("wheel", (e) => this.zoom(e), { passive: false });
    host.addEventListener("dblclick", () => this.fit());
    let drag: { x: number; y: number; view: [number, number, number, number] } | null = null;
    host.addEventListener("pointerdown", (e) => {
      if (!this.view) return;
      drag = { x: e.clientX, y: e.clientY, view: [...this.view] };
      host.setPointerCapture(e.pointerId);
      host.classList.add("dragging");
    });
    host.addEventListener("pointermove", (e) => {
      if (!drag || !this.svg) return;
      const k = drag.view[2] / this.svg.clientWidth;
      const scale = Math.max(k, drag.view[3] / this.svg.clientHeight);
      this.setView([drag.view[0] - (e.clientX - drag.x) * scale, drag.view[1] - (e.clientY - drag.y) * scale, drag.view[2], drag.view[3]]);
    });
    const end = () => {
      drag = null;
      host.classList.remove("dragging");
    };
    host.addEventListener("pointerup", end);
    host.addEventListener("pointercancel", end);
  }

  show(sheet: SheetOut | null, pattern: Pattern | null): void {
    if (!sheet) {
      this.host.innerHTML = "";
      this.svg = null;
      this.view = null;
      return;
    }
    const { w, h, items } = sheet;
    const sameSize = this.sheetSize[0] === w && this.sheetSize[1] === h;
    this.sheetSize = [w, h];
    const overlay: string[] = [];
    if (pattern) {
      const rows = pattern.grid.length, cols = pattern.grid[0].length;
      for (const pc of items) {
        if (!pc.picture) continue;
        const [[x0, y0], [x1, y1], [x2, y2]] = pc.picture;
        const ux = (x1 - x0) / cols, uy = (y1 - y0) / cols, vx = (x2 - x0) / rows, vy = (y2 - y0) / rows;
        pattern.grid.forEach((row, j) => row.forEach((idx, i) => {
          const cx = x0 + i * ux + j * vx, cy = y0 + i * uy + j * vy;
          const pts = [[cx, cy], [cx + ux, cy + uy], [cx + ux + vx, cy + uy + vy], [cx + vx, cy + vy]]
            .map(([x, y]) => `${x.toFixed(3)},${y.toFixed(3)}`).join(" ");
          overlay.push(`<polygon points="${pts}" fill="${hexcolor(pattern.palette[idx])}"/>`);
        }));
      }
    }
    this.host.innerHTML =
      `<svg xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet" viewBox="0 0 ${w} ${h}">` +
      `<rect width="${w}" height="${h}" fill="var(--sheet)"/>` +
      `<g>${overlay.join("")}</g>${piecesMarkup(items).join("")}</svg>`;
    this.svg = this.host.querySelector("svg");
    if (sameSize && this.view) this.setView(this.view);
    else this.fit();
  }

  fit(): void {
    const [w, h] = this.sheetSize;
    const pad = Math.max(w, h) * 0.02;
    this.setView([-pad, -pad, w + 2 * pad, h + 2 * pad]);
  }

  private setView(v: [number, number, number, number]): void {
    this.view = v;
    this.svg?.setAttribute("viewBox", v.map((n) => n.toFixed(3)).join(" "));
  }

  private zoom(e: WheelEvent): void {
    if (!this.view || !this.svg) return;
    e.preventDefault();
    const rect = this.svg.getBoundingClientRect();
    const [vx, vy, vw, vh] = this.view;
    // Map the pointer into sheet coordinates (meet: uniform scale, centred).
    const scale = Math.max(vw / rect.width, vh / rect.height);
    const ox = vx - (rect.width * scale - vw) / 2, oy = vy - (rect.height * scale - vh) / 2;
    const px = ox + (e.clientX - rect.left) * scale, py = oy + (e.clientY - rect.top) * scale;
    const f = Math.exp(Math.sign(e.deltaY) * Math.min(Math.abs(e.deltaY), 100) * 0.002);
    const nw = Math.min(Math.max(vw * f, this.sheetSize[0] * 0.02), this.sheetSize[0] * 4);
    const k = nw / vw;
    this.setView([px - (px - vx) * k, py - (py - vy) * k, vw * k, vh * k]);
  }
}
