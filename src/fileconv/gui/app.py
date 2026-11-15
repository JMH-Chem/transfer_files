"""GUI 진입점:  fconv gui  또는  fconv-gui [파일...]"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

from .. import __version__

_showing_error = False


def error_log_path() -> Path:
    """예기치 못한 오류를 적어 두는 파일. Windows 에서는 %LOCALAPPDATA%\\fileconv\\error.log"""
    from PySide6.QtCore import QStandardPaths

    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    return (Path(base) if base else Path.home() / ".fileconv") / "error.log"


def handle_unexpected_error(exc_type, exc, tb) -> None:
    """어디서도 처리하지 못한 오류를 파일에 기록하고 안내 창을 띄운다.

    창 전용 실행 파일(FileConverter.exe)에는 콘솔이 없어서, 이렇게 하지 않으면 오류가 아무 표시 없이 사라진다.
    (변환 중의 오류는 엔진이 잡아서 화면 아래 기록에 보여 주므로 여기까지 오지 않는다)
    """
    global _showing_error
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    log: Path | None = None
    try:
        log = error_log_path()
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S}  fileconv {__version__} ===\n")
            fh.write("".join(traceback.format_exception(exc_type, exc, tb)))
    except OSError:
        log = None
    sys.__excepthook__(exc_type, exc, tb)

    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance()
    # 안내 창은 화면을 그리는 스레드에서만, 한 번에 하나만 띄운다
    if app is None or QThread.currentThread() != app.thread() or _showing_error:
        return
    _showing_error = True
    try:
        where = f"\n\n자세한 내용을 다음 파일에 기록했습니다:\n{log}" if log else ""
        QMessageBox.critical(None, "파일 변환기 오류",
                             f"예기치 못한 오류가 발생했습니다.\n\n{exc_type.__name__}: {exc}{where}")
    finally:
        _showing_error = False


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    argv = list(sys.argv if argv is None else argv)
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("fileconv")
    app.setStyle("Fusion")
    sys.excepthook = handle_unexpected_error
    from .icon import qicon

    app.setWindowIcon(qicon())
    files = [a for a in argv[1:] if not a.startswith("-") and a != "gui"]
    window = MainWindow(initial_files=files)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
