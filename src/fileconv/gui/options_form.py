"""변환기의 Option 선언을 보고 입력 폼을 자동으로 만든다."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from ..core.base import Converter, Option


class OptionsForm(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QFormLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._widgets: dict[str, tuple[Option, QWidget]] = {}

    def set_converter(self, conv: Converter | None) -> None:
        while self._layout.rowCount():
            self._layout.removeRow(0)
        self._widgets.clear()
        if conv is None:
            return
        if not conv.options:
            self._layout.addRow(QLabel("이 변환에는 옵션이 없습니다."))
            return
        for opt in conv.options:
            widget = self._make_widget(opt)
            widget.setToolTip(opt.help)
            label = QLabel(opt.display)
            label.setToolTip(opt.help)
            self._layout.addRow(label, widget)
            self._widgets[opt.name] = (opt, widget)

    @staticmethod
    def _make_widget(opt: Option) -> QWidget:
        if opt.type == "bool":
            box = QCheckBox()
            box.setChecked(bool(opt.default))
            return box
        if opt.type == "choice":
            combo = QComboBox()
            combo.addItems(list(opt.choices))
            if opt.default in opt.choices:
                combo.setCurrentText(str(opt.default))
            return combo
        if opt.type == "int":
            spin = QSpinBox()
            spin.setRange(int(opt.min if opt.min is not None else -1_000_000),
                          int(opt.max if opt.max is not None else 1_000_000))
            spin.setValue(int(opt.default or 0))
            if opt.min == 0:
                spin.setSpecialValueText("0 (사용 안 함)")
            return spin
        if opt.type == "float":
            dspin = QDoubleSpinBox()
            dspin.setDecimals(2)
            dspin.setSingleStep(0.1 if (opt.max or 100) <= 10 else 1.0)
            dspin.setRange(float(opt.min if opt.min is not None else -1e6), float(opt.max if opt.max is not None else 1e6))
            dspin.setValue(float(opt.default or 0.0))
            return dspin
        line = QLineEdit()
        line.setPlaceholderText(opt.help)
        if opt.default not in (None, ""):
            line.setText(str(opt.default))
        line.setClearButtonEnabled(True)
        return line

    def set_values(self, values: dict[str, Any]) -> None:
        """저장해 둔 값을 폼에 채운다. 맞지 않는 값(선택지에 없음 등)은 무시하고 기본값 유지."""
        for name, value in values.items():
            if name not in self._widgets:
                continue
            opt, widget = self._widgets[name]
            try:
                parsed = opt.parse(value)
            except Exception:
                continue
            if parsed is None:
                continue
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(parsed))
            elif isinstance(widget, QComboBox) and str(parsed) in opt.choices:
                widget.setCurrentText(str(parsed))
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(parsed))
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(parsed))
            elif isinstance(widget, QLineEdit) and opt.name != "password" and "password" not in opt.name:
                widget.setText(str(value))

    def values(self) -> dict[str, Any]:
        """현재 입력값 (빈 칸은 빼서 기본값이 쓰이도록)."""
        result: dict[str, Any] = {}
        for name, (opt, widget) in self._widgets.items():
            if isinstance(widget, QCheckBox):
                result[name] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                result[name] = widget.currentText()
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                result[name] = widget.value()
            elif isinstance(widget, QLineEdit):
                text = widget.text().strip()
                if text:
                    result[name] = text
        return result
