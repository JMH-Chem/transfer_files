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


def ffmpeg_info(path: Path) -> str:
    """'ffmpeg -i 파일' 이 알려주는 스트림 정보 (ffprobe 없이)."""
    proc = subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",  # Windows 기본 인코딩(cp949/cp1252)으로 읽으면 한글 파일 이름에서 깨짐
        errors="replace",
        **popen_kwargs(),
    )
    return proc.stderr or ""


def probe(ffmpeg: str, path: Path) -> tuple[float | None, bool]:
    """(재생 시간 초, 오디오 트랙 존재 여부)."""
    info = ffmpeg_info(path)
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
    description = "동영상에서 음성만 뽑아내거나, 음성 파일 형식을 바꿉니다. 구간 자르기, 여러 파일 이어 붙이기 가능."
    inputs = frozenset(VIDEO_EXTS | AUDIO_EXTS)
    outputs = frozenset(AUDIO_OUT)
    default_output = "mp3"
    priority = 10
    can_merge = True  # 여러 파일을 이어 붙여 음성 파일 하나로
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

    @staticmethod
    def codec_args(job: Job) -> list[str]:
        """출력 형식에 맞는 코덱·비트레이트·샘플레이트·채널 인자."""
        codec, uses_bitrate = CODECS[job.target if job.target in CODECS else "mp3"]
        args = ["-c:a", codec]
        if uses_bitrate:
            args += ["-b:a", job.opt("bitrate", "192k")]
        if job.opt("sample_rate", "원본") != "원본":
            args += ["-ar", job.opt("sample_rate")]
        elif job.target == "opus":
            args += ["-ar", "48000"]  # opus 는 48kHz 등 정해진 값만 허용
        channels = job.opt("channels", "원본")
        if channels != "원본":
            args += ["-ac", "1" if channels == "mono" else "2"]
        if job.target == "aac":
            args += ["-f", "adts"]
        elif job.target == "m4a":
            args += ["-f", "ipod"]
        return args

    def merge(self, job: Job) -> list[Path]:
        """여러 파일을 순서대로 이어 붙인다 (형식·샘플레이트가 달라도 됨)."""
        ffmpeg = find_ffmpeg()
        total = 0.0
        args: list[str] = []
        for path in job.inputs:
            duration, has_audio = probe(ffmpeg, path)
            if not has_audio:
                raise ConvertError(f"오디오 트랙이 없는 파일입니다: {path.name}")
            total += duration or 0
            args += ["-i", str(path)]
        rate = job.opt("sample_rate", "원본")
        rate = "48000" if rate == "원본" else rate
        layout = "mono" if job.opt("channels") == "mono" else "stereo"
        n = len(job.inputs)
        chain = "".join(f"[{i}:a:0]aresample={rate},aformat=sample_fmts=fltp:channel_layouts={layout}[a{i}];"
                        for i in range(n))
        chain += "".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1"
        start, end = job.opt("start"), job.opt("end")
        if start is not None or end is not None:
            if start is not None and end is not None and end <= start:
                raise ConvertError("끝 시간은 시작 시간보다 뒤여야 합니다")
            trim = ":".join(p for p in (f"start={start}" if start is not None else "",
                                        f"end={end}" if end is not None else "") if p)
            chain += f",atrim={trim},asetpts=PTS-STARTPTS"
            total = (min(end, total) if end is not None else total) - (start or 0)
        volume = job.opt("volume", 1.0)
        if abs(volume - 1.0) > 1e-9:
            chain += f",volume={volume}"
        args += ["-filter_complex", chain + "[out]", "-map", "[out]", *self.codec_args(job), "-y", str(job.output)]
        run_ffmpeg(args, job, total or None)
        return [job.output]

    def convert(self, job: Job) -> list[Path]:
        if len(job.inputs) > 1:
            return self.merge(job)
        src = job.inputs[0]
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
        args += ["-map", "0:a:0", "-vn", "-sn", "-dn", "-map_metadata", "0"]
        volume = job.opt("volume", 1.0)
        if abs(volume - 1.0) > 1e-9:
            args += ["-af", f"volume={volume}"]
        args += [*self.codec_args(job), "-y", str(job.output)]

        effective = duration
        if duration is not None:
            effective = (min(end, duration) if end is not None else duration) - (start or 0)
        run_ffmpeg(args, job, effective)
        return [job.output]


# ---------------------------------------------------------------- 동영상 → 동영상 / GIF / 자막

VIDEO_OUT = {"mp4", "mkv", "webm", "mov", "avi"}
QUALITY = {  # (x264 CRF, VP9 CRF, mpeg4 qscale)
    "높음": (20, 28, 3),
    "보통": (23, 33, 5),
    "낮음(작은 파일)": (28, 40, 9),
}
RESOLUTIONS = {"원본": None, "2160p": 2160, "1440p": 1440, "1080p": 1080, "720p": 720, "480p": 480, "360p": 360, "240p": 240}
TRIM_OPTS = (
    Option("start", "time", None, "시작 시간", "이 시간부터 (예: 1:30)"),
    Option("end", "time", None, "끝 시간", "이 시간까지 (예: 3:00)"),
)


def _trim_args(job: Job, src: Path, duration: float | None) -> tuple[list[str], list[str], float | None]:
    """(입력 앞 인자, 입력 뒤 인자, 실제 길이)."""
    start, end = job.opt("start"), job.opt("end")
    if start is not None and end is not None and end <= start:
        raise ConvertError("끝 시간은 시작 시간보다 뒤여야 합니다")
    if duration is not None and start is not None and start >= duration:
        raise ConvertError(f"시작 시간이 파일 길이({duration:.1f}초)보다 깁니다")
    before = ["-ss", f"{start:.3f}"] if start is not None else []
    after = ["-t", f"{end - (start or 0):.3f}"] if end is not None else []
    effective = None
    if duration is not None:
        effective = (min(end, duration) if end is not None else duration) - (start or 0)
    return before + ["-i", str(src)], after, effective


@register
class VideoConvert(Converter):
    name = "video-convert"
    title = "동영상 형식 변환·압축"
    category = "media"
    description = "동영상 형식을 바꾸거나 해상도·화질을 낮춰 용량을 줄입니다. GIF → MP4 도 가능."
    inputs = frozenset(VIDEO_EXTS | {"gif"})
    outputs = frozenset(VIDEO_OUT)
    default_output = "mp4"
    priority = 5
    options = (
        Option("quality", "choice", "보통", "화질", "낮을수록 파일이 작아집니다", choices=tuple(QUALITY)),
        Option("resolution", "choice", "원본", "최대 해상도", "원본보다 크게 만들지는 않습니다", choices=tuple(RESOLUTIONS)),
        Option("fps", "choice", "원본", "초당 프레임", choices=("원본", "60", "30", "24", "15")),
        Option("audio", "bool", True, "소리 포함", "끄면 소리 없는 동영상"),
        Option("preset", "choice", "medium", "인코딩 속도", "느릴수록 같은 화질에서 파일이 작음 (mp4/mkv/mov)",
               choices=("ultrafast", "veryfast", "fast", "medium", "slow")),
        *TRIM_OPTS,
    )

    def is_available(self) -> tuple[bool, str]:
        return ToAudio.is_available(self)  # type: ignore[arg-type]

    def convert(self, job: Job) -> list[Path]:
        src = job.inputs[0]
        duration, has_audio = probe(find_ffmpeg(), src)
        before, after, effective = _trim_args(job, src, duration)
        crf_x264, crf_vp9, q_mpeg4 = QUALITY[job.opt("quality", "보통")]
        filters: list[str] = []
        height = RESOLUTIONS[job.opt("resolution", "원본")]
        if height:  # '720p' = 짧은 변이 720 (세로 영상은 가로를 720 으로)
            filters.append(f"scale='if(gte(iw,ih),-2,min({height},iw))':'if(gte(iw,ih),min({height},ih),-2)':flags=lanczos")
        filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")  # 대부분의 코덱은 짝수 크기만 허용
        if job.opt("fps", "원본") != "원본":
            filters.append(f"fps={job.opt('fps')}")

        args = [*before, *after, "-map", "0:v:0", "-vf", ",".join(filters), "-pix_fmt", "yuv420p"]
        target = job.target
        if target == "webm":
            args += ["-c:v", "libvpx-vp9", "-crf", str(crf_vp9), "-b:v", "0", "-row-mt", "1"]
        elif target == "avi":
            args += ["-c:v", "mpeg4", "-q:v", str(q_mpeg4)]
        else:
            args += ["-c:v", "libx264", "-crf", str(crf_x264), "-preset", job.opt("preset", "medium")]
            if target in ("mp4", "mov"):
                args += ["-movflags", "+faststart"]
        if job.opt("audio", True) and has_audio:
            args += ["-map", "0:a:0"]
            if target == "webm":
                args += ["-c:a", "libopus", "-b:a", "128k"]
            elif target == "avi":
                args += ["-c:a", "libmp3lame", "-b:a", "192k"]
            else:
                args += ["-c:a", "aac", "-b:a", "160k"]
        else:
            args += ["-an"]
        args += ["-sn", "-y", str(job.output)]
        run_ffmpeg(args, job, effective)
        return [job.output]


@register
class VideoToGif(Converter):
    name = "video-to-gif"
    title = "동영상 → GIF"
    category = "media"
    description = "동영상의 일부를 움직이는 GIF 로 만듭니다 (색상 최적화 적용)."
    inputs = frozenset(VIDEO_EXTS)
    outputs = frozenset({"gif"})
    default_output = "gif"
    priority = 4
    options = (
        Option("width", "int", 480, "가로 크기(px)", "세로는 비율에 맞춰 자동", min=32, max=1920),
        Option("fps", "int", 10, "초당 프레임", "높을수록 부드럽지만 파일이 커짐", min=1, max=50),
        Option("loop", "bool", True, "무한 반복"),
        *TRIM_OPTS,
    )

    def is_available(self) -> tuple[bool, str]:
        return ToAudio.is_available(self)  # type: ignore[arg-type]

    def convert(self, job: Job) -> list[Path]:
        src = job.inputs[0]
        duration, _ = probe(find_ffmpeg(), src)
        before, after, effective = _trim_args(job, src, duration)
        if effective is not None and effective > 60 and job.opt("end") is None:
            raise ConvertError("GIF 는 길면 파일이 매우 커집니다. 60초 이하가 되도록 '끝 시간'을 지정하세요")
        vf = (f"fps={job.opt('fps', 10)},scale={job.opt('width', 480)}:-1:flags=lanczos,"
              "split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5")
        args = [*before, *after, "-vf", vf, "-loop", "0" if job.opt("loop", True) else "-1", "-y", str(job.output)]
        run_ffmpeg(args, job, effective)
        return [job.output]


@register
class SubtitleExtract(Converter):
    name = "subtitle-extract"
    title = "동영상 자막 추출"
    category = "media"
    description = "동영상(mkv/mp4 등)에 들어 있는 자막 트랙을 srt/ass/vtt 파일로 꺼냅니다 (그림 자막은 불가)."
    inputs = frozenset(VIDEO_EXTS)
    outputs = frozenset({"srt", "ass", "vtt"})
    default_output = "srt"
    options = (Option("track", "int", 1, "자막 트랙 번호", "자막이 여러 개면 몇 번째 것인지 (1부터)", min=1),)

    def is_available(self) -> tuple[bool, str]:
        return ToAudio.is_available(self)  # type: ignore[arg-type]

    def convert(self, job: Job) -> list[Path]:
        src = job.inputs[0]
        info = ffmpeg_info(src)
        count = len(re.findall(r"Stream #\d+:\d+.*?: Subtitle:", info))
        if count == 0:
            raise ConvertError(f"자막 트랙이 없는 동영상입니다: {src.name}")
        track = job.opt("track", 1)
        if track > count:
            raise ConvertError(f"자막 트랙은 {count}개뿐입니다")
        codec = {"srt": "srt", "ass": "ass", "vtt": "webvtt"}[job.target]
        run_ffmpeg(["-i", str(src), "-map", f"0:s:{track - 1}", "-c:s", codec, "-y", str(job.output)], job, None)
        return [job.output]
