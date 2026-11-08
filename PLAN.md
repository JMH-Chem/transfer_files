# 만능 파일 변환기 (fileconv) 설계안

## 진행 상황 (2026-11-09 새벽 기준)

**완료**
- 변환 엔진(core), 명령줄 `fconv`, 창 프로그램(GUI), 변환기 20종 — 사용법은 README.md
- 1단계 기능 4종 전부 + 2단계 일부 (동영상 변환/압축, GIF, 자막 추출, 텍스트·Markdown→PDF, PDF 용량 줄이기·암호,
  음성 이어 붙이기, 이미지 회전·흑백, 한글 HWP/HWPX→텍스트, 한컴오피스가 있으면 HWP→PDF)
- 테스트 약 90개 (GitHub Actions 에서 Windows·Linux 자동 실행), 별도 코드 검토로 찾은 버그 12건 수정, Windows .exe 자동 빌드(Actions 의 FileConverter-windows)
- 탐색기 "보내기" 메뉴 등록 `fconv sendto`

**사용자 PC(Windows)에서 직접 확인이 필요한 것** — 이 환경에는 없어서 시험하지 못함
- MS Word/엑셀/PPT → PDF (MS Office 자동화), 한컴오피스 → PDF, `fconv sendto`, 만든 .exe 를 더블클릭해서 실행

**정해 주셔야 하는 것 (다음 단계 후보)**
- 문자 인식(OCR): 스캔 PDF/사진 → 글자. Tesseract 설치(약 100MB) 또는 온라인 서비스 사용 여부
- 음성 → 텍스트 받아쓰기(Whisper): 모델 다운로드 수백 MB~수 GB, PC 성능 필요
- PDF → Word(docx): 쓸 만한 라이브러리(pdf2docx 등)가 AGPL/GPL 라이선스 — 개인용이면 문제없음
- 실행 파일 배포 방식: 폴더째 zip(현재) / 설치 프로그램(Inno Setup) / 파일 하나짜리 exe(실행이 느림)

---

## Context
`transfer_files` 저장소에 다양한 파일 변환 기능을 **하나의 프로그램**으로 모으려 한다.
- 사용 형태: **CLI + 데스크톱 GUI** (같은 변환 엔진을 공유)
- 주 사용 OS: **Windows** (나중에 .exe 배포까지 고려)
- 1단계 기능: 비디오→음성, 이미지→PDF, 텍스트→엑셀, 문서 변환(PDF/Word 등)

핵심 목표: 변환 기능을 "플러그인"처럼 계속 추가할 수 있는 구조. 새 변환기는 파일 하나만 추가하면 CLI와 GUI에 자동으로 나타나야 한다.

---

## 1. 기술 선택

| 영역 | 선택 | 이유 |
|---|---|---|
| 언어 | Python 3.11+ | 변환 라이브러리 생태계가 가장 풍부 |
| CLI | Typer + Rich | 명령어/도움말 자동 생성, 진행률 표시 |
| GUI | PySide6 (Qt) | Windows 드래그앤드롭·진행률·백그라운드 스레드가 안정적 |
| 비디오/오디오 | FFmpeg (`imageio-ffmpeg`) | pip 설치만으로 ffmpeg.exe가 함께 깔림 → 사용자가 따로 설치할 필요 없음 |
| 이미지/이미지→PDF | Pillow, img2pdf, (선택) pillow-heif | img2pdf는 JPG를 재압축 없이 PDF에 넣음(화질 손실 없음). 아이폰 HEIC 지원 |
| 텍스트→엑셀 | pandas, openpyxl, charset-normalizer | **한글 CSV(cp949/euc-kr) 인코딩 자동 감지**가 중요 |
| PDF 처리 | pypdf (합치기/분할/회전/텍스트), pypdfium2 (PDF→이미지) | 둘 다 순수 pip 설치, Poppler 등 외부 설치 불필요, 라이선스 자유로움 |
| Word→PDF | docx2pdf (MS Word 이용) → 실패 시 LibreOffice `soffice --headless` | Windows에선 Word가 있으면 가장 깔끔한 결과 |
| Markdown→HTML | markdown | |
| 배포 | PyInstaller (onedir) | `FileConverter.exe` 생성, ffmpeg 포함 |
| 테스트 | pytest | |

---

## 2. 프로젝트 구조

```
transfer_files/
├── pyproject.toml            # 패키지/의존성, 명령어 `fconv` 등록
├── README.md                 # 설치·사용법·지원 변환 목록
├── src/fileconv/
│   ├── __main__.py           # python -m fileconv
│   ├── core/
│   │   ├── base.py           # Converter 추상 클래스, Option 선언, ConversionResult
│   │   ├── registry.py       # @register 데코레이터, (입력확장자→출력확장자)로 변환기 찾기
│   │   ├── engine.py         # 배치 실행, 진행률 콜백, 출력 경로 결정, 오류 수집
│   │   ├── tools.py          # 외부 도구 탐색 (ffmpeg, Word, LibreOffice)
│   │   └── utils.py          # 인코딩 감지, 파일명 충돌 처리, Windows 와일드카드 확장
│   ├── converters/
│   │   ├── media.py          # 비디오→오디오, 오디오↔오디오
│   │   ├── image.py          # 이미지→PDF, 이미지↔이미지
│   │   ├── table.py          # txt/csv/tsv/json → xlsx, xlsx → csv
│   │   └── document.py       # PDF 합치기/분할/→이미지/→텍스트, docx→pdf, md→html
│   ├── cli/main.py           # Typer 앱
│   └── gui/
│       ├── app.py            # GUI 진입점
│       ├── main_window.py    # 카테고리 탭 + 드래그앤드롭 파일 목록
│       ├── options_form.py   # 변환기 Option 선언 → 입력 폼 자동 생성
│       └── worker.py         # QThread 백그라운드 변환 (UI 멈춤 방지, 취소 지원)
├── tests/
│   ├── conftest.py           # 샘플 파일을 코드로 생성하는 fixture (동영상/이미지/한글 CSV/PDF)
│   └── test_*.py
└── scripts/build_exe.ps1     # Windows용 exe 빌드 스크립트
```

---

## 3. 핵심 설계: 변환기 플러그인 구조

모든 변환기는 같은 틀을 따른다:

```python
@register
class VideoToAudio(Converter):
    name = "video-to-audio"
    category = "media"                         # GUI 탭 분류
    inputs  = {"mp4", "mkv", "avi", "mov", "webm", "flv"}
    outputs = {"mp3", "wav", "aac", "m4a", "flac", "ogg", "opus"}
    multi_input = False                        # True면 여러 파일 → 하나 (이미지→PDF, PDF 합치기)
    options = [
        Option("bitrate", "choice", ["128k", "192k", "320k"], default="192k", label="비트레이트"),
        Option("start", "time", label="시작 시간"), Option("end", "time", label="끝 시간"),
    ]
    def is_available(self) -> tuple[bool, str]: ...   # 필요한 도구/라이브러리 있는지
    def convert(self, inputs, output, opts, progress) -> list[Path]: ...
```

- **`options` 선언 하나로 CLI 옵션(`--opt bitrate=320k`)과 GUI 입력 폼이 동시에 자동 생성** → 변환기 추가 시 UI 코드 수정 불필요
- `is_available()`: Word가 없거나 선택 라이브러리가 없으면 "사용 불가: ○○ 설치 필요"로 표시 (프로그램이 죽지 않음)
- `progress(0.0~1.0)` 콜백: CLI는 Rich 진행바, GUI는 QProgressBar로 표시
- 엔진 공통 처리: 출력 경로 기본값 = 원본과 같은 폴더, 이름 충돌 시 `파일 (1).mp3`, 여러 파일 일괄 처리, 실패한 파일만 모아서 보고

---

## 4. 1단계 변환 기능 상세

**① 미디어 (media.py)** — ffmpeg 호출
- 비디오(mp4/mkv/avi/mov/webm/flv) → 오디오(mp3/wav/aac/m4a/flac/ogg/opus)
- 오디오 ↔ 오디오 포맷 변환
- 옵션: 비트레이트, 샘플레이트, 모노/스테레오, 구간 자르기(시작~끝)
- 진행률: ffmpeg `-progress pipe:1` 출력 파싱

**② 이미지 (image.py)**
- 여러 이미지(jpg/png/webp/bmp/tiff/heic) → **PDF 한 개** (순서 지정 가능)
- 옵션: 페이지 크기(A4/원본 크기), 여백, 가로/세로 자동 맞춤
- PNG 투명 배경은 흰색으로 평탄화 후 삽입
- 이미지 ↔ 이미지 포맷 변환 (품질 옵션)

**③ 표 (table.py)**
- txt/csv/tsv/json → xlsx
- 구분자 자동 감지(쉼표/탭/세미콜론/파이프), **한글 인코딩 자동 감지**(utf-8/cp949)
- 옵션: 첫 줄 헤더 여부, 시트 이름, 열 너비 자동 맞춤
- 역방향: xlsx → csv (엑셀에서 한글 안 깨지도록 `utf-8-sig`로 저장)

**④ 문서 (document.py)**
- PDF 합치기 / 분할(페이지 범위) / 회전
- PDF → 이미지(png/jpg, DPI 옵션), PDF → 텍스트(txt)
- docx → pdf (Word → LibreOffice 순으로 시도)
- md → html

---

## 5. 사용 방법 (완성 모습)

**CLI**
```
fconv list                                   # 지원 변환 목록 표
fconv convert 강의.mp4 -t mp3 --opt bitrate=320k
fconv convert *.jpg -t pdf --merge -o 앨범.pdf  # Windows cmd에서도 *.jpg 직접 확장 처리
fconv convert 데이터.csv -t xlsx
fconv convert a.pdf b.pdf -t pdf --merge -o 합본.pdf
fconv gui                                    # GUI 실행
```

**GUI 화면 구성**
- 상단 탭: 미디어 / 이미지 / 표 / 문서
- 가운데: 파일을 끌어다 놓는 목록 (드래그로 순서 변경 — 이미지→PDF에 중요)
- 오른쪽: 출력 포맷 선택, 옵션 폼(자동 생성), 저장 폴더
- 하단: 진행률 바, 로그, [변환 시작] [취소]

---

## 6. 개발 단계 (구현 순서)

1. **뼈대**: `pyproject.toml`, `core/` (base·registry·engine·utils), `fconv list`
2. **변환기 4종**: media → image → table → document 순서로 구현, 각각 테스트 함께 작성
3. **CLI 완성**: `convert` 명령, 배치/병합/옵션/진행률
4. **GUI**: 메인 창 → 드래그앤드롭 → 옵션 폼 자동 생성 → 백그라운드 워커/취소
5. **배포**: PyInstaller 빌드 스크립트, README 정리
6. 각 단계마다 `claude/busy-turing-8vwryd` 브랜치에 커밋·푸시

## 7. 이후 확장 아이디어 (2단계 이후)
- 동영상 → GIF, 동영상 압축/해상도 변경, 자막(srt) 추출
- 이미지 일괄 리사이즈/압축/워터마크
- PDF → Word(pdf2docx), 엑셀 → PDF
- OCR: 스캔 PDF/이미지 → 텍스트 (Tesseract)
- **HWP/HWPX → 텍스트/PDF** (한국 사용자 수요 큼)
- 음성 → 텍스트 받아쓰기 (Whisper)
- Windows 우클릭 메뉴 / "보내기" 메뉴 연동, 폴더 감시 자동 변환

---

## 8. 검증 방법
- `pytest`: fixture가 테스트 파일을 직접 생성 (ffmpeg `lavfi`로 2초짜리 테스트 동영상, Pillow로 이미지, cp949 한글 CSV, 이미지로 만든 PDF) → 변환 후 결과 확인
  - 오디오: 출력 파일 길이/코덱 확인
  - PDF: 페이지 수 확인 (pypdf)
  - 엑셀: 셀 값·한글 정상 여부 확인 (openpyxl)
- CLI 수동 확인: `fconv list`, 각 카테고리별 `fconv convert` 실행
- GUI: `QT_QPA_PLATFORM=offscreen`으로 창 생성·변환 실행 스모크 테스트
- 참고: 개발 환경(클라우드)은 Linux이므로 코드는 크로스플랫폼으로 작성하고, Word 기반 docx→pdf·exe 빌드는 사용자의 Windows PC에서 최종 확인 필요 (클라우드에서는 LibreOffice 경로로 테스트하거나 skip)
