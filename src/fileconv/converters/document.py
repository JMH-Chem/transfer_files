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
from ..core.tools import find_soffice, has_ms_office, popen_kwargs
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


# ---------------------------------------------------------------- PDF → PDF

@register
class PdfTools(Converter):
    name = "pdf-tools"
    default_output = "pdf"
    title = "PDF 합치기·페이지 추출·회전"
    category = "document"
    description = "여러 PDF 를 하나로 합치거나, 원하는 페이지만 뽑거나, 페이지를 회전합니다."
    inputs = frozenset({"pdf"})
    outputs = frozenset({"pdf"})
    can_merge = True
    merge_default = True
    priority = 10
    options = (
        PAGES_OPT,
        Option("rotate", "choice", "0", "회전", "시계 방향 회전 각도", choices=("0", "90", "180", "270")),
        PASSWORD_OPT,
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
    app = None
    try:
        if ext in WORD_EXTS:
            app = win32com.client.DispatchEx("Word.Application")
            app.Visible = False
            app.DisplayAlerts = 0
            doc = app.Documents.Open(str(src), ReadOnly=True, AddToRecentFiles=False, ConfirmConversions=False)
            try:
                doc.ExportAsFixedFormat(str(dst), 17)  # wdExportFormatPDF
            finally:
                doc.Close(0)
        elif ext in EXCEL_EXTS:
            app = win32com.client.DispatchEx("Excel.Application")
            app.Visible = False
            app.DisplayAlerts = False
            wb = app.Workbooks.Open(str(src), ReadOnly=True)
            try:
                wb.ExportAsFixedFormat(0, str(dst))  # xlTypePDF
            finally:
                wb.Close(False)
        else:
            app = win32com.client.DispatchEx("PowerPoint.Application")
            pres = app.Presentations.Open(str(src), True, False, False)  # ReadOnly, Untitled, WithWindow
            try:
                pres.SaveAs(str(dst), 32)  # ppSaveAsPDF
            finally:
                pres.Close()
    except Exception as exc:
        raise ConvertError(f"MS Office 로 PDF 변환 실패: {exc}") from exc
    finally:
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


def _libreoffice_convert(src: Path, dst: Path, fmt: str, job: Job, timeout: float = 600) -> None:
    """LibreOffice 로 변환. 이미 LibreOffice 가 열려 있어도 충돌하지 않도록 임시 프로필을 쓴다."""
    soffice = find_soffice()
    with tempfile.TemporaryDirectory(prefix="fconv-lo-") as tmp:
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
            proc.kill()
            proc.wait()
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
        Option("title", "str", "", "제목", "비우면 첫 번째 제목 또는 파일 이름"),
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
