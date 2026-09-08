"""Reading order and offsets: everything downstream depends on these."""

from factlayer.ingest.pdf import read_pdf
from factlayer.ingest.segment import iter_units, verify
from factlayer.ingest.text import collapse_ws, normalise


def test_normalisation_is_idempotent():
    raw = "  ₹ 8,142 Cr  – infra-\nstructure “x” "
    once = normalise(raw)
    assert once == normalise(once)
    assert once == '₹ 8,142 Cr - infrastructure "x"'


def test_collapse_ws():
    assert collapse_ws(" a  b\nc ") == "a b c"


def test_two_column_page_is_read_in_reading_order(starter_pdf):
    """Interleaved columns would put half of one sentence next to another's."""
    document = read_pdf(starter_pdf)
    page = next(p for p in document.pages if "standalone basis for FY24" in p.text)
    start = page.text.index("The revenue from operations on standalone")
    sentence = page.text[start:start + 130]
    assert "stood at ₹ 74,540.82 million" in sentence
    assert page.n_columns > 1


def test_cell_offsets_address_the_page_text(starter_pdf):
    document = read_pdf(starter_pdf)
    for page in document.pages[:12]:
        for cell in page.cells:
            assert page.text[cell.start:cell.end] == cell.text


def test_unit_offsets_round_trip(probe_pdfs):
    for path in probe_pdfs:
        for page in read_pdf(path).pages:
            units = iter_units(page.text)
            verify(page.text, units)          # raises on any drift


def test_sentences_split_but_abbreviations_do_not(probe_pdfs):
    text = ("Rs. 8,142 crore was reported by Mr. Barua. The next sentence starts here.")
    units = iter_units(text)
    assert len(units) == 2
    assert units[0].text.endswith("Barua.")


def test_table_rows_are_kept_whole():
    units = iter_units("Term loan 199 126")
    assert len(units) == 1 and units[0].kind == "row"


def test_document_identity_is_content_addressed(probe_pdfs):
    first = read_pdf(probe_pdfs[0])
    again = read_pdf(probe_pdfs[0])
    assert first.sha256 == again.sha256 and first.doc_id == again.doc_id
