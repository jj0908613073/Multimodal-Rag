"""Two-layer document parsing pipeline.

Architecture
============

Vision Layer
------------
  PDF / DOCX / PPTX / …
      → Docling internal layout analysis
      → text / table / figure / formula blocks + page images

  Image files (PNG / JPG / …)
      → PP-DocLayoutV3 (PP-Structure) layout detection
      → text / table / figure / … regions

Parsing Layer
-------------
  Text blocks    → Docling extracted text  (already done in Vision Layer)
  Table blocks   → GLM-OCR (VisualParser)
  Figure blocks  → GLM-OCR (VisualParser)
  Formula blocks → Docling extracted LaTeX (already done)

The two passes are orchestrated here in DocumentPipeline.process().

Usage
-----
    from glmocr.pipeline.document_pipeline import DocumentPipeline
    from glmocr.config import load_config

    cfg = load_config()
    with DocumentPipeline(cfg.pipeline) as pipeline:
        result = pipeline.process("report.pdf")
        result.save(output_dir="./output")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

from glmocr.parser_result import PipelineResult
from glmocr.parsing.block_formatter import blocks_to_markdown
from glmocr.parsing.block_schema import Block, BlockType

if TYPE_CHECKING:
    from glmocr.config import PipelineConfig
    from PIL.Image import Image as PILImage

logger = logging.getLogger(__name__)

# Extensions natively supported by Docling (no LibreOffice conversion needed).
# .doc / .xls / .ods / .odt must be converted to PDF first via LibreOffice.
_DOCLING_EXTENSIONS = frozenset(
    {".pdf", ".docx", ".pptx", ".xlsx"}
)

# PP-Structure label → BlockType
_PP_LABEL_MAP: dict[str, BlockType] = {
    "text": "text",
    "title": "title",
    "doc_title": "title",
    "paragraph_title": "section_header",
    "figure": "figure",
    "figure_caption": "caption",
    "table": "table",
    "table_caption": "caption",
    "formula": "formula",
    "caption": "caption",
    "header": "page_header",
    "footer": "page_footer",
    "reference": "text",
    "abstract": "text",
    "list": "list_item",
    "image": "figure",
    "seal": "figure",
    "chart": "figure",
}


class DocumentPipeline:
    """Two-layer document parsing pipeline.

    Supports both structured documents (PDF/DOCX via Docling) and
    plain images (PNG/JPG via PP-Structure + GLM-OCR).

    Args:
        config: PipelineConfig (from GlmOcrConfig.pipeline).
    """

    def __init__(self, config: "PipelineConfig"):
        self.config = config

        # ---------- Shared components (both paths) -------------------------
        from glmocr.ocr_client import OCRClient
        from glmocr.dataloader import PageLoader
        from glmocr.parsing.visual_parser import VisualParser

        self.ocr_client = OCRClient(config.ocr_api)
        self.page_loader = PageLoader(config.page_loader)
        self.visual_parser = VisualParser(
            ocr_client=self.ocr_client,
            page_loader=self.page_loader,
            max_workers=config.max_workers,
            # Skip Docling's table fallback markdown and always re-OCR with GLM-OCR
            # Set skip_if_content=True to keep Docling's output for DOCX tables
            skip_if_content=getattr(config, "docling_skip_table_ocr", True),
        )

        # ---------- Vision Layer: PP-Structure (for image inputs) ----------
        self._layout_detector = None
        if config.enable_layout:
            try:
                from glmocr.layout import PPDocLayoutDetector
                self._layout_detector = PPDocLayoutDetector(config.layout)
            except Exception as exc:
                logger.warning(
                    "[DocumentPipeline] Could not load PP-Structure: %s. "
                    "Image inputs will be processed without layout detection.",
                    exc,
                )

        # ---------- Vision Layer: Docling (for document inputs) -----------
        self._docling_parser = None  # lazy-loaded

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self.ocr_client.start()
        if self._layout_detector is not None:
            self._layout_detector.start()
        logger.info("[DocumentPipeline] Started")

    def stop(self) -> None:
        self.ocr_client.stop()
        if self._layout_detector is not None:
            self._layout_detector.stop()
        logger.info("[DocumentPipeline] Stopped")

    def __enter__(self) -> "DocumentPipeline":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(self, source: str) -> PipelineResult:
        """Parse a document or image file.

        Routes to:
        - :meth:`_process_document` for PDF / DOCX / … (Docling + GLM-OCR)
        - :meth:`_process_image`    for PNG / JPG / … (PP-Structure + GLM-OCR)

        Args:
            source: Absolute or relative path to the input file.

        Returns:
            :class:`~glmocr.parser_result.PipelineResult`
        """
        ext = Path(source).suffix.lower()
        if ext in _DOCLING_EXTENSIONS:
            return self._process_document(source)
        return self._process_image(source)

    # ------------------------------------------------------------------
    # Document path  (PDF / DOCX)
    # ------------------------------------------------------------------

    def _process_document(self, source: str) -> PipelineResult:
        """Vision Layer: Docling layout  →  Parsing Layer: text + GLM-OCR."""
        docling = self._get_docling_parser()

        # ── Vision Layer ─────────────────────────────────────────────
        logger.info("[DocumentPipeline] Vision Layer (Docling): %s", source)
        blocks_per_page, page_images = docling.parse(source)

        if not blocks_per_page:
            return PipelineResult(
                json_result=[],
                markdown_result="",
                original_images=[source],
            )

        # ── Parsing Layer ────────────────────────────────────────────
        logger.info("[DocumentPipeline] Parsing Layer (GLM-OCR visual blocks)")
        blocks_per_page = self.visual_parser.parse_document(page_images, blocks_per_page)

        return self._build_result(blocks_per_page, source)

    # ------------------------------------------------------------------
    # Image path  (PNG / JPG)
    # ------------------------------------------------------------------

    def _process_image(self, source: str) -> PipelineResult:
        """Vision Layer: PP-Structure  →  Parsing Layer: GLM-OCR for all regions."""
        from PIL import Image

        image = Image.open(source).convert("RGB")

        # ── Vision Layer ─────────────────────────────────────────────
        if self._layout_detector is not None:
            logger.info(
                "[DocumentPipeline] Vision Layer (PP-Structure): %s", source
            )
            layout_results = self._layout_detector.process([image])
            regions = layout_results[0]
        else:
            regions = []

        if not regions:
            # No layout detected → treat whole image as a single text block
            logger.info(
                "[DocumentPipeline] No regions detected; processing full image"
            )
            blocks = self._full_image_ocr(image)
        else:
            blocks = [
                Block(
                    page=0,
                    index=idx,
                    type=_PP_LABEL_MAP.get(r.get("label", "text"), "text"),
                    bbox=tuple(r["bbox_2d"]) if r.get("bbox_2d") else None,
                )
                for idx, r in enumerate(regions)
            ]

        # ── Parsing Layer ────────────────────────────────────────────
        blocks = self.visual_parser.parse_page(image, blocks)

        return self._build_result([blocks], source)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _full_image_ocr(self, image: "PILImage") -> List[Block]:
        """OCR the whole image as a single block (fallback)."""
        req = self.page_loader.build_request_from_image(image, "text")
        response, status = self.ocr_client.process(req)
        content = ""
        if status == 200:
            content = (
                response.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
        return [Block(page=0, index=0, type="text", content=content)]

    def _get_docling_parser(self):
        if self._docling_parser is None:
            from glmocr.parsing.docling_parser import DoclingParser, _DOCLING_AVAILABLE

            if not _DOCLING_AVAILABLE:
                raise ImportError(
                    "docling is required to parse PDF/DOCX documents.\n"
                    "Install with: pip install docling"
                )
            cfg = getattr(self.config, "docling", None)
            use_ocr = getattr(cfg, "use_ocr", False) if cfg else False
            self._docling_parser = DoclingParser(
                use_ocr=use_ocr,
                generate_page_images=True,
                generate_picture_images=True,
            )
        return self._docling_parser

    @staticmethod
    def _build_result(
        blocks_per_page: List[List[Block]],
        source: str,
    ) -> PipelineResult:
        """Convert blocks → PipelineResult (JSON + Markdown)."""
        json_result = [
            [b.to_dict() for b in page]
            for page in blocks_per_page
        ]
        markdown_result = blocks_to_markdown(blocks_per_page)

        return PipelineResult(
            json_result=json_result,
            markdown_result=markdown_result,
            original_images=[source],
        )
