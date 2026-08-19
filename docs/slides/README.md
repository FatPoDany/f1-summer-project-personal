# Apex feature review deck

`apex-features.tex` is a Beamer deck (16:9, pdflatex) covering every screen of
the current build. Every figure in `figures/` is a real screenshot of the app,
rendered headless.

## Rebuild the PDF

```bash
pdflatex apex-features.tex && pdflatex apex-features.tex
```

Needs only stock LaTeX: beamer, graphicx, xcolor, booktabs, array, tikz,
helvet, caption, microtype.

## Regenerate the screenshots

`make-screenshots.py` drives the real application through every screen and
saves one PNG per feature. It uses the local IBM Granite 4.1 server for the
coaching screens, so start that first; without it the script still runs and the
live advice panel stays in its honest offline state.

```bash
QT_QPA_PLATFORM=offscreen \
APEX_WORKSPACE=/tmp/apex-shots-ws \
APEX_RESEARCH_MODE=1 \
python docs/slides/make-screenshots.py docs/slides/figures
```

`APEX_RESEARCH_MODE=1` is what exposes the facilitator-only Robot Pilot screen.

The Collect Data and Robot Pilot screens are shown in their completed states:
capturing them live would need a running TORCS session and a full synthetic
batch. Every other screen is captured from real data, and the coaching content
is a genuine model response, not a mock. Slide 20 of the deck records this.
