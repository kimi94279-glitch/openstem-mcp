---
name: lab-report
description: Write a science or engineering lab report from the user's measured data - plots with error bars and fitted curves, uncertainty and unit checks, and a docx or pdf report. Use for 실험 보고서, 결과 보고서, 예비 보고서, 측정값 그래프, 기울기 구해줘.
---

# Lab report

Use only data the user provides. Never invent or "clean up" measurements; if something is missing, ask.

1. Put the data in `plot_data` (`series` with x, y, optional yerr/xerr, or paste the table as `table` and pick columns by header). Choose `fit` from the theory (linear, poly2, exponential, power, log). Report the fit equation with ± standard errors and R², and mention points flagged as outliers.
2. Derive the quantity the experiment measures (for example g from a slope). Check the algebra with `verify_math` and the units with `check_units`.
3. Compare with the accepted value: percent error and likely sources of error.
4. Write the sections in the user's language: purpose, theory, method, data and results (with the plot), discussion, conclusion. Use the section names the user's course expects if they give them.
5. Export with `export_document` (`format="docx"` unless the user wants pdf) and give the file path.

If the openstem tools are not available in this session (for example in claude.ai chat, where only the skills load), say so and point the user to the install guide: https://github.com/kimi94279-glitch/openstem-mcp#설치. Then you can still write the report text from the user's data, but compute by hand, say there is no plot file, and mark the numbers as unverified.
