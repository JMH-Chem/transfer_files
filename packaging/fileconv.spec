# PyInstaller 설정: dist/FileConverter/ 폴더에 GUI(FileConverter.exe)와 CLI(fconv.exe)를 함께 만든다.
#   pyinstaller packaging/fileconv.spec --noconfirm --clean
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

datas, binaries, hiddenimports = [], [], []
for pkg in ("pypdfium2", "pypdfium2_raw", "pillow_heif", "imageio_ffmpeg"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += collect_data_files("docx")  # python-docx 기본 템플릿
hiddenimports += collect_submodules("markdown.extensions")  # 이름 문자열로 불러오는 확장
hiddenimports += collect_submodules("fileconv")
hiddenimports += ["olefile", "openpyxl", "charset_normalizer"]

excludes = [
    "tkinter", "matplotlib", "IPython", "pytest",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf",
]

common = dict(
    pathex=["../src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
gui_a = Analysis(["gui_entry.py"], **common)
cli_a = Analysis(["cli_entry.py"], **common)

gui_pyz = PYZ(gui_a.pure)
cli_pyz = PYZ(cli_a.pure)

gui_exe = EXE(
    gui_pyz, gui_a.scripts, [],
    exclude_binaries=True,
    name="FileConverter",
    console=False,          # 검은 콘솔 창 없이 실행
    upx=False,
)
cli_exe = EXE(
    cli_pyz, cli_a.scripts, [],
    exclude_binaries=True,
    name="fconv",
    console=True,
    upx=False,
)
coll = COLLECT(
    gui_exe, gui_a.binaries, gui_a.datas,
    cli_exe, cli_a.binaries, cli_a.datas,
    name="FileConverter",
    upx=False,
)
