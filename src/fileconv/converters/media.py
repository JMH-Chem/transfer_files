"""미디어 변환: 동영상에서 음성 추출, 음성 형식끼리 변환 (ffmpeg 사용)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..core.base import Converter, Job, Option
from ..core.errors import ConvertError
from ..core.registry import register
from ..core.tools import find_ffmpeg, popen_kwargs

VIDEO_EXTS = {"mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mpg", "mpeg", "ts", "3gp"}
AUDIO_EXTS = {"mp3", "wav", "aac", "m4a", "flac", "ogg", "opus", "wma", "aiff"}
AUDIO_OUT = {"mp3", "wav", "aac", "m4a", "flac", "ogg", "opus"}

# 출력 형식별 ffmpeg 오디오 코덱과 비트레이트 지정 가능 여부
CODECS: dict[str, tuple[str, bool]] = {
    "mp3": ("libmp3lame", True),
    "wav": ("pcm_s16le", False),
    "aac": ("aac", True),
    "m4a": ("aac", True),
    "flac": ("flac", False),
    "ogg": ("libvorbis", True),
    "opus": ("libopus", True),
}

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_AUDIO_STREAM_RE = re.compile(r"Stream #\d+:\d+.*?: Audio:")


def probe(ffmpeg: str, path: Path) -> tuple[float | None, bool]:
    """(재생 시간 초, 오디오 트랙 존재 여부). ffprobe 없이 'ffmpeg -i' 출력에서 읽는다."""
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **popen_kwargs(),
    )
    info = proc.stderr
    m = _DURATION_RE.search(info)
    duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None
    return duration, bool(_AUDIO_STREAM_RE.search(info))


def run_ffmpeg(args: list[str], job: Job, duration: float | None) -> None:
    """ffmpeg 실행 + 진행률 표시 + 취소 처리."""
    ffmpeg = find_ffmpeg()
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-progress", "pipe:1", "-nostats", *args]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        **popen_kwargs(),
    )
    errors: list[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            key, sep, value = line.partition("=")
            if sep and key in ("out_time_us", "out_time_ms"):
                if duration and value.isdigit():
                    job.progress(int(value) / 1_000_000 / duration)
                else:
                    job.cancel.check()
            elif sep and key in ("progress", "frame", "fps", "bitrate", "total_size", "speed",
                                 "out_time", "dup_frames", "drop_frames", "stream_0_0_q"):
                job.cancel.check()
            elif line:
                errors.append(line)
        proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    if proc.returncode != 0:
        detail = "\n".join(errors[-5:]) or f"종료 코드 {proc.returncode}"
        raise ConvertError(f"ffmpeg 변환 실패: {detail}")


@register
class ToAudio(Converter):
    name = "to-audio"
    title = "동영상/음성 → 음성"
    category = "media"
    description = "동영상에서 음성만 뽑아내거나, 음성 파일 형식을 바꿉니다 (구간 자르기 가능)."
    inputs = frozenset(VIDEO_EXTS | AUDIO_EXTS)
    outputs = frozenset(AUDIO_OUT)
    default_output = "mp3"
    options = (
        Option("bitrate", "choice", "192k", "비트레이트", "음질(mp3/aac/m4a/ogg/opus에만 적용). 클수록 고음질·큰 파일",
               choices=("64k", "96k", "128k", "160k", "192k", "256k", "320k")),
        Option("sample_rate", "choice", "원본", "샘플레이트", "Hz 단위. 보통은 원본 유지",
               choices=("원본", "22050", "44100", "48000")),
        Option("channels", "choice", "원본", "채널", "모노/스테레오", choices=("원본", "mono", "stereo")),
        Option("start", "time", None, "시작 시간", "이 시간부터 (예: 1:30, 90, 00:01:30)"),
        Option("end", "time", None, "끝 시간", "이 시간까지 (예: 3:00)"),
        Option("volume", "float", 1.0, "볼륨 배율", "1.0 = 그대로, 2.0 = 두 배", min=0.0, max=10.0),
    )

    def is_available(self) -> tuple[bool, str]:
        if find_ffmpeg():
            return True, ""
        return False, "ffmpeg 를 찾을 수 없습니다. 'pip install imageio-ffmpeg' 로 설치하세요."

    def convert(self, job: Job) -> list[Path]:
        src = job.inputs[0]
        codec, uses_bitrate = CODECS[job.target if job.target in CODECS else "mp3"]
        duration, has_audio = probe(find_ffmpeg(), src)
        if not has_audio:
            raise ConvertError(f"오디오 트랙이 없는 파일입니다: {src.name}")

        start = job.opt("start")
        end = job.opt("end")
        if start is not None and end is not None and end <= start:
            raise ConvertError("끝 시간은 시작 시간보다 뒤여야 합니다")
        if duration is not None and start is not None and start >= duration:
            raise ConvertError(f"시작 시간이 파일 길이({duration:.1f}초)보다 깁니다")

        args: list[str] = []
        if start is not None:
            args += ["-ss", f"{start:.3f}"]
        args += ["-i", str(src)]
        if end is not None:
            args += ["-t", f"{end - (start or 0):.3f}"]
        args += ["-map", "0:a:0", "-vn", "-sn", "-dn", "-map_metadata", "0", "-c:a", codec]
        if uses_bitrate:
            args += ["-b:a", job.opt("bitrate", "192k")]
        if job.opt("sample_rate", "원본") != "원본":
            args += ["-ar", job.opt("sample_rate")]
        elif job.target == "opus":
            args += ["-ar", "48000"]  # opus 는 48kHz 등 정해진 값만 허용
        channels = job.opt("channels", "원본")
        if channels != "원본":
            args += ["-ac", "1" if channels == "mono" else "2"]
        volume = job.opt("volume", 1.0)
        if abs(volume - 1.0) > 1e-9:
            args += ["-af", f"volume={volume}"]
        if job.target == "aac":
            args += ["-f", "adts"]
        elif job.target == "m4a":
            args += ["-f", "ipod"]
        args += ["-y", str(job.output)]

        effective = duration
        if duration is not None:
            effective = (min(end, duration) if end is not None else duration) - (start or 0)
        run_ffmpeg(args, job, effective)
        return [job.output]
