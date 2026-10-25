# Bundled emoji

`twemoji-72.zip` holds the 72x72 PNGs from [Twemoji](https://github.com/jdecked/twemoji)
v17.0.3, plus `index.json` (code point → name, used by the picker search) and the
graphics license. pixelkit reads the zip directly, so no download is needed.

Twemoji graphics are Copyright Twitter, Inc and other contributors, licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Kits made from them must
credit Twemoji; pixelkit adds the credit line to the pattern sheet and notes file.

To update to a newer release, run `python3 fetch_emoji.py [tag]` from the parent folder.
