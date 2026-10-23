import functools
import re
import threading
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


_CONVERT_LOCK = threading.Lock()  # ponytail: one conversion at a time; a converter per thread if docs queue up


@functools.cache
def _converter(ocr: bool):
    """Built once per setting: loading Docling's models takes 3-6 s.

    OCR only runs on PDFs with a page that has no text layer (see extract). On a PDF that has one
    it re-reads text the PDF already holds: on the 51-page lease it was 62 of 65 s and added only
    logo text. TableFormer stays in accurate mode although fast halves the AMD 10-K's conversion:
    fast misread 23 of its 65 tables, e.g. a "December 25, 2021" column header as "December 2021".
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions(do_ocr=ocr)
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def has_text_layer(path: Path) -> bool:
    """Every page has extractable text: born digital, or a scan that was OCR'd already."""
    import pypdfium2  # Docling's own PDF dependency

    pdf = pypdfium2.PdfDocument(str(path))
    try:
        return all(page.get_textpage().get_text_range().strip() for page in pdf)
    finally:
        pdf.close()


# A data cell: an amount, a percentage, a dash for zero, N/A.
_NUMERIC_CELL = re.compile(r"[-–—$€£%().,\s\d]*\d[-–—$€£%().,\s\d]*|[-–—]\s*%?|N/A", re.I)


def _table_rows(table) -> list[str]:
    """One sentence per body row, each readable on its own, so a chunk holding a few rows still
    says what its numbers are:

        Common stock / Balance, end of period: Year Ended December 31, 2022: $ 16; ...

    Column names join the header rows' texts; a row with a label and no values (a sub-heading in
    a financial statement) prefixes the rows under it. Cells spanning columns appear once.

    Only data tables (at least half the value cells numbers): a text table is usually a list of
    names, like the 10-K's exhibit index, whose hundreds of "Certificate of Incorporation ..." rows
    pushed the answers out of the top hits for "When was AMD incorporated?". Text tables stay out,
    as all tables did before.
    """
    grid = table.data.grid
    values = [c.text.strip() for r in grid for c in r[1:] if c.text.strip() and not c.column_header]
    if not values or sum(bool(_NUMERIC_CELL.fullmatch(v)) for v in values) < len(values) / 2:
        return []
    header_rows = [r for r in grid if any(c.text.strip() for c in r)
                   and all(c.column_header or not c.text.strip() for c in r)]
    headers = []
    for j in range(table.data.num_cols):
        parts: list[str] = []
        for r in header_rows:
            t = r[j].text.strip()
            if t and (not parts or parts[-1] != t):
                parts.append(t)
        headers.append(" ".join(parts))
    rows, section = [], ""
    for r in grid:
        if r in header_rows:
            continue
        seen, cells = set(), []
        for j, c in enumerate(r):
            if (c.start_col_offset_idx, c.end_col_offset_idx) not in seen:
                seen.add((c.start_col_offset_idx, c.end_col_offset_idx))
                cells.append((headers[j], c.text.strip()))
        label = cells[0][1] if cells else ""
        values = [(h, v) for h, v in cells[1:] if v]
        if not values:
            section = label.rstrip(":") or section
            continue
        name = f"{section} / {label}" if section and label else label or section
        body = "; ".join(f"{h}: {v}" if h else v for h, v in values)
        rows.append(f"{name}: {body}." if name else f"{body}.")
    return rows


def extract(path: Path) -> Extracted:
    """Run Docling and return items in reading order with provenance.

    Non-PDFs won't have page/bbox; we still emit an HTML render that
    the frontend can display with injected data-chunk-id spans later.
    """
    from docling_core.types.doc import DocItemLabel, TableItem

    path = Path(path)
    ocr = path.suffix.lower() != ".pdf" or not has_text_layer(path)
    with _CONVERT_LOCK:
        result = _converter(ocr).convert(str(path))
    doc = result.document

    items: list[ExtractedItem] = []
    page_line_map: dict[int, list[str]] = {}
    max_page = 0

    for text_item, _level in doc.iterate_items():
        # A TableItem has no .text: its cells are rows of text here. A table of contents
        # (document_index) only lists section names, which would outrank the sections themselves.
        if isinstance(text_item, TableItem):
            texts = [] if text_item.label == DocItemLabel.DOCUMENT_INDEX else _table_rows(text_item)
        else:
            text = getattr(text_item, "text", None)
            texts = [text.strip()] if text and text.strip() else []
        if not texts:
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
        for text in texts:  # a table's rows share its page and bbox
            items.append(ExtractedItem(text=text, page=page, bbox=bbox))
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
