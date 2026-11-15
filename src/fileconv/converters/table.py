"""표 변환: 텍스트/CSV/JSON → 엑셀, 엑셀 → CSV/JSON, CSV ↔ TSV ↔ JSON (pandas, openpyxl)."""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from pathlib import Path

import pandas as pd

from ..core.base import Converter, Job, Option
from ..core.errors import ConvertError
from ..core.registry import register
from ..core.utils import read_text_auto, safe_sheet_name, unique_path

TEXT_EXTS = {"csv", "tsv", "txt", "json"}

csv.field_size_limit(2**31 - 1)  # 기본 한도(128KB)보다 큰 칸도 읽기 (Windows 의 C long 한도에 맞춤)
EXCEL_EXTS = {"xlsx", "xlsm"}

DELIMITERS = {"쉼표": ",", "탭": "\t", "세미콜론": ";", "세로선": "|", "공백": r"\s+", "없음": None}
ENCODING_CHOICES = ("자동", "utf-8", "cp949", "euc-kr", "utf-16")
_LEADING_ZERO = re.compile(r"^-?0\d")
# 숫자로 바꿔도 되는 모양: 123 / -1,234,567 / 0.5  (1E5 같은 지수 꼴, +82… 같은 전화번호는 글자로 둔다)
_NUMBER_RE = re.compile(r"^-?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?$")
EXCEL_MAX_DIGITS = 15        # 엑셀은 숫자를 15자리까지만 정확히 기억한다 (16자리 카드번호는 끝자리가 0 이 됨)
EXCEL_PLAIN_FROM = 10 ** 11  # 이보다 큰 정수는 엑셀이 8.8E+12 처럼 보여 주므로 표시 형식을 '0' 으로 지정
EXCEL_MAX_ROWS = 1_048_576   # 시트 하나에 들어가는 최대 행 수
EXCEL_MAX_COLS = 16_384

DELIM_OPT = Option("delimiter", "choice", "자동", "구분자", "열을 나누는 문자. 자동 = 파일을 보고 추측",
                   choices=("자동", *DELIMITERS))
ENC_OPT = Option("encoding", "choice", "자동", "읽기 인코딩", "한글이 깨지면 cp949 또는 utf-8 로 지정",
                 choices=ENCODING_CHOICES)
HEADER_OPT = Option("header", "bool", True, "첫 줄은 제목", "첫 줄을 열 이름으로 사용")
NUMBERS_OPT = Option("numbers", "bool", True, "숫자 자동 변환",
                     "숫자로만 된 열을 숫자로 저장 (앞자리 0이 있는 값 예: 우편번호는 글자로 유지)")


# ---------------------------------------------------------------- 읽기

def _guess_delimiter(sample: str, ext: str) -> str | None:
    if ext == "tsv":
        return "\t"
    lines = [ln for ln in sample.splitlines() if ln.strip()][:50]
    if not lines:
        return ","
    try:
        dialect = csv.Sniffer().sniff("\n".join(lines), delimiters=",\t;|")
        return dialect.delimiter
    except csv.Error:
        pass
    if ext == "csv":
        return ","
    # 모든 줄의 칸 수가 같은 구분자를 찾는다
    for delim in ("\t", ",", ";", "|"):
        counts = {ln.count(delim) for ln in lines}
        if len(counts) == 1 and counts.pop() > 0:
            return delim
    if all(len(ln.split()) > 1 for ln in lines) and len({len(ln.split()) for ln in lines}) == 1:
        return r"\s+"
    return None  # 구분자 없음 → 한 줄이 한 칸


def split_rows(text: str, delim: str | None) -> list[list[str]]:
    """글자를 줄·칸으로 나눈다. 따옴표("a,b")와 따옴표 안 줄바꿈도 처리. 빈 줄은 건너뜀."""
    if delim is None:  # 구분자 없음 → 한 줄이 한 칸
        return [[line] for line in text.splitlines() if line.strip()]
    if delim == r"\s+":
        return [line.split() for line in text.splitlines() if line.strip()]
    from io import StringIO

    return [row for row in csv.reader(StringIO(text), delimiter=delim) if row]  # 완전히 빈 줄만 건너뜀


def rows_to_frame(rows: list[list[str]], header: bool) -> pd.DataFrame:
    """줄마다 칸 수가 달라도 버리지 않는다: 모자라면 빈칸, 남으면 '열N' 열을 더 만든다.
    칸 안의 값(앞뒤 공백 포함)은 그대로 둔다. 제목 줄이 있을 때, 제목도 내용도 전부 비어 있는
    오른쪽 열(줄 끝 쉼표 때문에 생긴 열)만 지운다."""
    width = max((len(r) for r in rows), default=0)
    rows = [r + [""] * (width - len(r)) for r in rows]
    names: list[str] = []
    if header and rows:
        used: set[str] = set()
        for i, raw in enumerate(rows[0]):
            name = raw if raw.strip() else f"열{i + 1}"
            candidate, n = name, 2
            while candidate in used:  # 같은 이름 열은 '이름_2' 처럼 구분
                candidate, n = f"{name}_{n}", n + 1
            used.add(candidate)
            names.append(candidate)
        body = rows[1:]
        header_blank = [not c.strip() for c in rows[0]]
    else:
        names = [f"열{i + 1}" for i in range(width)]
        body = rows
        header_blank = [False] * width  # 제목 줄이 없으면 열을 지우지 않는다
    keep = width
    while keep > 1 and header_blank[keep - 1] and all(not r[keep - 1].strip() for r in body):
        keep -= 1
    df = pd.DataFrame([r[:keep] for r in body], columns=names[:keep], dtype=object)
    return df.replace("", pd.NA)


def _smart_numbers(df: pd.DataFrame) -> pd.DataFrame:
    """글자로 읽은 표에서 '진짜 숫자' 열만 숫자로 바꾼다.

    열의 모든 값이 숫자 모양일 때만 바꾸고, 아래 경우는 글자 그대로 둔다 (엑셀에서 값이 망가지지 않게):
    - 0 으로 시작하는 값이 있음 (우편번호 01234, 전화번호)
    - 16자리 이상인 값이 있음 (카드번호: 엑셀 숫자는 15자리까지만 정확)
    """
    for col in df.columns:
        series = df[col]
        values = series.dropna().astype(str).str.strip()
        values = values[values != ""]
        if values.empty or not values.str.match(_NUMBER_RE).all() or values.str.match(_LEADING_ZERO).any():
            continue
        digits = values.str.replace(r"\D", "", regex=True).str.lstrip("0")
        if (digits.str.len() > EXCEL_MAX_DIGITS).any():
            continue
        full = pd.to_numeric(series.astype(str).str.strip().str.replace(",", "", regex=False), errors="coerce")
        if (full.dropna() == full.dropna().round()).all():
            full = full.astype("Int64")
        df[col] = full
    return df


def read_text_table(path: Path, job: Job) -> dict[str, pd.DataFrame]:
    """파일 하나를 읽어 {시트이름 후보: 표}. JSON 은 표가 여러 개일 수 있다."""
    ext = path.suffix.lower().lstrip(".")
    enc_choice = job.opt("encoding", "자동")
    text = read_text_auto(path, None if enc_choice == "자동" else enc_choice)

    if ext == "json":
        return _read_json(path, text)

    choice = job.opt("delimiter", "자동")
    delim = _guess_delimiter(text[:20000], ext) if choice == "자동" else DELIMITERS[choice]
    try:
        rows = split_rows(text, delim)
    except csv.Error as exc:
        raise ConvertError(f"{path.name}: 표로 읽을 수 없습니다 ({exc})") from exc
    df = rows_to_frame(rows, job.opt("header", True))
    if job.opt("numbers", True):
        df = _smart_numbers(df)
    return {path.stem: df}


def _read_json(path: Path, text: str) -> dict[str, pd.DataFrame]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 한 줄에 JSON 하나씩 (JSON Lines)
        try:
            data = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
        except json.JSONDecodeError as exc:
            raise ConvertError(f"{path.name}: 올바른 JSON 이 아닙니다 ({exc})") from exc
    if isinstance(data, dict) and data and all(isinstance(v, list) for v in data.values()):
        if all(all(isinstance(r, dict) for r in v) for v in data.values()):
            return {str(k): pd.json_normalize(v) for k, v in data.items()}  # {"시트": [레코드...]}
        return {path.stem: pd.DataFrame(dict(data))} if len({len(v) for v in data.values()}) == 1 else {
            str(k): pd.DataFrame({k: v}) for k, v in data.items()}
    if isinstance(data, dict):
        return {path.stem: pd.json_normalize(data)}
    if isinstance(data, list):
        if all(isinstance(r, dict) for r in data):
            return {path.stem: pd.json_normalize(data)}
        if all(isinstance(r, list) for r in data):
            return {path.stem: pd.DataFrame(data)}
        return {path.stem: pd.DataFrame({"값": data})}
    return {path.stem: pd.DataFrame({"값": [data]})}


# ---------------------------------------------------------------- 쓰기

def _display_width(value) -> int:
    text = "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)
    longest = max(text.split("\n"), key=len) if text else ""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in longest)


def _object_series(series: pd.Series, func) -> pd.Series:
    """값마다 func 를 적용하되 값의 종류를 그대로 지킨다 (Series.map 은 정수 2 를 2.0 으로 바꿔 버릴 수 있다)."""
    return pd.Series([func(v) for v in series], index=series.index, dtype=object)


def _writable(df: pd.DataFrame, for_excel: bool) -> pd.DataFrame:
    """파일에 쓸 수 있는 값으로 다듬는다.

    - 목록·사전 값(JSON 안의 배열/객체)은 JSON 글자로: ["x", "y"]
    - 엑셀용이면 엑셀이 받지 못하는 제어 문자를 지운다 (그대로 두면 파일 전체가 저장 실패)
    """
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

    def fix(value):
        if isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False)
        if for_excel and isinstance(value, str):
            value = ILLEGAL_CHARACTERS_RE.sub("", value)
        return value

    out = df.copy()
    for col in out.columns:
        if not pd.api.types.is_numeric_dtype(out[col]) and not pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = _object_series(out[col], fix)
    if for_excel:
        out.columns = [fix(c) for c in out.columns]
    return out


def _split_for_sheets(name: str, df: pd.DataFrame, header: bool) -> list[tuple[str, pd.DataFrame]]:
    """시트 하나에 다 안 들어가는 표는 '이름', '이름_2', … 로 나눈다."""
    if len(df.columns) > EXCEL_MAX_COLS:
        raise ConvertError(f"열이 너무 많아 엑셀에 넣을 수 없습니다 ({len(df.columns):,}열, 최대 {EXCEL_MAX_COLS:,}열)")
    room = EXCEL_MAX_ROWS - (1 if header else 0)
    if len(df) <= room:
        return [(name, df)]
    return [(name if i == 0 else f"{name}_{i // room + 1}", df.iloc[i:i + room]) for i in range(0, len(df), room)]


def write_excel(sheets: list[tuple[str, pd.DataFrame]], output: Path, header: bool = True) -> None:
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    used: set[str] = set()
    pieces = [piece for raw_name, df in sheets for piece in _split_for_sheets(raw_name, df, header)]
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for raw_name, df in pieces:
            df = _writable(df, for_excel=True)
            name = safe_sheet_name(raw_name, used)
            df.to_excel(writer, sheet_name=name, index=False, header=header)
            ws = writer.sheets[name]
            for row in ws.iter_rows():
                for cell in row:
                    if cell.data_type == "f":  # '=' 로 시작하는 글자가 엑셀 수식으로 저장되지 않게 (수식 주입 방지)
                        cell.data_type = "s"
                    elif cell.data_type == "n" and isinstance(cell.value, int) and abs(cell.value) >= EXCEL_PLAIN_FROM:
                        cell.number_format = "0"  # 13자리 바코드가 8.80123E+12 로 보이지 않게
            for idx, col in enumerate(df.columns, start=1):
                sample = df[col].head(500).tolist()
                width = max([_display_width(col) if header else 0] + [_display_width(v) for v in sample])
                ws.column_dimensions[get_column_letter(idx)].width = min(max(width + 2, 6), 60)
            if header and len(df.columns):
                ws.freeze_panes = "A2"
                fill = PatternFill("solid", fgColor="DDEBF7")
                for cell in ws[1]:
                    cell.font = Font(bold=True)
                    cell.fill = fill


def _tidy_dates(df: pd.DataFrame, sep: str) -> pd.DataFrame:
    """엑셀에서 읽은 날짜 칸을 보기 좋은 글자로 바꾼다.

    시각이 모두 0시인 열은 날짜만(2024-03-01), 아니면 날짜와 시각(2024-03-01 14:30:00).
    그대로 두면 날짜만 적힌 칸도 전부 '2024-03-01 00:00:00' 으로 나온다.
    """
    import datetime as dt

    out = df.copy()
    for col in out.columns:
        stamps = [v for v in out[col] if isinstance(v, dt.datetime)]
        if not stamps:
            continue
        date_only = all(v.time() == dt.time(0, 0) for v in stamps)

        def show(value, date_only=date_only):
            if isinstance(value, dt.datetime):
                if date_only:
                    return value.date().isoformat()
                return value.isoformat(sep=sep, timespec="seconds" if value.microsecond == 0 else "milliseconds")
            if isinstance(value, (dt.date, dt.time)):
                return value.isoformat()
            return value

        out[col] = _object_series(out[col], show)
    return out


def write_text_table(df: pd.DataFrame, output: Path, fmt: str, encoding: str, header: bool = True) -> None:
    if fmt == "json":
        records = json.loads(_tidy_dates(df, "T").to_json(orient="records", force_ascii=False, date_format="iso"))
        output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    table = _writable(_tidy_dates(df, " "), for_excel=False)
    try:
        table.to_csv(output, sep="\t" if fmt == "tsv" else ",", index=False, header=header,
                     encoding=encoding, lineterminator="\r\n")
    except UnicodeEncodeError as exc:
        raise ConvertError(f"'{encoding}' 로 저장할 수 없는 글자가 있습니다 ({exc.object[exc.start:exc.end]!r}). "
                           "저장 인코딩을 utf-8-sig 로 바꾸세요") from exc


# ---------------------------------------------------------------- 변환기

@register
class TextToExcel(Converter):
    name = "text-to-excel"
    title = "텍스트/CSV/JSON → 엑셀"
    category = "table"
    description = "txt/csv/tsv/json 파일을 엑셀(xlsx)로 바꿉니다. 여러 파일을 합치면 파일마다 시트가 하나씩 생깁니다."
    inputs = frozenset(TEXT_EXTS)
    outputs = frozenset({"xlsx"})
    can_merge = True
    priority = 10
    default_output = "xlsx"
    options = (DELIM_OPT, ENC_OPT, HEADER_OPT, NUMBERS_OPT,
               Option("sheet_name", "str", "", "시트 이름", "비우면 파일 이름 사용 (합치기에서는 무시)", remember=False))

    def convert(self, job: Job) -> list[Path]:
        sheets: list[tuple[str, pd.DataFrame]] = []
        for i, path in enumerate(job.inputs):
            for name, df in read_text_table(path, job).items():
                sheets.append((name, df))
            job.progress(0.7 * (i + 1) / len(job.inputs))
        if len(sheets) == 1 and job.opt("sheet_name"):
            sheets = [(job.opt("sheet_name"), sheets[0][1])]
        write_excel(sheets, job.output, header=job.opt("header", True) or any(
            p.suffix.lower() == ".json" for p in job.inputs))
        return [job.output]


@register
class ExcelToText(Converter):
    name = "excel-to-text"
    title = "엑셀 → CSV/TSV/JSON"
    category = "table"
    description = "엑셀 시트를 CSV/TSV/JSON 으로 저장합니다. 기본 인코딩은 엑셀에서 한글이 깨지지 않는 utf-8-sig."
    inputs = frozenset(EXCEL_EXTS)
    outputs = frozenset({"csv", "tsv", "json"})
    multi_output = True
    default_output = "csv"
    options = (
        Option("sheet", "str", "", "시트", "시트 이름 또는 번호(1부터). 비우면 첫 시트, '전체' = 모든 시트를 각각 저장",
               remember=False),
        Option("out_encoding", "choice", "utf-8-sig", "저장 인코딩", "utf-8-sig = 엑셀에서 열어도 한글 안 깨짐",
               choices=("utf-8-sig", "utf-8", "cp949")),
    )

    def convert(self, job: Job) -> list[Path]:
        src = job.inputs[0]
        try:
            book = pd.read_excel(src, sheet_name=None, dtype=object, engine="openpyxl")
        except Exception as exc:
            raise ConvertError(f"엑셀 파일을 열 수 없습니다: {src.name} ({exc})") from exc
        for sheet in book.values():  # 제목이 빈 열은 pandas 가 'Unnamed: 3' 이라고 붙인다 → '열4'
            sheet.columns = [re.sub(r"^Unnamed: (\d+)$", lambda m: f"열{int(m.group(1)) + 1}", str(c))
                             for c in sheet.columns]
        job.progress(0.5)
        names = list(book)
        choice = str(job.opt("sheet", "")).strip()
        if choice in ("전체", "all", "*"):
            selected = names
        elif choice == "":
            selected = names[:1]
        elif choice.isdigit() and 1 <= int(choice) <= len(names):
            selected = [names[int(choice) - 1]]
        elif choice in names:
            selected = [choice]
        else:
            raise ConvertError(f"'{choice}' 시트가 없습니다. 시트 목록: {', '.join(names)}")

        outputs: list[Path] = []
        for name in selected:
            if len(selected) == 1:
                dest = unique_path(job.output, job.overwrite)
            else:
                safe = re.sub(r'[\\/:*?"<>|]', "_", name)
                dest = unique_path(job.output.with_name(f"{job.output.stem}_{safe}{job.output.suffix}"), job.overwrite)
            write_text_table(book[name], job.new_file(dest), job.target, job.opt("out_encoding", "utf-8-sig"))
            outputs.append(dest)
        return outputs


@register
class TableConvert(Converter):
    name = "table-convert"
    title = "CSV/TSV/JSON 형식·인코딩 변환"
    category = "table"
    description = "CSV ↔ TSV ↔ JSON 변환, 또는 cp949(한글 윈도우) CSV 를 utf-8 로 바꾸는 등 인코딩 변환."
    inputs = frozenset(TEXT_EXTS)
    outputs = frozenset({"csv", "tsv", "json"})
    default_output = "csv"
    options = (
        DELIM_OPT, ENC_OPT, HEADER_OPT,
        Option("out_encoding", "choice", "utf-8-sig", "저장 인코딩", "utf-8-sig = 엑셀에서 열어도 한글 안 깨짐",
               choices=("utf-8-sig", "utf-8", "cp949")),
    )

    def convert(self, job: Job) -> list[Path]:
        job.options = {**job.options, "numbers": False}  # 형식 변환은 값을 글자 그대로 옮긴다
        tables = read_text_table(job.inputs[0], job)
        df = pd.concat(list(tables.values()), ignore_index=True) if len(tables) > 1 else next(iter(tables.values()))
        job.progress(0.6)
        write_text_table(df, job.output, job.target, job.opt("out_encoding", "utf-8-sig"),
                         header=bool(job.opt("header", True)))
        return [job.output]
