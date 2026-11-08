"""이미지 변환: 여러 이미지 → PDF, 이미지 형식끼리 변환 (Pillow, img2pdf)."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps

from ..core.base import Converter, Job, Option
from ..core.errors import ConvertError
from ..core.registry import register

try:  # 아이폰 사진(HEIC/HEIF) 지원
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_OK = True
except Exception:  # pragma: no cover
    HEIF_OK = False

IMAGE_EXTS = {"jpg", "png", "webp", "bmp", "gif", "tiff", "heic", "heif", "ico", "jfif"}
IMAGE_OUT = {"jpg", "png", "webp", "bmp", "gif", "tiff", "ico"}

PAGE_SIZES_MM = {"A3": (297, 420), "A4": (210, 297), "A5": (148, 210), "B5": (176, 250), "Letter": (215.9, 279.4)}
# 손실 압축 원본은 PDF에 넣을 때 PNG(무손실)로 바꾸면 용량만 커지므로 JPEG 로 다시 저장
_LOSSY_SOURCES = {"HEIF", "HEIC", "AVIF", "WEBP", "MPO"}
_PIL_FORMATS = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP", "bmp": "BMP", "gif": "GIF", "tiff": "TIFF", "ico": "ICO"}


def open_image(path: Path) -> Image.Image:
    try:
        img = Image.open(path)
        img.load()
    except Exception as exc:
        raise ConvertError(f"이미지를 열 수 없습니다: {path.name} ({exc})") from exc
    return img


def flatten(img: Image.Image, background=(255, 255, 255)) -> Image.Image:
    """투명 배경을 흰색으로 채우고 RGB(또는 흑백)로 바꾼다."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, background)
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    if img.mode in ("RGB", "L"):
        return img
    return img.convert("RGB")


def _pdf_ready_bytes(path: Path) -> bytes:
    """img2pdf 가 그대로 넣을 수 있는 이미지 바이트. JPEG/일반 PNG 는 재압축 없이 원본 그대로."""
    img = open_image(path)
    fmt = (img.format or "").upper()
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    if fmt == "JPEG" and img.mode in ("RGB", "L", "CMYK"):
        return path.read_bytes()
    if fmt == "PNG" and not has_alpha and img.mode in ("RGB", "L", "P", "1") and not img.info.get("interlace"):
        return path.read_bytes()
    img = ImageOps.exif_transpose(img)
    flat = flatten(img)
    buf = io.BytesIO()
    if fmt in _LOSSY_SOURCES:
        flat.save(buf, "JPEG", quality=95)
    else:
        flat.save(buf, "PNG", optimize=False)
    return buf.getvalue()


@register
class ImagesToPdf(Converter):
    name = "images-to-pdf"
    title = "이미지 → PDF"
    category = "image"
    description = "이미지 여러 장을 PDF 하나로 묶거나, 이미지마다 PDF를 만듭니다. JPG는 화질 손실 없이 들어갑니다."
    inputs = frozenset(IMAGE_EXTS)
    outputs = frozenset({"pdf"})
    can_merge = True
    merge_default = True
    priority = 10
    default_output = "pdf"
    options = (
        Option("page_size", "choice", "원본크기", "용지 크기", "원본크기 = 이미지 크기 그대로 한 쪽씩",
               choices=("원본크기", "A4", "A3", "A5", "B5", "Letter")),
        Option("orientation", "choice", "자동", "용지 방향", "자동 = 이미지 모양에 맞춰 가로/세로",
               choices=("자동", "세로", "가로")),
        Option("margin_mm", "float", 0.0, "여백(mm)", "용지 크기를 정했을 때만 적용", min=0, max=100),
        Option("dpi", "int", 0, "DPI", "원본크기일 때 인쇄 크기 기준 (0 = 이미지 정보 사용)", min=0, max=1200),
    )

    def convert(self, job: Job) -> list[Path]:
        import img2pdf

        page_size = job.opt("page_size", "원본크기")
        orientation = job.opt("orientation", "자동")
        margin = img2pdf.mm_to_pt(job.opt("margin_mm", 0.0))
        dpi = job.opt("dpi", 0)

        if page_size == "원본크기":
            layout = img2pdf.get_fixed_dpi_layout_fun((dpi, dpi)) if dpi else img2pdf.default_layout_fun
        else:
            w_mm, h_mm = PAGE_SIZES_MM[page_size]
            w, h = img2pdf.mm_to_pt(w_mm), img2pdf.mm_to_pt(h_mm)
            if orientation == "가로":
                w, h = max(w, h), min(w, h)
            layout = img2pdf.get_layout_fun(
                (w, h),
                None,
                (margin, margin) if margin else None,
                img2pdf.FitMode.into,
                orientation == "자동",
            )

        total = len(job.inputs)
        images: list[bytes] = []
        for i, path in enumerate(job.inputs):
            images.append(_pdf_ready_bytes(path))
            job.progress(0.8 * (i + 1) / total)
        try:
            pdf = img2pdf.convert(images, layout_fun=layout, rotation=img2pdf.Rotation.ifvalid)
        except Exception:
            # 특이한 형식이면 Pillow 로 다시 저장해서 한 번 더 시도
            images = []
            for path in job.inputs:
                buf = io.BytesIO()
                flatten(ImageOps.exif_transpose(open_image(path))).save(buf, "PNG")
                images.append(buf.getvalue())
            try:
                pdf = img2pdf.convert(images, layout_fun=layout)
            except Exception as exc:
                raise ConvertError(f"PDF 만들기 실패: {exc}") from exc
        job.output.write_bytes(pdf)
        return [job.output]


@register
class ImageConvert(Converter):
    name = "image-convert"
    title = "이미지 형식 변환"
    category = "image"
    description = "JPG/PNG/WEBP/HEIC 등 이미지 형식을 바꾸고, 크기 줄이기·회전·흑백 변환도 할 수 있습니다."
    inputs = frozenset(IMAGE_EXTS)
    outputs = frozenset(IMAGE_OUT)
    default_output = "jpg"
    options = (
        Option("quality", "int", 90, "품질", "JPG/WEBP 품질 (1~100)", min=1, max=100),
        Option("max_width", "int", 0, "최대 가로(px)", "0 = 줄이지 않음. 비율은 유지", min=0),
        Option("max_height", "int", 0, "최대 세로(px)", "0 = 줄이지 않음. 비율은 유지", min=0),
        Option("rotate", "choice", "0", "회전", "시계 방향 회전 각도", choices=("0", "90", "180", "270")),
        Option("flip", "choice", "안 함", "뒤집기", choices=("안 함", "좌우", "상하")),
        Option("grayscale", "bool", False, "흑백으로"),
        Option("keep_exif", "bool", False, "촬영 정보 유지", "JPG/WEBP 저장 시 EXIF(촬영일, 위치 등) 유지"),
    )

    def convert(self, job: Job) -> list[Path]:
        src = job.inputs[0]
        img = open_image(src)
        img = ImageOps.exif_transpose(img)  # 회전 정보 적용 (EXIF 의 회전 태그도 함께 정리됨)
        exif = img.info.get("exif") if job.opt("keep_exif") else None
        job.progress(0.3)

        max_w, max_h = job.opt("max_width", 0), job.opt("max_height", 0)
        if max_w or max_h:
            limit = (max_w or img.width, max_h or img.height)
            if img.width > limit[0] or img.height > limit[1]:
                img = img.copy()
                img.thumbnail(limit, Image.Resampling.LANCZOS)

        turns = {"90": Image.Transpose.ROTATE_270, "180": Image.Transpose.ROTATE_180, "270": Image.Transpose.ROTATE_90}
        if job.opt("rotate", "0") in turns:
            img = img.transpose(turns[job.opt("rotate")])
        if job.opt("flip", "안 함") == "좌우":
            img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        elif job.opt("flip") == "상하":
            img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        if job.opt("grayscale"):
            img = img.convert("LA") if img.mode in ("RGBA", "LA", "PA") else img.convert("L")

        fmt = _PIL_FORMATS[job.target if job.target in _PIL_FORMATS else "png"]
        save_kwargs: dict = {}
        if fmt == "JPEG":
            img = flatten(img.convert("RGBA") if img.mode == "LA" else img)
            if img.mode == "CMYK":
                img = img.convert("RGB")
            save_kwargs = {"quality": job.opt("quality", 90), "optimize": True}
        elif fmt == "WEBP":
            save_kwargs = {"quality": job.opt("quality", 90), "method": 4}
        elif fmt == "BMP":
            img = flatten(img)
        elif fmt == "ICO":
            size = min(256, max(img.width, img.height))
            save_kwargs = {"sizes": [(s, s) for s in (16, 32, 48, 64, 128, 256) if s <= size] or [(size, size)]}
        elif fmt == "PNG":
            save_kwargs = {"optimize": True}
        if exif and fmt in ("JPEG", "WEBP"):
            save_kwargs["exif"] = exif
        job.progress(0.6)
        try:
            img.save(job.output, fmt, **save_kwargs)
        except Exception as exc:
            raise ConvertError(f"이미지 저장 실패 ({src.name} → {fmt}): {exc}") from exc
        return [job.output]
