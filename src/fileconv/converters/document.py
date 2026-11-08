"""문서 변환: PDF 합치기/편집/나누기, PDF → 이미지/텍스트, 오피스 → PDF, Markdown → HTML, Word → 텍스트."""

from __future__ import annotations

import html
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from ..core.base import Converter, Job, Option, parse_pages
from ..core.errors import ConvertError
from ..core.registry import register
from ..core.tools import IS_WINDOWS as IS_WINDOWS_, find_soffice, has_ms_office, popen_kwargs
from ..core.utils import unique_path

PAGES_OPT = Option("pages", "pages", None, "페이지", "예: 1-3,5,8- (비우면 전체)")
PASSWORD_OPT = Option("password", "str", "", "PDF 비밀번호", "암호가 걸린 PDF 일 때만")


def open_pdf(path: Path, password: str = ""):
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(path)
    except (PdfReadError, OSError, ValueError) as exc:
        raise ConvertError(f"PDF 를 열 수 없습니다: {path.name} ({exc})") from exc
    if reader.is_encrypted:
        try:
            ok = reader.decrypt(password or "")
        except Exception as exc:
            raise ConvertError(f"{path.name}: 암호 해제 실패 ({exc})") from exc
        if not ok:
            raise ConvertError(f"{path.name}: 암호가 걸린 PDF 입니다. 'password' 옵션에 비밀번호를 입력하세요")
    return reader


def open_pdfium(path: Path, password: str = ""):
    import pypdfium2 as pdfium

    try:
        return pdfium.PdfDocument(str(path), password=password or None)
    except pdfium.PdfiumError as exc:
        msg = str(exc)
        if "password" in msg.lower():
            raise ConvertError(f"{path.name}: 암호가 걸린 PDF 입니다. 'password' 옵션에 비밀번호를 입력하세요") from exc
        raise ConvertError(f"PDF 를 열 수 없습니다: {path.name} ({msg})") from exc


def encrypt_writer(writer, password: str) -> None:
    try:
        writer.encrypt(user_password=password, owner_password=None, algorithm="AES-256")
    except Exception:  # AES 에 필요한 cryptography 패키지가 없으면 RC4 로
        writer.encrypt(user_password=password, owner_password=None, algorithm="RC4-128")


# ---------------------------------------------------------------- PDF → PDF

@register
class PdfTools(Converter):
    name = "pdf-tools"
    default_output = "pdf"
    title = "PDF 합치기·페이지 추출·회전"
    category = "document"
    description = "여러 PDF 를 하나로 합치거나, 원하는 페이지만 뽑거나, 회전하거나, 암호를 걸고 풉니다."
    inputs = frozenset({"pdf"})
    outputs = frozenset({"pdf"})
    can_merge = True
    merge_default = True
    priority = 10
    options = (
        PAGES_OPT,
        Option("rotate", "choice", "0", "회전", "시계 방향 회전 각도", choices=("0", "90", "180", "270"), remember=False),
        PASSWORD_OPT,
        Option("new_password", "str", "", "새 비밀번호 걸기", "결과 PDF 에 열기 암호를 겁니다 (비우면 암호 없음)"),
    )

    def convert(self, job: Job) -> list[Path]:
        from pypdf import PdfWriter

        writer = PdfWriter()
        rotate = int(job.opt("rotate", "0"))
        for i, path in enumerate(job.inputs):
            reader = open_pdf(path, job.opt("password", ""))
            for index in parse_pages(job.opt("pages"), len(reader.pages)):
                page = writer.add_page(reader.pages[index])
                if rotate:
                    page.rotate(rotate)
            job.progress(0.9 * (i + 1) / len(job.inputs))
        if not writer.pages:
            raise ConvertError("선택한 페이지가 없습니다")
        if job.opt("new_password"):
            encrypt_writer(writer, job.opt("new_password"))
        with open(job.output, "wb") as fh:
            writer.write(fh)
        return [job.output]


@register
class PdfSplit(Converter):
    name = "pdf-split"
    default_output = "pdf"
    priority = 8
    title = "PDF 나누기"
    category = "document"
    description = "PDF 를 N쪽씩 여러 파일로 나눕니다 (기본: 한 쪽당 한 파일)."
    inputs = frozenset({"pdf"})
    outputs = frozenset({"pdf"})
    multi_output = True
    options = (
        Option("every", "int", 1, "몇 쪽씩", "이 쪽수마다 새 파일", min=1),
        PAGES_OPT,
        PASSWORD_OPT,
    )

    def convert(self, job: Job) -> list[Path]:
        from pypdf import PdfWriter

        src = job.inputs[0]
        reader = open_pdf(src, job.opt("password", ""))
        if len(reader.pages) == 0:
            raise ConvertError(f"페이지가 없는 PDF 입니다: {src.name}")
        indices = parse_pages(job.opt("pages"), len(reader.pages))
        every = job.opt("every", 1)
        chunks = [indices[i:i + every] for i in range(0, len(indices), every)]
        width = max(3, len(str(len(reader.pages))))
        outputs: list[Path] = []
        for n, chunk in enumerate(chunks):
            writer = PdfWriter()
            for index in chunk:
                writer.add_page(reader.pages[index])
            first, last = chunk[0] + 1, chunk[-1] + 1
            label = f"{first:0{width}d}" if first == last else f"{first:0{width}d}-{last:0{width}d}"
            dest = unique_path(job.output.with_name(f"{job.output.stem}_p{label}.pdf"), job.overwrite)
            with open(dest, "wb") as fh:
                writer.write(fh)
            outputs.append(dest)
            job.progress((n + 1) / len(chunks))
        return outputs


@register
class PdfCompress(Converter):
    name = "pdf-compress"
    default_output = "pdf"
    priority = 7
    title = "PDF 용량 줄이기"
    category = "document"
    description = "PDF 안의 사진을 다시 압축하고 크기를 줄여 용량을 줄입니다 (스캔·사진이 많은 PDF 에 효과적)."
    inputs = frozenset({"pdf"})
    outputs = frozenset({"pdf"})
    options = (
        Option("image_quality", "int", 60, "사진 품질", "낮을수록 작아짐 (30~80 권장)", min=10, max=95),
        Option("max_image_px", "int", 1600, "사진 최대 크기(px)", "긴 변 기준. 0 = 크기는 그대로", min=0),
        Option("grayscale", "bool", False, "사진을 흑백으로"),
        PASSWORD_OPT,
    )

    def convert(self, job: Job) -> list[Path]:
        from pypdf import PdfWriter

        from .image import flatten

        src = job.inputs[0]
        reader = open_pdf(src, job.opt("password", ""))
        writer = PdfWriter(clone_from=reader)
        quality, max_px = job.opt("image_quality", 60), job.opt("max_image_px", 1600)
        pages = list(writer.pages)
        for n, page in enumerate(pages):
            for img in list(page.images):
                try:
                    obj = img.indirect_reference.get_object() if img.indirect_reference else None
                    if obj is not None and ("/SMask" in obj or "/Mask" in obj):
                        continue  # 투명도가 있는 그림은 건드리지 않음
                    pil = img.image
                    if pil is None or max(pil.size) < 64:
                        continue
                    if max_px and max(pil.size) > max_px:
                        pil = pil.copy()
                        pil.thumbnail((max_px, max_px))
                    if job.opt("grayscale"):
                        pil = pil.convert("L")
                    elif pil.mode not in ("RGB", "L"):
                        pil = flatten(pil)
                    img.replace(pil, quality=quality)
                except Exception:
                    continue  # 특이한 형식의 그림은 원래대로 둔다
            page.compress_content_streams()
            job.progress(0.9 * (n + 1) / max(1, len(pages)))
        try:
            writer.compress_identical_objects()  # 같은 내용 객체 합치기 (기본값: 중복·고아 객체 제거)
        except AttributeError:  # 오래된 pypdf
            pass
        with open(job.output, "wb") as fh:
            writer.write(fh)
        if job.output.stat().st_size >= src.stat().st_size:  # 더 커졌으면 원본 그대로
            job.output.write_bytes(src.read_bytes())
        return [job.output]


# ---------------------------------------------------------------- PDF → 이미지 / 텍스트

@register
class PdfToImage(Converter):
    name = "pdf-to-image"
    default_output = "png"
    priority = 6
    title = "PDF → 이미지"
    category = "document"
    description = "PDF 각 페이지를 PNG/JPG 이미지로 저장합니다."
    inputs = frozenset({"pdf"})
    outputs = frozenset({"png", "jpg", "webp"})
    multi_output = True
    options = (
        Option("dpi", "int", 150, "해상도(DPI)", "높을수록 선명·큰 파일 (화면용 96~150, 인쇄용 300)", min=36, max=1200),
        PAGES_OPT,
        Option("quality", "int", 90, "품질", "JPG/WEBP 품질 (1~100)", min=1, max=100),
        PASSWORD_OPT,
    )

    def convert(self, job: Job) -> list[Path]:
        src = job.inputs[0]
        pdf = open_pdfium(src, job.opt("password", ""))
        try:
            indices = parse_pages(job.opt("pages"), len(pdf))
            scale = job.opt("dpi", 150) / 72
            width = max(3, len(str(len(pdf))))
            fmt = {"png": "PNG", "jpg": "JPEG", "webp": "WEBP"}[job.target if job.target != "jpeg" else "jpg"]
            outputs: list[Path] = []
            for n, index in enumerate(indices):
                page = pdf[index]
                image = page.render(scale=scale).to_pil()
                page.close()
                if fmt == "JPEG" and image.mode != "RGB":
                    image = image.convert("RGB")
                if len(indices) == 1:
                    dest = unique_path(job.output, job.overwrite)
                else:
                    dest = unique_path(
                        job.output.with_name(f"{job.output.stem}_p{index + 1:0{width}d}{job.output.suffix}"),
                        job.overwrite,
                    )
                kwargs = {"quality": job.opt("quality", 90)} if fmt in ("JPEG", "WEBP") else {}
                image.save(dest, fmt, **kwargs)
                outputs.append(dest)
                job.progress((n + 1) / len(indices))
            return outputs
        finally:
            pdf.close()


@register
class PdfToText(Converter):
    name = "pdf-to-text"
    default_output = "txt"
    priority = 5
    title = "PDF → 텍스트"
    category = "document"
    description = "PDF 안의 글자를 텍스트 파일로 뽑아냅니다 (스캔한 PDF 는 글자가 없어서 비어 있을 수 있음)."
    inputs = frozenset({"pdf"})
    outputs = frozenset({"txt"})
    options = (
        PAGES_OPT,
        Option("page_marks", "bool", True, "페이지 구분 표시", "각 페이지 앞에 '=== 1쪽 ===' 표시"),
        PASSWORD_OPT,
    )

    def convert(self, job: Job) -> list[Path]:
        src = job.inputs[0]
        pdf = open_pdfium(src, job.opt("password", ""))
        try:
            indices = parse_pages(job.opt("pages"), len(pdf))
            parts: list[str] = []
            for n, index in enumerate(indices):
                page = pdf[index]
                textpage = page.get_textpage()
                text = textpage.get_text_bounded().replace("\r\n", "\n").replace("\r", "\n")
                textpage.close()
                page.close()
                if job.opt("page_marks", True):
                    parts.append(f"=== {index + 1}쪽 ===\n{text.strip()}\n")
                else:
                    parts.append(text.strip() + "\n")
                job.progress((n + 1) / len(indices))
        finally:
            pdf.close()
        job.output.write_text("\n".join(parts), encoding="utf-8")
        return [job.output]


# ---------------------------------------------------------------- 오피스 문서 → PDF

WORD_EXTS = {"docx", "doc", "rtf", "odt"}
EXCEL_EXTS = {"xlsx", "xls", "ods"}
PPT_EXTS = {"pptx", "ppt", "odp"}


def _office_com_to_pdf(src: Path, dst: Path) -> None:
    """Windows: 설치된 MS Office 로 PDF 저장 (pywin32 필요)."""
    import pythoncom
    import win32com.client

    ext = src.suffix.lower().lstrip(".")
    pythoncom.CoInitialize()  # GUI 백그라운드 스레드에서도 동작하도록
    app = doc = None
    started_by_us = True
    try:
        if ext in WORD_EXTS:
            app = win32com.client.DispatchEx("Word.Application")  # 새 Word 를 따로 띄움 (사용자 문서와 무관)
            app.Visible = False
            app.DisplayAlerts = 0
            doc = app.Documents.Open(str(src), ReadOnly=True, AddToRecentFiles=False, ConfirmConversions=False)
            doc.ExportAsFixedFormat(str(dst), 17)  # wdExportFormatPDF
            doc.Close(0)
        elif ext in EXCEL_EXTS:
            app = win32com.client.DispatchEx("Excel.Application")
            app.Visible = False
            app.DisplayAlerts = False
            doc = app.Workbooks.Open(str(src), ReadOnly=True)
            doc.ExportAsFixedFormat(0, str(dst))  # xlTypePDF
            doc.Close(False)
        else:
            # PowerPoint 는 프로그램이 하나만 떠서, 사용자가 이미 열어 둔 PowerPoint 에 붙는다 → 그때는 끄면 안 됨
            try:
                win32com.client.GetActiveObject("PowerPoint.Application")
                started_by_us = False
            except Exception:
                started_by_us = True
            app = win32com.client.DispatchEx("PowerPoint.Application")
            doc = app.Presentations.Open(str(src), True, False, False)  # ReadOnly, Untitled, WithWindow
            doc.SaveAs(str(dst), 32)  # ppSaveAsPDF
            doc.Close()
        doc = None
    except Exception as exc:
        raise ConvertError(f"MS Office 로 PDF 변환 실패: {exc}") from exc
    finally:
        if doc is not None:
            try:
                doc.Close(0) if ext in WORD_EXTS else doc.Close(False) if ext in EXCEL_EXTS else doc.Close()
            except Exception:
                pass
        if app is not None and started_by_us:
            try:
                app.Quit()
            except Exception:
                pass
        doc = app = None  # COM 객체를 모두 놓은 뒤에 CoUninitialize 해야 스레드가 죽지 않는다
        import gc

        gc.collect()
        pythoncom.CoUninitialize()


def _kill_tree(proc: subprocess.Popen) -> None:
    """프로세스와 그 자식까지 종료 (Windows 의 soffice.exe 는 soffice.bin 을 따로 띄우기 때문)."""
    if IS_WINDOWS_:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, **popen_kwargs())
    proc.kill()
    proc.wait()


def _libreoffice_convert(src: Path, dst: Path, fmt: str, job: Job, timeout: float = 600) -> None:
    """LibreOffice 로 변환. 이미 LibreOffice 가 열려 있어도 충돌하지 않도록 임시 프로필을 쓴다."""
    soffice = find_soffice()
    # 강제로 끈 LibreOffice 가 임시 폴더를 잠깐 잡고 있어도 정리 오류가 '취소' 결과를 덮지 않게
    with tempfile.TemporaryDirectory(prefix="fconv-lo-", ignore_cleanup_errors=True) as tmp:
        tmp_path = Path(tmp)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        cmd = [
            soffice,
            f"-env:UserInstallation={(tmp_path / 'profile').as_uri()}",
            "--headless", "--norestore", "--nolockcheck",
            "--convert-to", fmt, "--outdir", str(out_dir), str(src),
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **popen_kwargs())
        started = time.monotonic()
        try:
            while proc.poll() is None:
                job.cancel.check()
                elapsed = time.monotonic() - started
                if elapsed > timeout:
                    raise ConvertError(f"LibreOffice 변환 시간 초과 ({timeout:.0f}초)")
                job.progress(min(0.9, elapsed / 30))  # 실제 진행률을 알 수 없어 시간 기준으로 표시
                time.sleep(0.2)
        except BaseException:
            _kill_tree(proc)
            raise
        output = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
        produced = list(out_dir.glob("*"))
        if proc.returncode != 0 or not produced:
            raise ConvertError(f"LibreOffice 변환 실패: {output.strip()[-300:] or '결과 파일이 없습니다'}")
        shutil.move(str(produced[0]), str(dst))


@register
class OfficeToPdf(Converter):
    name = "office-to-pdf"
    default_output = "pdf"
    priority = 9
    title = "Word/엑셀/PPT → PDF"
    category = "document"
    description = "오피스 문서를 PDF 로 바꿉니다. Windows 에서는 MS Office, 없으면 LibreOffice 를 사용합니다."
    inputs = frozenset(WORD_EXTS | EXCEL_EXTS | PPT_EXTS)
    outputs = frozenset({"pdf"})
    options = (
        Option("engine", "choice", "자동", "변환 프로그램", "자동 = MS Office 우선, 없으면 LibreOffice",
               choices=("자동", "MS Office", "LibreOffice")),
    )

    def is_available(self) -> tuple[bool, str]:
        if find_soffice() or any(has_ms_office(a) for a in ("Word", "Excel", "PowerPoint")):
            return True, ""
        return False, ("MS Office(Word 등) 또는 LibreOffice 가 필요합니다. "
                       "LibreOffice 는 https://ko.libreoffice.org 에서 무료로 설치할 수 있습니다.")

    def convert(self, job: Job) -> list[Path]:
        src = job.inputs[0].resolve()
        ext = src.suffix.lower().lstrip(".")
        app = "Word" if ext in WORD_EXTS else "Excel" if ext in EXCEL_EXTS else "PowerPoint"
        engine = job.opt("engine", "자동")
        use_office = engine == "MS Office" or (engine == "자동" and has_ms_office(app))
        if use_office:
            if not has_ms_office(app):
                raise ConvertError(f"MS {app} 을(를) 찾을 수 없습니다")
            job.progress(0.1)
            _office_com_to_pdf(src, job.output.resolve())
        else:
            if not find_soffice():
                raise ConvertError("LibreOffice 를 찾을 수 없습니다. https://ko.libreoffice.org 에서 설치하세요")
            _libreoffice_convert(src, job.output, "pdf", job)
        return [job.output]


# ---------------------------------------------------------------- Markdown / Word → 텍스트·HTML

HTML_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<main>
{body}
</main>
</body>
</html>
"""

DEFAULT_CSS = """
body { margin: 0; background: #fff; color: #1f2328;
  font-family: "Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans KR", system-ui, sans-serif; line-height: 1.7; }
main { max-width: 860px; margin: 0 auto; padding: 32px 20px 64px; }
h1, h2 { border-bottom: 1px solid #d0d7de; padding-bottom: .3em; }
code { background: #f3f4f6; padding: .1em .35em; border-radius: 4px; font-family: Consolas, monospace; }
pre { background: #f6f8fa; padding: 14px; border-radius: 8px; overflow-x: auto; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; margin: 1em 0; }
th, td { border: 1px solid #d0d7de; padding: 6px 12px; }
th { background: #f6f8fa; }
blockquote { margin: 0; padding: 0 1em; color: #59636e; border-left: 4px solid #d0d7de; }
img { max-width: 100%; }
@media (prefers-color-scheme: dark) {
  body { background: #0d1117; color: #e6edf3; }
  code, pre, th { background: #161b22; }
  th, td, h1, h2 { border-color: #30363d; }
}
"""


@register
class MarkdownToHtml(Converter):
    name = "markdown-to-html"
    default_output = "html"
    priority = 3
    title = "Markdown → HTML"
    category = "document"
    description = "Markdown(.md) 문서를 보기 좋은 HTML 웹페이지로 바꿉니다 (표, 코드 블록, 목차 지원)."
    inputs = frozenset({"md", "txt"})
    outputs = frozenset({"html"})
    options = (
        Option("title", "str", "", "제목", "비우면 첫 번째 제목 또는 파일 이름", remember=False),
        Option("style", "bool", True, "기본 스타일 적용", "끄면 CSS 없이 순수 HTML"),
    )

    def convert(self, job: Job) -> list[Path]:
        import markdown

        from ..core.utils import detect_encoding

        src = job.inputs[0]
        text = src.read_text(encoding=detect_encoding(src))
        md = markdown.Markdown(extensions=["extra", "toc", "sane_lists", "nl2br"])
        body = md.convert(text)
        title = job.opt("title") or next(
            (t["name"] for t in getattr(md, "toc_tokens", []) if t.get("name")), src.stem)
        page = HTML_TEMPLATE.format(
            title=html.escape(html.unescape(title)), css=DEFAULT_CSS if job.opt("style", True) else "", body=body)
        job.output.write_text(page, encoding="utf-8")
        return [job.output]


@register
class DocxToText(Converter):
    name = "docx-to-text"
    default_output = "txt"
    priority = 2
    title = "Word(docx) → 텍스트"
    category = "document"
    description = "Word 문서의 글자(표 포함)를 텍스트 파일로 뽑아냅니다. Word 가 없어도 됩니다."
    inputs = frozenset({"docx"})
    outputs = frozenset({"txt", "md"})
    options = (Option("tables", "bool", True, "표 포함", "표 내용을 탭으로 구분해서 포함"),)

    def convert(self, job: Job) -> list[Path]:
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        try:
            document = docx.Document(str(job.inputs[0]))
        except Exception as exc:
            raise ConvertError(f"Word 문서를 열 수 없습니다: {job.inputs[0].name} ({exc})") from exc
        markdown_out = job.target == "md"
        lines: list[str] = []
        for block in document.iter_inner_content():
            if isinstance(block, Paragraph):
                text = block.text
                style = (block.style.name or "") if block.style is not None else ""
                if markdown_out and text.strip():
                    if style.startswith("Heading") and style[-1:].isdigit():
                        text = "#" * int(style[-1]) + " " + text
                    elif style == "Title":
                        text = "# " + text
                    elif "List" in style:
                        text = "- " + text
                lines.append(text)
            elif isinstance(block, Table) and job.opt("tables", True):
                rows = [[cell.text.strip().replace("\n", " ") for cell in row.cells] for row in block.rows]
                if markdown_out and rows:
                    lines.append("| " + " | ".join(rows[0]) + " |")
                    lines.append("|" + "---|" * len(rows[0]))
                    lines.extend("| " + " | ".join(r) + " |" for r in rows[1:])
                else:
                    lines.extend("\t".join(r) for r in rows)
                lines.append("")
        job.output.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        job.progress(1.0)
        return [job.output]


# ---------------------------------------------------------------- 텍스트/Markdown/HTML → PDF (Qt 로 그림 → 한글 글꼴 문제 없음)

_qt_app = None
PRINT_CSS = """
body { font-size: %(size)dpt; }
h1 { font-size: %(h1)dpt; } h2 { font-size: %(h2)dpt; } h3 { font-size: %(h3)dpt; }
pre, code { font-family: %(mono)s; background-color: #f3f4f6; }
table { border-collapse: collapse; } th, td { border: 1px solid #999; padding: 4px; }
th { background-color: #eeeeee; }
blockquote { color: #555; margin-left: 12px; }
"""


def _ensure_qt() -> None:
    """Qt 글꼴/그리기 기능을 쓰기 위한 QApplication (GUI 실행 중이면 이미 있음)."""
    global _qt_app
    import os

    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is None:
        if not IS_WINDOWS_ and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        # QGuiApplication 이 아닌 QApplication 으로 만들어야 같은 프로세스에서 GUI 창도 띄울 수 있다
        _qt_app = QApplication([])


def _default_font() -> str:
    from PySide6.QtGui import QFontDatabase

    families = set(QFontDatabase.families())
    for name in ("Malgun Gothic", "맑은 고딕", "Apple SD Gothic Neo", "Noto Sans CJK KR", "Noto Sans KR",
                 "NanumGothic", "나눔고딕", "NanumBarunGothic"):
        if name in families:
            return name
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()


def _mono_font() -> str:
    from PySide6.QtGui import QFontDatabase

    families = set(QFontDatabase.families())
    for name in ("D2Coding", "Consolas", "NanumGothicCoding", "Noto Sans Mono CJK KR", "DejaVu Sans Mono", "Courier New"):
        if name in families:
            return name
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()


@register
class TextToPdf(Converter):
    name = "text-to-pdf"
    title = "텍스트/Markdown/HTML → PDF"
    category = "document"
    description = "txt·md·html 문서를 PDF 로 만듭니다. 한글 글꼴(맑은 고딕 등)을 자동으로 사용합니다."
    inputs = frozenset({"txt", "md", "html"})
    outputs = frozenset({"pdf"})
    default_output = "pdf"
    priority = 4
    options = (
        Option("page_size", "choice", "A4", "용지 크기", choices=("A4", "A3", "A5", "B5", "Letter")),
        Option("landscape", "bool", False, "가로 방향"),
        Option("font_size", "int", 11, "글자 크기(pt)", min=6, max=48),
        Option("margin_mm", "float", 20.0, "여백(mm)", min=0, max=60),
        Option("font", "str", "", "글꼴", "비우면 자동 (Windows: 맑은 고딕)"),
        Option("monospace", "bool", False, "고정폭 글꼴(txt)", "txt 파일을 코드처럼 고정폭 글꼴로"),
    )

    def convert(self, job: Job) -> list[Path]:
        _ensure_qt()
        from PySide6.QtCore import QMarginsF
        from PySide6.QtGui import QFont, QPageLayout, QPageSize, QPdfWriter, QTextDocument

        from ..core.utils import detect_encoding

        src = job.inputs[0]
        text = src.read_text(encoding=detect_encoding(src))
        ext = src.suffix.lower().lstrip(".")
        size = job.opt("font_size", 11)
        family = job.opt("font") or _default_font()
        mono = _mono_font()

        doc = QTextDocument()
        doc.setDefaultFont(QFont(mono if (ext == "txt" and job.opt("monospace")) else family, size))
        doc.setDefaultStyleSheet(PRINT_CSS % {"size": size, "h1": size * 2, "h2": int(size * 1.6),
                                              "h3": int(size * 1.3), "mono": f"'{mono}'"})
        if ext == "md":
            import markdown

            doc.setHtml(markdown.markdown(text, extensions=["extra", "sane_lists", "nl2br"]))
        elif ext in ("html", "htm"):
            doc.setHtml(text)
        else:
            doc.setPlainText(text)
        job.progress(0.4)

        page_ids = {"A4": QPageSize.PageSizeId.A4, "A3": QPageSize.PageSizeId.A3, "A5": QPageSize.PageSizeId.A5,
                    "B5": QPageSize.PageSizeId.B5, "Letter": QPageSize.PageSizeId.Letter}
        writer = QPdfWriter(str(job.output))
        writer.setTitle(src.stem)
        writer.setCreator("fileconv")
        writer.setResolution(300)
        margin = job.opt("margin_mm", 20.0)
        layout = QPageLayout(
            QPageSize(page_ids[job.opt("page_size", "A4")]),
            QPageLayout.Orientation.Landscape if job.opt("landscape") else QPageLayout.Orientation.Portrait,
            QMarginsF(margin, margin, margin, margin),
            QPageLayout.Unit.Millimeter,
        )
        writer.setPageLayout(layout)
        doc.print_(writer)
        del writer  # 파일을 닫아 저장을 끝냄
        if not job.output.exists() or job.output.stat().st_size == 0:
            raise ConvertError("PDF 를 만들지 못했습니다")
        return [job.output]
