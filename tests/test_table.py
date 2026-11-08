import json

import openpyxl
import pandas as pd

from helpers import convert


def test_korean_csv_to_excel(korean_csv):
    [xlsx] = convert([korean_csv], "xlsx")
    ws = openpyxl.load_workbook(xlsx).active
    rows = [[c.value for c in r] for r in ws.iter_rows()]
    assert rows[0] == ["이름", "나이", "우편번호", "금액"]
    assert rows[1] == ["홍길동", 30, "01234", 1000]  # 우편번호는 글자 유지, 금액 '1,000' 은 숫자
    assert ws.freeze_panes == "A2"


def test_tab_and_whitespace_txt(tmp_path):
    tab = tmp_path / "tab.txt"
    tab.write_text("a\tb\n1\t2\n3\t4\n", encoding="utf-8")
    [x] = convert([tab], "xlsx")
    assert pd.read_excel(x).values.tolist() == [[1, 2], [3, 4]]
    space = tmp_path / "space.txt"
    space.write_text("x   y\n1   2\n", encoding="utf-8")
    [x] = convert([space], "xlsx")
    assert list(pd.read_excel(x).columns) == ["x", "y"]


def test_no_header_option(tmp_path):
    f = tmp_path / "n.csv"
    f.write_text("1,2\n3,4\n", encoding="utf-8")
    [x] = convert([f], "xlsx", options={"header": "false"})
    ws = openpyxl.load_workbook(x).active
    assert [[c.value for c in r] for r in ws.iter_rows()] == [[1, 2], [3, 4]]


def test_json_to_excel_sheets(tmp_path):
    f = tmp_path / "data.json"
    f.write_text(json.dumps({"직원": [{"이름": "가", "부서": {"이름": "개발"}}], "부서": [{"코드": 1}]}, ensure_ascii=False), encoding="utf-8")
    [x] = convert([f], "xlsx")
    wb = openpyxl.load_workbook(x)
    assert wb.sheetnames == ["직원", "부서"]
    assert [c.value for c in wb["직원"][1]] == ["이름", "부서.이름"]


def test_merge_csvs_into_sheets(tmp_path, korean_csv):
    other = tmp_path / "other.csv"
    other.write_text("a,b\n1,2\n", encoding="utf-8")
    [x] = convert([korean_csv, other], "xlsx", merge=True)
    assert openpyxl.load_workbook(x).sheetnames == ["고객목록", "other"]


def test_excel_to_csv_roundtrip(korean_csv):
    [xlsx] = convert([korean_csv], "xlsx")
    [csv_out] = convert([xlsx], "csv")
    raw = csv_out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # 엑셀 호환 BOM
    assert "홍길동" in raw.decode("utf-8-sig")


def test_excel_all_sheets(tmp_path):
    f = tmp_path / "book.xlsx"
    with pd.ExcelWriter(f) as w:
        pd.DataFrame({"a": [1]}).to_excel(w, sheet_name="첫째", index=False)
        pd.DataFrame({"b": [2]}).to_excel(w, sheet_name="둘째", index=False)
    outs = convert([f], "json", options={"sheet": "전체"})
    assert sorted(o.name for o in outs) == ["book_둘째.json", "book_첫째.json"]
    assert json.loads(outs[0].read_text(encoding="utf-8")) in ([{"a": 1}], [{"b": 2}])


def test_csv_encoding_conversion(korean_csv):
    [out] = convert([korean_csv], "csv", options={"out_encoding": "utf-8"})
    text = out.read_text(encoding="utf-8")
    assert "홍길동" in text and "01234" in text and "1,000" in text


def test_ragged_rows_are_kept(tmp_path):
    f = tmp_path / "ragged.csv"
    f.write_text("이름,나이,도시\n홍길동,30,서울,\n김철수,25,부산, 강서구,추가\n", encoding="utf-8")
    [x] = convert([f], "xlsx")
    rows = [[c.value for c in r] for r in openpyxl.load_workbook(x).active.iter_rows()]
    assert rows[0] == ["이름", "나이", "도시", "열4", "열5"]
    assert rows[1] == ["홍길동", 30, "서울", None, None]  # 줄 끝 쉼표 때문에 이름이 사라지면 안 됨
    assert rows[2] == ["김철수", 25, "부산", "강서구", "추가"]  # 칸이 많은 줄도 버리지 않음


def test_trailing_comma_column_removed(tmp_path):
    f = tmp_path / "t.csv"
    f.write_text("a,b,\n1,2,\n3,4,\n", encoding="utf-8")
    [x] = convert([f], "xlsx")
    assert pd.read_excel(x).columns.tolist() == ["a", "b"]


def test_formula_like_text_stays_text(tmp_path):
    f = tmp_path / "f.csv"
    f.write_text('제목,링크\n=== 중요 ===,"=HYPERLINK(""http://x"")"\n', encoding="utf-8")
    [x] = convert([f], "xlsx")
    ws = openpyxl.load_workbook(x).active
    assert ws["A2"].value == "=== 중요 ===" and ws["A2"].data_type == "s"
    assert ws["B2"].data_type == "s"
