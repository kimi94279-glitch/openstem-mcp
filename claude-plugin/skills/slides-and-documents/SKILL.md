---
name: slides-and-documents
description: Make presentation slides or documents that contain equations, scientific figures and checked results. Use when the user asks for 발표 자료, PPT, 슬라이드, 보고서 PDF, 워드 문서, or wants study notes exported as a file.
---

# Slides and documents

openstem's strength is the scientific content: figures, equations and verified numbers. Layout and visual design can come from elsewhere.

1. Build the content first: figures with the stem-figures skill, numbers and derivations checked with the solve-and-check skill.
2. Choose how to make the file:
   - If a dedicated presentation or document skill is available in this session (for example a pptx or docx skill), use it for layout and design, and insert openstem's saved figure files and the checked equations.
   - Otherwise use `export_document`: `format="pptx"` (each `##` heading is one slide, `::: notes` for speaker notes), `format="pdf"` or `format="docx"`. Math goes in `$...$`; figures as `![caption](path from render_*/plot_*)`.
3. Keep slides short: one idea per slide, at most about 6 lines. If export warns that a slide is too long, split it.
4. Give the user the file path and a one-line outline of what is inside.
