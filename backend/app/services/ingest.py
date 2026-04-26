from dataclasses import dataclass, field
from pathlib import Path

from app.utils.text import clean_text


@dataclass
class ExtractedItem:
    text: str
    page: int | None = None
    bbox: dict | None = None  # {x0, y0, x1, y1}


@dataclass
class Extracted:
    items: list[ExtractedItem] = field(default_factory=list)
    html: str = ""
    pages: int = 0
    full_text: str = ""
    stats: dict = field(default_factory=dict)


def extract(path: Path) -> Extracted:
    """Run Docling and return items in reading order with provenance.

    Non-PDFs won't have page/bbox; we still emit an HTML render that
    the frontend can display with injected data-chunk-id spans later.
    """
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(str(path))
    doc = result.document

    items: list[ExtractedItem] = []
    page_line_map: dict[int, list[str]] = {}
    max_page = 0

    for text_item, _level in doc.iterate_items():
        text = getattr(text_item, "text", None)
        if not text or not text.strip():
            continue
        page = None
        bbox = None
        prov_list = getattr(text_item, "prov", None) or []
        if prov_list:
            prov = prov_list[0]
            page = getattr(prov, "page_no", None)
            b = getattr(prov, "bbox", None)
            if b is not None:
                # Docling bbox is {l, t, r, b} in PDF points from bottom-left.
                bbox = {
                    "x0": float(getattr(b, "l", 0.0)),
                    "y0": float(getattr(b, "b", 0.0)),
                    "x1": float(getattr(b, "r", 0.0)),
                    "y1": float(getattr(b, "t", 0.0)),
                }
        items.append(ExtractedItem(text=text.strip(), page=page, bbox=bbox))
        if page is not None:
            max_page = max(max_page, int(page))
            page_line_map.setdefault(int(page), []).extend(text.splitlines())

    page_lines_list = [page_line_map[p] for p in sorted(page_line_map)] if page_line_map else None
    full_raw = "\n".join(it.text for it in items)
    cleaned, stats = clean_text(full_raw, page_lines_list)

    try:
        html = doc.export_to_html()
    except Exception:
        html = "<pre>" + cleaned.replace("<", "&lt;") + "</pre>"

    return Extracted(
        items=items,
        html=html,
        pages=max_page or 0,
        full_text=cleaned,
        stats=stats,
    )
