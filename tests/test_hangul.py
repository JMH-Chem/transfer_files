import struct
import zipfile

from fileconv.converters.hangul import HWPTAG_PARA_TEXT, decode_para_text, hwp_section_text, iter_records

from helpers import convert


def record(tag: int, payload: bytes, level: int = 0) -> bytes:
    size = len(payload)
    if size >= 0xFFF:
        return struct.pack("<II", tag | (level << 10) | (0xFFF << 20), size) + payload
    return struct.pack("<I", tag | (level << 10) | (size << 20)) + payload


def para(text: str) -> bytes:
    return text.encode("utf-16-le") + struct.pack("<H", 13)


def test_decode_para_text_controls():
    # 탭(9)은 8글자 크기 확장 제어, 10은 줄바꿈, 30은 묶음 빈칸
    tab = struct.pack("<H", 9) + b"\x00" * 14
    payload = "가".encode("utf-16-le") + tab + "나".encode("utf-16-le") + struct.pack("<HH", 10, 30) + "다😀".encode("utf-16-le")
    assert decode_para_text(payload) == "가\t나\n 다😀"


def test_section_records_and_long_record():
    long_text = "긴" * 3000  # 0xFFF 바이트 이상 → 확장 길이 필드
    data = record(66, b"\x00" * 10) + record(HWPTAG_PARA_TEXT, para("첫 문단")) + record(HWPTAG_PARA_TEXT, para(long_text))
    assert [t for t, _, _ in iter_records(data)] == [66, HWPTAG_PARA_TEXT, HWPTAG_PARA_TEXT]
    import zlib

    comp = zlib.compressobj(wbits=-15)
    compressed = comp.compress(data) + comp.flush()
    assert hwp_section_text(compressed, compressed=True) == ["첫 문단", long_text]


def test_hwpx_to_text(tmp_path):
    ns = 'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"'
    section = (f'<hs:sec {ns}><hp:p><hp:run><hp:t>안녕하세요<hp:tab/>탭 뒤</hp:t></hp:run></hp:p>'
               '<hp:p><hp:run><hp:tbl><hp:tr><hp:tc><hp:subList><hp:p><hp:run><hp:t>표 안 글자</hp:t></hp:run></hp:p>'
               '</hp:subList></hp:tc></hp:tr></hp:tbl></hp:run></hp:p></hs:sec>')
    path = tmp_path / "문서.hwpx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/section0.xml", section)
    [out] = convert([path], "txt", options={"skip_empty": "true"})
    assert out.read_text(encoding="utf-8") == "안녕하세요\t탭 뒤\n표 안 글자\n"
