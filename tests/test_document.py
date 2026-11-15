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


# ---------------------------------------------------------------- 3차 검토에서 찾은 문제

@pytest.fixture
def aes_pdfs(pdf_file, tmp_path):
    """요즘 PDF 프로그램이 쓰는 AES 방식으로 잠근 PDF (pikepdf 는 img2pdf 와 함께 설치됨)."""
    import pikepdf

    paths = {}
    with pikepdf.open(pdf_file) as pdf:
        for name, level in (("aes256", 6), ("aes128", 4)):
            paths[name] = tmp_path / f"{name}.pdf"
            pdf.save(paths[name], encryption=pikepdf.Encryption(user="1234", owner="1234", R=level))
    return paths


@pytest.mark.parametrize("kind", ["aes256", "aes128"])
def test_aes_encrypted_pdf_opens_with_password(aes_pdfs, kind):
    from fileconv.core import engine

    locked = aes_pdfs[kind]
    [unlocked] = convert([locked], "pdf", options={"password": "1234"})
    reader = PdfReader(unlocked)
    assert not reader.is_encrypted and len(reader.pages) == 4
    outs = convert([locked], "pdf", using="pdf-split", options={"password": "1234", "every": "2"})
    assert len(outs) == 2
    [small] = convert([locked], "pdf", using="pdf-compress", options={"password": "1234"})
    assert small.exists()
    results = engine.run(engine.plan([locked], "pdf", options={"password": "wrong"}))
    assert "암호가 걸린" in results[0].error


def test_new_password_is_aes256(pdf_file, tmp_path):
    import pikepdf

    [locked] = convert([pdf_file], "pdf", options={"new_password": "abcd"}, output=tmp_path / "locked.pdf")
    with pikepdf.open(locked, password="abcd") as pdf:
        assert pdf.encryption.R == 6 and pdf.encryption.bits == 256  # 구식 RC4 가 아니라 AES-256


def test_scanned_pdf_to_text_is_reported(pdf_file):
    """글자가 없는(그림뿐인) PDF 를 텍스트로 바꾸면 빈 파일을 '성공'으로 내놓지 않는다."""
    from fileconv.core import engine

    results = engine.run(engine.plan([pdf_file], "txt"))
    assert not results[0].ok and "글자가 들어 있지 않은" in results[0].error
    assert not pdf_file.with_suffix(".txt").exists()


def test_cancelled_multi_output_leaves_no_files(pdf_file):
    """PDF → 이미지를 도중에 취소하면 이미 만든 쪽 이미지도 남기지 않는다."""
    from fileconv.core import engine
    from fileconv.core.base import CancelToken

    token = CancelToken()
    calls = []

    def on_progress(_i, _f):
        calls.append(1)
        if len(calls) == 2:
            token.cancel()

    results = engine.run(engine.plan([pdf_file], "png"), on_progress=on_progress, cancel=token)
    assert results[0].cancelled
    assert not list(pdf_file.parent.glob("문서_p*.png"))


def test_failed_split_leaves_no_files(pdf_file, monkeypatch):
    from pypdf import PdfWriter

    from fileconv.core import engine

    original = PdfWriter.write
    count = {"n": 0}

    def flaky(self, stream):
        count["n"] += 1
        if count["n"] == 3:
            raise OSError("디스크 오류 흉내")
        return original(self, stream)

    monkeypatch.setattr(PdfWriter, "write", flaky)
    results = engine.run(engine.plan([pdf_file], "pdf", using="pdf-split"))
    assert not results[0].ok
    assert not list(pdf_file.parent.glob("문서_p*.pdf"))


def _blue_pixels(pdf_path) -> int:
    import numpy as np
    import pypdfium2

    page = pypdfium2.PdfDocument(str(pdf_path))[0].render(scale=1).to_pil().convert("RGB")
    arr = np.asarray(page)
    return int(((arr[..., 2] > 200) & (arr[..., 0] < 60) & (arr[..., 1] < 60)).sum())


@pytest.mark.parametrize("ext", ["md", "html"])
def test_local_images_appear_in_pdf(tmp_path, ext):
    """문서와 같은 폴더의 그림이 PDF 에 들어간다 (프로그램을 어느 폴더에서 실행하든)."""
    folder = tmp_path / "문서 폴더"
    folder.mkdir()
    Image.new("RGB", (200, 100), "blue").save(folder / "pic.png")
    doc = folder / f"그림.{ext}"
    doc.write_text("# 제목\n\n![그림](pic.png)\n" if ext == "md" else "<h1>제목</h1><p><img src='pic.png'></p>",
                   encoding="utf-8")
    [pdf] = convert([doc], "pdf")
    assert _blue_pixels(pdf) > 5000


def test_wide_image_is_not_cut_off_in_pdf(tmp_path):
    """쪽 너비보다 큰 그림(화면 캡처 등)은 잘리지 않고 쪽 너비에 맞게 줄어든다."""
    import numpy as np
    import pypdfium2

    wide = Image.new("RGB", (1600, 200), "blue")
    wide.paste((255, 0, 0), (1500, 0, 1600, 200))  # 오른쪽 끝에 빨간 띠 → 잘리면 안 보인다
    wide.save(tmp_path / "wide.png")
    doc = tmp_path / "캡처.md"
    doc.write_text("![캡처](wide.png)\n", encoding="utf-8")
    [pdf] = convert([doc], "pdf")
    arr = np.asarray(pypdfium2.PdfDocument(str(pdf))[0].render(scale=1).to_pil().convert("RGB"))
    red = int(((arr[..., 0] > 200) & (arr[..., 1] < 60) & (arr[..., 2] < 60)).sum())
    assert red > 100


def _shared_image_pdf(tmp_path, pages=5):
    """모든 쪽이 같은 그림 하나를 함께 쓰는 PDF (머리글 로고·배경 그림과 같은 구조)."""
    import io

    import img2pdf
    import numpy as np
    from pypdf import PdfWriter

    noisy = tmp_path / "logo.jpg"
    Image.fromarray(np.random.default_rng(0).integers(0, 255, (900, 700, 3), dtype=np.uint8)).save(noisy, quality=95)
    one = PdfReader(io.BytesIO(img2pdf.convert(str(noisy))))
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_page(one.pages[0])
    path = tmp_path / "shared.pdf"
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def test_pdf_compress_recompresses_shared_image_once(tmp_path, monkeypatch):
    src = _shared_image_pdf(tmp_path)
    image_cls = type(next(iter(PdfReader(src).pages[0].images)))
    calls = []
    original = image_cls.replace

    def counting(self, *args, **kwargs):
        calls.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(image_cls, "replace", counting)
    [small] = convert([src], "pdf", using="pdf-compress", options={"image_quality": "50", "max_image_px": "0"})
    assert len(calls) == 1  # 5쪽이 함께 쓰는 그림을 5번 다시 압축하지 않는다
    assert small.stat().st_size < src.stat().st_size / 2 and len(PdfReader(small).pages) == 5


def test_pdf_compress_keeps_one_bit_scans(tmp_path):
    """1비트(흑백) 스캔 그림은 JPEG 로 바꾸면 흐려지고 오히려 커지므로 건드리지 않는다."""
    import img2pdf
    import numpy as np

    rng = np.random.default_rng(1)
    Image.fromarray((rng.integers(0, 2, (800, 600)) * 255).astype(np.uint8)).convert("1").save(tmp_path / "bw.png")
    Image.fromarray(rng.integers(0, 255, (900, 700, 3), dtype=np.uint8)).save(tmp_path / "color.jpg", quality=95)
    src = tmp_path / "mixed.pdf"
    src.write_bytes(img2pdf.convert([str(tmp_path / "bw.png"), str(tmp_path / "color.jpg")]))
    [small] = convert([src], "pdf", using="pdf-compress", options={"image_quality": "40"})
    reader = PdfReader(small)
    assert reader.pages[0].images[0].image.mode == "1"
    assert small.stat().st_size < src.stat().st_size


def test_compress_result_note(tmp_path):
    from fileconv.core import engine

    src = _shared_image_pdf(tmp_path, pages=1)
    [result] = engine.run(engine.plan([src], "pdf", using="pdf-compress", options={"image_quality": "40"}))
    assert result.ok and "→" in result.note and "%" in result.note


def _ink_box(pdf_path, page=0):
    """쪽에서 흰색이 아닌 부분의 범위 (왼쪽, 위, 오른쪽, 아래) — 단위 pt."""
    import numpy as np
    import pypdfium2

    arr = np.asarray(pypdfium2.PdfDocument(str(pdf_path))[page].render(scale=1).to_pil().convert("L"))
    ys, xs = np.nonzero(arr < 200)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


@pytest.mark.parametrize("margin_mm", [10, 30])
def test_pdf_margin_matches_option(tmp_path, margin_mm):
    """'여백' 옵션에 적은 값이 실제 여백이다 (예전에는 2cm 가 더 붙어 20mm → 40mm 였다)."""
    txt = tmp_path / "가득.txt"
    txt.write_text(("가나다라마바사아자차카타파하 " * 40 + "\n") * 60, encoding="utf-8")
    [pdf] = convert([txt], "pdf", options={"margin_mm": str(margin_mm)})
    left, top, right, bottom = _ink_box(pdf)
    expected = margin_mm / 25.4 * 72
    assert expected - 2 <= left <= expected + 8 and expected - 2 <= top <= expected + 8
    assert 595 - expected - 14 <= right <= 595 - expected + 2  # A4 너비 595pt, 줄바꿈 때문에 오른쪽은 조금 남을 수 있음
    assert bottom <= 842 - expected + 2


def test_pdf_pages_are_numbered(tmp_path):
    import pypdfium2

    txt = tmp_path / "긴글.txt"
    txt.write_text("줄\n" * 200, encoding="utf-8")
    [pdf] = convert([txt], "pdf")
    doc = pypdfium2.PdfDocument(str(pdf))
    assert len(doc) >= 3
    for index in (0, len(doc) - 1):
        assert doc[index].get_textpage().get_text_bounded().split()[-1] == str(index + 1)
