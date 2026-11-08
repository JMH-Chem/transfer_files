"""경로·파일명·인코딩 관련 도우미."""

from __future__ import annotations

import glob
import os
import re
from pathlib import Path
from typing import Iterable

from .errors import ConvertError

_NUM_RE = re.compile(r"(\d+)")


def natural_key(path: Path | str) -> list:
    """'img2' 가 'img10' 보다 앞에 오도록 정렬하는 키 (이미지→PDF 순서에 중요)."""
    text = str(path).lower()
    return [int(t) if t.isdigit() else t for t in _NUM_RE.split(text)]


def expand_inputs(items: Iterable[str | Path], recursive: bool = False) -> list[Path]:
    """입력 목록을 실제 파일 목록으로 펼친다.

    - Windows cmd/PowerShell 은 *.jpg 를 직접 펼쳐주지 않으므로 여기서 glob 처리
    - 폴더를 주면 그 안의 파일들 (recursive=True 면 하위 폴더까지)
    - 와일드카드/폴더에서 나온 파일은 자연 정렬, 직접 적은 파일은 적은 순서 유지
    """
    result: list[Path] = []
    seen: set[Path] = set()

    def add(p: Path) -> None:
        key = p.resolve()
        if key not in seen:
            seen.add(key)
            result.append(p)

    for item in items:
        text = str(item)
        # '[MV] 노래.mp4' 처럼 이름에 [ ] 가 있는 실제 파일/폴더는 와일드카드로 보지 않는다
        if any(ch in text for ch in "*?[") and not Path(text).expanduser().exists():
            matches = [Path(m) for m in glob.glob(text, recursive=True) if Path(m).is_file() and not is_junk(Path(m))]
            if not matches:
                raise ConvertError(f"패턴에 맞는 파일이 없습니다: {text}")
            for m in sorted(matches, key=natural_key):
                add(m)
            continue
        path = Path(text).expanduser()
        if path.is_dir():
            files = path.rglob("*") if recursive else path.iterdir()
            for f in sorted((f for f in files if f.is_file() and not is_junk(f)), key=natural_key):
                add(f)
        elif path.is_file():
            add(path)
        else:
            raise ConvertError(f"파일을 찾을 수 없습니다: {text}")
    return result


_JUNK_NAMES = {"desktop.ini", "thumbs.db", ".ds_store", "ehthumbs.db"}


def is_junk(path: Path) -> bool:
    """폴더를 통째로 넣을 때 건너뛸 시스템/임시 파일 (desktop.ini, Thumbs.db, ~$문서.docx, 숨김 파일, 변환 중 임시 파일)."""
    name = path.name
    return (name.lower() in _JUNK_NAMES or name.startswith(("~$", "."))
            or ".fconv-part." in name)


def unique_path(path: Path, overwrite: bool = False, reserved: set[Path] | None = None) -> Path:
    """이미 있는 파일이면 '이름 (1).확장자' 처럼 겹치지 않는 경로를 만든다."""
    reserved = reserved if reserved is not None else set()
    if overwrite and path.resolve() not in reserved:
        return path
    candidate = path
    n = 1
    while candidate.exists() or candidate.resolve() in reserved:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


_INVALID_SHEET = re.compile(r"[\[\]:*?/\\]")


def safe_sheet_name(name: str, used: set[str]) -> str:
    """엑셀 시트 이름 규칙(31자, 특수문자 금지, 중복 금지)에 맞게 고친다."""
    base = _INVALID_SHEET.sub("_", name).strip("'") or "Sheet"
    base = base[:31]
    candidate = base
    n = 2
    while candidate.lower() in {u.lower() for u in used}:
        suffix = f"_{n}"
        candidate = base[: 31 - len(suffix)] + suffix
        n += 1
    used.add(candidate)
    return candidate


def detect_encoding(path: Path, sample_size: int = 256 * 1024) -> str:
    """텍스트 파일 인코딩 추정. 한국어 환경에서 흔한 utf-8 / cp949 를 우선 판단한다."""
    raw = path.read_bytes()[:sample_size]
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    # 샘플 끝에서 글자가 잘렸을 수 있으므로 증분 디코더(final=False)로 판단
    import codecs

    for enc in ("utf-8", "cp949"):
        try:
            codecs.getincrementaldecoder(enc)().decode(raw, final=len(raw) < sample_size)
            return enc
        except UnicodeDecodeError:
            continue
    from charset_normalizer import from_bytes

    best = from_bytes(raw).best()
    return best.encoding if best else "utf-8"


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{n}B"


def open_in_file_manager(path: Path) -> None:
    """탐색기에서 폴더 열기 (GUI 용)."""
    import subprocess
    import sys

    target = path if path.is_dir() else path.parent
    if sys.platform == "win32":
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])
