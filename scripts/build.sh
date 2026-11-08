#!/usr/bin/env bash
# macOS / Linux 용 실행 파일 만들기. 결과: dist/FileConverter/
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pip install -e ".[dev]"
cd packaging
pyinstaller fileconv.spec --noconfirm --clean --distpath ../dist --workpath ../build
echo "완료: dist/FileConverter/"
