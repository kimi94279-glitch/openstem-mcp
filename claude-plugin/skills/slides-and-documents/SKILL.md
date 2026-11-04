---
name: slides-and-documents
description: Make presentation slides or documents that contain equations, scientific figures and checked results. Use when the user asks for 발표 자료, PPT, 슬라이드, 보고서 PDF, 워드 문서, or wants study notes exported as a file.
---

# Slides and documents

openstem's strength is the scientific content: figures, equations and verified numbers. Layout and visual design can come from elsewhere.

1. Build the content first: figures with the stem-figures skill, numbers and derivations checked with the solve-and-check skill.
2. Choose how to make the file:
   - If a dedicated presentation or document skill is available in this session (for example a pptx or docx skill), use it for layout and design, and insert openstem's saved figure files and the checked equations.
   - Otherwise use `export_document`: `format="pptx"` (each `##` heading is one slide, `::: notes` for speaker notes), `format="pdf"` or `format="docx"`. Math goes in `$...$`; figures as `![caption](path from render_*/plot_*)`. If the materials include a reference file (.bib, .json or .yaml), pass it as `bibliography="refs.bib"` and cite as [@key] (`citation_style`: chicago, apa, ieee, mla). docx and pptx get a clean default style; to use the school's own Word/PowerPoint template, pass it as `template="양식.docx"`.
3. Keep slides short: one idea per slide, at most about 6 lines. If export warns that a slide is too long, split it.
4. Give the user the file path and a one-line outline of what is inside.

For jobs openstem does not do (editing an existing pptx or xlsx, writing HWP files, finding papers or checking DOIs), suggest a tool from the companion list instead of improvising: https://github.com/kimi94279-glitch/openstem-mcp/blob/main/docs/companions.md

If the openstem tools are not available in this session (for example in claude.ai chat, where only the skills load), say so and point the user to the install guide: https://github.com/kimi94279-glitch/openstem-mcp#설치. Then use a document or presentation skill if this session has one, and say that figures and equations were not rendered or checked.
