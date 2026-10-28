# pixelkit

Generate laser-cut pixel art kits as SVG files: a tray to hold the picture,
sheets of square tiles, and a printable color-by-number pattern.

Give it a grid size and your material, optionally a picture (any image file, or
an emoji from the bundled Twemoji set), and it writes cut-ready SVGs with kerf
compensation built in.

There are two versions with the same features and the same output:

- **In the browser:** <https://osbock.github.io/pixelkit/>. Nothing to install,
  and it works offline after the first visit. The source is in [`web/`](web/).
- **Python desktop app:** `pixelkit.py`, with a Tk GUI and a command line.
  See [Requirements](#requirements) and [Usage](#usage).

## Web version

Open <https://osbock.github.io/pixelkit/>, or run it locally:

```bash
cd web
npm install
npm run dev
```

It works like the desktop app, with a few differences:

- **Download kit (.zip)** saves every file of the kit in one zip. The files
  are also listed for downloading one at a time.
- Choose a picture with **Choose image…**, by dropping an image anywhere on the
  page, or with **Emoji…**.
- **Edit pixels…** opens the same pixel editor as the desktop app. Saving
  makes the edited picture the image, and it goes in the kit download as
  `<picture>_scaled.png`.
- The preview updates as you type. Scroll to zoom, drag to pan, and
  double-click to fit.
- Settings are kept in the browser, and so is the last picture you chose.
  **Export settings** and **Import settings** move a kit's settings between
  machines.

The web version is a TypeScript port of `pixelkit.py`, checked against it by
tests:

- The test suite runs the Python CLI and compares every SVG byte for byte
  across tray styles, joints, tile layouts and the kerf test.
- Emoji, JPEG and photo pictures must give exactly Pillow's palette and grid.
- Run the tests with `npm test` in `web/` (the parity tests need `python3` with
  Pillow).

Every push to `main` runs the tests and publishes the site to GitHub Pages
(`.github/workflows/pages.yml`).

## What you get

- **Tray**, in one of two styles:
  - `box`: a base with finger joints on all four edges plus four finger-jointed
    walls.
  - `layered`: a flat base plus a glued stack of frame rings.

  The base can be engraved with the tile grid and with each tile's color number.
- **Tile sheets:** square tiles cut on a shared grid, with one cut line between
  neighbouring tiles. By default all tiles go on one grid, to be painted or
  stained after cutting. Alternatively there is one set of sheets per color,
  for colored stock.
- **Pattern sheet** (when a picture is given): a numbered color grid with a
  legend of tile counts per color, including spares.
- **Scaled image** (when a picture is given): `<picture name>_scaled.png`,
  with one pixel per tile in the final colors. It is meant for touching up
  in any paint program, then loading back as the picture.
- **Notes file:** dimensions, tile counts, paint counts per color and the
  settings used.
- **Kerf test sheet** (the **Kerf test** button or `--kerf-test`): seven
  pairs of pieces in tray stock, for kerf values from 0.15 mm below to
  0.15 mm above the current setting, in 0.05 mm steps. Each pair is a plate
  with two slots and a piece with two matching tabs, engraved with its kerf
  value. The joint is the same as the box's base-to-wall joint and uses the
  current finger width and joint play. The slots have 0.3 mm extra room
  across the stock thickness, so the test checks only the finger-width fit
  and isn't thrown off if the thickness setting is slightly wrong.

All parts are packed onto sheets of your laser bed size. Spare tiles are trimmed
when they alone would need another sheet, but the cut never drops below what
the picture needs.

## Requirements

- Python 3.9 or newer, with Tkinter (included with the python.org installers
  and Homebrew's `python-tk`)
- [Pillow](https://pypi.org/project/Pillow/), needed only for pictures and
  emoji:

  ```bash
  pip install -r requirements.txt
  ```

## Usage

### GUI

```bash
python3 pixelkit.py
```

1. Set the grid, material thickness, and the tray and tile kerf.
2. Choose a picture with **…** (an image file) or **Emoji…** (the bundled
   emoji).
3. **Set grid from image** detects the grid of upscaled pixel art. For an
   AI-generated 1856 × 2304 px image drawn on a 40 × 50 grid, it sets
   40 × 50.
4. **Edit pixels…** opens the mosaic in a pop-out editor. Pick one of the
   mosaic's colors and click or drag to repaint tiles; right-click a tile to
   pick up its color. **Save** writes `<picture>_scaled.png` to the output
   folder and makes it the image.
5. The preview on the right updates as you change settings, and ◀ ▶ step
   through the sheets. **Show picture** draws the finished picture in the
   tray; it turns on by itself when you choose an image or an emoji.
6. **Generate SVGs** writes the files to the output folder.

The GUI remembers your last settings in `~/.pixelkit.json`.

### Command line

```bash
python3 pixelkit.py --cli --cols 24 --rows 24 --image my-art.png
python3 pixelkit.py --cli --emoji 🌻 --background "#1e8cdc" --sheet-width 304 --sheet-height 304
python3 pixelkit.py --cli --find-emoji heart
python3 pixelkit.py --cli --kerf-test --tray-thickness 3 --kerf 0.15
python3 pixelkit.py --cli --help
```

## Settings

All lengths are in mm.

| Setting | Default | Meaning |
|---|---|---|
| `cols`, `rows` | 20, 25 | Picture size in tiles |
| `pixel` | 10 | Finished tile size |
| `tile_thickness`, `tray_thickness` | 3, 3 | Stock thickness for the tiles and for the tray |
| `kerf` | 0.15 | Tray kerf: the width of the laser cut in tray stock. Tray outlines are offset by kerf/2 so joints fit. |
| `tile_kerf` | 0.15 | Tile kerf: tiles are spaced at pixel + tile kerf, so each comes out at pixel + tile kerf − your real kerf. Set it lower than your real kerf (down to −1) for smaller, looser tiles: each 0.05 mm less makes every tile 0.05 mm smaller. |
| `tray_style` | box | `box`, `layered` or `none` |
| `tile_clearance` | 1.0 | Total slack between the tiles and the tray wall |
| `lip` | 4 | How far the wall stands above the tiles. The wall height is tray thickness + tile thickness + lip, so 10 mm by default. |
| `finger_width` | 6 | Target finger (tab) length for box joints |
| `joint_play` | 0 | Extra gap per finger joint: positive is looser, negative is tighter |
| `frame_width` | 10 | Frame border width, `layered` style only |
| `engrave_grid`, `engrave_numbers` | on, off | Engrave the tile grid and the color numbers on the base |
| `sheet_width`, `sheet_height` | 304, 304 | Laser bed or stock size (304 mm is about 12 in) |
| `spacing` | 3 | Gap between parts and from the sheet edges |
| `tile_border` | 5 | Waste frame around each tile block (0 = none) |
| `tile_tabs` | off | Leave small uncut bridges on the vertical tile cuts, so tiles stay in the sheet until you snap them out |
| `tab_width` | 0.5 | Width of each bridge. The gaps are sized from the tray kerf, so bridges keep this width whatever the tile kerf. |
| `spare_percent` | 5 | Extra tiles, trimmed if they would need another sheet |
| `tile_sheets` | combined | `combined` (cut, then paint) or `per_color` (colored stock) |
| `image` | | Image file, or `emoji:<code>` from the picker |
| `background` | #ffffff | Color used for transparent parts of the picture |
| `colors` | 8 | Maximum number of colors. Near-identical colors are merged. |

**Pictures larger than the grid** are scaled down to fit without distortion,
and any leftover rows or columns are filled with the picture's border color.
Each tile takes the most common color in its area, so pixel art stays crisp.

## Output conventions

- SVG units are mm. Each file's size is the sheet size.
- **Red `#ff0000` = cut**, **blue `#0000ff` = engrave/score**. Hairlines are
  0.1 mm.
- Run engrave layers before cut layers.
- Engraved color numbers are SVG `<text>`. If your laser software doesn't
  import text, convert it to paths there.

## Tips

- Cut the kerf test first, in the stock you'll use for the tray. Push each
  tab piece into the plate above it, then set **Tray kerf** to the value of
  the pair that fits snugly by hand. If they're all too loose or too tight,
  set Tray kerf to the best one and cut the test again. Start **Tile kerf** at
  the same value.
- If the tiles are too tight in the tray, either lower **Tile kerf**, which
  shrinks every tile, or raise **Tile clearance**, which enlarges the tray
  opening. Lowering the tile kerf keeps the gaps between tiles even, and the
  play adds up along a row: at 0.05 mm smaller per tile, a 20-tile row gets
  1 mm of play on top of the clearance.
- To check the thickness setting, look at a cut box corner. Fingers that
  stick out proud mean the setting is thicker than the stock; recessed
  fingers mean it's thinner. Soft plywood often measures thicker with
  calipers than it behaves in a joint.
- To find your real kerf, measure a cut tile with calipers:
  real kerf = pixel + tile kerf − measured tile size. Measure the stock too
  and enter its real thickness. A tray kerf that fits best above your real
  kerf usually means the stock is thicker than the thickness setting, since
  the extra compensation is making up for tight slots.
- To fix individual tiles, use **Edit pixels…**. You can also edit
  `<picture name>_scaled.png` in any paint program and choose it as the
  image, keeping the same grid size; it is then used exactly as drawn. The
  exceptions are colors closer than about 24 RGB steps, which are merged, and
  a color count over Max colors. An edited emoji keeps its Twemoji credit,
  because the PNG records its source.
- Turn on **Tabs between tiles** if cut tiles fall through your laser bed or
  get blown around. Each tile keeps a 0.5 mm bridge to its left and right
  neighbours, at mid-height. The horizontal cuts are left whole, so each row
  comes away as a strip, and the ends of each row hold on to the side frame.
  Snap the tiles apart by hand and sand the nubs if needed.
- Then cut a small kit (for example 6 × 6 tiles) to tune `tile_clearance`
  for how the tiles fit in the tray.
- Low walls with few corner segments become butt joints. The notes file says
  when that happens, so glue those corners.

## Emoji

The **Emoji…** picker and `--emoji` use the bundled set in
[`emoji/twemoji-72.zip`](emoji/): 4,009 Twemoji v17.0.3 images at 72 × 72 px,
with a name index for searching. Nothing is downloaded at run time. To update
to a newer Twemoji release, run:

```bash
python3 fetch_emoji.py v17.0.3
```

(substituting the new release tag).

Kits made from an emoji must credit Twemoji. pixelkit adds the credit line below
to the pattern sheet and the notes file automatically.

## Credits

**Emoji graphics:** [Twemoji](https://github.com/jdecked/twemoji), Copyright
Twitter, Inc and other contributors, licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The graphics are
included unmodified in `emoji/twemoji-72.zip`, together with their license file.

## License

pixelkit (the code and documentation) is © 2026 Kevin Osborn, released under
the [MIT License](LICENSE).

The bundled Twemoji graphics are not covered by the MIT License. They remain
under CC BY 4.0, so anything you make or share with them must keep the credit
in [Credits](#credits).
