import subprocess

import pytest

from fileconv.core import engine
from fileconv.core.tools import find_ffmpeg
from fileconv.converters.media import ffmpeg_info, probe

from helpers import convert


def duration_of(path):
    d, _ = probe(find_ffmpeg(), path)
    return d


@pytest.mark.parametrize("fmt", ["mp3", "wav", "flac", "m4a", "aac", "ogg", "opus"])
def test_video_to_audio_formats(video, fmt):
    [out] = convert([video], fmt)
    assert out.suffix == f".{fmt}" and out.stat().st_size > 0
    assert duration_of(out) == pytest.approx(3.0, abs=0.3)


def test_trim_and_options(video):
    [out] = convert([video], "mp3", options={"start": "0:01", "end": "2.5", "channels": "mono", "bitrate": "128k"})
    assert duration_of(out) == pytest.approx(1.5, abs=0.2)
    info = ffmpeg_info(out)
    assert "mono" in info


def test_audio_to_audio(video):
    [wav] = convert([video], "wav")
    [flac] = convert([wav], "flac")
    assert duration_of(flac) == pytest.approx(3.0, abs=0.3)


def test_no_audio_track(silent_video):
    results = engine.run(engine.plan([silent_video], "mp3"))
    assert not results[0].ok and "오디오 트랙이 없는" in results[0].error


def test_progress_reported(video):
    seen = []
    engine.run(engine.plan([video], "mp3"), on_progress=lambda i, f: seen.append(f))
    assert seen and seen[-1] == 1.0


def test_cancel(video):
    from fileconv.core.base import CancelToken

    token = CancelToken()
    token.cancel()
    results = engine.run(engine.plan([video], "mp3"), cancel=token)
    assert results[0].cancelled
    assert not list(video.parent.glob("*.mp3"))


def test_merge_audio_files(video, tmp_path):
    [wav] = convert([video], "wav", options={"channels": "mono", "sample_rate": "22050"})
    [mp3] = convert([video], "mp3")
    [out] = convert([wav, mp3], "m4a", merge=True, output=tmp_path / "이어붙이기.m4a")
    assert duration_of(out) == pytest.approx(6.0, abs=0.4)
    [trimmed] = convert([wav, mp3], "mp3", merge=True, options={"start": "2", "end": "5"})
    assert duration_of(trimmed) == pytest.approx(3.0, abs=0.3)
