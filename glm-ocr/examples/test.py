from __future__ import annotations
import time
from pathlib import Path
from glmocr.api import GlmOcr

def main() -> int:
    # 直接指定你的檔案路徑
    input_file = Path(__file__).resolve().parent / "source" / "docparse.png"
    output_dir = Path(__file__).resolve().parent / "result"
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_file.exists():
        print(f"❌ 錯誤：找不到檔案 {input_file}")
        return 1

    print(f"🚀 開始處理單一圖片：{input_file.name}")
    print(f"📂 結果將存放在：{output_dir}")

    start_time = time.time()

    try:
        # 使用 GLM-OCR (請確保 config.yaml 已改為 glm-ocr:q8_0)
        with GlmOcr() as parser:
            print(f"\n=== 進行 OCR 辨識中，請稍候... ===")
            
            # 解析圖片
            result = parser.parse(str(input_file))
            
            # 儲存結果 (Markdown 與圖片)
            result.save(output_dir=output_dir)
            
            # 計算耗時
            total_elapsed = time.time() - start_time
            minutes = int(total_elapsed // 60)
            seconds = total_elapsed % 60

            print("\n" + "="*40)
            print(f"✅ 辨識完成！")
            print(f"⏱️ 總執行時間：{total_elapsed:.2f} 秒 ({minutes} 分 {seconds:.2f} 秒)")
            print(f"📄 請至 {output_dir}/{input_file.stem} 查看結果")
            print("="*40)
            
    except Exception as e:
        print(f"❌ 辨識失敗：{e}")
        return 1

    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
