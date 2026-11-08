"""한글(HWP/HWPX) 문서 → 텍스트. 한컴오피스 없이 글자만 뽑아낸다.

- HWPX: zip 안의 Contents/section*.xml 에서 <hp:t> 글자를 읽는다.
- HWP 5.0: OLE 파일의 BodyText/Section* 스트림(압축된 레코드)에서 문단 글자(HWPTAG_PARA_TEXT)를 읽는다.
  배포용(복사 방지) 문서와 암호 문서는 읽을 수 없다.
"""

from __future__ import annotations

import re
import struct
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

from ..core.base import Converter, Job, Option
from ..core.errors import ConvertError
from ..core.registry import register
from ..core.tools import has_hancom
from ..core.utils import natural_key

HWPTAG_PARA_TEXT = 0x10 + 51
# 문단 글자 안의 제어 문자: 이 코드들은 글자 8개(16바이트) 크기를 차지한다
_WIDE_CONTROLS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}


def iter_records(data: bytes):
    """HWP 레코드 (tag, level, payload) 를 차례로 돌려준다."""
    pos, end = 0, len(data)
    while pos + 4 <= end:
        (header,) = struct.unpack_from("<I", data, pos)
        pos += 4
        tag, level, size = header & 0x3FF, (header >> 10) & 0x3FF, (header >> 20) & 0xFFF
        if size == 0xFFF:
            if pos + 4 > end:
                break
            (size,) = struct.unpack_from("<I", data, pos)
            pos += 4
        yield tag, level, data[pos:pos + size]
        pos += size


def decode_para_text(payload: bytes) -> str:
    """HWPTAG_PARA_TEXT 내용(UTF-16LE + 제어 문자)을 일반 글자로."""
    out: list[str] = []
    i, n = 0, len(payload) // 2
    while i < n:
        (code,) = struct.unpack_from("<H", payload, i * 2)
        if code >= 32:
            out.append(chr(code))
            i += 1
        elif code in _WIDE_CONTROLS:
            if code == 9:
                out.append("\t")
            i += 8
        else:
            if code == 10:
                out.append("\n")
            elif code in (30, 31):
                out.append(" ")
            elif code == 24:
                out.append("-")
            i += 1
    text = "".join(out)
    # UTF-16 서러게이트 쌍 합치기 (이모지 등)
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def hwp_section_text(data: bytes, compressed: bool) -> list[str]:
    if compressed:
        try:
            data = zlib.decompress(data, -15)
        except zlib.error as exc:
            raise ConvertError(f"HWP 본문 압축을 풀 수 없습니다 ({exc})") from exc
    return [decode_para_text(payload) for tag, _lvl, payload in iter_records(data) if tag == HWPTAG_PARA_TEXT]


def read_hwp(path: Path) -> list[str]:
    import olefile

    if not olefile.isOleFile(str(path)):
        raise ConvertError(f"HWP 5.0 형식이 아닙니다 (HWP 3.x 이하 문서는 지원하지 않음): {path.name}")
    with olefile.OleFileIO(str(path)) as ole:
        header = ole.openstream("FileHeader").read()
        if not header.startswith(b"HWP Document File"):
            raise ConvertError(f"올바른 HWP 문서가 아닙니다: {path.name}")
        (flags,) = struct.unpack_from("<I", header, 36)
        if flags & 0x2:
            raise ConvertError(f"암호가 걸린 HWP 문서는 읽을 수 없습니다: {path.name}")
        if flags & 0x4:
            raise ConvertError(f"배포용(복사 방지) HWP 문서는 읽을 수 없습니다: {path.name}")
        compressed = bool(flags & 0x1)
        sections = sorted(
            ("/".join(entry) for entry in ole.listdir() if len(entry) == 2 and entry[0] == "BodyText"),
            key=natural_key,
        )
        paragraphs: list[str] = []
        for name in sections:
            paragraphs.extend(hwp_section_text(ole.openstream(name).read(), compressed))
        return paragraphs


def read_hwpx(path: Path) -> list[str]:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ConvertError(f"올바른 HWPX 문서가 아닙니다: {path.name}") from exc
    with zf:
        names = sorted((n for n in zf.namelist() if re.match(r"Contents/section\d+\.xml$", n)), key=natural_key)
        if not names:
            raise ConvertError(f"HWPX 문서에서 본문을 찾을 수 없습니다: {path.name}")
        paragraphs: list[str] = []
        for name in names:
            root = ET.fromstring(zf.read(name))
            for elem in root.iter():
                if not elem.tag.endswith("}p"):
                    continue
                parts: list[str] = []
                for run in elem:
                    if not run.tag.endswith("}run"):
                        continue
                    for t in run:
                        if not t.tag.endswith("}t"):
                            continue
                        parts.append(t.text or "")
                        for child in t:
                            if child.tag.endswith("}tab"):
                                parts.append("\t")
                            elif child.tag.endswith("}lineBreak"):
                                parts.append("\n")
                            parts.append(child.tail or "")
                paragraphs.append("".join(parts))
        return paragraphs


@register
class HwpToText(Converter):
    name = "hwp-to-text"
    title = "한글(HWP/HWPX) → 텍스트"
    category = "document"
    description = "한글 문서의 글자를 텍스트 파일로 뽑아냅니다. 한컴오피스가 없어도 됩니다 (서식·그림 제외)."
    inputs = frozenset({"hwp", "hwpx"})
    outputs = frozenset({"txt"})
    default_output = "txt"
    priority = 1
    options = (Option("skip_empty", "bool", False, "빈 줄 제거", "빈 문단을 빼고 저장"),)

    def is_available(self) -> tuple[bool, str]:
        try:
            import olefile  # noqa: F401
        except ImportError:
            return False, "'pip install olefile' 이 필요합니다"
        return True, ""

    def convert(self, job: Job) -> list[Path]:
        src = job.inputs[0]
        paragraphs = read_hwpx(src) if src.suffix.lower() == ".hwpx" else read_hwp(src)
        job.progress(0.8)
        if job.opt("skip_empty"):
            paragraphs = [p for p in paragraphs if p.strip()]
        job.output.write_text("\n".join(p.rstrip("\r") for p in paragraphs).strip() + "\n", encoding="utf-8")
        return [job.output]


def _hancom_to_pdf(src: Path, dst: Path) -> None:
    """Windows: 설치된 한컴오피스 한글로 PDF 저장 (pywin32 필요)."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()  # GUI 백그라운드 스레드에서도 동작하도록
    hwp = None
    try:
        hwp = win32com.client.Dispatch("HWPFrame.HwpObject")
        try:  # 보안 승인 모듈이 등록되어 있으면 '파일 접근 허용' 확인 창이 뜨지 않는다
            hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")
        except Exception:
            pass
        try:
            hwp.XHwpWindows.Item(0).Visible = False
        except Exception:
            pass
        fmt = "HWPX" if src.suffix.lower() == ".hwpx" else "HWP"
        if not hwp.Open(str(src), fmt, "forceopen:true"):
            raise ConvertError(f"한글에서 문서를 열 수 없습니다: {src.name}")
        if not hwp.SaveAs(str(dst), "PDF", ""):
            raise ConvertError("한글에서 PDF 로 저장하지 못했습니다")
    except ConvertError:
        raise
    except Exception as exc:
        raise ConvertError(f"한컴오피스로 PDF 변환 실패: {exc}") from exc
    finally:
        if hwp is not None:
            try:
                hwp.Clear(1)
                hwp.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


@register
class HwpToPdf(Converter):
    name = "hwp-to-pdf"
    title = "한글(HWP/HWPX) → PDF"
    category = "document"
    description = ("한컴오피스 한글을 이용해 한글 문서를 PDF 로 바꿉니다 (Windows + 한컴오피스 필요). "
                   "처음 실행할 때 한글이 '파일 접근 허용' 여부를 물을 수 있습니다.")
    inputs = frozenset({"hwp", "hwpx"})
    outputs = frozenset({"pdf"})
    default_output = "pdf"
    priority = 2

    def is_available(self) -> tuple[bool, str]:
        if has_hancom():
            return True, ""
        return False, "Windows 에 한컴오피스 한글이 설치되어 있어야 합니다 (글자만 필요하면 'HWP → 텍스트' 사용)"

    def convert(self, job: Job) -> list[Path]:
        job.progress(0.1)
        _hancom_to_pdf(job.inputs[0].resolve(), job.output.resolve())
        if not job.output.exists():
            raise ConvertError("PDF 파일이 만들어지지 않았습니다")
        return [job.output]
