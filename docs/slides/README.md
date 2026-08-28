# Apex slide decks

Two Beamer decks (16:9, pdflatex). Neither is checked in as a PDF you should
trust without rebuilding — the figures come from the app, and the app moves.

| deck | for | figures |
|---|---|---|
| `apex-features.tex` | a tour of every screen in the build | `figures/*.png` |
| `apex-progress.tex` | **study progress, for a supervision meeting** — what the instrument measures, what the first three participants showed, what is still missing | `figures/progress/*.png` |

## Build a PDF

```bash
cd docs/slides
pdflatex apex-progress.tex && pdflatex apex-progress.tex
```

Twice, so the frame numbers settle. Needs only stock LaTeX: beamer, graphicx,
xcolor, booktabs, array, tikz, helvet, microtype. There is no TeX installation
on the AVD session host and MSI installs are policy-blocked there, so this is
normally built on Overleaf: upload `apex-progress.tex` together with the
`figures/` folder and it compiles unchanged.

## Regenerate the progress figures

```bash
python docs/slides/make-progress-screenshots.py docs/slides/figures/progress
```

It copies the real study sessions into a throwaway workspace
(`~/apex-deck-ws` by default, given as the optional second argument) so nothing
it does can touch the collected data, then drives the real application through
each screen.

Deliberately **not** run under `QT_QPA_PLATFORM=offscreen`: that plugin ships no
font engine on Windows, so every label renders as an empty box. The real
platform plugin is used and each window gets `WA_DontShowOnScreen`, which lays it
out and paints it into a pixmap without ever mapping it to the desktop — so it
neither steals focus nor flashes on screen.

The script borrows three things from the installed package at `~/repos/Apex`,
because a source checkout has none of them and the honest "not available" states
they produce would misrepresent the build being presented:

- `APEX_FFMPEG` — otherwise the review window cannot cut its clip
- `TORCS_PREFIX` — otherwise Collect Data reports the simulator missing
- `LLAMA_SERVER_BIN` + `GRANITE_MODEL_PATH` — otherwise the Garage shows
  `AI UNAVAILABLE` instead of the real `ANALYSED` state

The coaching shown is a genuine IBM Granite 4.1 report, **restored from the audit
record it wrote at the time** rather than regenerated for the deck, so the slide
shows what a participant was actually told.

Two figures are cropped, and the deck says so on the slide:

- `04-review.png` stops before the video pane. The clip is cut and loaded, but a
  video surface is composited by the graphics stack rather than painted into the
  widget, so `QWidget.grab()` returns black for it on a window that was never
  mapped. A black rectangle reads as a broken feature.
- `02-garage.png` is cropped to the populated rows.
