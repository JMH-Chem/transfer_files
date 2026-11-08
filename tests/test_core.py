from pathlib import Path

import pytest

from fileconv.core import engine
from fileconv.core.base import Option, parse_pages, parse_time
from fileconv.core.errors import ConvertError, NoConverterError, OptionError
from fileconv.core.registry import all_converters, find, targets_for
from fileconv.core.utils import detect_encoding, expand_inputs, natural_key, safe_sheet_name, unique_path


def test_all_converters_registered_and_valid():
    convs = all_converters()
    assert len(convs) >= 13
    for c in convs:
        assert c.title and c.description and c.inputs and c.outputs
        if c.default_output:
            assert c.default_output in c.outputs, c.name
        for opt in c.options:  # 기본값이 자기 자신의 검증을 통과해야 한다
            if opt.default not in (None, ""):
                opt.parse(opt.default)


def test_find_prefers_priority():
    assert find("pdf", "pdf")[0].name == "pdf-tools"
    assert find("JPEG", ".pdf")[0].name == "images-to-pdf"
    assert "mp3" in targets_for("mkv")


@pytest.mark.parametrize("text,expected", [("90", 90), ("1:30", 90), ("00:01:30.5", 90.5), ("1:00:00", 3600)])
def test_parse_time(text, expected):
    assert parse_time(text) == expected


def test_parse_pages():
    assert parse_pages("1-3,5,8-", 9) == [0, 1, 2, 4, 7, 8]
    assert parse_pages(None, 3) == [0, 1, 2]
    with pytest.raises(OptionError):
        parse_pages("10", 3)


def test_option_parsing():
    assert Option("x", "bool").parse("예") is True
    assert Option("x", "choice", choices=("A4", "A3")).parse("a4") == "A4"
    with pytest.raises(OptionError):
        Option("x", "int", min=1, max=10).parse("11")
    with pytest.raises(OptionError):
        Option("x", "choice", choices=("a",)).parse("b")


def test_unknown_option_rejected(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(OptionError, match="알 수 없는 옵션"):
        engine.plan([f], "xlsx", options={"nope": "1"})


def test_no_converter_message(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("a\n", encoding="utf-8")
    with pytest.raises(NoConverterError, match="가능한 형식"):
        engine.plan([f], "mp3")


def test_natural_sort_and_glob(tmp_path):
    for n in (10, 2, 1):
        (tmp_path / f"img{n}.jpg").write_bytes(b"x")
    files = expand_inputs([str(tmp_path / "*.jpg")])
    assert [f.name for f in files] == ["img1.jpg", "img2.jpg", "img10.jpg"]
    assert natural_key("a2") < natural_key("a10")
    with pytest.raises(ConvertError):
        expand_inputs([str(tmp_path / "*.png")])


def test_unique_path_and_never_overwrite_input(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"x")
    assert unique_path(f).name == "a (1).pdf"
    # pdf → pdf 에서 overwrite 를 켜도 원본 파일은 덮어쓰지 않는다
    tasks = engine.plan([f], "pdf", overwrite=True)
    assert tasks[0].output != f


def test_output_rules(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    for p in (a, b):
        p.write_text("x\n1\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    tasks = engine.plan([a, b], "xlsx", output=out_dir)
    assert [t.output for t in tasks] == [out_dir / "a.xlsx", out_dir / "b.xlsx"]
    tasks = engine.plan([a, b], "xlsx", merge=True, output=tmp_path / "합본")
    assert len(tasks) == 1 and tasks[0].output.name == "합본.xlsx"
    tasks = engine.plan([a, b], "xlsx", merge=True)
    assert tasks[0].output.name == "a_merged.xlsx"


def test_merge_requires_support(tmp_path):
    files = []
    for n in ("a", "b"):
        p = tmp_path / f"{n}.md"
        p.write_text("# hi", encoding="utf-8")
        files.append(p)
    with pytest.raises(ConvertError, match="합치기"):
        engine.plan(files, "html", merge=True)


def test_failed_task_leaves_no_partial_file(tmp_path):
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not an image")
    results = engine.run(engine.plan([bad], "jpg"))
    assert not results[0].ok and "이미지를 열 수 없습니다" in results[0].error
    assert sorted(p.name for p in tmp_path.iterdir()) == ["broken.png"]


def test_detect_encoding(tmp_path):
    p = tmp_path / "k.txt"
    p.write_text("안녕하세요 " * 100, encoding="cp949")
    assert detect_encoding(p) == "cp949"
    p.write_text("안녕하세요", encoding="utf-8-sig")
    assert detect_encoding(p) == "utf-8-sig"


def test_safe_sheet_name():
    used: set[str] = set()
    assert safe_sheet_name("a/b:c", used) == "a_b_c"
    assert safe_sheet_name("A_B_C", used) == "A_B_C_2"
    assert len(safe_sheet_name("x" * 40, used)) == 31


def test_permission_error_message(tmp_path, monkeypatch):
    import os

    f = tmp_path / "a.md"
    f.write_text("# hi", encoding="utf-8")

    def locked(src, dst):
        raise PermissionError(13, "Permission denied", str(dst))

    monkeypatch.setattr(os, "replace", locked)
    results = engine.run(engine.plan([f], "html"))
    assert "다른 프로그램" in results[0].error
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.md"]  # 임시 파일도 정리됨


def test_excel_single_sheet_does_not_overwrite(tmp_path):
    import pandas as pd

    book = tmp_path / "b.xlsx"
    pd.DataFrame({"a": [1]}).to_excel(book, index=False)
    existing = tmp_path / "b.csv"
    existing.write_text("keep", encoding="utf-8")
    results = engine.run(engine.plan([book], "csv"))
    assert results[0].outputs[0].name == "b (1).csv"
    assert existing.read_text(encoding="utf-8") == "keep"
