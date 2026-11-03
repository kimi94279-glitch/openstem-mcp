"""Claude Desktop 확장(.mcpb)의 진입점.

호스트가 manifest의 명령(`uv run --directory <확장 폴더> --frozen --no-dev --extra latex launch.py`)으로 띄운다.
uv가 이 폴더의 pyproject.toml·uv.lock으로 의존성과 openstem 패키지(src/)를 설치한 가상환경(.venv)에서 돈다.
"""

from openstem.server import main

if __name__ == "__main__":
    main()
