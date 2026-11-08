"""변환을 백그라운드 스레드에서 실행해서 창이 멈추지 않게 한다."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ..core import engine
from ..core.base import CancelToken


class ConvertWorker(QThread):
    task_started = Signal(int, str)          # (작업 번호, 설명)
    task_progress = Signal(int, float)       # (작업 번호, 0~1)
    task_done = Signal(int, bool, str)       # (작업 번호, 성공 여부, 메시지)
    all_done = Signal(int, int)              # (성공 수, 실패 수)

    def __init__(self, tasks: list[engine.Task], parent=None) -> None:
        super().__init__(parent)
        self.tasks = tasks
        self.cancel_token = CancelToken()
        self.outputs: list = []

    def cancel(self) -> None:
        self.cancel_token.cancel()

    def run(self) -> None:
        ok = failed = 0

        def on_done(i: int, result: engine.TaskResult) -> None:
            nonlocal ok, failed
            if result.ok:
                ok += 1
                self.outputs.extend(result.outputs)
                names = ", ".join(p.name for p in result.outputs[:3])
                more = f" 외 {len(result.outputs) - 3}개" if len(result.outputs) > 3 else ""
                self.task_done.emit(i, True, f"{names}{more} ({result.seconds:.1f}초)")
            else:
                failed += 1
                self.task_done.emit(i, False, result.error or "실패")

        engine.run(
            self.tasks,
            on_start=lambda i, t: self.task_started.emit(i, t.label),
            on_progress=lambda i, f: self.task_progress.emit(i, f),
            on_done=on_done,
            cancel=self.cancel_token,
        )
        self.all_done.emit(ok, failed)
