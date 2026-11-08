import os
import sys
import time

import pytest

if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from PySide6.QtCore import QSettings

    QSettings("fileconv", "fileconv").clear()
    return app


def wait(app, window, timeout=30):
    start = time.time()
    while window.worker is not None and time.time() - start < timeout:
        app.processEvents()
        time.sleep(0.01)
    assert window.worker is None


def test_gui_drop_selects_converter_and_converts(qapp, images, tmp_path):
    from fileconv.gui.main_window import MainWindow

    w = MainWindow()
    w.add_paths([str(p) for p in images])
    assert w.converter.name == "images-to-pdf"
    assert w.target_combo.currentText() == "pdf" and w.merge_check.isChecked()
    w.merge_name.setText("결과")
    w.start_conversion()
    wait(qapp, w)
    assert (tmp_path / "결과.pdf").exists()
    w.close()


def test_gui_unsupported_files_skipped(qapp, images, korean_csv, tmp_path):
    from fileconv.gui.main_window import MainWindow

    w = MainWindow()
    w.add_paths([str(korean_csv)])
    w.add_paths([str(images[0])])  # 엑셀 변환기에선 지원 안 함 → 건너뜀 표시
    assert w.converter.name == "text-to-excel"
    tasks = w.build_tasks()
    assert [t.inputs[0].name for t in tasks] == ["고객목록.csv"]
    w.close()
