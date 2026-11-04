---
name: exam-prep
description: Turn course materials into exam preparation - summaries, one-page A4 cheat sheets, practice exams with worked solutions, and flashcards. Use when the user mentions lecture notes, slides, past exams, textbook chapters or their school LMS, or asks for 요약, 치트시트, 족보, 예상문제, 시험 대비, 암기 카드.
---

# Exam prep from materials

## 1. Get the material (never invent course content)

- Files on the computer: `read_document` with no path lists the materials folder; read with `pages` for long PDFs, and `mode="images"` for scanned or formula-heavy pages. Korean HWP files work.
- School LMS: `lms` `action="sync"` with the course name downloads all new course files into the materials folder (call again if it says files remain), then read them with `read_document`. For one file use `action="files"` then `action="download"`.
- Nothing available: ask the user to put the files in the materials folder or attach them.

## 2. Make the output

- **Cheat sheet**: dense, one A4 page → `export_document` with `layout="cheatsheet"`, `format="pdf"`. If the result says more than one page, cut and export again.
- **Practice exam**: new problems in the style of the material, not copies of past questions. Solve each one and check the answers with `verify_math` and `check_units` (follow the solve-and-check skill). Write `## Problem N (points)` sections, then one heading `# 정답과 해설` (`# Answers` for a non-Korean exam) with the solutions → `export_document` `layout="exam"`.
- **Flashcards**: a two-column markdown table (front | back) of definitions and formulas → `export_document` `layout="flashcards"`.
- Add figures from the stem-figures skill by putting the saved file path in markdown image syntax.

Write the document in the user's language and tell them the saved file paths.

If the openstem tools are not available in this session (for example in claude.ai chat, where only the skills load), say so and point the user to the install guide: https://github.com/kimi94279-glitch/openstem-mcp#설치. Then you can still summarize material the user attaches and write problems, but give the result as markdown in the chat and mark answers as unverified.
