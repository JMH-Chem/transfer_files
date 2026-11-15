from typer.testing import CliRunner

from fileconv.cli.main import app

runner = CliRunner()


def test_list_and_info():
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0 and "to-audio" in result.output
    result = runner.invoke(app, ["list", "--from", "mp4"])
    assert result.exit_code == 0 and "mp3" in result.output
    result = runner.invoke(app, ["info", "images-to-pdf"])
    assert result.exit_code == 0 and "page_size" in result.output


def test_convert_with_glob_and_merge(images, tmp_path):
    result = runner.invoke(app, ["convert", str(tmp_path / "img*.jpg"), "-t", "pdf", "--merge", "-o", str(tmp_path / "out.pdf")])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "out.pdf").exists()


def test_convert_errors(tmp_path, korean_csv):
    result = runner.invoke(app, ["convert", str(korean_csv), "-t", "mp3"])
    assert result.exit_code == 2 and "가능한 형식" in result.output
    result = runner.invoke(app, ["convert", str(korean_csv), "-t", "xlsx", "-O", "badformat"])
    assert result.exit_code == 2


def test_dry_run(korean_csv):
    result = runner.invoke(app, ["convert", str(korean_csv), "-t", "xlsx", "--dry-run"])
    assert result.exit_code == 0 and "text-to-excel" in result.output
    assert not korean_csv.with_suffix(".xlsx").exists()


def test_doctor():
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "ffmpeg" in result.output and "사용 가능" in result.output


def test_bracket_names_are_shown_literally(tmp_path):
    """파일 이름의 [bold] 같은 글자가 화면 꾸밈 명령으로 해석되어 사라지면 안 된다."""
    from PIL import Image

    good = tmp_path / "Movie [x264].png"
    Image.new("RGB", (10, 10), "red").save(good)
    bad = tmp_path / "bad[bold].png"
    bad.write_bytes(b"not an image")
    wide = {"COLUMNS": "400"}  # 긴 임시 폴더 경로 때문에 이름이 줄바꿈으로 잘리지 않게
    result = runner.invoke(app, ["convert", str(good), str(bad), "-t", "jpg"], env=wide)
    assert result.exit_code == 1
    assert "Movie [x264].jpg" in result.output
    assert "bad[bold].png" in result.output
    result = runner.invoke(app, ["convert", str(good), "-t", "webp", "--dry-run"], env=wide)
    assert "Movie [x264].webp" in result.output


def test_markup_like_text_does_not_crash(tmp_path):
    wide = {"COLUMNS": "400"}
    # 폴더 경로를 붙이지 않고 글자 그대로 넘긴다: Windows 에서는 '/' 가 폴더 구분자라 경로로 만들면 모양이 바뀐다
    result = runner.invoke(app, ["convert", "없는[/red]파일.png", "-t", "jpg"], env=wide)
    assert result.exit_code == 2, result.output  # MarkupError 로 죽지 않고 '파일 없음' 안내
    assert "없는[/red]파일.png" in result.output
    result = runner.invoke(app, ["info", "없는[/b]변환기"], env=wide)
    assert result.exit_code == 1 and "없는[/b]변환기" in result.output


def test_many_files_use_one_progress_bar(tmp_path):
    """파일이 많으면 파일마다 막대를 그리지 않는다 (60개 → 화면이 막대로 뒤덮이던 문제)."""
    from PIL import Image

    for i in range(25):
        Image.new("RGB", (8, 8), "red").save(tmp_path / f"img{i:02d}.png")
    result = runner.invoke(app, ["convert", str(tmp_path), "-t", "jpg"], env={"COLUMNS": "400"})
    assert result.exit_code == 0, result.output
    assert "완료 25개, 실패 0개" in result.output
    assert len(list(tmp_path.glob("*.jpg"))) == 25
    assert result.output.count("img00.png →") <= 2  # 막대 25개가 아니라 전체 막대 하나
