"""Generate example outputs.

This script parses all files under examples/source/ and writes results into
examples/result/ (one folder per input file).

Supported input formats: PNG, JPG, JPEG, PDF, DOC, DOCX, PPTX, XLS, XLSX, ODS.
Word/PPT/Excel/OpenDocument (doc, docx, pptx, xls, xlsx, ods) require LibreOffice for conversion to PDF before OCR.
"""

from __future__ import annotations

from pathlib import Path

from glmocr.api import GlmOcr
import time  # 新增：匯入時間模組

def main() -> int:
    here = Path(__file__).resolve().parent
    source_dir = here / "source"
    output_dir = here / "result"
    output_dir.mkdir(parents=True, exist_ok=True)

    if not source_dir.exists():
        raise RuntimeError(f"Missing examples source dir: {source_dir}")

    inputs = sorted(
        [
            *source_dir.glob("*.png"),
            *source_dir.glob("*.jpg"),
            *source_dir.glob("*.jpeg"),
            *source_dir.glob("*.pdf"),
            *source_dir.glob("*.doc"),
            *source_dir.glob("*.docx"),
            *source_dir.glob("*.pptx"),
            *source_dir.glob("*.xls"),
            *source_dir.glob("*.xlsx"),
            *source_dir.glob("*.ods"),
        ]
    )
    if not inputs:
        raise RuntimeError(f"No input files found under: {source_dir}")

    print(f"Found {len(inputs)} inputs under {source_dir}")
    print(f"Writing results to {output_dir}")

    with GlmOcr(enable_document_pipeline=True) as parser:
        for p in inputs:
            print(f"\n=== Parsing: {p.name} ===")
            start_time = time.time()  # 新增：記錄開始時間
            try:
                result = parser.parse(str(p))
                result.save(output_dir=output_dir)
                end_time = time.time()  # 新增：記錄結束時間
                duration = end_time - start_time  # 新增：計算價差
                print(f"Success: {p.name} (耗時: {duration:.2f} 秒)") # 新增：印出時間
            except Exception as e:
                print(f"Failed: {p.name}: {e}")
                continue

    print("\nAll done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())