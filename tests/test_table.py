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
    assert rows[2] == ["김철수", 25, "부산", " 강서구", "추가"]  # 칸이 많은 줄도 버리지 않음 (값은 그대로)


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


def test_table_convert_is_lossless(tmp_path):
    f = tmp_path / "원본.csv"
    f.write_text('코드,메모\n" 0012 ","  들여쓰기"\n,\n3,끝\n', encoding="cp949")
    [out] = convert([f], "csv", options={"out_encoding": "utf-8"})
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[1] == " 0012 ,  들여쓰기"  # 앞뒤 공백 유지
    assert lines[2] == ","  # 빈 칸만 있는 줄도 유지
    assert lines[3] == "3,끝"


def test_huge_cell(tmp_path):
    f = tmp_path / "big.csv"
    f.write_text("a,b\n" + "x" * 200_000 + ",1\n", encoding="utf-8")
    [js] = convert([f], "json")
    assert len(json.loads(js.read_text(encoding="utf-8"))[0]["a"]) == 200_000
    [x] = convert([f], "xlsx")  # 엑셀 한 칸의 최대 글자 수(32767)까지만 들어가지만 변환은 성공
    assert len(openpyxl.load_workbook(x).active["A2"].value) == 32767


# ---------------------------------------------------------------- 3차 검토에서 찾은 문제

def _rows(xlsx):
    return [[c.value for c in r] for r in openpyxl.load_workbook(xlsx).active.iter_rows()]


def test_control_characters_do_not_break_excel(tmp_path):
    f = tmp_path / "ctrl.csv"
    f.write_text("이름,메모\n홍길동,줄\x0b바꿈\n김철수,정상\x1f\n", encoding="utf-8")
    [x] = convert([f], "xlsx")
    assert _rows(x) == [["이름", "메모"], ["홍길동", "줄바꿈"], ["김철수", "정상"]]


def test_long_digit_strings_are_not_damaged(tmp_path):
    f = tmp_path / "ids.csv"
    f.write_text("카드번호,바코드,코드,전화\n1234567812345678,8801234567890,1E5,+821012345678\n"
                 "9999888877776666,8809876543210,2E3,+821098765432\n", encoding="utf-8")
    [x] = convert([f], "xlsx")
    ws = openpyxl.load_workbook(x).active
    assert ws["A2"].value == "1234567812345678"  # 16자리: 엑셀 숫자는 15자리까지만 정확 → 글자로 둔다
    assert ws["B2"].value == 8801234567890 and ws["B2"].number_format == "0"  # 지수 표기(8.8E+12)로 안 보이게
    assert ws["C2"].value == "1E5"  # 코드가 100000 으로 바뀌면 안 됨
    assert ws["D2"].value == "+821012345678"


def test_mixed_plain_and_comma_numbers(tmp_path):
    f = tmp_path / "money.csv"
    f.write_text('품목,금액,비율\n가,500,0.5\n나,"1,200",1.25\n다,"98,765,432,100",-3\n', encoding="utf-8")
    [x] = convert([f], "xlsx")
    rows = _rows(x)
    assert [r[1] for r in rows[1:]] == [500, 1200, 98765432100]
    assert [r[2] for r in rows[1:]] == [0.5, 1.25, -3]


def test_excel_dates_headers_and_integers_to_csv(tmp_path):
    import datetime

    book = tmp_path / "book.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["날짜", "시각", "값", None, "수량"])
    ws.append([datetime.date(2024, 3, 1), datetime.datetime(2024, 3, 1, 14, 30), 1.5, None, 3])
    ws.append([datetime.date(2024, 3, 2), None, 2, None, 4])
    wb.save(book)
    [out] = convert([book], "csv")
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == "날짜,시각,값,열4,수량"  # 빈 제목 열이 'Unnamed: 3' 이 아님
    assert lines[1] == "2024-03-01,2024-03-01 14:30:00,1.5,,3"  # 날짜만 있는 열에 00:00:00 이 붙지 않음
    assert lines[2] == "2024-03-02,,2,,4"  # 정수 2 가 2.0 으로 바뀌지 않음
    [js] = convert([book], "json")
    first = json.loads(js.read_text(encoding="utf-8"))[0]
    assert first["날짜"] == "2024-03-01" and first["시각"] == "2024-03-01T14:30:00"


def test_unencodable_text_gives_clear_error(tmp_path):
    from fileconv.core import engine

    book = tmp_path / "emoji.xlsx"
    pd.DataFrame({"글": ["😀"]}).to_excel(book, index=False)
    results = engine.run(engine.plan([book], "csv", options={"out_encoding": "cp949"}))
    assert not results[0].ok and "utf-8-sig" in results[0].error and "예상치 못한" not in results[0].error


def test_rows_beyond_excel_limit_go_to_extra_sheets(tmp_path, monkeypatch):
    from fileconv.converters import table

    monkeypatch.setattr(table, "EXCEL_MAX_ROWS", 4)  # 실제 한도(1,048,576) 대신 작은 값으로 같은 경로를 시험
    f = tmp_path / "big.csv"
    f.write_text("n\n" + "\n".join(str(i) for i in range(1, 8)) + "\n", encoding="utf-8")
    [x] = convert([f], "xlsx")
    wb = openpyxl.load_workbook(x)
    assert wb.sheetnames == ["big", "big_2", "big_3"]
    assert [[c.value for c in r] for r in wb["big_2"].iter_rows()] == [["n"], [4], [5], [6]]


def test_encoding_fallback_when_korean_appears_late(tmp_path):
    f = tmp_path / "late.csv"
    f.write_bytes(b"a,b\n" + b"1,2\n" * 80000 + "홍길동,서울\n".encode("cp949"))  # 앞 256KB 는 영문·숫자뿐
    [x] = convert([f], "csv", options={"out_encoding": "utf-8"})
    assert x.read_text(encoding="utf-8").rstrip().endswith("홍길동,서울")


def test_json_nested_values_are_written_as_json_text(tmp_path):
    f = tmp_path / "nested.json"
    f.write_text(json.dumps([{"이름": "가", "태그": ["x", "y"], "정보": {"a": [1, 2]}}], ensure_ascii=False), encoding="utf-8")
    [x] = convert([f], "xlsx")
    ws = openpyxl.load_workbook(x).active
    header = [c.value for c in ws[1]]
    assert ws.cell(2, header.index("태그") + 1).value == '["x", "y"]'
    [c] = convert([f], "csv")
    assert '"[""x"", ""y""]"' in c.read_text(encoding="utf-8-sig")
