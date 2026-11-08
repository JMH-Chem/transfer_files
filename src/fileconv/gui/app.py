"""GUI 진입점:  fconv gui  또는  fconv-gui [파일...]"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    argv = list(sys.argv if argv is None else argv)
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("fileconv")
    app.setStyle("Fusion")
    files = [a for a in argv[1:] if not a.startswith("-") and a != "gui"]
    window = MainWindow(initial_files=files)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
