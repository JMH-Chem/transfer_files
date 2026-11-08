# Windows 용 실행 파일 만들기 (PowerShell 에서 저장소 최상위 폴더에서 실행)
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
# 결과: dist\FileConverter\FileConverter.exe (GUI), dist\FileConverter\fconv.exe (명령줄)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".venv-build")) {
    python -m venv .venv-build
}
.\.venv-build\Scripts\python.exe -m pip install --upgrade pip
.\.venv-build\Scripts\python.exe -m pip install -e ".[dev]"

Push-Location packaging
try {
    ..\.venv-build\Scripts\pyinstaller.exe fileconv.spec --noconfirm --clean --distpath ..\dist --workpath ..\build
} finally {
    Pop-Location
}

# 결과 폴더를 zip 으로도 묶어 둔다 (다른 PC 로 옮기기 쉽게)
Compress-Archive -Path dist\FileConverter\* -DestinationPath dist\FileConverter-windows.zip -Force
Write-Host ""
Write-Host "완료: dist\FileConverter\FileConverter.exe" -ForegroundColor Green
