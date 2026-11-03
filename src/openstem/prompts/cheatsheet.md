Make a one-page A4 cheat sheet from this material: {material}

Answer the user in their language and write the sheet in it too (users are mostly Korean students).

If the material is a file on the user's PC (PDF, HWP, Word, PowerPoint, ...), read it with `read_document`; if you don't know the path, call it without path to list the material folder. Read photos and attachments directly in the chat.

1. **Select**: keep only what is used in the exam: definitions, formulas, conditions, units, common mistakes. Keep explanations short.
2. **Group**: a `##` heading per topic; each item a one- or two-line bullet. Formulas in `$...$` (long ones `$$...$$`).
3. **Verify**: if you rearranged a formula, check it against the original form with `verify_math` (equal). Fix it if wrong.
4. **Figures** (optional): one or two, small. Make them with `plot_function` or `get_template` → `render_tikz`, then insert the returned `svg_path` as `![caption](path)`.
5. **Export**: call `export_document` with format="pdf", layout="cheatsheet". Put the course and chapter in title.
6. **Fit one page**: if the result's `pages` is more than 1, drop or shorten less important items and export again (at most 3 times). Check the preview for broken math or text.
7. **Wrap up**: tell the user the saved path (`path`) and the topics covered in a line or two.
