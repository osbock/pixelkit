# pixelkit specification

This document describes what pixelkit does and why, in enough detail to
rebuild it from scratch. It records requirements, behavior, algorithms and the
design decisions behind them (marked **Why:**). Exact constants are given where
they matter for compatibility or fit; anything not specified is an
implementation choice.

## 1. Purpose

pixelkit generates **laser-cut pixel art kits** as SVG files. A kit is:

1. **A tray** that holds the finished picture. Its base can be engraved with the
   tile grid and with each tile's color number.
2. **Square tiles** cut from sheet stock.
3. **A printable pattern** showing which color goes where, with per-color counts.

The kits are modelled on classroom and maker kits: wooden tiles in a
finger-jointed tray, painted or stained to match a numbered chart.

Users are makers with a CO₂ or diode laser and its software (LightBurn or
similar). They may not be programmers, so the main interface is a small
desktop GUI. A command-line mode exists for scripting.

### Non-goals

- Driving the laser or producing G-code. The output is SVG only.
- Photo-realistic mosaics. The color count is deliberately small (paint pots).
- Arbitrary tile shapes. Tiles are always square.

## 2. Platform and packaging

- A single Python 3 script, `pixelkit.py` (Python 3.9+; needs
  `argparse.BooleanOptionalAction` and `math.dist`).
- **The standard library only**, except that **Pillow** (`Pillow>=9.1`, listed
  in `requirements.txt`) is needed for anything involving images or emoji.
  Pillow is imported lazily, so tray and tile generation works without it.
- The GUI uses **Tkinter/ttk**. It must work on macOS; Windows and Linux are
  expected to work.
- **Entry point:** no arguments starts the GUI. Any arguments mean CLI mode,
  conventionally `--cli ...`.
- **The repository must work offline after cloning.** All emoji assets are
  bundled (§9).
- A browser version with the same behavior lives in `web/` (§13).
- License: MIT for pixelkit. The bundled Twemoji graphics remain CC BY 4.0
  and must be credited (§9.4).

## 3. Units and SVG conventions

- **Units:** all lengths are in millimetres. Each SVG sets
  `width="<w>mm" height="<h>mm"` and `viewBox="0 0 <w> <h>"`, so one user unit
  is 1 mm.
- **Stroke colors:**
  - cut: `#ff0000`
  - engrave/score: `#0000ff`
  - stroke width: 0.1 (hairline)

  **Why:** these are the de-facto conventions that laser software maps to
  layers.
- **Engraved text** is SVG `<text>` filled `#0000ff`, in Arial/Helvetica/sans-serif,
  with an explicit `text-anchor`. Rotated text uses
  `transform="rotate(a x y)"`. Some laser software doesn't import text, so the
  notes tell the user to convert it to paths.
- **Drawing order:** within each part (`<g id="<part name>">`), write the
  engraving first, then inner cuts (holes, interior tile lines), then the
  outer outline. **Why:** engraving before cutting keeps the part from
  shifting, and cutting holes before outlines keeps the part held by the
  sheet.
- **Line direction:** alternate the direction of successive parallel grid
  lines (serpentine) to reduce travel.
- **Sheet size:** each tray or tile SVG is the full sheet size (the user's bed
  or stock size), so the file lines up on the bed. Exception: the kerf test
  (§8) is sized to its contents.

## 4. Settings

All settings live in one flat record. The same names are used as GUI fields,
CLI flags (`--snake-case` → `--kebab-case`) and keys in saved JSON.

| Name | Type | Default | Meaning |
|---|---|---|---|
| `cols`, `rows` | int | 20, 25 | Picture size in tiles |
| `pixel` | float | 10.0 | Finished tile edge |
| `tile_thickness` | float | 3.0 | Tile stock thickness |
| `tray_thickness` | float | 3.0 | Tray stock thickness |
| `kerf` | float | 0.15 | Laser cut width in tray stock (tray parts and the kerf test) |
| `tile_kerf` | float | 0.15 | Kerf compensation for tile sheets (§5.7). Lower than the real kerf, even negative, gives smaller, looser tiles. |
| `tray_style` | choice | `box` | `box`, `layered` or `none` |
| `tile_clearance` | float | 1.0 | Total slack between the tile area and the tray wall (per axis) |
| `lip` | float | 4.0 | How far the wall stands above the tiles. A box's wall height is `tray_thickness + tile_thickness + lip`, which is 10 mm by default. |
| `finger_width` | float | 6.0 | Target finger/tab length for box joints |
| `joint_play` | float | 0.0 | Extra total gap per finger joint (+ looser, − tighter) |
| `frame_width` | float | 10.0 | Frame border width (`layered` only) |
| `engrave_grid` | bool | true | Engrave the tile grid on the base |
| `engrave_numbers` | bool | false | Engrave color numbers on the base (needs a picture) |
| `sheet_width`, `sheet_height` | float | 304, 304 | Bed or stock size (about 12 in square) |
| `spacing` | float | 3.0 | Gap between parts, and from the sheet edges |
| `tile_border` | float | 5.0 | Waste frame around each tile block (0 = none) |
| `tile_tabs` | bool | false | Leave uncut bridges on the vertical tile cuts (§5.7) |
| `tab_width` | float | 0.5 | Width of each bridge |
| `spare_percent` | float | 5.0 | Extra tiles (may be trimmed, see §6.3) |
| `tile_sheets` | choice | `combined` | `combined` (cut, then paint) or `per_color` (colored stock) |
| `image` | str | "" | Image path, or `emoji:<code> <name>` (§9) |
| `background` | str | `#ffffff` | Color composited behind transparent image areas |
| `colors` | int | 8 | Maximum number of colors |
| `kit_type` | choice | `mosaic` | `mosaic` (tray + tiles) or `paint_by_number` (§15) |
| `pbn_use` | choice | `engrave` | Paint by number: `engrave` or `print` |
| `pbn_min_area` | float | 6.0 | Regions smaller than this (mm²) are absorbed |
| `pbn_min_width` | float | 1.5 | Regions nowhere this wide (mm) are absorbed |
| `pbn_simplify` | float | 0.05 | Outline simplification tolerance (mm) |
| `pbn_smooth` | bool | true | Smooth the outlines' pixel steps |
| `pbn_margin` | float | 10.0 | Board border around the picture |
| `pbn_line_width` | float | 0.3 | Outline width when printing |
| `pbn_text_min`, `pbn_text_max` | float | 2.0, 6.0 | Number size limits |
| `pbn_zones` | str | "" | Detail zones `x0,y0,x1,y1;...`, fractions of the grid (§15) |
| `pbn_zone_area`, `pbn_zone_width` | float | 0.25, 0.0 | Absorbing limits inside a zone |
| `pbn_zone_colors` | int | 2 | Most colors each zone may add |
| `canvas` | choice | `off` | Paint-by-number board size: a preset, `custom` or `off` (§15) |
| `canvas_landscape` | bool | false | Turn a preset sideways |
| `canvas_width`, `canvas_height` | float | 203.2, 254.0 | Custom canvas size |
| `canvas_fit` | choice | `fit` | `fit` or `fill` |
| `output_dir` | str | `~/pixelkit_output` | Output folder (`~` expanded) |
| `name` | str | `pixelart` | Prefix for output file names |

**Why these defaults:**
- **6 mm fingers and 10 mm walls** give 3 interlocking corner segments in
  3 mm stock. At 7 mm walls, corners degrade to butt joints.
- **`combined`** matches the common workflow: cut natural wood, then paint.
- **304 × 304 mm sheets** match common 12-inch stock and small laser beds. A
  20 × 25 kit's tray and tiles each fit on one such sheet.
- **Saved settings win:** a saved settings file (`~/.pixelkit.json`, or
  browser storage on the web) overrides these defaults. Keys missing from it,
  such as settings added in a later version, take the defaults.

### Validation (refuse to generate, listing every problem)

- `cols` and `rows` must be at least 1 and at most **300**. **Why:** a user once
  set a 1856 × 2304 grid from an image's pixel size and the layout hung.
- `pixel`, both thicknesses and both sheet dimensions must be greater than 0.
- `kerf` must satisfy 0 ≤ kerf < pixel.
- `tile_kerf` must satisfy −1 ≤ tile_kerf < pixel. Negative values are allowed
  for deliberately small, loose tiles.
- With `tile_tabs`, `tab_width` must satisfy 0 < tab_width < pixel/2.
- Choice fields must hold a listed value.
- In `box` style, `finger_width` must be at least 2·kerf + 1.
- `background` must parse as a color (any Pillow `ImageColor` string). Skip
  this check if Pillow is absent.
- `engrave_numbers` requires an `image`.

## 5. Geometry

### 5.1 Parts

A part holds:
- cut polylines and engrave polylines, each open or closed;
- engraved text items (x, y, size, string, angle, anchor);
- for the tray base only, the three corners (top-left, top-right, bottom-left)
  of the tile area, used to draw the picture in previews.

Parts support:
- translate;
- normalize, which moves the part so its bounding box starts at (0, 0);
- **rotate 90°** as (x, y) → (−y, x), then normalize, adding 90° to each
  text's angle.

All of these transform every element, including the picture corners.

### 5.2 Outlines from rectangles

Every non-tile part is described as a **union of axis-aligned rectangles minus
another union of rectangles**. The outline is traced as follows:

1. **Build a cell grid.** Collect every x and every y edge of all rectangles
   into sorted coordinate lists. Together they form a grid of cells. A cell is
   filled when its centre lies strictly inside some "add" rectangle and
   inside no "subtract" rectangle.
2. **Collect boundary edges.** For each filled cell, emit each side whose
   neighbour is empty as a directed edge with the material on its left.
3. **Chain the edges into loops.** Where two loops touch at a vertex, take the
   leftmost turn, so diagonally touching regions stay separate loops.
4. **Drop collinear vertices.**

**Why:** all tray geometry is rectilinear. This is simpler and more robust than
general polygon boolean operations, and it naturally produces outer loops
(positive signed area) and holes (negative area).

### 5.3 Kerf compensation

The laser removes `kerf` centred on the path. Every traced loop is therefore
offset **away from the material by kerf/2**, using mitred corners. Each vertex
moves along n₁ + n₂, scaled by d / (1 + n₁·n₂), where n₁ and n₂ are the outward
unit normals of the two edges that meet there.

- Outer outlines grow by kerf/2.
- Holes shrink by kerf/2.
- As a result, fingers are cut kerf-wider and slots kerf-narrower, and both
  come out at their nominal size.

Cut holes first: sort loops by signed area, ascending.

### 5.4 Finger segmentation

To divide a length L with a target finger length f:
1. Choose an **odd** segment count n near L/f. Of the two odd counts on either
   side of L/f, pick the one whose segment length is closer to f.
2. Enforce a minimum count: 3 for base edges, 1 for corner edges.
3. Use equal segments of length L/n.

**Why:** odd counts make each edge symmetric and give both ends to the same
part. Rounding to the nearest odd count matters: plain rounding turned a 10 mm
wall with 6 mm fingers into a single segment.

### 5.5 Box tray (`box`)

Let:
- t = `tray_thickness`;
- W = cols·pixel + clearance and H = rows·pixel + clearance (the inside size);
- Z = t + tile_thickness + lip (the wall height);
- q = joint_play / 4.

The outer size is (W + 2t) × (H + 2t) × Z.

**Base**
- A core rectangle W × H, placed at (t, t).
- Along each edge, the segments of that edge (from §5.4) alternate between
  fingers and gaps. The **odd-indexed** segments are fingers, protruding t
  outward to the outside face of the walls. Each finger is narrowed by q on
  each side.
- The base's t × t corners are empty; the walls fill them.

**Walls**
- There are two pairs:
  - front/back, of length W + 2t;
  - left/right, of length H + 2t.
- All walls are Z high, drawn with their bottom edge at the bottom.
- **Base slots:** each wall's bottom edge has notches t deep where the base's
  fingers enter. The notches sit at the base's odd segments, offset by t, and
  are widened by q on each side.
- **Corner joints:** the vertical corner edges (the end strips t wide) are
  divided into segments along Z, with minimum count 1.
  - Front/back walls own the **even** segments and are notched at the odd ones.
  - Left/right walls own the **odd** segments and are notched at the even ones.
  - Every corner notch is widened by q on each side.
  - With one segment, the front/back walls own the whole corner (a butt joint).
    The notes then warn the user to glue the corners.

**Why q = play/4 per side:** a joint is a finger in a slot. Half the play goes
to each part, split across two sides, so the total gap per joint equals
`joint_play`.

**Base engraving**
- The tile area's top-left corner is the base's inner corner plus
  clearance/2 on each axis.
- **Grid:** `cols+1` vertical and `rows+1` horizontal engrave lines at pitch
  `pixel` (not pixel + tile_kerf; the tiles come out at `pixel`).
- **Color numbers:** the 1-based palette index, centred in each cell, at
  size 0.42·pixel. The baseline is the cell centre + 0.35·size.
- The base always records the tile-area corners, for previews.

**Notes** report:
- the outer size and wall height;
- the number of corner segments;
- the number of base fingers per long and short edge.

### 5.6 Layered tray (`layered`)

Let b = `frame_width`. The outer size is (W + 2b) × (H + 2b).

- **Base:** a plain rectangle, engraved as in §5.5, with the tile area offset
  by b.
- **Frame rings:** N = max(1, ⌈(tile_thickness + lip) / t⌉) of them. Each is the
  outer rectangle with a W × H hole.

Kerf compensation applies to these parts as to all others. Notes report the
rim height relative to the tiles: t·N − tile_thickness.

### 5.7 Tile blocks

- **Pitch:** p = pixel + tile_kerf. **Why:** with a shared cut line between
  neighbours, each tile loses kerf/2 on each side, so each comes out at
  exactly `pixel` when tile_kerf equals the real kerf. `tile_kerf` is
  separate from the tray's `kerf`, so tiles can be made deliberately looser
  (a lower value shrinks every tile by the difference) or matched to tile
  stock that cuts differently.
- **Block:** c × r tiles, laid out as:
  - c−1 interior vertical lines and r−1 interior horizontal lines (serpentine);
  - then the outer rectangle of the tile area;
  - then, if `tile_border` > 0, a waste-frame rectangle `tile_border` outside it.

  Tiles aren't drawn as individual squares, which avoids double-cutting the
  shared edges.
- **Tabs (`tile_tabs`):** every vertical line, including the tile area's left
  and right edges, is cut as separate segments. Each line leaves a gap of
  `tab_width + kerf`, centred at each tile's mid-height (bd + (j + ½)·p). The
  beam reaches kerf/2 past each end of a cut, so the uncut bridge comes out at
  `tab_width`. The gap uses the tray `kerf` (the real beam width), not
  `tile_kerf`. **Why:** `tile_kerf` may be set below the real kerf on purpose
  to loosen tiles, and using it here would shrink the bridges, or remove them
  once it went negative.
  - **Result:** every tile has two bridges, one to the left and one to the
    right. The horizontal cuts stay unbroken, so rows come apart from each
    other, and each row's ends hold on to the side frame (or, with no border,
    to the surrounding stock).
  - **Segment order and direction:** a line's segments run top to bottom on
    odd lines and bottom to top on even lines (serpentine).
  - **Outer boundary order:** with tabs, the closed outer rectangle becomes
    open lines in this order: top edge (left to right), right edge, bottom edge
    (right to left), left edge.
  - **Notes** report the tabs and say whether rows are held by the frame.
- **Label:** when the block has a label (per-color mode) and a border of at
  least 3 mm, engrave the label (e.g. `C2 #f5be28`) in the top border.
  Its size is 0.6·border and it is left-anchored at (border, 0.8·border).
- **Capacity per sheet:**
  - maxc = ⌊(sheet_w − 2·spacing − 2·border) / p⌋
  - maxr is the same, using the sheet height.
  - If either is below 1, fail.
- **Splitting n tiles into blocks:** repeat until all tiles are placed:
  1. m = min(n, maxc·maxr);
  2. c = min(maxc, ⌈√m⌉);
  3. r = ⌈m / c⌉;
  4. if r > maxr, set c = maxc and recompute r.

  Partial rows are rounded up, and the extra tiles are spares.

## 6. Tile counts

### 6.1 Groups

- **`combined`, or no picture:** one group of `cols × rows` tiles, keyed `all`.
- **`per_color` with a picture:** one group per palette color, keyed
  `c01`, `c02`, …, each with its own sheets.

### 6.2 Spares per group

- want = ⌈exact · (1 + spare_percent/100)⌉
- cap = maxc · maxr
- **cut = max(exact, min(want, ⌈exact/cap⌉ · cap))**

In words, trim spares only when they alone would spill onto another sheet, and
**never cut fewer tiles than the picture needs**. The notes say when spares
were trimmed. **Why:** the user wanted a 20 × 25 kit on one 12-inch (304 mm)
sheet "even if it means fewer spares, but always enough".

### 6.3 Paint counts in combined mode

With a picture and a single group, the tiles cut are split across the colors:
1. each color gets its exact count;
2. plus one spare each, if there are at least as many spares as colors;
3. the remaining spares are shared in proportion to each color's count, using
   the largest remainder.

The notes list `C<i> #rrggbb x<n>`. These numbers fill the "with spares"
column of the pattern legend. **Why:** without step 2, a 5-tile color got no
spares.

## 7. Layout (packing)

Parts are packed onto as few sheets as possible with a **max-rects** packer:
- **Order:** sort parts by longest side (descending), then by area.
- **Free space:** each sheet keeps a list of free rectangles, starting as the
  sheet inset by `spacing` from the top-left. Each part is padded by `spacing`
  on its right and bottom, so parts end up `spacing` apart and from every edge.
- **Placement:** for each part, try existing sheets in order. On each sheet,
  try the part upright and rotated 90°, and choose by **best short side fit**
  (then long side). Ties prefer upright. Use the first sheet with any fit.
- **After placing:** split every intersecting free rectangle into up to four
  remainders, and drop free rectangles contained in others.
- **No fit:** open a new sheet. A part larger than the sheet, even rotated,
  gets its own sheet enlarged to fit, and the notes warn that it is larger than
  the bed.

**Why:** an earlier shelf packer put the 207 × 257 mm base and its walls on two
304 mm sheets. Max-rects with rotation stands two walls beside the base and
fits all of it on one.

**Output grouping:**
- Tray parts are packed together into `<name>_tray_<n>.svg`.
- Each tile group is packed separately into `<name>_tiles_<key>_<n>.svg`.
  **Why:** tiles may use different stock from the tray.

## 8. Kerf test sheet

**Purpose:** find the right kerf for a material before cutting a kit.

**Values:** 7 kerf values centred on the current kerf in 0.05 mm steps
(kerf − 0.15 … kerf + 0.15). Drop negative values and normalise −0.0 to 0.0.

**Pieces:** one pair per value, cut with that value's compensation, in tray
stock. With f = finger_width, t = tray_thickness, margin 4 mm and
width w = 2f + 3·margin:
- **Plate:** w × (margin + t + 9).
  - Two slots, each f × t, at x = margin and x = w − margin − f, starting
    margin from the top.
  - Each slot is widened by q = joint_play/4 on all sides.
  - Each slot gets **0.3 mm extra** across the stock thickness (f × (t + 0.3)).
    **Why:** box fingers enter from an open edge, so only the finger-width fit
    matters there. Without the extra room, a slightly wrong thickness setting
    made every pair read tighter or looser than the box joints would.
  - The value is engraved below the slots (e.g. "0.15"), 3.5 mm high, centred.
- **Tab piece:** a w × 10 body with two tabs f × t on one long edge, at the
  same x positions.
  - Each tab is narrowed by q on each side.
  - The value is engraved centred on the body.

This is the same joint as the box's base-to-wall fit.

**Layout:** one column per value, with the plate above its tab piece, spaced
max(spacing, 2) mm apart. The SVG is sized to the content (about 193 × 39 mm
at the defaults), so it can be placed on scrap.

**Output:** `<name>_kerf_test.svg`. Notes tell the user to set Tray kerf to the
pair that fits snugly by hand, and to re-run centred on the best value if none
fit.

## 9. Pictures

### 9.1 Loading

- **Sources:** an image path (any format Pillow reads), or an emoji reference
  `emoji:<code>[ <name>]`, which is read from the bundle (§9.3).
- **Transparency:** convert to RGBA and composite over `background`, then use
  RGB.

### 9.2 Reducing to the grid

The goal is a cols × rows grid of palette indices, ordered most common color
first.

1. **Fit, don't stretch.**
   - Compute scale = min(cols/w, rows/h).
   - If scale < 1 (the image is larger than the grid), the target is
     nw = min(cols, round(w·scale)) by nh = min(rows, round(h·scale)).
   - Otherwise use the image 1:1. Never enlarge it. **Why:** enlarging pixel
     art by a non-integer factor duplicates pixels unevenly.
2. **Majority color per tile.**
   - When shrinking, resample with a box filter to (nw·sub) × (nh·sub), where
     sub = clamp(⌊min(w/nw, h/nh)⌋, 1, 8).
   - Reduce the colors (step 3).
   - Each tile takes the most frequent color among its sub × sub samples.

   **Why:** averaging blurred AI-generated pixel art into muddy blends.
   Majority voting keeps edges crisp and is fine for photos, since the output
   is a posterized mosaic anyway.
3. **Color reduction.**
   - If there are more unique colors than `colors`, quantize with median cut
     to `colors`.
   - Then greedily merge near-duplicates: visit colors by frequency and fold
     each one into an already-kept color within Euclidean RGB distance
     **24**, if any.

   **Why:** JPEG noise split one flat background into five near-identical
   whites, wasting palette slots.
4. **Pad.** If the result is smaller than the grid, centre it. Fill the rest
   with the most common color on the reduced image's border.
5. **Order the palette** by count (descending), with the RGB value as tie
   break. The pattern holds the palette, the counts, the index grid and a
   human-readable note, for example:
   `Pattern: image 1856 x 2304 px scaled to 20 x 15 (majority color per tile), centred and padded with #1e8cdc`

### 9.3 Grid detection ("Set grid from image")

**Purpose:** infer the native grid of upscaled pixel art. For example, an
AI-generated 1856 × 2304 image drawn on a 40 × 50 grid has cells of about
46 px.

1. **Downscale** (nearest neighbour) so the longer side is at most about
   1000 px; scale results back afterwards.
2. **Edge profiles.** For each axis, take the absolute difference between
   neighbouring pixels. Threshold the grayscale difference at 16. Average
   each column (or row) to get a 1-D edge profile.
3. **Peaks.**
   - Keep positions above 10% of the maximum that are a local maximum
     within ±2.
   - Merge peaks closer than 3 px.
4. **Gaps.** Collect the gaps between consecutive peaks on both axes. With
   fewer than 6 gaps, report no grid.
5. **Choose the cell size.** Score each candidate gap g as the sum over all
   gaps of 1/m, where m = round(gap/g) ≥ 1 and |gap − m·g| ≤ max(1.5, 4%·gap).
   Pick the best-scoring g, preferring larger g on ties.
6. **Refine.** The cell size is the mean of the gaps within tolerance of g.
   If it is under 1.5 px, the image is already one pixel per cell.
7. **Result:** grid = (round(w/cell), round(h/cell)).

**Why gaps rather than a global period fit:** AI pixel art drifts by half a
cell in places, so a strict periodic fit failed on one axis.

**GUI behavior:**
- If a grid is found and is at most 300 per side, set Columns and Rows to it.
- Otherwise keep the column count and set rows = round(cols·h/w) to match the
  image's shape.
- Report the outcome in the log.

### 9.4 Bundled emoji

**Set:** Twemoji (`jdecked/twemoji`, release v17.0.3), 72 × 72 PNGs (4,009
emoji), licensed CC BY 4.0. Fluent Flat and OpenMoji were considered;
Twemoji was chosen for its flat style, easy code-point filenames and
permissive license.

**Storage:** `emoji/twemoji-72.zip`, which contains:
- `72x72/<code>.png`, where `<code>` is the hex code points joined by `-`
  (e.g. `1f33b`, `1f468-200d-1f469`);
- `index.json`, mapping `{code: name}`;
- `LICENSE-GRAPHICS`;
- `SOURCE.txt`.

The program reads the zip directly. **Why:** one 5 MB file instead of 4,009
loose files in the repository, and it works offline.

**`fetch_emoji.py [tag]`** rebuilds the zip from a release tarball. It keeps
only the 72x72 PNGs and the license. Names come from Python's
`unicodedata`, as follows:
- **Regional-indicator pairs** become `flag: xx`.
- **Skin-tone modifiers** become words, e.g. `medium skin tone`.
- **Invisible characters are skipped:** ZWJ, FE0F, keycap and tag characters.
- **Keycaps** are prefixed `keycap`.
- **Sequences** join their parts with ` + `.

A few emoji newer than Python's Unicode data get empty names; they can be
found only by pasting.

**Search** returns at most 80 results:
- **Empty query:** a fixed list of about 70 popular starter emoji.
- **Query containing a character above U+2000 (a pasted emoji):** convert it
  to a code and match exactly, also trying without `fe0f`.
- **Otherwise:** every query word must appear in the name. Rank by:
  1. exact name;
  2. all words matching whole words;
  3. not a skin-tone variant;
  4. name starts with the query;
  5. shorter name;
  6. code.

  **Why:** so "poo" finds 💩 before "poodle" and "spoon".

**Image setting:** an emoji is stored as `emoji:<code> <name>`. Only the token
after `emoji:` up to the first space is significant.

**Picture source:** a pattern records where its picture came from:
- `emoji:<code> <name>` for a bundled emoji;
- for a PNG that pixelkit wrote, the value of its `pixelkit:source` tEXt
  chunk;
- otherwise empty.

Scaled PNGs (§10) store this source, so an edited emoji keeps its credit
after the PNG replaces `emoji:…` as the image.

**Credit:** when the picture's source is an emoji, add
`Emoji graphics: Twemoji, CC BY 4.0 (Twitter, Inc and other contributors)` to
the notes, the log and the bottom of the pattern sheet. The README carries
the same credit.

## 10. Outputs of "Generate"

All files go in `output_dir`, which is created if needed.

| File | When | Contents |
|---|---|---|
| `<name>_tray_<n>.svg` | `tray_style` ≠ none | Packed tray parts |
| `<name>_tiles_<key>_<n>.svg` | always | Tile blocks |
| `<name>_pattern.svg` | with a picture | Printable chart (below) |
| `<picture>_scaled.png` | with a picture | The final grid, one pixel per tile, in palette colors |
| `<name>_notes.txt` | always | Summary, notes, conventions and the settings as JSON |

**Pattern sheet:**
- **Cells:** cell size = min(8, 180/cols, 230/rows) mm, with a 12 mm margin.
  Each cell is filled with its color and shows its number, in black or white
  depending on the color's luminance (0.299R + 0.587G + 0.114B > 140 → black).
- **Grid lines:** thin, with every 5th line bold.
- **Labels:** column and row numbers along the edges, and a title
  `<name>: <cols> x <rows> @ <pixel> mm`.
- **Legend:** swatch, number, hex color, count in the picture, and count with
  spares.
- **Credit:** the emoji credit at the bottom, if applicable.

**Scaled PNG:**
- **Name:** the image's file stem plus `_scaled`, or for an emoji, its name
  slug (non-alphanumerics → `-`) plus `_scaled`. A stem that already ends in
  `_scaled` isn't suffixed again.
- **Never overwrite the source:** if the destination is the image being used,
  skip writing and say so.
- **Round trip:** loading this PNG back with the same grid size must
  reproduce the same picture exactly, since it fits 1:1 and its colors are
  already reduced. Edits carry through, apart from merging and the
  Max colors limit. (The palette colors are pairwise at least 24 apart, so
  repainting with them never merges.)
- **Source chunk:** when the picture has a source, write it as a tEXt chunk
  with the key `pixelkit:source`. Both versions read and write it.

**Summary text** (shown live in the GUI and at the top of the notes):
- picture size in tiles and mm;
- tile pitch;
- tray inside size;
- box outside size, or layered outside size and layer count.

## 11. GUI

**Window:** "Pixel Art Kit Generator", using ttk widgets.
- **Left side:** two columns of labelled sections:
  - first column: Grid, Material, Tray;
  - second column: Sheets, Pattern (optional), Output.
- **Right side:** the summary text, a preview canvas, sheet navigation, a log
  area and the action buttons.

**Fields:** one per setting.
- Booleans are checkboxes. Choices are read-only comboboxes. Everything else is
  an entry.
- **Image** has three buttons: **…** (file dialog), **Emoji…** (picker) and
  **Set grid from image** (§9.3).
- **Background** has **…**, which opens the system color chooser.
- **Folder** has **…**, which opens a directory chooser.

**Live updates:**
- Any field change refreshes the summary immediately.
- About 500 ms after typing stops, the preview rebuilds (debounced).
- On invalid input, keep the last good preview and show the error in the log,
  with no dialog.
- After a rebuild, stay on the same sheet if the sheet list is unchanged.

**Why:** a user changed the background after generating and saw a stale
preview. Auto-refresh keeps the preview truthful.

**Preview canvas:**
- Draws one sheet scaled to fit: sheet background, engrave lines in blue, cut
  lines in red, and text when it is large enough to read.
- ◀ ▶ step through the sheets, with a label `i/N <file stem> (<w> x <h> mm)`.

**Show picture** (a checkbox by the navigation):
- Fills the tray base's tile area with the pattern colors, using the base's
  three recorded corners, so it stays correct when the packer rotates the
  base.
- Turning it on jumps to the sheet containing the base.
- Choosing an image or an emoji turns it on automatically.
- Without a picture, the log explains how to get one.

**Buttons:**
- **Preview:** rebuild now. Errors appear in a dialog.
- **Generate SVGs:** write all outputs (§10), show the notes and the file
  count, and save the settings to `~/.pixelkit.json`. Settings load from this
  file at startup, ignoring unknown or invalid keys.
- **Kerf test:** write the test sheet (§8) and show it in the preview.
- **Open folder:** open the output folder in the OS file browser.

**Emoji picker** (a transient dialog):
- **Search box:** live search, debounced by 200 ms.
- **Thumbnail grid:** 10 per row, using the 72 px PNGs shown at half size.
  They are loaded as Tk PhotoImages from the zip. **Why:** Tk can't reliably
  render color emoji glyphs on macOS.
- **Status line:** the selected emoji's name and code points, or the result
  count.
- **Selecting:** click to select. Double-click, Enter or **Use** confirms.
- **On confirm:** set Image to `emoji:<code> <name>`, set rows = cols (emoji
  are square), turn Show picture on, and log the choice and the credit.

**Pixel editor** (**Edit pixels…**, a pop-out window):
- **Opens on the current mosaic:** the pattern after scaling, padding and
  color reduction, at cell size min(28, 720 / larger side) px, minimum 4.
  Thin cell outlines are drawn at 8 px and up.
- **Palette:** the mosaic's own colors, as numbered swatches; there are no
  new colors. Click a swatch, or press 1–9.
- **Painting:**
  - click or drag to paint;
  - right-click (also Ctrl-click, and Alt-click on the web) picks a tile's
    color;
  - Undo reverts whole strokes (Ctrl/Cmd-Z works too);
  - a live count of tiles per color is shown.
- **Save:**
  - **Python:** write the grid as `<picture>_scaled.png` (§10 naming, with
    the source chunk) in the output folder, overwriting it if it's already
    the image, then set Image to that file.
  - **Web:** the PNG becomes the in-memory picture and is stored like any
    chosen picture; it is also the kit download's `_scaled.png`.
  - **Both:** turn Show picture on, and log what was saved above the
    rebuilt notes.
- **Cancel** or closing the window asks before discarding changes.
- **Immediate previews:** an immediate preview cancels one that is still
  scheduled. **Why:** otherwise a preview scheduled from earlier typing ran
  late and overwrote fresher results.

## 12. Command line

`pixelkit.py --cli [options]`:
- **Settings:** every setting is a flag. Booleans use `--x/--no-x`, choices
  are validated, and other values are typed from their defaults.
- **`--emoji QUERY`:** use the first search hit (a pasted emoji or a name) as
  the picture. Sets rows = cols unless `--rows` was given explicitly. With no
  match, exit with an error suggesting `--find-emoji`.
- **`--find-emoji QUERY`:** print up to 40 matches (`<emoji> <code> <name>`)
  and exit.
- **`--kerf-test`:** write only the kerf test sheet and exit.
- **Normal run:** print the summary and notes, then `wrote <path>` for each
  file. On validation errors, exit non-zero with `error: <problems>`.

## 13. Web version

A TypeScript port in `web/` runs entirely in the browser. It is a static site
(Vite) published to GitHub Pages and works offline through a service worker.

**Structure**
- `src/core/` is pure TypeScript with no browser APIs. It mirrors
  `pixelkit.py` module by module: settings, geometry, parts, packing, SVG,
  pattern, emoji and kit.
- `src/image/` holds browser image I/O and an exact PNG decoder.
- `src/ui/` holds the page: the form, live preview, emoji picker, downloads and
  storage.

**Parity requirement:** for the same settings and pixels, the web version must
write byte-identical SVGs and choose the same palette and grid as
`pixelkit.py`. That takes three things:
- **Python's numerics:** `round()` with half-to-even ties, `%.Nf` formatting
  with exact-tie rounding, and `%g`.
- **Pillow's integer math:**
  - alpha compositing with 7 precision bits;
  - the box resampling filter, which includes +0.5 and excludes −0.5, uses 22
    precision bits, and runs a horizontal pass then a vertical pass;
  - the L conversion weights 19595/38470/7471;
  - nearest-neighbour resizing as `ImagingScaleAffine` does it: sample
    positions start at scale / 2 and step by *adding* scale, a running sum,
    then truncate. Computing (x + ½)·scale per pixel rounds differently at
    fractional scales.
- **A step-for-step port of Pillow's median-cut quantizer** (`libImaging/Quant.c`):
  - a hash scale that keeps it at 65,536 entries or fewer;
  - a max-heap ordered by pixel count, with Pillow's exact sift order;
  - the split axis chosen by luminance-weighted range (77/150/29);
  - a split at the pixel-count median;
  - box means with +0.5 rounding;
  - each color mapped to its nearest palette entry, searched from its own box.

**Images in the browser:**
- **PNGs:** decoded by a small built-in decoder (including Adam7 interlacing),
  because canvas decoding premultiplies alpha and rounds the edge pixels of
  emoji.
- **Other formats:** decoded by the browser. Browsers use libjpeg-turbo for
  JPEG, as Pillow does.
- **Emoji:** loaded from the same `emoji/twemoji-72.zip`, which the build copies
  into the site.

**Platform differences:**
- **Output:** downloads, as one zip plus individual files, instead of an output
  folder.
- **Settings:** kept in `localStorage` with JSON export and import, instead of
  `~/.pixelkit.json`.
- **Last picture:** kept in IndexedDB.
- **No CLI.**

**Tests:** `web/tests/` holds:
- the §14 acceptance checks;
- the byte-for-byte SVG parity tests against the Python CLI;
- exact picture parity, for two emoji, the sample JPEG and a gradient;
- the PNG decoder checked against `pngjs` on all 4,009 emoji.

The parity tests skip themselves when `python3` with Pillow isn't available.

## 14. Acceptance checks

A reimplementation should pass these checks.

1. **Defaults:** generating with all defaults (20 × 25 tiles) gives a
   207 × 257 × 10 mm box, with 3 corner segments and 16/20 base fingers per
   long/short edge. Base fingers come out at seg + kerf and wall slots at
   seg − kerf in the cut geometry, so both are nominal after cutting.
2. **Kerf test:** at kerf 0.15, finger 6 and thickness 3, the slot cut paths
   are 5.85 × 3.15 mm (thickness plus 0.3 mm room, less the kerf) and the tab
   cut paths are 6.15 mm wide.
3. **Packing:** a 20 × 25 kit at 10 mm on a 304 × 304 mm sheet gives one tray
   sheet and one tile sheet (529 tiles, a 23 × 23 block).
4. **Spare trimming:** 27 × 28 tiles at 10% spares on 304 mm gives 784 tiles
   (trimmed). 30 × 30 gives 953 tiles on 2 sheets (not trimmed).
5. **Upscaled art:** a 10× upscaled 20 × 25 pixel-art PNG reduces to exactly
   the original colors and counts.
6. **Fit and pad:** an 800 × 600 image on a 20 × 25 grid gives a 20 × 15
   picture padded top and bottom. A 16 × 16 image on a 20 × 25 grid is used
   1:1 and padded.
7. **Grid detection:** the sample `sample-images/poop-emoji.jpeg`
   (1856 × 2304) detects as 40 × 50, and its JPEG-noisy background merges into
   one color.
8. **Emoji search:** `emoji_search("poo")[0]` is `1f4a9`, and a pasted 🌻
   resolves to `1f33b`.
9. **Scaled PNG round trip:** reloading `_scaled.png` reproduces the grid
   exactly, and generating from it in the output folder doesn't overwrite it.
10. **Rotated base:** the Show picture overlay lines up with the engraved grid
    when the base is rotated 90°.
11. **Grid limit:** a 1856 × 2304 grid is rejected by validation rather than
    hanging the program.

## 15. Paint by number

With `kit_type = paint_by_number`, pixelkit makes one numbered board instead
of a tray and tiles. The picture is processed exactly as for a mosaic (§9),
except for detail zones, then:

**Detail zones** are boxes, as fractions of the grid: cell ranges are
⌊x0·cols⌋, ⌊y0·rows⌋ to ⌈x1·cols⌉, ⌈y1·rows⌉. They apply to paint by number
only. Each is processed in order, after color reduction and before the
majority vote, over the samples it covers (with merge distance 24):

1. Pool the zone's raw colors in bins of 8 levels per channel, taking each
   bin's mean rounded down, in bin order.
2. Skip bins within merge distance of a known color. For each other bin, group
   the bins within merge distance of it; a candidate is the group's weighted
   mean, with the group's sample count.
3. Up to `pbn_zone_colors` times, add the candidate farthest from all colors
   so far. It must be at least merge distance away and have at least
   max(1, ⌊min cells for pbn_zone_area / 2⌋) · sub² samples. Earlier candidates
   win ties.
4. Any sample at least merge distance from its assigned color moves to the
   nearest known color. Other samples stay put, so flat areas and the zone's
   edge don't change.

Why this and not a second quantizer: a highlight is a tiny share of a zone, so
median cut never gives it a box, and it is often near a palette color already
but was assigned a dark one. In step 2 of the region stages below, a region
with at least half its cells in zones uses thresholds from `pbn_zone_area` and
`pbn_zone_width`. A number centred in a zone may shrink to half of
`pbn_text_min`.

1. **Regions:** 4-connected cells of one color, labelled in row-major
   discovery order. The flood fill visits up, down, left, right.
2. **Absorbing small and thin regions:**
   - **Thresholds:**
     min cells = max(1, ⌈min_area / pixel² − 1e-9⌉) and
     min depth = max(1, ⌈(min_width / pixel + 1) / 2 − 1e-9⌉).
     A region's widest square is (2·depth − 1) cells.
   - **Removed regions:** those with fewer cells than min cells, or whose
     deepest cell (§15.7's 8-neighbour depth) is below min depth.
   - **Regrowth:** a 4-neighbour multi-source BFS starts from every kept cell
     in row-major order, and each removed cell takes the color of the kept
     region that reaches it first. If nothing or everything would be removed,
     the grid is left unchanged.
   - **Why:** at high resolution, JPEG artifacts and anti-aliasing leave
     1–2-cell fringes of in-between colors along every boundary. Merging a
     fringe whole into one neighbour would paint that color along every edge
     it touches; growing from both sides splits it down the middle. A speck
     inside one region simply fills in. The thresholds are in mm, so they mean
     the same at any cell size.
   - **Limit:** paint-by-number grids may be up to 1,500 cells per side,
     against 300 for mosaics. Mosaic-only validation (kerf, tile kerf, tabs,
     fingers) doesn't apply to boards, which may use cells smaller than the
     kerf.
3. **Renumbering:** colors are renumbered by cell count (descending, then
   RGB), as for mosaics, because merging changes the counts. Colors left with
   no cells are dropped.
4. **Outline chains:**
   - **Edges:** a cell edge is a boundary edge where the colors on its two
     sides differ, or on the picture border.
   - **Junctions:** grid vertices with 1, 3 or 4 boundary edges, plus the four
     picture corners.
   - **Chains:** each chain runs from a junction to a junction, visiting
     junctions row-major and directions in the order right, down, left, up.
     Leftover edges form closed loops, each starting at its first vertex in
     row-major order.
   - **Result:** every boundary edge is in exactly one chain, so shared
     borders are drawn once.
5. **Smoothing** (`pbn_smooth`):
   - **Method:** take the midpoints of a chain's unit edges, then two passes
     of Chaikin corner cutting (¼–¾ points).
   - **End points:** open chains keep their end points, so chains still meet
     at junctions and the picture border stays straight.
   - **Clean-up:** afterwards, points collinear with their neighbours are
     dropped (tolerance 1e-9).
   - **Simplification:** Douglas–Peucker with tolerance pbn_simplify / pixel
     (in cells):
     - **Method:** iterative; distances are compared as c² / |ab|² against
       tol², where c is the cross product. That avoids square roots, so both
       versions agree to the bit. A zero-length segment uses the squared
       distance to its start.
     - **Closed loops:** split at the first point and the point farthest
       from it.
     - **Why:** fine grids make long staircase chains with tens of thousands
       of points; a 0.05 mm tolerance cuts them a lot with no visible
       change.
   - **Why:** smooth curves read as classic paint-by-number, and cutting only
     within half a cell keeps shapes recognisable.
6. **Fills** (preview only):
   - **Sides:** each chain records the regions on its two sides. Side A is
     along the normal (dy, −dx) of its first edge.
   - **Outlines:** each region's outline is linked from the smoothed chains
     that border it.
   - **Paths:** each color is one even-odd path, so the fills meet the drawn
     lines exactly.
7. **Numbers:**
   - **Spot:** one per region, at the cell with the greatest 8-neighbour depth
     from the region's edge, found by multi-source BFS from edge cells. Ties
     go to the cell nearest the region's centroid (squared distance, computed
     with `dx*dx`), then the first in row-major order.
   - **Size:** with room = (2·depth − 1)·pixel, size = min(text_max, 0.8·room,
     0.8·room / (0.6·digits)). Below `pbn_text_min` the minimum is used, and
     the notes count those regions.
   - **Text:** the color's number, centred, with the baseline at the cell
     centre + 0.35·size.
8. **Board:**
   - **Size:** cols·pixel + 2·margin by rows·pixel + 2·margin.
   - **Groups and colors:**
     - `outlines`: stroke `#0000ff`, 0.1 wide when engraving, or `#000000` at
       `pbn_line_width` with round joins when printing.
     - `numbers`: fill `#00e000` when engraving, or `#555555` when printing.
     - `cut`, engrave only: a red rectangle around the board.
   - **Why separate colors:** so each element can get its own laser settings.
     The colors match LightBurn's default layers 01, 03 and 02.
   - **Not packed:** the board is a single piece, and the notes warn if it is
     larger than the sheet.
9. **Files:**
   - `<name>_pbn.svg`: the board;
   - `<name>_pbn_preview.svg`: the fills plus 0.15 `#333333` outlines;
   - `<name>_pbn_key.svg`: per color, a swatch, the number, hex, regions and
     cells, and the credit if needed;
   - `<picture>_scaled.png`: the merged grid;
   - the notes file.

   No tray, tile or kerf-test files are made.
10. **Notes** report:
    - cells, board size, use, the number of regions and colors, and how many
      regions were merged;
    - how many numbers are below the minimum size;
    - the layer colors (engrave) or line color and width (print);
    - any sheet-size warning.
11. **UI:** a Kit type setting, either Mosaic tiles or Paint by number.
    Settings used by only one kit type are greyed out (desktop) or hidden
    (web); a web section disappears when none of its settings apply. The web
    version also hides the Kerf test button. The previews draw the board in
    its layer colors, and Show picture fills the smooth regions (web) or the
    cells (desktop).

12. **Canvas** (`canvas` other than `off`):
    - **Presets:** 8×10, 9×12, 11×14, 12×12, 12×16, 16×20 and 18×24 in, and 20×30,
      30×40 and 40×50 cm. The sizes are portrait (width, height) in mm, e.g.
      203.2 × 254.0; landscape swaps them. Custom uses `canvas_width` ×
      `canvas_height` as given.
    - **Grid:** cols = ⌊(W − 2·margin) / pixel⌋ and rows likewise. These
      replace the cols and rows settings everywhere: pattern, board, notes,
      summary and scaled PNG.
      **Why** this direction: the cell size sets how fine the painting is, so
      it stays as chosen.
    - **Limits:** a grid over 1,500 per side is rejected with a canvas-specific
      message, as is a canvas with no room for a cell.
    - **Board:** exactly W × H, with the grid centred:
      offset ((W − cols·pixel) / 2, (H − rows·pixel) / 2).
    - **Picture:** `load_pattern` gets `fit`:
      - **`fill`:** first crop the picture, centred, to the grid's aspect:
        crop width round(h·cols / rows) when the picture is wider than the
        grid, else crop height round(w·rows / cols), starting at
        ⌊(size − crop) / 2⌋. The result is then resized to exactly
        cols × rows.
      - **`fit`:** scale to fit inside the grid and pad as usual.
      - **Both, in canvas mode:** a picture smaller than the grid is enlarged
        with nearest-pixel sampling, so no new colors appear.
    - **Notes** add the canvas name and size, the fit, and the side and top
      margins.
    - **UI:**
      - Columns and Rows are disabled and show the computed grid. The web
        restores the user's own values when the canvas is turned off.
      - The custom size applies only to `custom`, and landscape only to
        presets.

**Checks:**
- A 3 × 3 grid with one odd center cell (min cells 2) becomes one color. A
  one-cell fringe between two wide areas is split between them.
- On the sample JPEG at 40 × 50:
  - no region is smaller than the minimum;
  - every region has exactly one number, inside it, showing its color;
  - every boundary edge appears in exactly one chain;
  - every color has fill outlines.
- **Parity:** board, preview, key and notes are byte-identical to
  pixelkit.py for:
  - an emoji (engrave, smoothed);
  - pixel art (print, steps, no absorbing);
  - the sample JPEG;
  - a photo-like gradient at 80 × 80;
  - aggressive absorbing (min area 600 mm², min width 10 mm, margin 0);
  - a high-resolution sample JPEG on 8×10 in at 0.4 mm;
  - canvases: 8×10 in fit with an enlarged emoji, 12×16 in landscape fill,
    custom 150 × 100 mm fill, and 30×40 cm fit in print mode;
  - detail zones on a padded fit and on a fill with colors added.
- **Detail zones:** a small light spot on a dark field survives inside a zone
  and is absorbed without one; cells outside zones keep their colors; a region
  mostly outside uses the normal limits; mosaics ignore zones; malformed zone
  text is reported.
- **Canvas grid:** 8×10 in at 3 mm gives 61 × 78 cells on a 203.2 × 254 mm
  board.
