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
