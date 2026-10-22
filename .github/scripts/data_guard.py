"""실제 견본 데이터가 레포에 들어오는 것을 막는다.

규칙 (ktl-hub/docs/05-협업규칙.md §6):
- 데이터 파일(csv, xlsx, 사진 …)은 synthetic/ 아래에만
- 진짜처럼 보이는 접수번호(NN-NNNNNN-NN) 금지. 가짜는 가운데 6자리가 123456 이거나 000으로 시작
- 5 MB 넘는 파일 금지
"""
import os
import re
import subprocess
import sys
from pathlib import PurePosixPath

DATA_EXT = {".csv", ".tsv", ".xlsx", ".xlsm", ".xls", ".hwp", ".hwpx", ".heic", ".db", ".sqlite", ".sqlite3", ".zip", ".7z"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
IMAGE_OK_DIRS = ("docs/images/", "assets/", "public/", "src/assets/", "frontend/public/", "frontend/src/assets/")
TEXT_EXT = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".ts", ".tsx", ".js", ".jsx", ".html", ".css", ".toml", ".cfg", ".ini", ".sql", ".sh"}
RECEIPT = re.compile(r"(?<![\d-])(\d{2})-(\d{6})-(\d{2})(?:-\d+)?(?![\d])")
MAX_BYTES = 5 * 1024 * 1024


def in_synthetic(path: str) -> bool:
    return "synthetic/" in path or path.startswith("tests/fixtures/synthetic")


def fake_receipt(mid: str) -> bool:
    return mid == "123456" or mid.startswith("000")


def main() -> int:
    files = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout.decode().split("\0")
    problems = []
    for f in filter(None, files):
        p = PurePosixPath(f)
        ext = p.suffix.lower()
        if f.startswith(".github/"):
            continue
        if not os.path.isfile(f):
            continue
        size = os.path.getsize(f)
        if size > MAX_BYTES:
            problems.append(f"{f}: 5 MB 초과 ({size // 1024} KB)")
        if ext in DATA_EXT and not in_synthetic(f):
            problems.append(f"{f}: 데이터 파일은 synthetic/ 아래에만 둘 수 있습니다")
        if ext in IMAGE_EXT and not in_synthetic(f) and not f.startswith(IMAGE_OK_DIRS):
            problems.append(f"{f}: 사진은 synthetic/ 또는 docs/images/ · assets/ 아래에만")
        if ext == ".txt" and not in_synthetic(f) and not re.match(r"(.*/)?(requirements[\w.-]*|LICENSE|NOTICE)\.txt$", f):
            problems.append(f"{f}: .txt 데이터 파일은 synthetic/ 아래에만")
        if ext == ".pdf" and not f.startswith("docs/"):
            problems.append(f"{f}: PDF는 docs/ 아래에만")
        if ext in TEXT_EXT or (ext in DATA_EXT and in_synthetic(f)):
            try:
                text = open(f, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for m in RECEIPT.finditer(text):
                if not fake_receipt(m.group(2)):
                    line = text.count("\n", 0, m.start()) + 1
                    problems.append(f"{f}:{line}: 실제처럼 보이는 접수번호 {m.group(0)} — 가짜는 NN-123456-NN 또는 NN-000xxx-NN")
    if problems:
        print("data-guard: 실제 데이터로 보이는 파일이 있습니다\n")
        for p in problems:
            print(" -", p)
        print("\n잘못 올렸다면 바로 PM(@didgmltmd)에게 알려 주세요.")
        return 1
    print(f"data-guard: 통과 ({len([f for f in files if f])}개 파일)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
