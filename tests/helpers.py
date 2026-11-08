from __future__ import annotations

from pathlib import Path

from fileconv.core import engine


def convert(inputs, target, **kwargs) -> list[Path]:
    """변환하고 성공했는지 확인한 뒤 결과 파일 목록을 돌려준다."""
    tasks = engine.plan([Path(p) for p in inputs], target, **kwargs)
    results = engine.run(tasks)
    for r in results:
        assert r.ok, r.error
    return [o for r in results for o in r.outputs]
