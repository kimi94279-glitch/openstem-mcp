# OpenSTEM

A study kit for science and engineering students. It connects Claude to a local MCP server, openstem-mcp, and adds skills that tell Claude when and how to use it.

## What it does

- **Figures**: circuits, free-body diagrams, vectors, geometry and block diagrams (TikZ, from 40 checked examples), function graphs with asymptote and discontinuity warnings, measured-data plots with fitted curves, and molecule drawings from SMILES.
- **Checking**: verifies solution steps with sympy (equality with counterexamples, equations, inequalities, calculus, ODEs, matrices, Laplace transforms, series, probability) and checks units and dimensions.
- **Materials**: reads PDF, Korean HWP/HWPX, Word, PowerPoint and Excel files from your materials folder.
- **Outputs**: A4 cheat sheets, practice exams with a separate answer section, flashcards, slides (pptx), lab reports (docx or Korean Hangul hwpx) and PDFs, with citations and a reference list from your .bib file.
- **School LMS (optional, read-only)**: courses, deadlines, announcements and course files from Korean universities' LearningX/Canvas and Coursemos/Moodle LMSs, using a token you create yourself. It never submits or posts.

## Install

In Claude Code:

```
/plugin install openstem --marketplace kimi94279-glitch/openstem-mcp
```

## Use it

Ask in your own words, for example "RC 직렬회로 그려줘", "이 적분 풀고 단계마다 검산해줘", "일반물리 강의자료 다 받아서 예상문제 10개 만들어줘", or "이 측정값으로 실험 보고서 써줘". The skills pick the tools. Results are saved in your output folder (default `~/OpenSTEM`), and figures show up as previews in the chat.

The MCP server is a local program, so it runs in Claude Code and in Cowork on your computer. In Claude Desktop chat, install the one-click extension (`.mcpb`) from the project's releases instead.

Setup options appear when you enable the plugin: output folder, materials folder, and, for the LMS, your school name plus an access token or calendar URL. All are optional.

## Other hosts and companion tools

The skills follow the [Agent Skills standard](https://agentskills.io/specification), so other hosts can load them too: copy the folders in `skills/` into the host's skills folder (Gemini CLI `~/.gemini/skills` or `.agents/skills`, Codex `.agents/skills`, VS Code `.github/skills`, Cursor `.cursor/skills`). Connect the openstem MCP server (`uvx openstem-mcp`) to that host as well; without it the skills say the tools are missing and link to the install guide. Folder paths, links to each host's documentation, and which ones we could not confirm: [docs/companions.md](https://github.com/kimi94279-glitch/openstem-mcp/blob/main/docs/companions.md). That page also lists other MCP servers and skills for jobs openstem does not do, such as paper search, DOI lookup, editing existing pptx or xlsx files, and filling in an HWP/HWPX form.

## What it runs and connects to

- Runs `uvx openstem-mcp==<version>`, which installs the pinned package and its Python dependencies from PyPI.
- If tectonic (the LaTeX engine) is missing, the server downloads tectonic 0.17.0 once from its official GitHub release and checks a SHA-256 hash pinned in the code; tectonic then fetches LaTeX packages from its bundle server. Set `OPENSTEM_NO_DOWNLOAD=1` to turn the download off.
- The lms tool contacts only your school's LMS address, and only when you have set it up.
- Nothing is sent to the authors. There is no telemetry.

## Privacy Policy

openstem-mcp collects no personal data and the authors run no server. Files stay in your output and materials folders until you delete them. Your LMS token and calendar URL are kept by Claude as secret settings and are sent only to your school's LMS over HTTPS. Tool results go to Claude under Anthropic's privacy policy. Full policy (data collection, usage and storage, third-party sharing, retention, contact): https://github.com/kimi94279-glitch/openstem-mcp/blob/main/PRIVACY.md

## Support

Source, documentation and issues: https://github.com/kimi94279-glitch/openstem-mcp

## License

MIT
