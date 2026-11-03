# Privacy Policy / 개인정보 처리방침

Last updated: 2026-11-03 · Applies to openstem-mcp (PyPI package, Claude Desktop extension `.mcpb`, and the `openstem` Claude plugin).

## Summary

openstem-mcp runs entirely on your own computer. The project authors run no server, collect no data, and include no analytics or telemetry. The server never calls an AI model; your AI app (Claude, ChatGPT, etc.) does that under its own privacy policy.

## Data collection

The authors do not collect, receive or store any personal data. Nothing you draw, check, read or export is sent to the authors.

## What the software sends over the network, and where

Only these connections are made, and only when needed:

- **Package install** — `uvx`/`uv` download openstem-mcp and its dependencies from PyPI (pypi.org).
- **LaTeX engine** — if tectonic is not installed, the server downloads tectonic 0.17.0 once from its official GitHub release and checks a SHA-256 hash pinned in the code. Set `OPENSTEM_NO_DOWNLOAD=1` to disable this.
- **TeX packages** — tectonic downloads the LaTeX packages it needs from its standard bundle server (fullyjustified.net). No document content is sent; only package files are fetched.
- **School LMS (optional)** — only if you configure it. The read-only `lms` tool sends your LMS access token or calendar URL only to your school's LMS address over HTTPS, never to any other host, and never posts or submits anything.
- **HTTP mode (optional)** — only when you run `openstem-mcp --http`. With `--tunnel`, your own installed `cloudflared` makes a temporary public address that your AI app connects to; requests need a secret key.

## Usage and storage

- Generated figures and documents are saved in your output folder (default `~/OpenSTEM`).
- `read_document` opens files only inside your materials folder and output folder.
- Files downloaded from your LMS are saved under `LMS/<course>/` in the materials folder, with a small `.openstem-sync.json` that records which files were downloaded.
- Your LMS token and calendar URL are stored by the app that runs the server (for example as a secure value in Claude's extension or plugin settings, or in your own config file). openstem-mcp does not write them anywhere else and removes them from every tool result.
- HTTP mode stores its secret key in your user config folder.

## Third-party sharing

The authors share nothing with anyone, because they receive nothing. Tool results (text and preview images) go to the AI app you connected, and that app's own privacy policy applies.

## Data retention

Everything stays on your computer until you delete it. Deleting the output and materials folders removes all files the software created; uninstalling removes the program. Revoke an LMS token in your LMS settings to cut access immediately.

## Contact

Questions or concerns: open an issue at https://github.com/kimi94279-glitch/openstem-mcp/issues

---

## 한국어 요약

- openstem-mcp는 내 컴퓨터에서만 돌고, 만든 사람은 서버를 운영하지 않으며 어떤 데이터도 모으지 않습니다(분석·추적 없음). 서버는 AI를 부르지 않습니다.
- 네트워크 연결은 필요할 때만 합니다: 설치(PyPI), tectonic 자동 설치(공식 GitHub 릴리스, 해시 확인), TeX 패키지(tectonic 서버), 설정한 경우 학교 LMS(토큰·캘린더 주소는 그 학교 주소로만), HTTP 모드를 켠 경우 내 cloudflared 터널.
- 결과물은 결과물 폴더에, LMS 자료는 자료 폴더 `LMS/과목명/`에 저장됩니다. 지우면 사라집니다.
- 툴 결과는 연결한 AI 앱으로 가고, 그 앱의 개인정보 처리방침을 따릅니다.
- 문의: https://github.com/kimi94279-glitch/openstem-mcp/issues
