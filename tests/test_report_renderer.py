from io import BytesIO
from zipfile import ZipFile

from coal_platform.report_renderer import render_docx, render_pdf


def test_report_renderers_generate_real_docx_and_pdf_bytes() -> None:
    content = {
        "title": "煤矿安标技术文档审核报告",
        "task": {"task_no": "SH-2026-000001", "customer_name": "测试企业", "product_model": "KBZ-500"},
        "conclusion": "through",
        "issue_summary": {"total": 1, "confirmed": 0},
        "execution_summary": {"total": 2},
        "issues": [{"title": "资料缺失", "severity": "一般", "description": "缺少试验报告"}],
    }
    docx = render_docx(content)
    with ZipFile(BytesIO(docx)) as archive:
        assert "word/document.xml" in archive.namelist()
        assert "资料缺失" in archive.read("word/document.xml").decode()
    pdf = render_pdf(content)
    assert pdf.startswith(b"%PDF-1.4")
    assert b"xref" in pdf and pdf.endswith(b"%%EOF\n")
