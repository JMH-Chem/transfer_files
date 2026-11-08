from PIL import Image
from pypdf import PdfReader

from helpers import convert


def test_images_to_single_pdf(images, tmp_path):
    [pdf] = convert(images, "pdf", merge=True, output=tmp_path / "앨범.pdf")
    reader = PdfReader(pdf)
    assert len(reader.pages) == 4
    # 원본크기: 첫 쪽은 400x300 이미지 비율
    box = reader.pages[0].mediabox
    assert abs(float(box.width) / float(box.height) - 4 / 3) < 0.01


def test_images_to_a4_with_margin(images):
    [pdf] = convert(images[:2], "pdf", merge=True, options={"page_size": "A4", "margin_mm": "10"})
    pages = PdfReader(pdf).pages
    sizes = {(round(float(p.mediabox.width)), round(float(p.mediabox.height))) for p in pages}
    # 자동 방향: 가로 이미지는 가로 A4, 세로 이미지는 세로 A4
    assert sizes == {(842, 595), (595, 842)}


def test_each_image_separate_pdf(images):
    outs = convert(images[:3], "pdf")
    assert len(outs) == 3 and all(len(PdfReader(o).pages) == 1 for o in outs)


def test_image_convert_and_resize(images):
    [out] = convert([images[3]], "jpg", options={"max_width": "100"})  # 투명 PNG → JPG
    img = Image.open(out)
    assert img.format == "JPEG" and img.size == (100, 100)
    assert img.getpixel((50, 50))[0] > 100  # 투명 부분이 흰색과 섞여 밝아짐 (검정 아님)


def test_image_to_webp_and_ico(images):
    [webp] = convert([images[0]], "webp", options={"quality": "50"})
    assert Image.open(webp).format == "WEBP"
    [ico] = convert([images[0]], "ico")
    assert Image.open(ico).format == "ICO"


def test_rotate_flip_grayscale(images):
    [out] = convert([images[0]], "png", options={"rotate": "90", "flip": "좌우", "grayscale": "true"})
    img = Image.open(out)
    assert img.size == (300, 400) and img.mode == "L"
    [jpg] = convert([images[3]], "jpg", options={"grayscale": "true"})  # 투명 + 흑백 → JPG
    assert Image.open(jpg).mode in ("L", "RGB")
