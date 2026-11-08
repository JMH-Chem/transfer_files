"""변환 엔진: 입력 파일 목록 → 작업 계획(plan) → 실행(run). CLI와 GUI가 함께 쓴다."""

from __future__ import annotations

import logging
import os
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .base import CancelToken, Converter, Job
from .errors import Cancelled, ConvertError, NoConverterError, UnavailableError
from .registry import find, get, norm_ext, targets_for
from .utils import unique_path

log = logging.getLogger("fileconv")


@dataclass
class Task:
    converter: Converter
    inputs: list[Path]
    output: Path
    target: str
    options: dict[str, Any] = field(default_factory=dict)
    overwrite: bool = False

    @property
    def label(self) -> str:
        if len(self.inputs) == 1:
            return f"{self.inputs[0].name} → {self.output.name}"
        return f"{self.inputs[0].name} 외 {len(self.inputs) - 1}개 → {self.output.name}"


@dataclass
class TaskResult:
    task: Task
    outputs: list[Path] = field(default_factory=list)
    error: str | None = None
    seconds: float = 0.0
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and not self.cancelled


def _ext(path: Path) -> str:
    return norm_ext(path.suffix)


def _pick_converter(path: Path, target: str, using: str | None) -> Converter:
    if using:
        conv = get(using)
        if _ext(path) not in conv.inputs:
            raise NoConverterError(f"'{conv.name}' 변환기는 {path.suffix or '(확장자 없음)'} 파일을 받지 않습니다: {path.name}")
        if norm_ext(target) not in conv.outputs:
            raise NoConverterError(f"'{conv.name}' 변환기는 .{target} 로 변환할 수 없습니다")
        return conv
    found = find(_ext(path), target)
    if found:
        return found[0]
    possible = sorted(targets_for(_ext(path)))
    hint = f" 가능한 형식: {', '.join(possible)}" if possible else " 지원하지 않는 파일 형식입니다."
    raise NoConverterError(f"{path.name} 을(를) .{target} 로 바꾸는 변환기가 없습니다.{hint}")


def plan(
    inputs: list[Path],
    target: str,
    *,
    output: Path | str | None = None,
    output_dir: Path | str | None = None,
    merge: bool = False,
    using: str | None = None,
    options: dict[str, Any] | None = None,
    overwrite: bool = False,
) -> list[Task]:
    """어떤 변환기로 어떤 출력 파일을 만들지 미리 계산한다 (아직 파일은 건드리지 않음).

    출력 경로 규칙
    - 아무것도 안 줌       → 원본과 같은 폴더에 '원본이름.target'
    - output_dir           → 그 폴더 안에 '원본이름.target' (없으면 만듦)
    - output 이 폴더       → 이미 있는 폴더, 끝이 / 또는 \\, 또는 입력이 여러 개인데 확장자 없는 이름
    - output 이 파일 경로   → 그 파일 (입력이 하나이거나 merge 일 때만). 확장자가 없으면 붙여 줌
    - 같은 이름 파일이 있으면 '이름 (1).target' (overwrite=True 면 덮어씀, 단 원본은 절대 덮어쓰지 않음)
    - 이번에 만들 결과 파일끼리도 이름이 겹치지 않게 한다
    """
    if not inputs:
        raise ConvertError("변환할 파일이 없습니다")
    suffix = target.strip().lstrip(".").lower()  # 파일 이름에 쓸 확장자 (사용자가 쓴 그대로: jpeg, tif …)
    if not suffix:
        raise ConvertError("출력 형식(확장자)을 지정하세요. 예: mp3, pdf, xlsx")
    key = norm_ext(suffix)  # 변환기에 넘길 표준 형식 이름 (jpeg → jpg, tif → tiff)
    options = options or {}

    convs = [_pick_converter(p, key, using) for p in inputs]
    reserved: set[Path] = {p.resolve() for p in inputs}
    out_dir: Path | None = Path(output_dir).expanduser() if output_dir else None
    out_file: Path | None = None
    if output is not None:
        text = str(output)
        output = Path(text).expanduser()
        single_output = merge or len(inputs) == 1
        if output.is_dir() or text.endswith(("/", "\\")):
            out_dir = output
        elif not single_output:
            if output.suffix or output.exists():
                hint = " 하나로 합치려면 --merge 를 쓰세요." if convs[0].can_merge else ""
                raise ConvertError(
                    f"파일 여러 개를 각각 변환할 때는 -o 에 폴더를 지정하세요 (예: -o 결과/).{hint}")
            out_dir = output
        else:
            out_file = output if output.suffix else output.with_suffix(f".{suffix}")

    def finalize(path: Path, conv: Converter, task_inputs: list[Path]) -> Path:
        if conv.multi_output and path.resolve() in {p.resolve() for p in task_inputs}:
            # 예: PDF 나누기는 '문서.pdf' 를 기준 이름으로만 쓰고 '문서_p001.pdf' 들을 만든다
            return path
        # 여러 파일을 만드는 변환기는 디스크의 기존 파일과의 충돌을 실행할 때 직접 피하므로 여기서는 계획끼리만 비교
        final = unique_path(path, overwrite=overwrite or conv.multi_output, reserved=reserved)
        reserved.add(final.resolve())
        return final

    if merge and len(inputs) > 1:
        conv = convs[0]
        if any(c is not conv for c in convs):
            names = ", ".join(sorted({c.name for c in convs}))
            raise ConvertError(f"서로 다른 종류의 파일은 하나로 합칠 수 없습니다 (필요한 변환기: {names})")
        if not conv.can_merge:
            raise ConvertError(f"'{conv.title}' 변환은 여러 파일 합치기를 지원하지 않습니다")
        parsed = conv.parse_options(options)
        if out_file is None:
            base = out_dir or inputs[0].parent
            out_file = base / f"{inputs[0].stem}_merged.{suffix}"
        return [Task(conv, list(inputs), finalize(out_file, conv, inputs), key, parsed, overwrite)]

    tasks: list[Task] = []
    parsed_cache: dict[str, dict[str, Any]] = {}
    for path, conv in zip(inputs, convs):
        if conv.name not in parsed_cache:
            parsed_cache[conv.name] = conv.parse_options(options)
        dest = out_file if out_file is not None else (out_dir or path.parent) / f"{path.stem}.{suffix}"
        tasks.append(Task(conv, [path], finalize(dest, conv, [path]), key, parsed_cache[conv.name], overwrite))
    return tasks


def supported_split(inputs: list[Path], target: str, using: str | None = None) -> tuple[list[Path], list[Path]]:
    """(이 형식으로 바꿀 수 있는 파일, 바꿀 수 없는 파일). 폴더를 통째로 넣었을 때 섞인 파일을 걸러낼 때 쓴다."""
    ok, skipped = [], []
    for path in inputs:
        try:
            _pick_converter(path, norm_ext(target.strip().lstrip(".")), using)
            ok.append(path)
        except NoConverterError:
            skipped.append(path)
    return ok, skipped


ProgressCb = Callable[[int, float], None]
DoneCb = Callable[[int, TaskResult], None]


def run(
    tasks: list[Task],
    *,
    on_progress: ProgressCb | None = None,
    on_start: Callable[[int, Task], None] | None = None,
    on_done: DoneCb | None = None,
    cancel: CancelToken | None = None,
) -> list[TaskResult]:
    """작업들을 차례대로 실행한다. 하나가 실패해도 나머지는 계속 진행한다."""
    cancel = cancel or CancelToken()
    results: list[TaskResult] = []
    for index, task in enumerate(tasks):
        if cancel.cancelled:
            result = TaskResult(task, cancelled=True, error="취소됨")
            results.append(result)
            if on_done:
                on_done(index, result)
            continue
        if on_start:
            on_start(index, task)
        result = _run_one(index, task, on_progress, cancel)
        results.append(result)
        if on_done:
            on_done(index, result)
    return results


def _run_one(index: int, task: Task, on_progress: ProgressCb | None, cancel: CancelToken) -> TaskResult:
    started = time.perf_counter()
    conv = task.converter
    result = TaskResult(task)

    def progress(fraction: float) -> None:
        cancel.check()
        if on_progress:
            on_progress(index, max(0.0, min(1.0, fraction)))

    # 단일 출력 변환은 임시 파일에 쓴 뒤 성공하면 이름을 바꾼다 → 실패/취소 시 반쪽짜리 파일이 남지 않음
    use_temp = not conv.multi_output
    final = task.output
    temp = final.with_name(f"{final.stem}.fconv-part{final.suffix}") if use_temp else final
    try:
        ok, why = conv.is_available()
        if not ok:
            raise UnavailableError(why)
        final.parent.mkdir(parents=True, exist_ok=True)
        job = Job(
            inputs=task.inputs,
            output=temp,
            target=task.target,
            options=task.options,
            progress=progress,
            cancel=cancel,
            overwrite=task.overwrite,
        )
        outputs = conv.convert(job)
        if use_temp:
            os.replace(temp, final)
            outputs = [final if Path(o) == temp else Path(o) for o in outputs]
        result.outputs = [Path(o) for o in outputs]
        if on_progress:
            on_progress(index, 1.0)
    except Cancelled as exc:
        result.cancelled = True
        result.error = str(exc)
    except ConvertError as exc:
        result.error = str(exc)
    except PermissionError as exc:
        name = Path(exc.filename).name if getattr(exc, "filename", None) else final.name
        result.error = f"파일에 쓸 수 없습니다: {name} — 다른 프로그램(엑셀, 한글, PDF 뷰어 등)에서 열려 있으면 닫고 다시 시도하세요"
    except Exception as exc:  # 예상 못 한 오류도 프로그램 전체를 멈추지 않게
        log.debug("변환 실패\n%s", traceback.format_exc())
        result.error = f"예상치 못한 오류 ({type(exc).__name__}): {exc}"
    finally:
        if use_temp and temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
        result.seconds = time.perf_counter() - started
    return result
