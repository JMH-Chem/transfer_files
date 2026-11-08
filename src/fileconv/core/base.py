"""변환기 공통 틀: Option(옵션 선언), Job(작업 1건), Converter(변환기 추상 클래스)."""

from __future__ import annotations

import re
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar, Literal

from .errors import Cancelled, OptionError

ProgressFn = Callable[[float], None]
OptionType = Literal["str", "int", "float", "bool", "choice", "time", "pages"]

_TIME_RE = re.compile(r"^(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)$")
_PAGES_RE = re.compile(r"^\s*\d+\s*(-\s*\d*\s*)?(,\s*\d+\s*(-\s*\d*\s*)?)*$")
_TRUE = {"1", "true", "yes", "y", "on", "예", "네"}
_FALSE = {"0", "false", "no", "n", "off", "아니오", "아니요"}


@dataclass(frozen=True)
class Option:
    """변환기 옵션 선언. CLI 옵션(--opt name=값)과 GUI 입력 폼이 이 선언 하나로 만들어진다."""

    name: str
    type: OptionType = "str"
    default: Any = None
    label: str = ""
    help: str = ""
    choices: tuple[str, ...] = ()
    min: float | None = None
    max: float | None = None
    remember: bool = True
    """GUI 가 다음 실행 때 이 값을 기억할지. 자르기 구간·페이지 범위·회전처럼 파일마다 다른 값은 False."""

    @property
    def should_remember(self) -> bool:
        return self.remember and self.type not in ("time", "pages") and "password" not in self.name

    @property
    def display(self) -> str:
        return self.label or self.name

    def parse(self, raw: Any) -> Any:
        """문자열(CLI) 또는 값(GUI)을 검증해서 실제 값으로 바꾼다. 빈 값은 None."""
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            return None
        try:
            value = self._parse(raw)
        except OptionError:
            raise
        except (TypeError, ValueError) as exc:
            raise OptionError(f"옵션 '{self.name}' 값이 올바르지 않습니다: {raw!r}") from exc
        if self.type in ("int", "float"):
            if self.min is not None and value < self.min:
                raise OptionError(f"옵션 '{self.name}'은(는) {self.min:g} 이상이어야 합니다 (입력: {value})")
            if self.max is not None and value > self.max:
                raise OptionError(f"옵션 '{self.name}'은(는) {self.max:g} 이하여야 합니다 (입력: {value})")
        return value

    def _parse(self, raw: Any) -> Any:
        if self.type == "str":
            return str(raw)
        if self.type == "int":
            return int(float(raw)) if isinstance(raw, str) else int(raw)
        if self.type == "float":
            return float(raw)
        if self.type == "bool":
            if isinstance(raw, bool):
                return raw
            text = str(raw).strip().lower()
            if text in _TRUE:
                return True
            if text in _FALSE:
                return False
            raise OptionError(f"옵션 '{self.name}'은(는) true/false 중 하나여야 합니다 (입력: {raw!r})")
        if self.type == "choice":
            text = str(raw).strip()
            lowered = {c.lower(): c for c in self.choices}
            if text.lower() in lowered:
                return lowered[text.lower()]
            raise OptionError(
                f"옵션 '{self.name}'은(는) 다음 중 하나여야 합니다: {', '.join(self.choices)} (입력: {raw!r})"
            )
        if self.type == "time":
            return parse_time(str(raw), self.name)
        if self.type == "pages":
            text = str(raw).strip()
            if not _PAGES_RE.match(text):
                raise OptionError(f"옵션 '{self.name}' 형식 예: 1-3,5,8- (입력: {raw!r})")
            return text.replace(" ", "")
        raise OptionError(f"알 수 없는 옵션 형식: {self.type}")


def parse_time(text: str, name: str = "time") -> float:
    """'90', '1:30', '00:01:30.5' 같은 시간을 초(float)로 바꾼다."""
    m = _TIME_RE.match(text.strip())
    if not m:
        raise OptionError(f"옵션 '{name}' 시간 형식 예: 90, 1:30, 00:01:30.5 (입력: {text!r})")
    a, b, sec = m.groups()
    parts = [p for p in (a, b) if p is not None]
    hours, minutes = (0, 0)
    if len(parts) == 2:
        hours, minutes = int(parts[0]), int(parts[1])
    elif len(parts) == 1:
        minutes = int(parts[0])
    return hours * 3600 + minutes * 60 + float(sec)


def parse_pages(spec: str | None, total: int) -> list[int]:
    """'1-3,5,8-' → 0부터 시작하는 페이지 번호 목록. None이면 전체."""
    if not spec:
        return list(range(total))
    result: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start = int(start_s)
            end = int(end_s) if end_s else total
        else:
            start = end = int(part)
        if start < 1 or start > total:
            raise OptionError(f"페이지 {start}은(는) 범위를 벗어났습니다 (전체 {total}쪽)")
        end = min(end, total)
        if end < start:
            raise OptionError(f"페이지 범위가 거꾸로 되어 있습니다: {part}")
        result.extend(range(start - 1, end))
    return result


class CancelToken:
    """GUI의 [취소] 버튼 등에서 작업 중단을 요청할 때 쓰는 신호."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise Cancelled("사용자가 작업을 취소했습니다")


@dataclass
class Job:
    """변환 작업 1건. 합치기(merge) 작업이면 inputs가 여러 개다."""

    inputs: list[Path]
    output: Path
    target: str
    options: dict[str, Any] = field(default_factory=dict)
    progress: ProgressFn = lambda _f: None
    cancel: CancelToken = field(default_factory=CancelToken)
    overwrite: bool = False

    def opt(self, name: str, default: Any = None) -> Any:
        value = self.options.get(name)
        return default if value is None else value


class Converter(ABC):
    """모든 변환기의 부모 클래스. 하위 클래스를 만들고 @register를 붙이면 CLI/GUI에 자동으로 나타난다."""

    name: ClassVar[str]
    title: ClassVar[str]
    category: ClassVar[str]
    inputs: ClassVar[frozenset[str]]
    outputs: ClassVar[frozenset[str]]
    description: ClassVar[str] = ""
    options: ClassVar[tuple[Option, ...]] = ()
    can_merge: ClassVar[bool] = False
    """True면 여러 입력 파일을 출력 파일 하나로 합칠 수 있다 (예: 이미지 여러 장 → PDF 1개)."""
    merge_default: ClassVar[bool] = False
    """GUI에서 '하나로 합치기'를 기본으로 켜둘지 여부."""
    multi_output: ClassVar[bool] = False
    """True면 입력 하나에서 파일 여러 개가 나올 수 있다 (예: PDF → 페이지별 이미지)."""
    priority: ClassVar[int] = 0
    """같은 형식 조합을 여러 변환기가 처리할 때, 또는 목록 정렬/자동 선택 시 높은 쪽이 먼저."""
    default_output: ClassVar[str] = ""
    """GUI 에서 처음 선택되어 있을 출력 형식."""

    def is_available(self) -> tuple[bool, str]:
        """필요한 프로그램/라이브러리가 있는지. (사용 가능 여부, 안내 메시지)"""
        return True, ""

    def option(self, name: str) -> Option:
        for opt in self.options:
            if opt.name == name:
                return opt
        raise OptionError(f"'{self.name}' 변환기에는 '{name}' 옵션이 없습니다")

    def parse_options(self, raw: dict[str, Any]) -> dict[str, Any]:
        """사용자 입력 옵션을 검증하고, 빠진 옵션은 기본값으로 채운다."""
        known = {o.name for o in self.options}
        unknown = set(raw) - known
        if unknown:
            valid = ", ".join(sorted(known)) or "(없음)"
            raise OptionError(f"알 수 없는 옵션: {', '.join(sorted(unknown))} — 사용 가능한 옵션: {valid}")
        parsed: dict[str, Any] = {}
        for opt in self.options:
            value = opt.parse(raw[opt.name]) if opt.name in raw else None
            parsed[opt.name] = opt.default if value is None else value
        return parsed

    @abstractmethod
    def convert(self, job: Job) -> list[Path]:
        """변환을 실행하고 만들어진 파일 경로 목록을 돌려준다."""
