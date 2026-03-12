"""Docling-based parser for the Vision + Parsing layers.

Vision Layer (for PDF/DOCX):
  - Docling's internal layout analysis identifies regions (text, table, figure, …)

Parsing Layer:
  - text / title / list / caption blocks  → content extracted directly by Docling
  - table blocks                           → content left empty; filled by VisualParser
  - figure / picture blocks               → image extracted by Docling (or cropped later)

Supported inputs: PDF, DOCX, DOC, PPTX, XLSX, XLS, ODS, ODT.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple, TYPE_CHECKING

from glmocr.parsing.block_schema import Block, BlockType

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Availability guard
# ---------------------------------------------------------------------------

try:
    from docling.document_converter import DocumentConverter
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        EasyOcrOptions,
    )
    from docling_core.types.doc import (
        DocItemLabel,
        TextItem,
        TableItem,
        PictureItem,
        SectionHeaderItem,
        ListItem,
    )

    _DOCLING_AVAILABLE = True
except ImportError:
    _DOCLING_AVAILABLE = False
    DocumentConverter = None  # type: ignore
    logger.warning(
        "docling is not installed. "
        "Install it with: pip install docling\n"
        "DocumentPipeline will fall back to the image-only path for all inputs."
    )

# ---------------------------------------------------------------------------
# Label → BlockType mapping
# ---------------------------------------------------------------------------

_LABEL_MAP: dict[str, BlockType] = {
    # DocItemLabel values (string form)
    "title": "title",
    "document_index": "title",
    "section_header": "section_header",
    "text": "text",
    "paragraph": "text",
    "footnote": "text",
    "caption": "caption",
    "list_item": "list_item",
    "table": "table",
    "picture": "figure",
    "figure": "figure",
    "formula": "formula",
    "code": "code",
    "page_header": "page_header",
    "page_footer": "page_footer",
    "reference": "text",
    "abstract": "text",
    "checkbox_selected": "text",
    "checkbox_unselected": "text",
    "form": "text",
    "key_value_region": "text",
}

_VISUAL_TYPES: frozenset[BlockType] = frozenset({"table", "figure"})


def _label_to_block_type(label: str) -> BlockType:
    return _LABEL_MAP.get(label.lower(), "text")


# ---------------------------------------------------------------------------
# DoclingParser
# ---------------------------------------------------------------------------


class DoclingParser:
    """Parse PDF/DOCX documents via Docling.

    Returns per-page Block lists.
    - Text/title/list/caption/formula blocks have ``content`` filled.
    - Table/figure blocks have ``content`` left empty and ``image`` set
      when Docling managed to extract it (figures from DOCX/PDF pipelines).
      VisualParser will OCR these blocks later.

    Page images are also returned so that VisualParser can crop regions that
    Docling could not extract as standalone images (e.g. PDF tables).
    """

    def __init__(
        self,
        use_ocr: bool = False,
        generate_page_images: bool = True,
        generate_picture_images: bool = True,
    ):
        """
        Args:
            use_ocr: Enable Docling's internal OCR (for scanned PDFs).
                     Keep False for native-text PDFs/DOCX to avoid re-OCRing.
            generate_page_images: Render full page images inside Docling.
                     These are used as fallback when a visual block has no
                     standalone image.
            generate_picture_images: Extract embedded figure images from Docling.
        """
        if not _DOCLING_AVAILABLE:
            raise ImportError(
                "docling is required. Install with: pip install docling"
            )
        self.use_ocr = use_ocr
        self.generate_page_images = generate_page_images
        self.generate_picture_images = generate_picture_images
        self._converter: Optional[DocumentConverter] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(
        self, source: str
    ) -> Tuple[List[List[Block]], List[Optional["PILImage"]]]:
        """Parse a document and return blocks + rendered page images.

        Args:
            source: Local path to a PDF, DOCX, PPTX, XLSX, ODS, … file.

        Returns:
            (blocks_per_page, page_images)
            - blocks_per_page: outer index = page (0-based),
                               inner list = Block objects in reading order.
            - page_images:     rendered PIL Image per page, or None if unavailable.
        """
        converter = self._get_converter()

        logger.info("[DoclingParser] Converting: %s", source)
        try:
            result = converter.convert(source)
        except Exception as exc:
            logger.error("[DoclingParser] Conversion failed for %s: %s", source, exc)
            raise

        doc = result.document

        # ---------------------------------------------------------------
        # Collect page dimensions (needed for bbox normalisation)
        # ---------------------------------------------------------------
        page_dims: dict[int, Tuple[float, float]] = {}
        for pno, page in doc.pages.items():
            if hasattr(page, "size") and page.size is not None:
                page_dims[pno] = (float(page.size.width), float(page.size.height))

        num_pages = max(page_dims.keys(), default=0)
        if not page_dims:
            logger.warning("[DoclingParser] No pages found in %s", source)
            return [], []

        # ---------------------------------------------------------------
        # Collect page images
        # ---------------------------------------------------------------
        page_images: List[Optional["PILImage"]] = [None] * num_pages

        # 1. Try Docling's own page images first
        for pno, page in doc.pages.items():
            idx = pno - 1  # 0-based
            if idx < 0 or idx >= num_pages:
                continue
            try:
                if (
                    hasattr(page, "image")
                    and page.image is not None
                    and hasattr(page.image, "pil_image")
                    and page.image.pil_image is not None
                ):
                    page_images[idx] = page.image.pil_image
            except Exception:
                pass

        # 2. Fall back to pypdfium2 for PDFs where Docling didn't render
        if any(img is None for img in page_images) and Path(source).suffix.lower() == ".pdf":
            page_images = self._render_pdf_pages(source, num_pages, page_images)

        # ---------------------------------------------------------------
        # Iterate document items → build blocks
        # ---------------------------------------------------------------
        blocks_per_page: List[List[Block]] = [[] for _ in range(num_pages)]
        index_per_page: List[int] = [0] * num_pages

        for item, level in doc.iterate_items():
            label_raw = getattr(item, "label", None)
            if label_raw is None:
                continue

            label_str = (
                label_raw.value
                if hasattr(label_raw, "value")
                else str(label_raw)
            )
            block_type: BlockType = _label_to_block_type(label_str)

            # Skip pure layout noise
            if block_type in ("page_header", "page_footer"):
                continue

            # Provenance (position / page info)
            prov_list = getattr(item, "prov", []) or []
            if not prov_list:
                continue
            prov = prov_list[0]

            page_no: int = getattr(prov, "page_no", 1)  # 1-indexed
            page_idx = page_no - 1  # 0-indexed

            if page_idx < 0 or page_idx >= num_pages:
                continue

            # Normalised bbox
            bbox = self._normalise_bbox(prov, page_dims.get(page_no))

            # -------------------------------------------------------
            # Content extraction
            # -------------------------------------------------------
            content = ""
            extracted_image: Optional["PILImage"] = None

            if block_type in _VISUAL_TYPES:
                # Visual blocks: content left empty for VisualParser.
                # Attempt to get the embedded image from Docling.
                if isinstance(item, PictureItem):
                    extracted_image = self._extract_picture_image(item)
                elif isinstance(item, TableItem):
                    # Tables: try Docling's markdown export as a fallback;
                    # VisualParser will override if GLM-OCR is configured.
                    try:
                        content = item.export_to_markdown(doc=doc) or ""
                    except Exception:
                        content = ""
            else:
                # Text-based blocks: use Docling's extracted text.
                if hasattr(item, "text") and item.text:
                    content = item.text
                elif hasattr(item, "export_to_markdown"):
                    try:
                        content = item.export_to_markdown(doc=doc) or ""
                    except Exception:
                        content = ""

            block = Block(
                page=page_idx,
                index=index_per_page[page_idx],
                type=block_type,
                content=content,
                bbox=bbox,
                image=extracted_image,
                metadata={
                    "docling_label": label_str,
                    "heading_level": level if block_type == "section_header" else None,
                },
            )
            blocks_per_page[page_idx].append(block)
            index_per_page[page_idx] += 1

        logger.info(
            "[DoclingParser] Parsed %d pages, %d total blocks",
            num_pages,
            sum(len(p) for p in blocks_per_page),
        )
        return blocks_per_page, page_images

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_converter(self) -> "DocumentConverter":
        if self._converter is None:
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = self.use_ocr
            pipeline_options.generate_page_images = self.generate_page_images
            pipeline_options.generate_picture_images = self.generate_picture_images

            from docling.document_converter import DocumentConverter as _DC

            self._converter = _DC()
        return self._converter

    @staticmethod
    def _normalise_bbox(
        prov, page_size: Optional[Tuple[float, float]]
    ) -> Optional[Tuple[float, float, float, float]]:
        """Convert Docling provenance bbox → normalised (x0,y0,x1,y1) in 0-1000."""
        if page_size is None:
            return None
        raw_bbox = getattr(prov, "bbox", None)
        if raw_bbox is None:
            return None

        pw, ph = page_size
        if pw <= 0 or ph <= 0:
            return None

        try:
            # to_top_left_origin handles PDF bottom-left ↔ top-left conversion
            if hasattr(raw_bbox, "to_top_left_origin"):
                tl = raw_bbox.to_top_left_origin(ph)
            else:
                tl = raw_bbox

            x0 = float(tl.l) / pw * 1000.0
            y0 = float(tl.t) / ph * 1000.0
            x1 = float(tl.r) / pw * 1000.0
            y1 = float(tl.b) / ph * 1000.0

            # Clamp + ensure ordering
            x0, x1 = sorted([max(0.0, min(1000.0, x0)), max(0.0, min(1000.0, x1))])
            y0, y1 = sorted([max(0.0, min(1000.0, y0)), max(0.0, min(1000.0, y1))])

            if x0 >= x1 or y0 >= y1:
                return None
            return (x0, y0, x1, y1)
        except Exception:
            return None

    @staticmethod
    def _extract_picture_image(item: "PictureItem") -> Optional["PILImage"]:
        """Try to get the PIL image stored inside a PictureItem."""
        try:
            if hasattr(item, "image") and item.image is not None:
                if hasattr(item.image, "pil_image"):
                    return item.image.pil_image
                if hasattr(item.image, "as_pil"):
                    return item.image.as_pil()
        except Exception:
            pass
        return None

    @staticmethod
    def _render_pdf_pages(
        source: str,
        num_pages: int,
        existing: List[Optional["PILImage"]],
    ) -> List[Optional["PILImage"]]:
        """Render PDF pages with pypdfium2, filling gaps in *existing*."""
        try:
            from glmocr.utils.image_utils import pdf_to_images_pil

            rendered = pdf_to_images_pil(source)
            result = list(existing)
            for i, img in enumerate(rendered):
                if i < num_pages and result[i] is None:
                    result[i] = img
            return result
        except Exception as exc:
            logger.warning(
                "[DoclingParser] pypdfium2 render failed for %s: %s", source, exc
            )
            return existing
