Make presentation slides from this material: {material}

Answer the user in their language and write the slides in it too (users are mostly Korean students).

If the material is a file on the user's PC (PDF, HWP, Word, PowerPoint, ...), read it with `read_document`; if you don't know the path, call it without path to list the material folder. Read photos and attachments directly in the chat.

You plan the content and judge; tools make the figures and the file.

1. **Outline**: read the talk length and audience from the request (if missing, assume 10 minutes and classmates in the same course, and say so). Plan about one slide per minute and first list the slide titles in this order: title → overview → body (a `#` heading per major section) → summary.
2. **Write slides**: one slide is one `## Title`. One message per slide, 3-6 bullets, each a short line (about 20 Hangul characters or 6 English words). Put what you would explain aloud as speaker notes between `::: notes` and `:::` at the end of that slide.
3. **Math**: `$...$` (becomes editable equations in pptx). At most one long formula per slide. If you rearranged a formula, check it against the original with `verify_math` (equal).
4. **Figures**: where a picture is faster than words (relationships between concepts, processes, graphs, circuits, results).
   - Function graphs `plot_function`, measured data with trend lines `plot_data`, molecules `render_molecule`.
   - Circuits, free-body diagrams, flowcharts, block diagrams, state diagrams, concept maps, timelines: read a similar example with `get_template` (including the diagrams category), adapt it, and draw with `render_tikz`.
   - Check the returned preview yourself, then insert `svg_path` as `![caption](path)`. On a slide with a figure, cut bullets to 2 or fewer, or keep only the figure and move the explanation to the notes.
5. **Export**: call `export_document` with format="pptx". Put the talk title in title (it becomes the title slide).
6. **Check**: pptx has no preview. Check that the result's `slides` (count) matches the plan (`##` headings + title slide + `#` sections) and whether `warnings` lists slides likely to overflow. If so, split or shorten them and export again (at most 2 times).
7. **Wrap up**: tell the user the saved path (`path`), the slide count and the slide titles briefly. Mention the speaker notes if you added them.
