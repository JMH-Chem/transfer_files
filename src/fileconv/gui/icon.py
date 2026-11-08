"""프로그램 아이콘을 코드로 그린다 (저장소에 이미지 파일을 두지 않기 위해). .exe 아이콘에도 쓴다."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


def make_icon(size: int = 256) -> Image.Image:
    scale = 4  # 크게 그린 뒤 줄여서 가장자리를 부드럽게
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=s // 5, fill=(37, 99, 235, 255))
    d.rounded_rectangle((0, s // 2, s - 1, s - 1), radius=s // 5, fill=(29, 78, 216, 255))
    d.rectangle((0, s // 2, s - 1, s // 2 + s // 5), fill=(29, 78, 216, 255))
    white = (255, 255, 255, 255)
    u = s / 16
    # 위: 오른쪽 화살표, 아래: 왼쪽 화살표 (⇄)
    d.rectangle((3 * u, 4.6 * u, 10 * u, 6.4 * u), fill=white)
    d.polygon([(10 * u, 2.8 * u), (13.4 * u, 5.5 * u), (10 * u, 8.2 * u)], fill=white)
    d.rectangle((6 * u, 9.6 * u, 13 * u, 11.4 * u), fill=white)
    d.polygon([(6 * u, 7.8 * u), (2.6 * u, 10.5 * u), (6 * u, 13.2 * u)], fill=white)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def save_ico(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    make_icon(256).save(path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return path


def qicon():
    """Qt 창 아이콘."""
    import io

    from PySide6.QtGui import QIcon, QPixmap

    icon = QIcon()
    for size in (16, 32, 48, 64, 128, 256):
        buf = io.BytesIO()
        make_icon(size).save(buf, format="PNG")
        pixmap = QPixmap()
        pixmap.loadFromData(buf.getvalue(), "PNG")
        icon.addPixmap(pixmap)
    return icon
