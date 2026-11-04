# openstem-mcp (English summary)

한국어: [README.md](README.md) — the Korean README is the full and authoritative version. This page is a short summary.

An MCP server that helps science and engineering students study. Connect it to the AI you already use (Claude and others) and it can **draw academic figures, check solution steps, and read course materials (PDF, Korean HWP files) to make practice exams, slides, flashcards and lab reports.**

The server never calls an AI model itself. It only draws and computes, so it needs **no extra subscription or API key**. Everything runs on your computer. The one exception is LaTeX packages, which tectonic downloads from its bundle server the first time they are used; your document content is not sent ([PRIVACY.md](PRIVACY.md)).

## Install

**Claude Code plugin** (server plus 6 skills: solve-and-check, figures, exam prep, lab reports, slides and documents, school LMS):

```
/plugin install openstem --marketplace kimi94279-glitch/openstem-mcp
```

**Claude Desktop, one click**: download `openstem-<version>.mcpb` from the [releases page](https://github.com/kimi94279-glitch/openstem-mcp/releases) and double-click it. The first start downloads Python packages, the tectonic LaTeX engine and TeX packages (about 500 MB in total), so it takes a few minutes.

**Any MCP host, with uvx**: install [uv](https://docs.astral.sh/uv/), prepare once in a terminal, then register the server.

```bash
uvx openstem-mcp --warmup                      # once: installs the package and prepares tectonic and TeX (3-5 min)
claude mcp add openstem -- uvx openstem-mcp    # Claude Code
```

For other hosts, use the command `uvx` with the argument `openstem-mcp` in the host's MCP settings. ChatGPT needs an HTTPS address. Run `uvx openstem-mcp --http --tunnel` to get one; the Korean README has the steps and the caveats we have not confirmed yet.

Outputs are saved in `~/OpenSTEM/`, or in `OPENSTEM_OUTPUT_DIR` if you set it. Course materials go in `~/OpenSTEM/자료/`, or in `OPENSTEM_INPUT_DIR`. The server does not open files outside these two folders.

To use the skills in Gemini CLI, Codex, VS Code or Cursor, and to find other tools for jobs openstem does not do, see [docs/companions.md](docs/companions.md) (Korean). Examples of such jobs are paper search, DOI lookup, editing existing pptx or xlsx files, and filling in an HWP/HWPX form.

## Tools

| Tool | What it does |
|---|---|
| `render_tikz` | TikZ, circuitikz, pgfplots or chemfig code → SVG, PDF and PNG. Korean labels work. Errors point to the line |
| `get_template` | 40 checked figure examples (circuits, physics, math, diagrams). Also available as MCP resources `template://{category}/{name}` |
| `plot_function` | Function graphs. Reports asymptotes, jumps and domain gaps, and can overlay data points |
| `plot_data` | Measured-data plots with error bars. Fits (linear, polynomial, exponential, power, log) with ± standard errors and R², and flags outliers |
| `render_molecule` | SMILES → molecule or reaction drawing, with molecular formula and weight |
| `verify_math` | Checks equality (with counterexamples), simplifies, and solves equations and inequalities. Also derivatives, integrals, limits, evaluation, ODEs, matrices, Laplace transforms, series, sums and probability (sympy) |
| `check_units` | Checks units and dimensions (explains which dimension is off) and converts units |
| `read_document` | Reads PDF, Korean HWP/HWPX, docx, pptx, xlsx, text and image files from the materials folder, with a listing per folder. Scanned PDFs can be read as page images. Past exams can be split into problems (one problem's text and a cropped image from a PDF) |
| `export_document` | Markdown (math, figures, tables) → pdf, docx, pptx or Korean Hangul (hwpx). Layouts: document, A4 cheat sheet, exam (answers on separate pages), flashcards. Citations and a reference list from a .bib/.json/.yaml file (APA, IEEE, MLA, Chicago); a clean default docx/pptx style or your own template |
| `lms` | Read-only access to Korean university LMSs (LearningX/Canvas, Coursemos/Moodle): courses, deadlines (also from a calendar URL), announcements, files, and syncing course files to the materials folder. Sync keeps files you edited and saves the new version as `name (2)`. It never submits or posts. It uses a token you create yourself and never asks for your password |

Prompts: `figure`, `solve`, `exam`, `cheatsheet`, `slides`, `flashcards`, `lab_report`.

## Security and privacy

- No tool runs arbitrary code. LaTeX, math and SMILES input is checked first, then processed in separate worker processes with time limits. TeX commands that read or write files are blocked both before compiling and inside TeX ([decision 0012](docs/decisions/0012-tex-defense-in-depth.md)). There is no OS-level sandbox yet. Scope, known limits and how to report a vulnerability: [SECURITY.md](SECURITY.md).
- The authors run no server and collect no data (no analytics or telemetry). Your LMS token is sent only to your school's LMS over HTTPS. Full policy: [PRIVACY.md](PRIVACY.md).
- Not tested with a real school LMS yet (only against a fake server). Canvas token policy caveats are in [decision 0008](docs/decisions/0008-lms-tokens-not-passwords.md) and the Korean README; LMS details in [docs/lms.md](docs/lms.md).

## More

[CONTRIBUTING.md](CONTRIBUTING.md) (development and tests) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/tools.md](docs/tools.md) · [docs/decisions/](docs/decisions/) · [CHANGELOG.md](CHANGELOG.md) — all in Korean.

## License

MIT
