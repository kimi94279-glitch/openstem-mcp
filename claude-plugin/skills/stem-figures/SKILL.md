---
name: stem-figures
description: Draw academic figures with openstem - circuits, free-body diagrams, vectors, geometry, flowcharts and block diagrams, function graphs, measured-data plots with fitted curves, and molecules or reactions. Use when the user asks to draw, plot or visualize something for study (그려줘, 회로도, 자유물체도, 그래프, 구조식, 다이어그램).
---

# STEM figures

Pick the tool by what is being drawn:

| Figure | Tool |
|---|---|
| Circuit, free-body diagram, vectors, geometry, flowchart, block or state diagram | `get_template` → adapt → `render_tikz` |
| y = f(x) | `plot_function` |
| Measured data, error bars, fitted line or curve | `plot_data` |
| Molecule or reaction | `render_molecule` (SMILES, not a name) |

## TikZ figures

1. Call `get_template` with no name to see the list, then read the closest example. Editing a checked example is far more reliable than writing TikZ from scratch.
2. Change coordinates, labels and parts; keep the structure. Korean labels work as they are.
3. Call `render_tikz`, then look at the preview image yourself: missing parts, wrong connections, overlapping labels, arrow directions, sign conventions.
4. On an error, read the `!` line and `l.N` (line N of your code), fix that line and render again. Stop after 3 tries and show the best result with what is still off.

## Other figures

- `plot_function`: read the warnings (asymptotes, jumps, domain gaps) and mention them; use `"2*pi"` style ranges.
- `plot_data`: only data the user gave; report the fit equation with ± errors and R².
- `render_molecule`: compare the returned formula and molecular weight with the intended molecule before showing it.

Tell the user where the files were saved (SVG and PDF for documents, PNG for chat). If another skill or tool is better at layout, for example a slide or document skill, still make the figure here and insert the saved file there.

If the openstem tools are not available in this session (for example in claude.ai chat, where only the skills load), say so and point the user to the install guide: https://github.com/kimi94279-glitch/openstem-mcp#설치. Then you may show TikZ code the user can compile elsewhere, but say clearly that it was not rendered or checked.
