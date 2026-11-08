import os
import sys
import time

import pytest

if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def qapp(tmp_path_factory):
    # 실제 사용자 설정(Windows 레지스트리 등) 대신 임시 ini 파일을 쓰게 한다
    os.environ["FCONV_SETTINGS"] = str(tmp_path_factory.mktemp("settings") / "fileconv.ini")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    os.environ.pop("FCONV_SETTINGS", None)


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


def test_gui_close_during_conversion_waits_for_worker(qapp, images, monkeypatch):
    """변환 중에 창을 닫아도 강제 종료되지 않고, 작업이 끝난 뒤 닫힌다."""
    from PySide6.QtWidgets import QMessageBox

    from fileconv.core.registry import get
    from fileconv.gui.main_window import MainWindow

    conv = get("images-to-pdf")
    original = type(conv).convert

    def slow(self, job):
        for _ in range(30):
            time.sleep(0.02)
            job.progress(0.1)  # 취소 요청을 확인하는 지점
        return original(self, job)

    monkeypatch.setattr(type(conv), "convert", slow)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    w = MainWindow()
    w.show()
    w.add_paths([str(images[0])])
    w.start_conversion()
    assert w.worker is not None
    w.close()
    assert w.isVisible()  # 아직 작업 중이라 바로 닫히지 않음
    wait(qapp, w)
    qapp.processEvents()
    assert not w.isVisible()


def test_gui_remembers_options_per_converter(qapp):
    from fileconv.gui.main_window import MainWindow

    w = MainWindow()
    w.select_converter("to-audio")
    w.target_combo.setCurrentText("flac")
    _, combo = w.options_form._widgets["bitrate"]
    combo.setCurrentText("320k")
    w._save_settings()
    w.close()
    w2 = MainWindow()
    w2.select_converter("to-audio")
    assert w2.target_combo.currentText() == "flac"
    assert w2.options_form.values()["bitrate"] == "320k"
    w2.select_converter("images-to-pdf")
    assert w2.target_combo.currentText() == "pdf"
    w2.close()


def test_gui_does_not_remember_one_off_options(qapp):
    from fileconv.gui.main_window import MainWindow

    w = MainWindow()
    w.select_converter("video-convert")
    _, start = w.options_form._widgets["start"]
    _, end = w.options_form._widgets["end"]
    _, quality = w.options_form._widgets["quality"]
    start.setText("0:10")
    end.setText("0:20")
    quality.setCurrentText("높음")
    w._save_settings()
    w.close()
    w2 = MainWindow()
    w2.select_converter("video-convert")
    values = w2.options_form.values()
    assert "start" not in values and "end" not in values  # 자르기 구간은 다음 파일에 적용되면 안 됨
    assert values["quality"] == "높음"  # 화질 같은 취향 설정은 기억
    w2.close()
