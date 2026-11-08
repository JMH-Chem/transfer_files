"""명령줄 프로그램 `fconv`."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from .. import __version__
from ..core import engine
from ..core.errors import ConvertError
from ..core.registry import CATEGORIES, all_converters, get, norm_ext, targets_for
from ..core.utils import expand_inputs, human_size

app = typer.Typer(
    help="만능 파일 변환기 — 동영상/음성, 이미지/PDF, 텍스트/엑셀, 문서 변환",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()
err_console = Console(stderr=True)


def _version(value: bool) -> None:
    if value:
        console.print(f"fileconv {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(False, "--version", "-V", callback=_version, is_eager=True, help="버전 표시"),
) -> None:
    pass


@app.command("list")
def list_cmd(
    source: Optional[str] = typer.Option(None, "--from", "-f", help="이 확장자(예: mp4)로 할 수 있는 변환만 보기"),
) -> None:
    """지원하는 변환 목록을 보여줍니다."""
    if source:
        targets = targets_for(source)
        if not targets:
            err_console.print(f"[red].{norm_ext(source)} 파일은 지원하지 않습니다[/red]")
            raise typer.Exit(1)
        table = Table(title=f".{norm_ext(source)} 파일을 바꿀 수 있는 형식")
        table.add_column("출력 형식", style="bold cyan")
        table.add_column("변환기")
        for ext, convs in targets.items():
            table.add_row(ext, ", ".join(f"{c.title} ({c.name})" for c in convs))
        console.print(table)
        return

    table = Table(title="지원하는 변환", show_lines=True)
    table.add_column("분류", style="magenta", no_wrap=True)
    table.add_column("이름 / 설명")
    table.add_column("입력", style="cyan")
    table.add_column("출력", style="green")
    table.add_column("합치기", justify="center")
    table.add_column("상태", justify="center")
    for conv in all_converters():
        ok, why = conv.is_available()
        table.add_row(
            CATEGORIES.get(conv.category, conv.category),
            f"[bold]{conv.title}[/bold]  [dim]{conv.name}[/dim]\n{conv.description}",
            " ".join(sorted(conv.inputs)),
            " ".join(sorted(conv.outputs)),
            "O" if conv.can_merge else "",
            "[green]사용 가능[/green]" if ok else f"[red]사용 불가[/red]\n[dim]{why}[/dim]",
        )
    console.print(table)
    console.print("[dim]옵션 보기: fconv info <이름>   예) fconv info to-audio[/dim]")


@app.command("info")
def info_cmd(name: str = typer.Argument(..., help="변환기 이름 (fconv list 에서 확인)")) -> None:
    """변환기의 옵션을 자세히 보여줍니다."""
    try:
        conv = get(name)
    except ConvertError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    ok, why = conv.is_available()
    console.print(f"[bold]{conv.title}[/bold] ({conv.name}) — {CATEGORIES.get(conv.category, conv.category)}")
    console.print(conv.description)
    console.print(f"입력: [cyan]{' '.join(sorted(conv.inputs))}[/cyan]")
    console.print(f"출력: [green]{' '.join(sorted(conv.outputs))}[/green]")
    if conv.can_merge:
        console.print("여러 파일 합치기: [bold]--merge[/bold] 지원")
    if not ok:
        console.print(f"[red]사용 불가: {why}[/red]")
    if conv.options:
        table = Table(title="옵션 ( -O 이름=값 )")
        table.add_column("이름", style="bold cyan")
        table.add_column("설명")
        table.add_column("값", style="green")
        table.add_column("기본값")
        for opt in conv.options:
            values = " / ".join(opt.choices) if opt.choices else {
                "int": "정수", "float": "숫자", "bool": "true/false", "time": "시간 (90, 1:30)",
                "pages": "페이지 (1-3,5)", "str": "글자"}.get(opt.type, opt.type)
            default = "" if opt.default in (None, "") else str(opt.default)
            table.add_row(opt.name, f"{opt.display}\n[dim]{opt.help}[/dim]", values, default)
        console.print(table)


@app.command("convert")
def convert_cmd(
    inputs: List[str] = typer.Argument(..., help="변환할 파일들 (*.jpg 같은 와일드카드, 폴더도 가능)"),
    to: str = typer.Option(..., "--to", "-t", help="출력 형식 (확장자). 예: mp3, pdf, xlsx"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="출력 파일 또는 폴더 (기본: 원본과 같은 폴더)"),
    merge: bool = typer.Option(False, "--merge", "-m", help="여러 파일을 하나로 합치기 (이미지→PDF, PDF 합치기, CSV→엑셀 시트)"),
    opts: List[str] = typer.Option([], "--opt", "-O", help="변환 옵션 '이름=값' (여러 번 가능). fconv info 로 확인"),
    using: Optional[str] = typer.Option(None, "--using", "-u", help="변환기를 직접 지정 (예: pdf-split)"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="폴더를 주면 하위 폴더까지 포함"),
    overwrite: bool = typer.Option(False, "--overwrite", help="같은 이름 파일이 있으면 덮어쓰기"),
    dry_run: bool = typer.Option(False, "--dry-run", help="실제로 바꾸지 않고 어떤 파일이 만들어질지만 보기"),
) -> None:
    """파일을 변환합니다.

    예)  fconv convert 강의.mp4 -t mp3 -O bitrate=320k
         fconv convert "사진/*.jpg" -t pdf --merge -o 앨범.pdf
         fconv convert 데이터.csv -t xlsx
         fconv convert 보고서.pdf -t pdf -u pdf-split
    """
    try:
        options = _parse_opts(opts)
        files = expand_inputs(inputs, recursive=recursive)
        tasks = engine.plan(files, to, output=output, merge=merge, using=using, options=options, overwrite=overwrite)
    except ConvertError as exc:
        err_console.print(f"[red]오류:[/red] {exc}")
        raise typer.Exit(2)

    if dry_run:
        for task in tasks:
            console.print(f"[cyan]{task.converter.name}[/cyan]  {task.label}  [dim]({task.output})[/dim]")
        return

    failed = 0
    with Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        bars = [progress.add_task(t.label, total=1.0, start=False) for t in tasks]

        def on_start(i: int, _task: engine.Task) -> None:
            progress.start_task(bars[i])

        def on_progress(i: int, fraction: float) -> None:
            progress.update(bars[i], completed=fraction)

        def on_done(i: int, result: engine.TaskResult) -> None:
            nonlocal failed
            if result.ok:
                progress.update(bars[i], completed=1.0)
            else:
                failed += 1
                progress.update(bars[i], description=f"[red]✗[/red] {result.task.label}")

        try:
            results = engine.run(tasks, on_start=on_start, on_progress=on_progress, on_done=on_done)
        except KeyboardInterrupt:
            err_console.print("[yellow]중단했습니다[/yellow]")
            raise typer.Exit(130)

    for result in results:
        if result.ok:
            for out in result.outputs:
                size = human_size(out.stat().st_size) if out.exists() else "?"
                console.print(f"[green]✓[/green] {out}  [dim]{size}, {result.seconds:.1f}초[/dim]")
        else:
            err_console.print(f"[red]✗ {result.task.label}[/red]\n  {result.error}")
    if len(results) > 1:
        console.print(f"완료 {len(results) - failed}개, 실패 {failed}개")
    if failed:
        raise typer.Exit(1)


@app.command("gui")
def gui_cmd() -> None:
    """GUI(창) 프로그램을 실행합니다."""
    from ..gui.app import main as gui_main

    gui_main()


def _parse_opts(items: list[str]) -> dict[str, str]:
    options: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ConvertError(f"옵션은 '이름=값' 형식이어야 합니다: {item!r}")
        key, value = item.split("=", 1)
        options[key.strip()] = value.strip()
    return options


def main() -> None:
    # Windows 콘솔에서 한글이 깨지지 않도록
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    app()


if __name__ == "__main__":
    main()
