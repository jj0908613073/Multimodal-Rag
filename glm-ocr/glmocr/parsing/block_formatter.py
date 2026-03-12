"""Convert Block lists to a Markdown string.

Rules:
  - title            → # heading
  - section_header   → ## / ### … heading (respects heading_level metadata)
  - formula          → $$ … $$  (display math)
  - code             → ``` … ``` fenced block
  - list_item        → - bullet
  - table            → rendered as-is (GLM-OCR usually returns HTML or Markdown table)
  - figure           → rendered as-is (GLM-OCR returns description / Markdown)
  - caption          → *italic*
  - page_header/footer → omitted
  - everything else  → plain paragraph

Pages are separated by a horizontal rule (---).
"""

from __future__ import annotations

from typing import List

from glmocr.parsing.block_schema import Block


def blocks_to_markdown(blocks_per_page: List[List[Block]]) -> str:
    """Convert all document blocks to a Markdown string.

    Args:
        blocks_per_page: Outer list = pages (0-based), inner = Blocks in order.

    Returns:
        A single Markdown string.
    """
    page_parts: List[str] = []

    for blocks in blocks_per_page:
        page_lines: List[str] = []
        for block in blocks:
            md = _block_to_markdown(block)
            if md:
                page_lines.append(md)

        if page_lines:
            page_parts.append("\n\n".join(page_lines))

    return "\n\n---\n\n".join(page_parts).strip()


# ---------------------------------------------------------------------------
# Per-block renderer
# ---------------------------------------------------------------------------

def _block_to_markdown(block: Block) -> str:
    content = (block.content or "").strip()
    if not content:
        return ""

    t = block.type

    if t == "title":
        return f"# {content}"

    if t == "section_header":
        level = block.metadata.get("heading_level") or 2
        # heading_level from Docling iterate_items is nesting depth (0-based);
        # map to H2–H6 so we don't conflict with the document title (H1).
        hashes = "#" * min(max(int(level) + 2, 2), 6)
        return f"{hashes} {content}"

    if t == "formula":
        # Already wrapped? pass through; otherwise wrap.
        if content.startswith("$$"):
            return content
        return f"$$\n{content}\n$$"

    if t == "code":
        if content.startswith("```"):
            return content
        return f"```\n{content}\n```"

    if t == "list_item":
        # Strip leading bullet characters added by Docling itself
        stripped = content.lstrip("-•·*◦▪▸► ").strip()
        return f"- {stripped}" if stripped else ""

    if t == "caption":
        return f"*{content}*"

    if t in ("page_header", "page_footer"):
        return ""  # omit

    # table / figure / text / paragraph — emit as-is
    return content
