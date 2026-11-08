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
from ..core.utils import detect_encoding, safe_sheet_name

TEXT_EXTS = {"csv", "tsv", "txt", "json"}
EXCEL_EXTS = {"xlsx", "xlsm"}

DELIMITERS = {"쉼표": ",", "탭": "\t", "세미콜론": ";", "세로선": "|", "공백": r"\s+", "없음": None}
ENCODING_CHOICES = ("자동", "utf-8", "cp949", "euc-kr", "utf-16")
_LEADING_ZERO = re.compile(r"^[+-]?0\d")

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


def _smart_numbers(df: pd.DataFrame) -> pd.DataFrame:
    """글자로 읽은 표에서 '진짜 숫자' 열만 숫자로 바꾼다 (0으로 시작하는 코드 값 등은 보존)."""
    for col in df.columns:
        series = df[col]
        values = series.dropna().astype(str).str.strip()
        values = values[values != ""]
        if values.empty or values.str.match(_LEADING_ZERO).any():
            continue
        cleaned = values.str.replace(",", "", regex=False) if values.str.match(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$").all() else values
        converted = pd.to_numeric(cleaned, errors="coerce")
        if converted.notna().all():
            full = pd.to_numeric(series.astype(str).str.strip().str.replace(",", "", regex=False), errors="coerce")
            if (full.dropna() == full.dropna().round()).all() and full.abs().max() < 2**53:
                full = full.astype("Int64")
            df[col] = full
    return df


def read_text_table(path: Path, job: Job) -> dict[str, pd.DataFrame]:
    """파일 하나를 읽어 {시트이름 후보: 표}. JSON 은 표가 여러 개일 수 있다."""
    ext = path.suffix.lower().lstrip(".")
    enc_choice = job.opt("encoding", "자동")
    encoding = detect_encoding(path) if enc_choice == "자동" else enc_choice
    try:
        text = path.read_text(encoding=encoding)
    except UnicodeDecodeError as exc:
        raise ConvertError(f"{path.name}: '{encoding}' 인코딩으로 읽을 수 없습니다. 인코딩 옵션을 바꿔보세요") from exc
    if text.startswith("﻿"):
        text = text[1:]

    if ext == "json":
        return _read_json(path, text)

    choice = job.opt("delimiter", "자동")
    delim = _guess_delimiter(text[:20000], ext) if choice == "자동" else DELIMITERS[choice]
    header = 0 if job.opt("header", True) else None
    from io import StringIO

    try:
        if delim is None:  # 구분자 없음 → 한 줄이 한 칸
            rows = text.splitlines()
            df = pd.DataFrame({rows[0]: rows[1:]}) if header == 0 and rows else pd.DataFrame({"내용": rows})
        else:
            df = pd.read_csv(
                StringIO(text),
                sep=delim,
                header=header,
                dtype=str,
                keep_default_na=False,
                engine="python",
                skipinitialspace=delim == r"\s+",
                on_bad_lines="warn",
            )
    except Exception as exc:
        raise ConvertError(f"{path.name}: 표로 읽을 수 없습니다 ({exc})") from exc
    if header is None:
        df.columns = [f"열{i + 1}" for i in range(len(df.columns))]
    df = df.replace("", pd.NA)
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


def write_excel(sheets: list[tuple[str, pd.DataFrame]], output: Path, header: bool = True) -> None:
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    used: set[str] = set()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for raw_name, df in sheets:
            name = safe_sheet_name(raw_name, used)
            df.to_excel(writer, sheet_name=name, index=False, header=header)
            ws = writer.sheets[name]
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


def write_text_table(df: pd.DataFrame, output: Path, fmt: str, encoding: str) -> None:
    if fmt == "json":
        records = json.loads(df.to_json(orient="records", force_ascii=False, date_format="iso"))
        output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    df.to_csv(output, sep="\t" if fmt == "tsv" else ",", index=False, encoding=encoding, lineterminator="\r\n")


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
               Option("sheet_name", "str", "", "시트 이름", "비우면 파일 이름 사용 (합치기에서는 무시)"))

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
        Option("sheet", "str", "", "시트", "시트 이름 또는 번호(1부터). 비우면 첫 시트, '전체' = 모든 시트를 각각 저장"),
        Option("out_encoding", "choice", "utf-8-sig", "저장 인코딩", "utf-8-sig = 엑셀에서 열어도 한글 안 깨짐",
               choices=("utf-8-sig", "utf-8", "cp949")),
    )

    def convert(self, job: Job) -> list[Path]:
        from ..core.utils import unique_path

        src = job.inputs[0]
        try:
            book = pd.read_excel(src, sheet_name=None, dtype=object, engine="openpyxl")
        except Exception as exc:
            raise ConvertError(f"엑셀 파일을 열 수 없습니다: {src.name} ({exc})") from exc
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
                dest = job.output
            else:
                safe = re.sub(r'[\\/:*?"<>|]', "_", name)
                dest = unique_path(job.output.with_name(f"{job.output.stem}_{safe}{job.output.suffix}"), job.overwrite)
            write_text_table(book[name], dest, job.target, job.opt("out_encoding", "utf-8-sig"))
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
        if not job.opt("header", True) and job.target != "json":
            df.to_csv(job.output, sep="\t" if job.target == "tsv" else ",", index=False, header=False,
                      encoding=job.opt("out_encoding", "utf-8-sig"), lineterminator="\r\n")
        else:
            try:
                write_text_table(df, job.output, job.target, job.opt("out_encoding", "utf-8-sig"))
            except UnicodeEncodeError as exc:
                raise ConvertError("cp949 로 저장할 수 없는 글자가 있습니다. 저장 인코딩을 utf-8-sig 로 바꾸세요") from exc
        return [job.output]
