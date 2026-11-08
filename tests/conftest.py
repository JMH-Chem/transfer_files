"""테스트용 샘플 파일을 코드로 직접 만든다 (저장소에 바이너리 파일을 넣지 않기 위해)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from fileconv.core.tools import find_ffmpeg


@pytest.fixture
def video(tmp_path: Path) -> Path:
    """3초짜리 동영상 (화면 + 440Hz 소리)."""
    out = tmp_path / "강의 영상.mp4"
    subprocess.run(
        [find_ffmpeg(), "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "3",
         "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(out)],
        check=True,
    )
    return out


@pytest.fixture
def silent_video(tmp_path: Path) -> Path:
    out = tmp_path / "무음.mp4"
    subprocess.run(
        [find_ffmpeg(), "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
         "-t", "1", "-c:v", "mpeg4", str(out)],
        check=True,
    )
    return out


@pytest.fixture
def images(tmp_path: Path) -> list[Path]:
    """일부러 이름 순서가 헷갈리는 이미지들: img1, img2, img10 + 투명 PNG."""
    paths = []
    for name, size, color in [("img1.jpg", (400, 300), "red"), ("img2.jpg", (300, 400), "green"),
                              ("img10.jpg", (500, 300), "blue")]:
        p = tmp_path / name
        Image.new("RGB", size, color).save(p, quality=90)
        paths.append(p)
    p = tmp_path / "투명.png"
    Image.new("RGBA", (200, 200), (0, 0, 255, 100)).save(p)
    paths.append(p)
    return paths


@pytest.fixture
def korean_csv(tmp_path: Path) -> Path:
    """한글 윈도우 엑셀에서 저장한 것 같은 cp949 CSV (앞자리 0 우편번호 포함)."""
    p = tmp_path / "고객목록.csv"
    p.write_text("이름,나이,우편번호,금액\n홍길동,30,01234,\"1,000\"\n김철수,25,06236,\"25,000\"\n", encoding="cp949")
    return p


@pytest.fixture
def pdf_file(tmp_path: Path, images: list[Path]) -> Path:
    """4쪽짜리 PDF (글자 포함)."""

    import img2pdf

    p = tmp_path / "문서.pdf"
    p.write_bytes(img2pdf.convert([str(x) for x in images[:3]] + [str(images[0])]))
    return p


@pytest.fixture
def text_pdf(tmp_path: Path) -> Path:
    """글자가 들어 있는 PDF (LibreOffice 없이 만들기 위해 PDF 를 직접 작성)."""
    p = tmp_path / "text.pdf"
    content = b"BT /F1 24 Tf 72 720 Td (Hello fileconv) Tj ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    p.write_bytes(bytes(out))
    return p


@pytest.fixture
def docx_file(tmp_path: Path) -> Path:
    import docx

    p = tmp_path / "보고서.docx"
    d = docx.Document()
    d.add_heading("주간 보고", level=1)
    d.add_paragraph("이번 주에는 변환기를 만들었습니다.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "항목", "상태"
    t.cell(1, 0).text, t.cell(1, 1).text = "CLI", "완료"
    d.save(p)
    return p
