import os
import sys
import base64
import tempfile
from pathlib import Path
from typing import Optional
from collections import defaultdict

# 由於我們即將使用 docling, 請確保安裝了 docling: pip install docling
from docling.document_converter import DocumentConverter
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
from docling.datamodel.document import DocItemLabel
from docling.document_converter import PdfFormatOption

# 引入我們先前建好的 Adapter
from src.model_clients.ollama_client import OllamaClient
from src.model_clients.base import MultiModalClient

# 簡體轉繁體（使用 OpenCC，如未安裝則略過）
try:
    from opencc import OpenCC
    HAS_OPENCC = True
except Exception:
    OpenCC = None  # type: ignore
    HAS_OPENCC = False

# 將 glm-ocr 加入環境路徑以便引用 PPDocLayoutDetector
glm_ocr_path = Path(__file__).resolve().parent.parent.parent / "glm-ocr"
if str(glm_ocr_path) not in sys.path:
    sys.path.append(str(glm_ocr_path))

try:
    from glmocr.config import load_config
    from glmocr.layout.layout_detector import PPDocLayoutDetector
    HAS_PP_STRUCTURE = True
except Exception as e:
    HAS_PP_STRUCTURE = False
    print(f"找不到 glm-ocr 或套件未安裝，將退回純 Docling 原生圖片裁切。({e})")


# ─────────────────────────────────────────────
# Docling 的 bbox 是 bottom-left origin (y 向上增加)
# PP-Structure 的 bbox 是 top-left origin (y 向下增加)
# 需要轉換才能比較
# ─────────────────────────────────────────────

def docling_bbox_to_topleft_norm(bbox, page_width, page_height):
    """把 Docling bbox (bottom-left origin) 轉成 PP-Structure 格式 (top-left, 0~1000)"""
    x1 = (bbox.l / page_width) * 1000
    # 注意：Docling 的 t 是 top（距底部距離高），b 是 bottom
    # 在 bottom-left origin: t > b (t是上方)
    # 轉到 top-left: y1 = 1 - t/h, y2 = 1 - b/h
    y1 = (1.0 - bbox.t / page_height) * 1000   # 上方邊界
    x2 = (bbox.r / page_width) * 1000
    y2 = (1.0 - bbox.b / page_height) * 1000   # 下方邊界
    # 確保 y1 < y2
    if y1 > y2:
        y1, y2 = y2, y1
    return [x1, y1, x2, y2]


def bbox_iou(box1, box2):
    """IoU 計算 (normalized 0~1000 箱子)"""
    x1_inter = max(box1[0], box2[0])
    y1_inter = max(box1[1], box2[1])
    x2_inter = min(box1[2], box2[2])
    y2_inter = min(box1[3], box2[3])

    if x2_inter <= x1_inter or y2_inter <= y1_inter:
        return 0.0

    inter_area = (x2_inter - x1_inter) * (y2_inter - y1_inter)
    area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
    area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
    union_area = area1 + area2 - inter_area
    if union_area <= 0:
        return 0.0
    return inter_area / union_area


class DocumentProcessor:
    """
    三效合一混合式文件解析器 (Docling + PP-Structure + VLM)
    ─────────────────────────────────────────────────────
    • 文字 / 標題 / 列表   →  Docling 原生解析（速度快、品質高）
    • 圖片 (figure/image)  →  PP-Structure 精準定位 + PIL 裁切
    • 表格 (table)         →  PP-Structure 精準定位裁切後，交給 VLM 轉 Markdown
    ─────────────────────────────────────────────────────
    """

    def __init__(self, vlm_client: Optional[MultiModalClient] = None, output_dir: str = "./data/processed"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 預設使用 OllamaClient
        self.vlm_client = vlm_client or OllamaClient()

        # OpenCC 轉換器（簡體→繁體），若套件不存在則為 None
        self.cc = OpenCC("s2t") if HAS_OPENCC else None

        # 配置 Docling PdfPipelineOptions
        pipeline_options = PdfPipelineOptions()
        pipeline_options.generate_page_images = True    # 取得頁面完整 PIL 圖
        pipeline_options.generate_picture_images = True
        pipeline_options.generate_table_images = True
        pipeline_options.do_table_structure = True      # 啟用表格結構 fallback

        # 初始化 Converter（PDF + DOCX）
        self.converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                InputFormat.DOCX: None
            }
        )

        # 初始化 PP-Structure (PPDocLayoutDetector)
        self.layout_detector = None
        if HAS_PP_STRUCTURE:
            config_path = glm_ocr_path / "glmocr" / "config.yaml"
            if config_path.exists():
                try:
                    cfg = load_config(str(config_path))
                    self.layout_detector = PPDocLayoutDetector(cfg.pipeline.layout)
                    self.layout_detector.start()
                    print("成功啟動 PPDocLayoutDetector 作為版面裁切輔助。")
                except Exception as e:
                    print(f"PPDocLayoutDetector 啟動失敗: {e}")
            else:
                print(f"找不到設定檔 {config_path}，無法啟動 PP-Structure。")

    # ─────────────────────────────────────────────
    #  工具方法
    # ─────────────────────────────────────────────

    def _run_layout_detection(self, document) -> dict:
        """對每個有 page image 的頁面執行 PP-Structure 版面偵測，回傳 {page_no: [item, ...]}`"""
        if not self.layout_detector:
            return {}

        page_images = {}  # page_no -> PIL image
        sorted_pages = sorted(document.pages.keys())
        for p_no in sorted_pages:
            page_obj = document.pages[p_no]
            if hasattr(page_obj, "image") and page_obj.image and hasattr(page_obj.image, "pil_image"):
                page_images[p_no] = page_obj.image.pil_image

        if not page_images:
            return {}

        # 批次推理（依排好的頁碼順序）
        valid_nos = sorted(page_images.keys())
        valid_imgs = [page_images[n] for n in valid_nos]
        try:
            pp_results = self.layout_detector.process(valid_imgs)
            return {p_no: pp_results[i] for i, p_no in enumerate(valid_nos)}
        except Exception as e:
            print(f"PP-Structure 推理發生錯誤: {e}")
            return {}

    def _crop_from_pp(self, pil_img, pp_box_norm):
        """依據 PP-Structure 的 0~1000 正規化 bbox，從 pil_img 裁切出子圖。"""
        w, h = pil_img.size
        x1 = max(0, int(pp_box_norm[0] * w / 1000))
        y1 = max(0, int(pp_box_norm[1] * h / 1000))
        x2 = min(w, int(pp_box_norm[2] * w / 1000))
        y2 = min(h, int(pp_box_norm[3] * h / 1000))
        if x2 <= x1 or y2 <= y1:
            return None
        return pil_img.crop((x1, y1, x2, y2))

    def _find_matching_pp_box(self, docling_bbox, page_no, page_size, layout_results, target_labels=("image", "chart",  "figure")):
        """在 PP-Structure 結果中找到與 Docling bbox 最吻合的 box。
        
        Note: Docling bbox uses bottom-left origin; PP-Structure uses top-left.
        We convert Docling bbox before comparison.
        """
        if page_no not in layout_results:
            return None
        dw = float(page_size.width)
        dh = float(page_size.height)
        if dw <= 0 or dh <= 0:
            return None

        # 用 Y 翻轉把 Docling bbox 轉換到 top-left coordinates
        doc_norm = docling_bbox_to_topleft_norm(docling_bbox, dw, dh)

        best_iou = 0.0
        best_box = None
        for item in layout_results[page_no]:
            if item["label"] not in target_labels:
                continue
            iou = bbox_iou(doc_norm, item["bbox_2d"])
            if iou > best_iou:
                best_iou = iou
                best_box = item["bbox_2d"]

        if best_iou > 0.05 and best_box:
            return best_box
        return None

    # ─────────────────────────────────────────────
    #  主流程
    # ─────────────────────────────────────────────

    def process(self, file_path: str) -> str:
        """
        處理單一文件 (PDF, Word 等)，返回最終組裝好的 Markdown 內容。
        """
        input_path = Path(file_path)
        if not input_path.exists():
            raise FileNotFoundError(f"找不到檔案: {input_path}")

        # 建立本文件的輸出資料夾 (與檔名同名)
        doc_output_dir = self.output_dir / input_path.stem
        img_output_dir = doc_output_dir / "images"
        img_output_dir.mkdir(parents=True, exist_ok=True)

        print(f"開始解析文件: {input_path.name}")

        # ── Step 1: Docling 轉換 ──────────────────────────────────────
        conv_result = self.converter.convert(str(input_path))
        document = conv_result.document

        # Cache page PIL images 以便後續裁切
        page_pil = {}
        for p_no, page_obj in document.pages.items():
            if hasattr(page_obj, "image") and page_obj.image and hasattr(page_obj.image, "pil_image"):
                page_pil[p_no] = page_obj.image.pil_image

        # ── Step 2: PP-Structure 版面偵測 ────────────────────────────
        layout_results = self._run_layout_detection(document)
        if layout_results:
            total_figs = sum(
                sum(1 for it in items if it["label"] in ("image", "chart", "figure"))
                for items in layout_results.values()
            )
            total_tabs = sum(
                sum(1 for it in items if it["label"] == "table")
                for items in layout_results.values()
            )
            print(f"PP-Structure: 偵測到 {total_figs} 個圖片 / {total_tabs} 個表格區域")

        # ── Step 3: 收集 PP-Structure 偵測到的所有 image 區域 ───────
        # （直接使用，不依賴 Docling 的 PICTURE 標記）
        # 記錄每個 PP image region 已被採用過（避免重複）
        pp_image_used = defaultdict(set)   # page_no -> set of tuple(bbox)

        # 先建立以 page/position 排好的 Docling 項目清單
        # 再比對 PP image 區域，依頁碼順序插入
        final_md_lines = []
        table_count = 0
        figure_count = 0

        # 用 page -> sorted pp image boxes 方便逐頁插入尚未配對的 images
        pp_images_per_page = {}  # page_no -> [bbox_norm, ...]
        if layout_results:
            for p_no, items in layout_results.items():
                imgs = [it["bbox_2d"] for it in items if it["label"] in ("image", "chart", "figure", "seal")]
                if imgs:
                    pp_images_per_page[p_no] = imgs

        # ── Step 4: 遍歷 Docling Elements ───────────────────────────
        # 最小有效圖片尺寸：過濾掉印章/頁眉等太小的廢圖
        MIN_FIGURE_PX = 50

        def _try_emit_pp_image(page_no: int, pp_box) -> bool:
            """裁切並保存一張 PP 補充圖，成功回傳 True，失敗或過濾則 False。"""
            nonlocal figure_count
            if tuple(pp_box) in pp_image_used[page_no]:
                return False  # 已處理過
            pil_page = page_pil.get(page_no)
            if not pil_page:
                return False
            img = self._crop_from_pp(pil_page, pp_box)
            if img is None:
                pp_image_used[page_no].add(tuple(pp_box))
                return False
            # 方案 A：尺寸過濾（< 50px 視為廢圖，如小印章/頁眉線）
            if img.width < MIN_FIGURE_PX or img.height < MIN_FIGURE_PX:
                pp_image_used[page_no].add(tuple(pp_box))  # 標記已跳過
                return False
            figure_count += 1
            img_filename = f"figure_{figure_count}.png"
            img_path = img_output_dir / img_filename
            try:
                img.save(img_path)
                final_md_lines.append(f"![PICTURE](./images/{img_filename})\n")
                pp_image_used[page_no].add(tuple(pp_box))
                return True
            except Exception as e:
                print(f"補入圖片 figure_{figure_count} 失敗: {e}")
                return False

        def _emit_pp_images_before_y(page_no: int, element_y1_norm: float):
            """在同頁中，把 Y 位置在 element_y1_norm 之前的 PP 補充圖先插入。

            PP bbox_2d[1] = top edge (top-left origin, 0~1000)。
            越小表示越靠頁面上方，所以 pp_box[1] < element_y1_norm 代表圖在元素之上。
            """
            if page_no not in pp_images_per_page:
                return
            # 依 Y 軸從上到下排序，依序插入低於 element_y1_norm 的圖
            for pp_box in sorted(pp_images_per_page[page_no], key=lambda b: b[1]):
                if tuple(pp_box) in pp_image_used[page_no]:
                    continue
                if pp_box[1] < element_y1_norm:
                    _try_emit_pp_image(page_no, pp_box)
                else:
                    break  # 剩下的 Y 都更大，不需要繼續

        def _emit_all_remaining_pp_for_page(page_no: int):
            """把某頁所有尚未插入的 PP 補充圖全部輸出（頁末清空用）。"""
            if page_no not in pp_images_per_page:
                return
            for pp_box in sorted(pp_images_per_page[page_no], key=lambda b: b[1]):
                _try_emit_pp_image(page_no, pp_box)

        prev_page = None

        for item, level in document.iterate_items():

            # 取得該 item 所在頁碼
            item_page = item.prov[0].page_no if item.prov else None

            # ── 頁面轉換：把上一頁所有剩餘 PP 圖先全部補齊 ──────────
            if item_page is not None and item_page != prev_page:
                if prev_page is not None:
                    _emit_all_remaining_pp_for_page(prev_page)
                prev_page = item_page

            # ── Y 軸座標插入：把同頁中位置在此元素上方的 PP 圖先插入 ──
            # Docling bbox.t 是 bottom-left origin 的 top edge → 轉成 top-left Y
            if item_page is not None and item.prov:
                bbox = item.prov[0].bbox
                page = document.pages.get(item_page)
                if page and page.size.height > 0:
                    dh = float(page.size.height)
                    element_y1_norm = (1.0 - bbox.t / dh) * 1000
                    _emit_pp_images_before_y(item_page, element_y1_norm)

            # --- 文字／標題 ---
            if item.label in [DocItemLabel.TEXT, DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER]:
                text = item.text
                if item.label == DocItemLabel.TITLE:
                    final_md_lines.append(f"# {text}\n")
                elif item.label == DocItemLabel.SECTION_HEADER:
                    prefix = "#" * max(2, level) if level else "##"
                    final_md_lines.append(f"{prefix} {text}\n")
                else:
                    final_md_lines.append(f"{text}\n")

            # --- 圖片 (Docling PICTURE 標籤) ---
            elif item.label == DocItemLabel.PICTURE:
                figure_count += 1
                img_filename = f"figure_{figure_count}.png"
                img_path = img_output_dir / img_filename

                try:
                    image_pil = None
                    if item.prov:
                        page_no = item.prov[0].page_no
                        bbox = item.prov[0].bbox
                        page = document.pages.get(page_no)
                        if page and page_pil.get(page_no):
                            pp_box = self._find_matching_pp_box(
                                bbox, page_no, page.size, layout_results,
                                target_labels=("image", "chart", "figure", "seal")
                            )
                            if pp_box:
                                image_pil = self._crop_from_pp(page_pil[page_no], pp_box)
                                pp_image_used[page_no].add(tuple(pp_box))

                    if not image_pil:
                        image_pil = item.get_image(document)

                    if image_pil:
                        image_pil.save(img_path)
                        final_md_lines.append(f"![PICTURE](./images/{img_filename})\n")
                except Exception as e:
                    print(f"無法提取圖片 {figure_count}: {e}")

            # --- 表格 → VLM ---
            elif item.label == DocItemLabel.TABLE:
                table_count += 1
                try:
                    image_pil = None
                    if item.prov:
                        page_no = item.prov[0].page_no
                        bbox = item.prov[0].bbox
                        page = document.pages.get(page_no)
                        if page and page_pil.get(page_no):
                            pp_box = self._find_matching_pp_box(
                                bbox, page_no, page.size, layout_results,
                                target_labels=("table",)
                            )
                            if pp_box:
                                image_pil = self._crop_from_pp(page_pil[page_no], pp_box)

                    if not image_pil:
                        image_pil = item.get_image(document)

                    if image_pil:
                        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
                            tmp_path = tmp_file.name
                            image_pil.save(tmp_path)

                        print(f"發現表格 {table_count}，正在交由 VLM 進行解析...")
                        try:
                            vlm_md_table = self.vlm_client.extract_table(tmp_path)
                            final_md_lines.append(f"\n{vlm_md_table}\n")
                        except Exception as vlm_err:
                            print(f"VLM 失敗 ({vlm_err})，使用 Docling 原生表格。")
                            final_md_lines.append(f"\n{item.export_to_markdown(doc=document)}\n")
                        finally:
                            if os.path.exists(tmp_path):
                                os.unlink(tmp_path)
                    else:
                        print(f"無法取得表格 {table_count} 的截圖，使用 Docling 原生解析...")
                        final_md_lines.append(f"\n{item.export_to_markdown(doc=document)}\n")

                except Exception as e:
                    print(f"表格 {table_count} 處理失敗 ({e})，使用 Docling 原生表格解析。")
                    final_md_lines.append(f"\n{item.export_to_markdown(doc=document)}\n")

            # --- 條列項目 ---
            elif item.label == DocItemLabel.LIST_ITEM:
                final_md_lines.append(f"- {item.text}\n")

        # ── Step 5: 補入最後一頁 + 任何全空白頁的剩餘 PP 圖 ─────────
        if prev_page is not None:
            _emit_all_remaining_pp_for_page(prev_page)
        # 補入沒有任何 Docling 元素的頁面（極少見）
        for p_no in sorted(pp_images_per_page.keys()):
            _emit_all_remaining_pp_for_page(p_no)  # 對已處理頁無效（used 過濾）

        # ── Step 6: 組合並儲存 ──────────────────────────────────────
        final_markdown = "\n".join(final_md_lines)

        # 若有安裝 OpenCC，將整份結果由簡體轉為繁體
        if self.cc is not None:
            try:
                final_markdown = self.cc.convert(final_markdown)
            except Exception as e:
                print(f"OpenCC 轉換失敗，將輸出原始內容: {e}")
        md_output_path = doc_output_dir / f"{input_path.stem}.md"

        with open(md_output_path, "w", encoding="utf-8") as f:
            f.write(final_markdown)

        print("文件解析完成。")
        print(f"總計萃取了 {table_count} 個表格 (VLM) + {figure_count} 張圖片 (PP-Structure 裁切)")
        print(f"結果儲存至: {md_output_path}")

        return final_markdown

    def process_folder(self, input_dir: str, exts: tuple[str, ...] = (".pdf", ".docx")):
        """
        批次處理某個資料夾底下的所有文件。
        預設只處理 .pdf / .docx，會遞迴掃描子資料夾。
        """
        input_root = Path(input_dir)
        if not input_root.exists():
            raise FileNotFoundError(f"找不到資料夾: {input_root}")

        # 收集所有符合副檔名的檔案
        files = [
            p for p in input_root.rglob("*")
            if p.is_file() and p.suffix.lower() in exts
        ]
        if not files:
            print(f"在 {input_root} 底下找不到符合 {exts} 的檔案。")
            return

        print(f"開始批次處理資料夾: {input_root} ({len(files)} 個檔案)")
        for f in sorted(files):
            try:
                self.process(str(f))
            except Exception as e:
                print(f"檔案 {f} 解析失敗: {e}")


# 使用範例
if __name__ == "__main__":
    from dotenv import load_dotenv
    import argparse

    load_dotenv()

    parser = argparse.ArgumentParser(description="批次解析文件（Docling + glm-ocr + VLM）")
    parser.add_argument(
        "--input-dir",
        type=str,
        default="./data/input",
        help="要解析的文件資料夾（預設: ./data/input）",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./data/processed",
        help="輸出 Markdown / 圖片的資料夾（預設: ./data/processed）",
    )
    args = parser.parse_args()

    processor = DocumentProcessor(output_dir=args.output_dir)
    processor.process_folder(args.input_dir)
