"""GLM-OCR visual parser — Parsing Layer for tables and figures.

For each Block that needs visual parsing (type in {"table", "figure"}):
  1. If block.image is already set (Docling extracted it): use it directly.
  2. Otherwise: crop the region from the rendered page image using block.bbox.
  3. Send the image to GLM-OCR via OCRClient.
  4. Store the result in block.content.

All visual blocks on a single page are submitted in parallel.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, TYPE_CHECKING

from glmocr.parsing.block_schema import Block
from glmocr.utils.image_utils import crop_image_region

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage
    from glmocr.ocr_client import OCRClient
    from glmocr.dataloader.page_loader import PageLoader

logger = logging.getLogger(__name__)

# Default task-type sent to the GLM-OCR prompt builder per block type
_TASK_MAP = {
    "table": "table",
    "figure": "image",
}


class VisualParser:
    """Parse visual blocks (tables / figures) with GLM-OCR.

    Designed to be used as the second pass after DoclingParser or
    PP-Structure layout detection.
    """

    def __init__(
        self,
        ocr_client: "OCRClient",
        page_loader: "PageLoader",
        max_workers: int = 8,
        visual_types: tuple = ("table", "figure"),
        skip_if_content: bool = True,
    ):
        """
        Args:
            ocr_client:      Initialised OCRClient pointing to the GLM-OCR endpoint.
            page_loader:     PageLoader used to build per-region OCR requests.
            max_workers:     Maximum concurrent GLM-OCR requests per page.
            visual_types:    Block types routed to GLM-OCR.
            skip_if_content: If True, blocks that already have non-empty content
                             (e.g. Docling table markdown) are not re-OCR'd.
                             Set False to always run GLM-OCR on visual blocks.
        """
        self.ocr_client = ocr_client
        self.page_loader = page_loader
        self.max_workers = max_workers
        self.visual_types = set(visual_types)
        self.skip_if_content = skip_if_content

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_page(
        self,
        page_image: Optional["PILImage"],
        blocks: List[Block],
    ) -> List[Block]:
        """OCR all visual blocks on a single page.

        Args:
            page_image: Rendered page image used for cropping.
                        May be None — blocks with a pre-extracted image
                        (block.image) can still be processed.
            blocks:     All blocks for this page.

        Returns:
            The same list with block.content filled for visual blocks.
        """
        targets = self._collect_targets(page_image, blocks)
        if not targets:
            return blocks

        logger.debug(
            "[VisualParser] Submitting %d visual block(s) to GLM-OCR", len(targets)
        )

        workers = min(self.max_workers, len(targets))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_block = {
                executor.submit(self._ocr_one, img, block): block
                for img, block in targets
            }
            for future in as_completed(future_to_block):
                block = future_to_block[future]
                try:
                    content = future.result()
                    if content:
                        block.content = content
                except Exception as exc:
                    logger.warning(
                        "[VisualParser] GLM-OCR failed for block %d (page %d): %s",
                        block.index,
                        block.page,
                        exc,
                    )

        return blocks

    def parse_document(
        self,
        page_images: List[Optional["PILImage"]],
        blocks_per_page: List[List[Block]],
    ) -> List[List[Block]]:
        """OCR all visual blocks across every page.

        Args:
            page_images:     One PIL Image per page (None if unavailable).
            blocks_per_page: All blocks grouped by page.

        Returns:
            Updated blocks_per_page.
        """
        for page_idx, (page_image, blocks) in enumerate(
            zip(page_images, blocks_per_page)
        ):
            visual = [b for b in blocks if b.type in self.visual_types]
            if not visual:
                continue

            if page_image is None and not any(b.image for b in visual):
                logger.warning(
                    "[VisualParser] Page %d has no image and no pre-extracted "
                    "images — skipping visual parsing",
                    page_idx,
                )
                continue

            blocks_per_page[page_idx] = self.parse_page(page_image, blocks)

        return blocks_per_page

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _collect_targets(
        self,
        page_image: Optional["PILImage"],
        blocks: List[Block],
    ) -> List[tuple["PILImage", Block]]:
        """Return (image, block) pairs ready for OCR."""
        targets = []
        for block in blocks:
            if block.type not in self.visual_types:
                continue
            if self.skip_if_content and block.content:
                continue

            img = self._get_image_for_block(block, page_image)
            if img is not None:
                targets.append((img, block))
            else:
                logger.debug(
                    "[VisualParser] Cannot obtain image for block %d (page %d) — skipped",
                    block.index,
                    block.page,
                )
        return targets

    @staticmethod
    def _get_image_for_block(
        block: Block,
        page_image: Optional["PILImage"],
    ) -> Optional["PILImage"]:
        """Return the image to OCR for this block.

        Priority:
        1. block.image  — pre-extracted by Docling (e.g. embedded figure)
        2. Crop from page_image using block.bbox
        """
        if block.image is not None:
            return block.image

        if page_image is None or block.bbox is None:
            return None

        x0, y0, x1, y1 = block.bbox
        bbox_2d = [int(x0), int(y0), int(x1), int(y1)]
        try:
            return crop_image_region(page_image, bbox_2d)
        except Exception as exc:
            logger.debug(
                "[VisualParser] Crop failed for block %d: %s", block.index, exc
            )
            return None

    def _ocr_one(self, image: "PILImage", block: Block) -> str:
        """Send one image to GLM-OCR and return the text content."""
        task_type = _TASK_MAP.get(block.type, "image")
        request = self.page_loader.build_request_from_image(image, task_type)
        response, status_code = self.ocr_client.process(request)
        if status_code != 200:
            logger.warning(
                "[VisualParser] OCR returned status %d for block %d",
                status_code,
                block.index,
            )
            return ""
        return (
            response.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
