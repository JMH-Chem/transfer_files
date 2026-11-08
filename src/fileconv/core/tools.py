"""외부 프로그램(ffmpeg, MS Word, LibreOffice) 찾기와 실행 도우미."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

# GUI(.exe)에서 실행할 때 검은 콘솔 창이 뜨지 않도록
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def popen_kwargs() -> dict:
    """외부 프로그램 실행 공통 인자.

    stdin 을 명시적으로 막아 둔다: 콘솔 없는 Windows GUI(.exe)에서는 부모의 표준 입력 핸들이 없어서
    지정하지 않으면 '핸들이 잘못되었습니다(WinError 6)' 로 실행이 실패한다.
    """
    kwargs: dict = {"stdin": subprocess.DEVNULL}
    if IS_WINDOWS:
        kwargs["creationflags"] = CREATE_NO_WINDOW
    return kwargs


@functools.lru_cache(maxsize=None)
def find_ffmpeg() -> str | None:
    """ffmpeg 실행 파일 경로. 환경변수 FCONV_FFMPEG → imageio-ffmpeg 내장 → PATH 순서."""
    env = os.environ.get("FCONV_FFMPEG")
    if env and Path(env).is_file():
        return env
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


@functools.lru_cache(maxsize=None)
def find_soffice() -> str | None:
    """LibreOffice(soffice) 경로. 환경변수 FCONV_SOFFICE → PATH → Windows 기본 설치 위치."""
    env = os.environ.get("FCONV_SOFFICE")
    if env and Path(env).is_file():
        return env
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    if IS_WINDOWS:
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base:
                candidate = Path(base) / "LibreOffice" / "program" / "soffice.exe"
                if candidate.is_file():
                    return str(candidate)
    elif sys.platform == "darwin":
        candidate = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        if candidate.is_file():
            return str(candidate)
    return None


@functools.lru_cache(maxsize=None)
def has_com_object(prog_id: str) -> bool:
    """Windows 에 해당 COM 프로그램이 등록되어 있고 pywin32 로 제어할 수 있는지."""
    if not IS_WINDOWS:
        return False
    try:
        import winreg

        import win32com.client  # noqa: F401

        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\CLSID"))
        return True
    except Exception:
        return False


def has_ms_office(app: str = "Word") -> bool:
    """MS Office 프로그램(Word/Excel/PowerPoint)이 설치되어 있는지."""
    return has_com_object(f"{app}.Application")


def has_hancom() -> bool:
    """한컴오피스 한글이 설치되어 있는지."""
    return has_com_object("HWPFrame.HwpObject")
