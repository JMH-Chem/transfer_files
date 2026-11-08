"""변환기 등록소. @register 로 등록된 변환기를 (입력 확장자, 출력 확장자)로 찾아준다."""

from __future__ import annotations

from .base import Converter
from .errors import NoConverterError

ALIASES = {"jpeg": "jpg", "tif": "tiff", "htm": "html", "markdown": "md", "yml": "yaml"}

CATEGORIES = {
    "media": "미디어",
    "image": "이미지",
    "table": "표/엑셀",
    "document": "문서/PDF",
}

_registry: dict[str, Converter] = {}
_loaded = False


def norm_ext(ext: str) -> str:
    ext = ext.strip().lower().lstrip(".")
    return ALIASES.get(ext, ext)


def register(cls: type[Converter]) -> type[Converter]:
    """변환기 클래스에 붙이는 데코레이터."""
    if cls.name in _registry:
        raise ValueError(f"변환기 이름 중복: {cls.name}")
    cls.inputs = frozenset(norm_ext(e) for e in cls.inputs)
    cls.outputs = frozenset(norm_ext(e) for e in cls.outputs)
    _registry[cls.name] = cls()
    return cls


def _ensure_loaded() -> None:
    global _loaded
    if not _loaded:
        _loaded = True
        from .. import converters  # noqa: F401  (import 시 모든 변환기가 등록됨)


def all_converters() -> list[Converter]:
    _ensure_loaded()
    order = list(CATEGORIES)
    return sorted(_registry.values(), key=lambda c: (order.index(c.category) if c.category in order else 99, -c.priority, c.name))


def get(name: str) -> Converter:
    _ensure_loaded()
    try:
        return _registry[name]
    except KeyError:
        names = ", ".join(sorted(_registry))
        raise NoConverterError(f"'{name}' 변환기가 없습니다. 사용 가능: {names}") from None


def find(src_ext: str, dst_ext: str) -> list[Converter]:
    """src → dst 변환이 가능한 변환기 목록 (우선순위 높은 순)."""
    _ensure_loaded()
    s, d = norm_ext(src_ext), norm_ext(dst_ext)
    found = [c for c in _registry.values() if s in c.inputs and d in c.outputs]
    return sorted(found, key=lambda c: -c.priority)


def targets_for(src_ext: str) -> dict[str, list[Converter]]:
    """이 확장자 파일을 어떤 형식으로 바꿀 수 있는지: {출력 확장자: [변환기...]}"""
    _ensure_loaded()
    s = norm_ext(src_ext)
    result: dict[str, list[Converter]] = {}
    for conv in all_converters():
        if s in conv.inputs:
            for out in sorted(conv.outputs):
                result.setdefault(out, []).append(conv)
    return result
