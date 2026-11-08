import subprocess

import pytest
from PIL import Image

from fileconv.core import engine
from fileconv.core.tools import find_ffmpeg
from fileconv.converters.media import ffmpeg_info, probe

from helpers import convert


def test_video_convert_resolution_and_quality(video):
    [out] = convert([video], "webm", options={"resolution": "360p", "quality": "낮음(작은 파일)"})
    info = ffmpeg_info(out)
    assert "vp9" in info and "opus" in info
    [mp4] = convert([video], "mkv", options={"audio": "false", "start": "1", "end": "2"})
    duration, has_audio = probe(find_ffmpeg(), mp4)
    assert not has_audio and duration == pytest.approx(1.0, abs=0.3)


def test_video_to_gif_and_back(video):
    [gif] = convert([video], "gif", options={"width": "100", "fps": "5"})
    img = Image.open(gif)
    assert img.format == "GIF" and img.size[0] == 100 and img.n_frames >= 10
    [mp4] = convert([gif], "mp4")
    assert probe(find_ffmpeg(), mp4)[0] == pytest.approx(3.0, abs=0.5)


def test_subtitle_extract(video, tmp_path):
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:02,000\n안녕하세요\n\n", encoding="utf-8")
    mkv = tmp_path / "sub.mkv"
    subprocess.run([find_ffmpeg(), "-loglevel", "error", "-i", str(video), "-i", str(srt), "-map", "0", "-map", "1",
                    "-c", "copy", "-c:s", "srt", str(mkv)], check=True)
    [out] = convert([mkv], "srt")
    assert "안녕하세요" in out.read_text(encoding="utf-8")
    results = engine.run(engine.plan([video], "srt"))
    assert "자막 트랙이 없는" in results[0].error


def test_portrait_video_resolution_uses_short_side(tmp_path):
    portrait = tmp_path / "세로.mp4"
    subprocess.run([find_ffmpeg(), "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=360x640:rate=10",
                    "-t", "1", "-c:v", "mpeg4", str(portrait)], check=True)
    [out] = convert([portrait], "mp4", options={"resolution": "240p"})
    import re

    w, h = map(int, re.search(r", (\d+)x(\d+)", ffmpeg_info(out)).groups())
    assert (w, h) == (240, 426)  # 짧은 변(가로)이 240
