"""Unified block schema for the two-layer parsing pipeline.

All parsers (Docling, GLM-OCR) produce Block objects.
The block formatter then converts them to Markdown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

BlockType = Literal[
    "text",
    "table",
    "figure",
    "formula",
    "title",
    "section_header",
    "list_item",
    "caption",
    "page_header",
    "page_footer",
    "code",
]


@dataclass
class Block:
    """A unified content block representing a parsed document region.

    Produced by any parser in the Parsing Layer and consumed by
    the block formatter to generate the final Markdown output.

    Attributes:
        page:     0-indexed page number.
        index:    0-indexed position within the page (used for ordering).
        type:     Semantic type of the block.
        content:  Final Markdown-formatted text content.
                  Empty string means the block has not been parsed yet
                  (visual blocks waiting for GLM-OCR).
        bbox:     (x0, y0, x1, y1) bounding box normalised to 0-1000
                  in top-left-origin coordinates.  Optional.
        image:    Pre-extracted PIL image for visual blocks (figures/tables).
                  When set, VisualParser uses this directly instead of
                  cropping from the rendered page image.
        metadata: Arbitrary extra data (docling label, heading level, …).
    """

    page: int
    index: int
    type: BlockType
    content: str = ""
    bbox: Optional[Tuple[float, float, float, float]] = None
    image: Optional["PILImage"] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def needs_visual_parsing(self) -> bool:
        """True if this block should be sent to the visual (GLM-OCR) parser."""
        return self.type in ("table", "figure") and not self.content

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page": self.page,
            "index": self.index,
            "label": self.type,
            "content": self.content,
            "bbox_2d": list(self.bbox) if self.bbox else None,
        }


@dataclass
class DocumentBlocks:
    """All blocks across all pages of a parsed document."""

    source: str
    pages: List[List[Block]] = field(default_factory=list)

    def add_page(self, blocks: List[Block]) -> None:
        self.pages.append(blocks)

    def all_blocks(self) -> List[Block]:
        return [b for page in self.pages for b in page]

    def to_json(self) -> List[List[Dict[str, Any]]]:
        return [[b.to_dict() for b in page] for page in self.pages]
