from io import BytesIO

import pytest
from docx import Document
from openpyxl import Workbook
from pypdf import PdfWriter

from coal_platform.document_parser import DocumentParseError, parse_document
from coal_platform.report_renderer import render_pdf


def _docx_bytes() -> bytes:
    document = Document()
    document.add_heading("Technical specification", level=1)
    document.add_paragraph("Model: DSJ80/40/2x75")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Parameter"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "Voltage"
    table.cell(1, 1).text = "1140 V"
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Parameters"
    sheet.append(["Name", "Value", "Unit"])
    sheet.append(["Power", 75, "kW"])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_parse_docx_preserves_paragraph_and_table_row_references() -> None:
    parsed = parse_document(_docx_bytes(), "specification.docx")

    assert parsed["summary"]["parser"] == "python-docx"
    assert parsed["summary"]["table_row_count"] == 2
    assert any(item["content_text"] == "Model: DSJ80/40/2x75" for item in parsed["blocks"])
    assert any(item["source_ref"] == "docx:table:1:row:2" for item in parsed["blocks"])


def test_parse_xlsx_preserves_sheet_and_cell_range() -> None:
    parsed = parse_document(_xlsx_bytes(), "parameters.xlsx")

    assert parsed["summary"]["sheet_names"] == ["Parameters"]
    assert parsed["blocks"][1]["content_text"] == "Power\t75\tkW"
    assert parsed["blocks"][1]["source_ref"] == "xlsx:Parameters!A2:C2"


def test_parse_pdf_extracts_text_and_marks_blank_pages_for_ocr() -> None:
    text_pdf = render_pdf({"title": "Safety report", "task": {"task_no": "TASK-001"}})
    parsed = parse_document(text_pdf, "report.pdf")
    assert parsed["summary"]["page_count"] == 1
    assert parsed["summary"]["needs_ocr"] is False
    assert "Safety report" in "".join(item["content_text"] for item in parsed["blocks"])

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = BytesIO()
    writer.write(output)
    blank = parse_document(output.getvalue(), "scan.pdf")
    assert blank["summary"]["needs_ocr"] is True
    assert blank["summary"]["empty_text_pages"] == [1]


def test_parse_document_rejects_unsupported_type() -> None:
    with pytest.raises(DocumentParseError, match="unsupported document type"):
        parse_document(b"binary", "drawing.dwg")
