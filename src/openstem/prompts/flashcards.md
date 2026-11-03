Make flashcards from this material: {material}

Answer the user in their language and write the cards in it too (users are mostly Korean students).

If the material is a file on the user's PC (PDF, HWP, Word, PowerPoint, ...), read it with `read_document`; if you don't know the path, call it without path to list the material folder. Read photos and attachments directly in the chat.

1. **Select** only what must be memorized: definitions, formulas, units, conditions under which laws hold, commonly confused pairs. Turn derivations into question cards like "Why ...?". If the request gives no count, make 16-24 cards (2-3 A4 pages).
2. **Write cards**: one thing per card.
   - Front: a question or term, one line. E.g. "Displacement under constant acceleration", "Ohm's law"
   - Back: the answer, one or two lines. Formulas in `$...$` with a short note on symbols and units. E.g. "$V = IR$ ($V$: voltage, $I$: current, $R$: resistance)"
   - If a formula and its name must be learned both ways, make two cards.
3. **Verify**: if you rearranged a formula, check it against the original with `verify_math` (equal); if you wrote units, check them with `check_units`.
4. **Tables**: a `## Topic` heading per topic with a 2-column table under it. The first row is the header in the user's language (`| Front | Back |`; Korean `| 앞면 | 뒷면 |`), the next row `|---|---|`, then one row per card. No line breaks and no `|` inside cells (absolute value: `$\lvert x \rvert$`).
5. **Export**: call `export_document` with format="pdf", layout="flashcards". Put the course and chapter in title.
6. **Check**: look at the preview (first page, fronts) to see that text and math fit in the cards. If `warnings` lists shrunk cards, shorten or split them and export again (at most 2 times).
7. **Wrap up**: tell the user the PDF path (`path`) and card count, and that printing double-sided (flip on long edge) and cutting along the dotted lines lines up fronts and backs. `tsv_path` is a tab-separated text file that flashcard apps can import; math in it is written as `\(...\)`.
