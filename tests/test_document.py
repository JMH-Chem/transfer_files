import pytest
from PIL import Image
from pypdf import PdfReader

from fileconv.core.tools import find_soffice

from helpers import convert


def test_pdf_merge_pages_rotate(pdf_file, text_pdf):
    [out] = convert([pdf_file, text_pdf], "pdf", merge=True)
    assert len(PdfReader(out).pages) == 5
    [out] = convert([pdf_file], "pdf", options={"pages": "2-3", "rotate": "90"})
    reader = PdfReader(out)
    assert len(reader.pages) == 2 and reader.pages[0].rotation == 90


def test_pdf_split(pdf_file):
    outs = convert([pdf_file], "pdf", using="pdf-split")
    assert [o.name for o in outs] == ["문서_p001.pdf", "문서_p002.pdf", "문서_p003.pdf", "문서_p004.pdf"]
    outs = convert([pdf_file], "pdf", using="pdf-split", options={"every": "3"}, overwrite=True)
    assert [len(PdfReader(o).pages) for o in outs] == [3, 1]


def test_pdf_to_images(pdf_file):
    outs = convert([pdf_file], "png", options={"dpi": "72", "pages": "1,3"})
    assert [o.name for o in outs] == ["문서_p001.png", "문서_p003.png"]
    assert Image.open(outs[0]).size == (300, 225)  # 400x300px(96dpi 기준 300x225pt) @72dpi


def test_pdf_to_text(text_pdf):
    [out] = convert([text_pdf], "txt")
    text = out.read_text(encoding="utf-8")
    assert "=== 1쪽 ===" in text and "Hello fileconv" in text


def test_markdown_to_html(tmp_path):
    md = tmp_path / "readme.md"
    md.write_text("# 제목\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```\ncode\n```\n", encoding="utf-8")
    [out] = convert([md], "html")
    html = out.read_text(encoding="utf-8")
    assert "<title>제목</title>" in html and "<table>" in html and "<code>" in html


def test_docx_to_text_and_md(docx_file):
    [txt] = convert([docx_file], "txt")
    content = txt.read_text(encoding="utf-8")
    assert "주간 보고" in content and "CLI\t완료" in content
    [md] = convert([docx_file], "md")
    content = md.read_text(encoding="utf-8")
    assert "# 주간 보고" in content and "| CLI | 완료 |" in content


@pytest.mark.skipif(not find_soffice(), reason="LibreOffice 가 없음")
def test_docx_to_pdf_libreoffice(docx_file):
    [pdf] = convert([docx_file], "pdf", options={"engine": "LibreOffice"})
    assert len(PdfReader(pdf).pages) >= 1


def test_text_and_markdown_to_pdf(tmp_path):
    import pypdfium2

    md = tmp_path / "회의록.md"
    md.write_text("# 회의록\n\n| 이름 | 역할 |\n|---|---|\n| 홍길동 | 개발 |\n", encoding="utf-8")
    txt = tmp_path / "메모.txt"
    txt.write_text("한글 메모입니다\n" * 200, encoding="cp949")
    outs = convert([md, txt], "pdf", options={"page_size": "A5", "font_size": "12"})
    md_pdf = pypdfium2.PdfDocument(str(outs[0]))
    assert "홍길동" in md_pdf[0].get_textpage().get_text_bounded()
    txt_pdf = pypdfium2.PdfDocument(str(outs[1]))
    assert len(txt_pdf) > 1  # 여러 쪽으로 나뉨
    w, h = txt_pdf[0].get_size()
    assert round(w) == 420 and round(h) == 595  # A5


def test_pdf_password_roundtrip(pdf_file, tmp_path):
    [locked] = convert([pdf_file], "pdf", options={"new_password": "1234"}, output=tmp_path / "locked.pdf")
    assert PdfReader(locked).is_encrypted
    from fileconv.core import engine

    results = engine.run(engine.plan([locked], "pdf"))
    assert "암호가 걸린" in results[0].error
    [unlocked] = convert([locked], "pdf", options={"password": "1234"})
    assert not PdfReader(unlocked).is_encrypted and len(PdfReader(unlocked).pages) == 4


def test_pdf_compress(tmp_path):
    import img2pdf
    import numpy as np

    noisy = tmp_path / "scan.jpg"
    Image.fromarray(np.random.default_rng(0).integers(0, 255, (1800, 1400, 3), dtype=np.uint8)).save(noisy, quality=95)
    big = tmp_path / "big.pdf"
    big.write_bytes(img2pdf.convert(str(noisy)))
    [small] = convert([big], "pdf", using="pdf-compress", options={"image_quality": "40", "max_image_px": "800"})
    assert small.stat().st_size < big.stat().st_size / 3
    assert len(PdfReader(small).pages) == 1


def test_hwp_to_pdf_reports_unavailable_without_hancom(tmp_path):
    from fileconv.core import engine
    from fileconv.core.tools import has_hancom

    if has_hancom():
        pytest.skip("한컴오피스가 설치된 PC")
    f = tmp_path / "a.hwp"
    f.write_bytes(b"x")
    results = engine.run(engine.plan([f], "pdf"))
    assert "한컴오피스" in results[0].error


def test_split_empty_pdf_reports_error(tmp_path):
    from pypdf import PdfWriter

    from fileconv.core import engine

    empty = tmp_path / "empty.pdf"
    with open(empty, "wb") as fh:
        PdfWriter().write(fh)
    results = engine.run(engine.plan([empty], "pdf", using="pdf-split"))
    assert not results[0].ok and "페이지가 없는" in results[0].error
