#!/usr/bin/env python3
"""
fetch_emoji.py - (re)build the bundled emoji set used by pixelkit's picker.

Downloads a Twemoji release from GitHub, keeps only the 72x72 PNGs and the
graphics license, and writes them with a searchable name index into
emoji/twemoji-72.zip next to this script. pixelkit reads that zip directly,
so a checkout works offline. Only needed when updating to a new release:

    python3 fetch_emoji.py            # default version
    python3 fetch_emoji.py v17.0.3

Twemoji graphics: Copyright Twitter, Inc and other contributors,
licensed CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/).
"""
import io
import json
import sys
import tarfile
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

VERSION = "v17.0.3"
URL = "https://github.com/jdecked/twemoji/archive/refs/tags/{v}.tar.gz"
OUT = Path(__file__).resolve().parent / "emoji" / "twemoji-72.zip"

SKIN = {0x1F3FB: "light skin tone", 0x1F3FC: "medium-light skin tone", 0x1F3FD: "medium skin tone",
        0x1F3FE: "medium-dark skin tone", 0x1F3FF: "dark skin tone"}
SILENT = {0x200D, 0xFE0F, 0x20E3}  # joiner, emoji presentation, keycap


def emoji_name(codepoints):
    """Readable, searchable name for a code point sequence, e.g.
    'sunflower', 'flag: us', 'man + medium skin tone'."""
    if all(0x1F1E6 <= c <= 0x1F1FF for c in codepoints):
        return "flag: " + "".join(chr(c - 0x1F1E6 + ord("a")) for c in codepoints)
    parts = []
    for c in codepoints:
        if c in SILENT:
            continue
        if c in SKIN:
            parts.append(SKIN[c])
        elif 0xE0020 <= c <= 0xE007F:  # tag characters spell subdivision flags (gbeng)
            continue
        else:
            parts.append(unicodedata.name(chr(c), "").lower())
    name = " + ".join(p for p in parts if p)
    if 0x20E3 in codepoints:
        name = "keycap " + name
    return name


def main(version):
    print(f"Downloading Twemoji {version} ...")
    with urllib.request.urlopen(URL.format(v=version)) as r:
        data = r.read()
    print(f"  {len(data) / 1e6:.1f} MB, extracting 72x72 PNGs")
    index = {}
    OUT.parent.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar, \
            zipfile.ZipFile(OUT, "w", zipfile.ZIP_STORED) as z:
        for m in tar:
            parts = m.name.split("/")
            if not m.isfile():
                continue
            if parts[1:3] == ["assets", "72x72"] and parts[-1].endswith(".png"):
                stem = parts[-1][:-4]
                z.writestr(f"72x72/{parts[-1]}", tar.extractfile(m).read())
                index[stem] = emoji_name([int(h, 16) for h in stem.split("-")])
            elif parts[1:] == ["LICENSE-GRAPHICS"]:
                z.writestr("LICENSE-GRAPHICS", tar.extractfile(m).read())
        z.writestr("index.json", json.dumps(dict(sorted(index.items())), indent=0))
        z.writestr("SOURCE.txt", f"Twemoji {version}\n{URL.format(v=version)}\n"
                                 "Graphics licensed CC BY 4.0, Copyright Twitter, Inc and other contributors\n")
    named = sum(1 for n in index.values() if n)
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB): {len(index)} emoji, {named} with names")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else VERSION)
