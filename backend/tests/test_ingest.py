"""Docling OCR runs only on PDFs that need it: every page without a text layer is a scan."""
from types import SimpleNamespace

from app.services import ingest


def _pdf(tmp_path, *pages: str):
    """A PDF whose pages show these texts; "" is a page with no text layer (like a scan)."""
    objs = ["<</Type/Catalog/Pages 2 0 R>>",
            "<</Type/Pages/Kids[%s]/Count %d>>" % (" ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages))), len(pages))]
    for i, text in enumerate(pages):
        stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET" if text else ""
        objs.append(f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents {4 + 2 * i} 0 R"
                    "/Resources<</Font<</F1<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>>>>>>>")
        objs.append(f"<</Length {len(stream)}>>stream\n{stream}\nendstream")
    out, offsets = "%PDF-1.4\n", []
    for n, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj{o}endobj\n"
    xref = "".join(f"{off:010d} 00000 n \n" for off in offsets)
    out += (f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n{xref}"
            f"trailer<</Size {len(objs) + 1}/Root 1 0 R>>\nstartxref\n{len(out)}\n%%EOF")
    path = tmp_path / f"{len(list(tmp_path.iterdir()))}.pdf"
    path.write_bytes(out.encode())
    return path


def test_text_layer_needs_text_on_every_page(tmp_path):
    assert ingest.has_text_layer(_pdf(tmp_path, "Lease term", "Rent"))
    assert not ingest.has_text_layer(_pdf(tmp_path, "Lease term", ""))
    assert not ingest.has_text_layer(_pdf(tmp_path, ""))


def test_only_pdfs_without_a_text_layer_are_ocrd(tmp_path, monkeypatch):
    asked = []
    empty = SimpleNamespace(iterate_items=lambda: [], export_to_html=lambda: "")
    monkeypatch.setattr(ingest, "_converter",
                        lambda ocr: asked.append(ocr) or SimpleNamespace(convert=lambda _p: SimpleNamespace(document=empty)))
    docx = tmp_path / "a.docx"
    docx.write_bytes(b"")
    for path in (_pdf(tmp_path, "Lease term"), _pdf(tmp_path, "Lease term", ""), docx):
        ingest.extract(path)
    assert asked == [False, True, True]  # a non-PDF keeps the default (images need OCR)


def _cell(text, row, col, span=1, header=False, rows=1):
    from docling_core.types.doc import TableCell
    return TableCell(text=text, start_row_offset_idx=row, end_row_offset_idx=row + rows, start_col_offset_idx=col,
                     end_col_offset_idx=col + span, col_span=span, row_span=rows, column_header=header)


def _table(cells, rows, cols, label="table"):
    from docling_core.types.doc import DocItemLabel, TableData, TableItem
    return TableItem(self_ref="#/tables/0", label=DocItemLabel(label),
                     data=TableData(num_rows=rows, num_cols=cols, table_cells=cells))


STATEMENT = _table([  # a two-row header with "Year Ended" spanning both years, a sub-heading, two rows
    _cell("Year Ended", 0, 1, span=2, header=True), _cell("Notes", 0, 3, header=True, rows=2),
    _cell("Dec 31, 2022", 1, 1, header=True), _cell("Dec 25, 2021", 1, 2, header=True),
    _cell("Common stock", 2, 0),
    _cell("Balance, end of period", 3, 0), _cell("$ 16", 3, 1), _cell("$ 12", 3, 2), _cell("(4)", 3, 3),
    _cell("Total equity", 4, 0), _cell("54,750", 4, 1, span=2),
], rows=5, cols=4)


def test_table_rows_carry_their_headers_and_sub_heading():
    assert ingest._table_rows(STATEMENT) == [
        "Common stock / Balance, end of period: Year Ended Dec 31, 2022: $ 16; Year Ended Dec 25, 2021: $ 12; "
        "Notes: (4).",  # "Notes" spans both header rows: named once
        "Common stock / Total equity: Year Ended Dec 31, 2022: 54,750.",  # a spanning cell appears once
    ]


def test_a_table_of_text_is_left_out():
    exhibits = _table([_cell("3.1", 0, 0), _cell("Certificate of Incorporation", 0, 1),
                       _cell("31.1", 1, 0), _cell("Certification of the CEO", 1, 1)], rows=2, cols=2)
    assert ingest._table_rows(exhibits) == []


def test_tables_become_items_but_a_table_of_contents_does_not(tmp_path, monkeypatch):
    toc = _table([_cell("Item 1A.", 0, 0), _cell("Risk Factors", 0, 1)], rows=1, cols=2, label="document_index")
    doc = SimpleNamespace(iterate_items=lambda: [(STATEMENT, 0), (toc, 0)], export_to_html=lambda: "")
    monkeypatch.setattr(ingest, "_converter", lambda ocr: SimpleNamespace(convert=lambda _p: SimpleNamespace(document=doc)))
    assert [i.text for i in ingest.extract(_pdf(tmp_path, "x")).items] == ingest._table_rows(STATEMENT)
