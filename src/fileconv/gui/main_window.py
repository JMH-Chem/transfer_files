"""메인 창: 왼쪽 변환 종류, 가운데 파일 목록(드래그앤드롭), 오른쪽 옵션, 아래 진행 상황."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core import engine
from ..core.base import Converter
from ..core.errors import ConvertError
from ..core.registry import CATEGORIES, all_converters, norm_ext
from ..core.utils import expand_inputs, natural_key
from .options_form import OptionsForm
from .worker import ConvertWorker

ROLE_PATH = Qt.ItemDataRole.UserRole
ROLE_CONV = Qt.ItemDataRole.UserRole + 1


class FileList(QListWidget):
    """탐색기에서 파일/폴더를 끌어다 놓을 수 있고, 끌어서 순서를 바꿀 수 있는 목록."""

    def __init__(self, on_drop, parent=None) -> None:
        super().__init__(parent)
        self._on_drop = on_drop
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            event.acceptProposedAction()
            self._on_drop(paths)
        else:
            super().dropEvent(event)

    def paths(self) -> list[Path]:
        return [Path(self.item(i).data(ROLE_PATH)) for i in range(self.count())]

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            for item in self.selectedItems():
                self.takeItem(self.row(item))
            self.model().rowsRemoved.emit(self.rootIndex(), 0, 0)
        else:
            super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, initial_files: list[str] | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"파일 변환기 fileconv {__version__}")
        self.resize(1100, 720)
        self.settings = QSettings("fileconv", "fileconv")
        self.converter: Converter | None = None
        self.worker: ConvertWorker | None = None
        self.last_output_dir: Path | None = None
        self._closing = False
        self.setAcceptDrops(True)
        self._build_ui()
        self.file_list.model().rowsRemoved.connect(lambda *_: self._update_count())
        self.file_list.model().rowsInserted.connect(lambda *_: self._update_count())
        self._restore_settings()
        if initial_files:
            self.add_paths(initial_files)

    # ------------------------------------------------------------ 화면 구성
    def _build_ui(self) -> None:
        # 왼쪽: 변환 종류
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(220)
        bold = QFont()
        bold.setBold(True)
        groups: dict[str, QTreeWidgetItem] = {}
        for conv in all_converters():
            if conv.category not in groups:
                group = QTreeWidgetItem([CATEGORIES.get(conv.category, conv.category)])
                group.setFont(0, bold)
                group.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.tree.addTopLevelItem(group)
                groups[conv.category] = group
            item = QTreeWidgetItem([conv.title])
            item.setData(0, ROLE_CONV, conv.name)
            ok, why = conv.is_available()
            item.setToolTip(0, conv.description + ("" if ok else f"\n\n사용 불가: {why}"))
            if not ok:
                item.setForeground(0, Qt.GlobalColor.gray)
            groups[conv.category].addChild(item)
        self.tree.expandAll()
        self.tree.currentItemChanged.connect(self._on_tree_changed)

        # 가운데: 파일 목록
        self.desc_label = QLabel()
        self.desc_label.setWordWrap(True)
        self.file_list = FileList(self.add_paths)
        self.drop_hint = QLabel("여기에 파일이나 폴더를 끌어다 놓으세요")
        self.drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_hint.setStyleSheet("color: gray;")
        btn_add = QPushButton("파일 추가…")
        btn_add.clicked.connect(self._pick_files)
        btn_folder = QPushButton("폴더 추가…")
        btn_folder.clicked.connect(self._pick_folder)
        btn_up = QPushButton("▲")
        btn_up.setToolTip("선택한 파일을 위로")
        btn_up.clicked.connect(lambda: self._move_selected(-1))
        btn_down = QPushButton("▼")
        btn_down.setToolTip("선택한 파일을 아래로")
        btn_down.clicked.connect(lambda: self._move_selected(1))
        btn_sort = QPushButton("이름순")
        btn_sort.clicked.connect(self._sort_files)
        btn_remove = QPushButton("선택 삭제")
        btn_remove.clicked.connect(self._remove_selected)
        btn_clear = QPushButton("모두 지우기")
        btn_clear.clicked.connect(self._clear_files)
        file_buttons = QHBoxLayout()
        for b in (btn_add, btn_folder, btn_up, btn_down, btn_sort, btn_remove, btn_clear):
            file_buttons.addWidget(b)
        file_buttons.addStretch()
        self.count_label = QLabel()

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.addWidget(self.desc_label)
        center_layout.addLayout(file_buttons)
        center_layout.addWidget(self.file_list, 1)
        center_layout.addWidget(self.drop_hint)
        center_layout.addWidget(self.count_label)

        # 오른쪽: 출력 설정 + 옵션
        self.target_combo = QComboBox()
        self.merge_check = QCheckBox("여러 파일을 하나로 합치기")
        self.merge_name = QLineEdit()
        self.merge_name.setPlaceholderText("합친 파일 이름 (비우면 자동)")
        self.merge_check.toggled.connect(self.merge_name.setEnabled)
        self.same_dir_radio = QRadioButton("원본과 같은 폴더")
        self.custom_dir_radio = QRadioButton("다른 폴더:")
        self.same_dir_radio.setChecked(True)
        self.out_dir_edit = QLineEdit()
        btn_browse = QPushButton("찾아보기…")
        btn_browse.clicked.connect(self._pick_output_dir)
        self.custom_dir_radio.toggled.connect(self.out_dir_edit.setEnabled)
        self.custom_dir_radio.toggled.connect(btn_browse.setEnabled)
        self.out_dir_edit.setEnabled(False)
        btn_browse.setEnabled(False)
        self.overwrite_check = QCheckBox("같은 이름 파일 덮어쓰기")

        out_box = QGroupBox("출력")
        out_form = QFormLayout(out_box)
        out_form.addRow("형식", self.target_combo)
        out_form.addRow(self.merge_check)
        out_form.addRow(self.merge_name)
        out_form.addRow(self.same_dir_radio)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.custom_dir_radio)
        dir_row.addWidget(self.out_dir_edit, 1)
        dir_row.addWidget(btn_browse)
        out_form.addRow(dir_row)
        out_form.addRow(self.overwrite_check)

        self.options_form = OptionsForm()
        opt_box = QGroupBox("옵션")
        opt_layout = QVBoxLayout(opt_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self.options_form)
        opt_layout.addWidget(scroll)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(out_box)
        right_layout.addWidget(opt_box, 1)
        right.setMinimumWidth(320)

        splitter = QSplitter()
        splitter.addWidget(self.tree)
        splitter.addWidget(center)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([230, 500, 360])

        # 아래: 진행 상황
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(True)
        self.progress.setFormat("대기 중")
        self.start_btn = QPushButton("변환 시작")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.setStyleSheet("font-weight: bold;")
        self.start_btn.clicked.connect(self.start_conversion)
        self.cancel_btn = QPushButton("취소")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_conversion)
        self.open_btn = QPushButton("결과 폴더 열기")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_output_dir)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setFixedHeight(140)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self.progress, 1)
        bottom_row.addWidget(self.start_btn)
        bottom_row.addWidget(self.cancel_btn)
        bottom_row.addWidget(self.open_btn)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.addWidget(splitter, 1)
        root_layout.addLayout(bottom_row)
        root_layout.addWidget(self.log)
        self.setCentralWidget(root)
        self._update_count()

    # ------------------------------------------------------------ 변환기 선택
    def _on_tree_changed(self, current: QTreeWidgetItem | None, _prev) -> None:
        if current is None:
            return
        name = current.data(0, ROLE_CONV)
        if not name:  # 분류 제목을 누르면 첫 번째 변환기로
            if current.childCount():
                self.tree.setCurrentItem(current.child(0))
            return
        conv = next(c for c in all_converters() if c.name == name)
        self.set_converter(conv)

    def select_converter(self, name: str) -> None:
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            for j in range(group.childCount()):
                if group.child(j).data(0, ROLE_CONV) == name:
                    self.tree.setCurrentItem(group.child(j))
                    return

    def set_converter(self, conv: Converter) -> None:
        self.converter = conv
        ok, why = conv.is_available()
        text = f"<b>{conv.title}</b><br>{conv.description}<br><span style='color:gray'>입력: {' '.join(sorted(conv.inputs))}</span>"
        if not ok:
            text += f"<br><span style='color:#c0392b'>사용 불가: {why}</span>"
        self.desc_label.setText(text)
        if self.worker is None:  # 쓸 수 없는 변환이면 시작 버튼을 막고 이유를 보여준다
            self.start_btn.setEnabled(ok)
            self.start_btn.setToolTip("" if ok else why)
        self.target_combo.clear()
        self.target_combo.addItems(sorted(conv.outputs))
        if conv.default_output in conv.outputs:
            self.target_combo.setCurrentText(conv.default_output)
        self.merge_check.setVisible(conv.can_merge)
        self.merge_name.setVisible(conv.can_merge)
        self.merge_check.setChecked(conv.can_merge and conv.merge_default)
        self.merge_name.setEnabled(self.merge_check.isChecked())
        self.options_form.set_converter(conv)
        self._refresh_file_marks()

    # ------------------------------------------------------------ 파일 목록
    def add_paths(self, paths: list[str]) -> None:
        try:
            files = expand_inputs(paths, recursive=True)
        except ConvertError as exc:
            self._log(f"⚠ {exc}")
            return
        if not files:
            return
        # 목록이 비어 있고 현재 변환기가 받지 못하는 파일이면, 그 파일을 받는 변환기로 자동 전환
        exts = {norm_ext(f.suffix) for f in files}
        if self.converter is None or (self.file_list.count() == 0 and not (exts & self.converter.inputs)):
            for conv in all_converters():
                if norm_ext(files[0].suffix) in conv.inputs and conv.is_available()[0]:
                    self.select_converter(conv.name)
                    self._log(f"'{conv.title}' 변환을 선택했습니다 (다른 변환은 왼쪽 목록에서 고르세요)")
                    break
        existing = {p.resolve() for p in self.file_list.paths()}
        added = 0
        for f in files:
            if f.resolve() in existing:
                continue
            item = QListWidgetItem(f.name)
            item.setData(ROLE_PATH, str(f))
            item.setToolTip(str(f))
            self.file_list.addItem(item)
            existing.add(f.resolve())
            added += 1
        self._refresh_file_marks()
        self._log(f"파일 {added}개 추가")

    def _refresh_file_marks(self) -> None:
        conv = self.converter
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            path = Path(item.data(ROLE_PATH))
            supported = conv is None or norm_ext(path.suffix) in conv.inputs
            item.setForeground(Qt.GlobalColor.gray if not supported else self.palette().text().color())
            item.setText(path.name if supported else f"{path.name}   (이 변환에서 지원 안 함 — 건너뜀)")
        self._update_count()

    def _update_count(self) -> None:
        n = self.file_list.count()
        self.drop_hint.setVisible(n == 0)
        self.count_label.setText(f"파일 {n}개" if n else "")

    def _pick_files(self) -> None:
        start = self.settings.value("last_input_dir", str(Path.home()))
        patterns = ""
        if self.converter:
            patterns = f"{self.converter.title} ({' '.join('*.' + e for e in sorted(self.converter.inputs))});;"
        files, _ = QFileDialog.getOpenFileNames(self, "파일 선택", start, patterns + "모든 파일 (*)")
        if files:
            self.settings.setValue("last_input_dir", str(Path(files[0]).parent))
            self.add_paths(files)

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "폴더 선택", self.settings.value("last_input_dir", str(Path.home())))
        if folder:
            self.add_paths([folder])

    def _pick_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "저장할 폴더", self.out_dir_edit.text() or str(Path.home()))
        if folder:
            self.out_dir_edit.setText(folder)

    def _move_selected(self, step: int) -> None:
        rows = sorted((self.file_list.row(i) for i in self.file_list.selectedItems()), reverse=step > 0)
        for row in rows:
            new = row + step
            if 0 <= new < self.file_list.count():
                item = self.file_list.takeItem(row)
                self.file_list.insertItem(new, item)
                item.setSelected(True)

    def _sort_files(self) -> None:
        items = [self.file_list.takeItem(0) for _ in range(self.file_list.count())]
        for item in sorted(items, key=lambda it: natural_key(Path(it.data(ROLE_PATH)).name)):
            self.file_list.addItem(item)

    def _remove_selected(self) -> None:
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))
        self._update_count()

    def _clear_files(self) -> None:
        self.file_list.clear()
        self._update_count()

    # ------------------------------------------------------------ 변환 실행
    def build_tasks(self) -> list[engine.Task]:
        conv = self.converter
        if conv is None:
            raise ConvertError("왼쪽에서 변환 종류를 고르세요")
        files = [p for p in self.file_list.paths() if norm_ext(p.suffix) in conv.inputs]
        if not files:
            raise ConvertError("변환할 파일을 추가하세요 (회색 파일은 이 변환에서 지원하지 않습니다)")
        target = self.target_combo.currentText()
        out_dir = Path(self.out_dir_edit.text()) if self.custom_dir_radio.isChecked() and self.out_dir_edit.text() else None
        if self.custom_dir_radio.isChecked() and out_dir is None:
            raise ConvertError("저장할 폴더를 지정하세요")
        merge = conv.can_merge and self.merge_check.isChecked() and len(files) > 1
        output: Path | None = None
        if merge and self.merge_name.text().strip():
            name = self.merge_name.text().strip()
            if not name.lower().endswith(f".{target}"):
                name += f".{target}"
            output = (out_dir or files[0].parent) / name
        return engine.plan(
            files, target, output=output, output_dir=out_dir, merge=merge, using=conv.name,
            options=self.options_form.values(), overwrite=self.overwrite_check.isChecked(),
        )

    def start_conversion(self) -> None:
        if self.worker is not None:
            return
        self._refresh_file_marks()  # 지난 결과 표시(✓/✗) 지우기
        try:
            tasks = self.build_tasks()
        except ConvertError as exc:
            QMessageBox.warning(self, "변환할 수 없음", str(exc))
            return
        self._save_settings()
        self._log(f"── 변환 시작: {self.converter.title}, 작업 {len(tasks)}개")
        self.worker = ConvertWorker(tasks, self)
        self._task_count = len(tasks)
        self.worker.task_started.connect(self._on_task_started)
        self.worker.task_progress.connect(self._on_task_progress)
        self.worker.task_done.connect(self._on_task_done)
        self.worker.all_done.connect(self._on_all_done)
        self._set_running(True)
        self.worker.start()

    def cancel_conversion(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)
            self._log("취소하는 중…")

    def _set_running(self, running: bool) -> None:
        available = self.converter is not None and self.converter.is_available()[0]
        self.start_btn.setEnabled(not running and available)
        self.cancel_btn.setEnabled(running)
        self.tree.setEnabled(not running)
        self.file_list.setEnabled(not running)

    def _on_task_started(self, index: int, label: str) -> None:
        self.progress.setFormat(f"{index + 1}/{self._task_count}  {label}  %p%")

    def _on_task_progress(self, index: int, fraction: float) -> None:
        overall = (index + fraction) / max(1, self._task_count)
        self.progress.setValue(int(overall * 1000))

    def _on_task_done(self, index: int, ok: bool, message: str) -> None:
        task = self.worker.tasks[index] if self.worker else None
        label = task.label if task else f"작업 {index + 1}"
        self._log(f"{'✓' if ok else '✗'} {label}\n    {message}")
        if ok and task:
            self.last_output_dir = task.output.parent
        if task:  # 목록의 파일 이름 앞에 결과 표시
            done = {str(p) for p in task.inputs}
            for i in range(self.file_list.count()):
                item = self.file_list.item(i)
                if item.data(ROLE_PATH) in done:
                    name = Path(item.data(ROLE_PATH)).name
                    item.setText(f"{'✓' if ok else '✗'} {name}")
                    item.setToolTip(f"{item.data(ROLE_PATH)}\n{message}")
                    item.setForeground(Qt.GlobalColor.darkGreen if ok else Qt.GlobalColor.red)

    def _on_all_done(self, ok: int, failed: int) -> None:
        self.worker.wait()
        self.worker = None
        if self._closing:
            self.close()
            return
        self._set_running(False)
        self.progress.setValue(1000 if not failed else self.progress.value())
        self.progress.setFormat(f"완료 {ok}개" + (f", 실패 {failed}개" if failed else ""))
        self.open_btn.setEnabled(self.last_output_dir is not None)
        self._log(f"── 끝: 성공 {ok}개, 실패 {failed}개")
        if failed and not ok:
            QMessageBox.warning(self, "변환 실패", "모든 작업이 실패했습니다. 아래 기록을 확인하세요.")

    def _open_output_dir(self) -> None:
        if self.last_output_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_output_dir)))

    def _log(self, text: str) -> None:
        self.log.appendPlainText(text)

    # ------------------------------------------------------------ 창 전체에 끌어다 놓기
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and self.worker is None:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.add_paths(paths)

    # ------------------------------------------------------------ 설정 저장
    def _restore_settings(self) -> None:
        name = self.settings.value("converter", "")
        self.select_converter(name or all_converters()[0].name)
        if self.converter is None:
            self.select_converter(all_converters()[0].name)
        out_dir = self.settings.value("output_dir", "")
        if out_dir:
            self.out_dir_edit.setText(out_dir)
        if self.settings.value("use_custom_dir", "false") == "true":
            self.custom_dir_radio.setChecked(True)

    def _save_settings(self) -> None:
        if self.converter:
            self.settings.setValue("converter", self.converter.name)
        self.settings.setValue("output_dir", self.out_dir_edit.text())
        self.settings.setValue("use_custom_dir", "true" if self.custom_dir_radio.isChecked() else "false")

    def closeEvent(self, event) -> None:
        if self.worker is not None:
            if not self._closing:
                answer = QMessageBox.question(self, "종료", "변환 중입니다. 취소하고 종료할까요?")
                if answer != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
                # 스레드가 도는 중에 창을 없애면 프로그램이 강제 종료되므로, 취소를 요청하고 끝나면 닫는다
                self._closing = True
                self.worker.cancel()
                self.cancel_btn.setEnabled(False)
                self.progress.setFormat("취소하는 중… 끝나면 자동으로 닫힙니다")
                self._log("종료 요청: 지금 하던 작업이 끝나는 대로 닫습니다")
            event.ignore()
            return
        self._save_settings()
        super().closeEvent(event)
