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


def test_gui_text_to_pdf_in_worker_thread(qapp, tmp_path):
    """Qt 로 PDF 를 그리는 변환이 GUI 백그라운드 스레드에서도 문제없이 도는지."""
    from fileconv.gui.main_window import MainWindow

    md = tmp_path / "메모.md"
    md.write_text("# 제목\n\n본문입니다.", encoding="utf-8")
    w = MainWindow()
    w.add_paths([str(md)])
    w.select_converter("text-to-pdf")
    assert w.target_combo.currentText() == "pdf"
    w.start_conversion()
    wait(qapp, w)
    assert (tmp_path / "메모.pdf").stat().st_size > 1000
    w.close()


def test_gui_start_disabled_for_unavailable_converter(qapp, monkeypatch):
    from fileconv.core.registry import get
    from fileconv.gui.main_window import MainWindow

    conv = get("hwp-to-pdf")
    monkeypatch.setattr(type(conv), "is_available", lambda self: (False, "한컴오피스 없음"))
    w = MainWindow()
    w.select_converter("hwp-to-pdf")
    assert not w.start_btn.isEnabled() and "한컴오피스" in w.start_btn.toolTip()
    w.select_converter("images-to-pdf")
    assert w.start_btn.isEnabled()
    w.close()
