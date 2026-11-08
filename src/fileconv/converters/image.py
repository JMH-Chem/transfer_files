"""이미지 변환: 여러 이미지 → PDF, 이미지 형식끼리 변환 (Pillow, img2pdf)."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence

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


def to_8bit(img: Image.Image) -> Image.Image:
    """16비트·32비트 흑백(I;16, I, F)은 값 범위를 0~255 로 맞춰 줄이고, CMYK 는 RGB 로 바꾼다.
    (그냥 convert('RGB') 하면 16비트 값이 잘려서 거의 하얗게 나온다)"""
    if img.mode.startswith("I;16") or img.mode in ("I", "F"):
        import numpy as np

        arr = np.asarray(img, dtype=np.float64)
        top = float(arr.max()) if arr.size else 1.0
        scale = 65535.0 if img.mode.startswith("I;16") or 255 < top <= 65535 else (255.0 if top <= 255 else top)
        return Image.fromarray(np.clip(arr / scale * 255.0, 0, 255).astype(np.uint8), "L")
    if img.mode == "CMYK":
        return img.convert("RGB")
    return img


def flatten(img: Image.Image, background=(255, 255, 255)) -> Image.Image:
    """투명 배경을 흰색으로 채우고 RGB(또는 흑백)로 바꾼다."""
    img = to_8bit(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, background)
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    if img.mode in ("RGB", "L"):
        return img
    return img.convert("RGB")


def _pdf_ready_pages(path: Path) -> list[bytes]:
    """img2pdf 에 넣을 이미지 바이트들 (보통 1개, 여러 쪽 TIFF 는 쪽마다 1개).
    JPEG/일반 PNG 는 재압축 없이 원본 그대로 넣어 화질 손실이 없다."""
    img = open_image(path)
    fmt = (img.format or "").upper()
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    if fmt == "JPEG" and img.mode in ("RGB", "L", "CMYK"):
        return [path.read_bytes()]
    if fmt == "PNG" and not has_alpha and img.mode in ("RGB", "L", "P", "1") and not img.info.get("interlace"):
        return [path.read_bytes()]
    frames = [img]
    if fmt == "TIFF" and getattr(img, "n_frames", 1) > 1:  # 여러 쪽 스캔 TIFF
        frames = [frame.copy() for frame in ImageSequence.Iterator(img)]
    pages: list[bytes] = []
    for frame in frames:
        flat = flatten(ImageOps.exif_transpose(frame))
        buf = io.BytesIO()
        if fmt in _LOSSY_SOURCES:
            flat.save(buf, "JPEG", quality=95)
        else:
            flat.save(buf, "PNG", optimize=False)
        pages.append(buf.getvalue())
    return pages


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
            images.extend(_pdf_ready_pages(path))
            job.progress(0.8 * (i + 1) / total)
        try:
            pdf = img2pdf.convert(images, layout_fun=layout, rotation=img2pdf.Rotation.ifvalid)
        except Exception:
            # 특이한 형식이면 Pillow 로 다시 저장해서 한 번 더 시도
            images = []
            for path in job.inputs:
                img = open_image(path)
                for frame in ImageSequence.Iterator(img) if (img.format or "").upper() == "TIFF" else [img]:
                    buf = io.BytesIO()
                    flatten(ImageOps.exif_transpose(frame.copy())).save(buf, "PNG")
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

    def _transform(self, img: Image.Image, job: Job, fmt: str) -> Image.Image:
        """크기 줄이기·회전·뒤집기·흑백·저장 형식에 맞는 색 변환 (애니메이션이면 프레임마다)."""
        img = to_8bit(img)
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
            img = img.convert("LA") if img.mode in ("RGBA", "LA", "PA", "P") and _has_alpha(img) else img.convert("L")
        if fmt in ("JPEG", "BMP"):
            img = flatten(img.convert("RGBA") if img.mode in ("LA", "PA") else img)
        elif img.mode not in ("1", "L", "LA", "P", "RGB", "RGBA"):
            img = img.convert("RGBA" if _has_alpha(img) else "RGB")
        return img

    def convert(self, job: Job) -> list[Path]:
        src = job.inputs[0]
        img = open_image(src)
        fmt = _PIL_FORMATS[job.target if job.target in _PIL_FORMATS else "png"]
        exif = None
        if job.opt("keep_exif"):
            exif = ImageOps.exif_transpose(img).info.get("exif")
        animated = getattr(img, "n_frames", 1) > 1 and fmt in ("GIF", "WEBP") and (img.format or "") in ("GIF", "WEBP", "PNG")
        if animated:  # 움직이는 GIF/WEBP 는 모든 프레임을 유지
            frames, durations = [], []
            for frame in ImageSequence.Iterator(img):
                durations.append(frame.info.get("duration", img.info.get("duration", 100)))
                frames.append(self._transform(frame.convert("RGBA"), job, fmt))
                job.cancel.check()
            first, rest = frames[0], frames[1:]
            save_kwargs: dict = {"save_all": True, "append_images": rest, "duration": durations,
                                 "loop": img.info.get("loop", 0)}
            if fmt == "WEBP":
                save_kwargs["quality"] = job.opt("quality", 90)
            else:
                save_kwargs["disposal"] = 2
        else:
            first = self._transform(ImageOps.exif_transpose(img), job, fmt)
            save_kwargs = {}
            if fmt == "JPEG":
                save_kwargs = {"quality": job.opt("quality", 90), "optimize": True}
            elif fmt == "WEBP":
                save_kwargs = {"quality": job.opt("quality", 90), "method": 4}
            elif fmt == "ICO":
                size = min(256, max(first.width, first.height))
                save_kwargs = {"sizes": [(s, s) for s in (16, 32, 48, 64, 128, 256) if s <= size] or [(size, size)]}
            elif fmt == "PNG":
                save_kwargs = {"optimize": True}
            if exif and fmt in ("JPEG", "WEBP"):
                save_kwargs["exif"] = exif
        job.progress(0.6)
        try:
            first.save(job.output, fmt, **save_kwargs)
        except Exception as exc:
            raise ConvertError(f"이미지 저장 실패 ({src.name} → {fmt}): {exc}") from exc
        return [job.output]


def _has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
