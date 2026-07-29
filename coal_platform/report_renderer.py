from __future__ import annotations

from html import escape
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile


def _lines(content: dict) -> list[str]:
    task = content.get("task") or {}
    summary = content.get("issue_summary") or {}
    execution = content.get("execution_summary") or {}
    lines = [
        content.get("title", "煤矿安标技术文档审核报告"),
        f"报告编号：{task.get('task_no', '')}",
        f"客户名称：{task.get('customer_name', '')}",
        f"产品名称：{task.get('product_name', '')}",
        f"产品型号：{task.get('product_model', '')}",
        f"审核结论：{content.get('conclusion', '')}",
        f"规则执行：{execution.get('total', 0)} 条",
        f"问题数量：{summary.get('total', 0)} 条，已确认 {summary.get('confirmed', 0)} 条",
        "问题明细：",
    ]
    for index, issue in enumerate(content.get("issues") or [], start=1):
        lines.append(f"{index}. {issue.get('title', '')} [{issue.get('severity', '')}] {issue.get('description', '')}")
    return lines


def render_docx(content: dict) -> bytes:
    paragraphs = "".join(f"<w:p><w:r><w:t>{escape(str(line))}</w:t></w:r></w:p>" for line in _lines(content))
    document = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{paragraphs}<w:sectPr/></w:body></w:document>'
    types = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)
    return output.getvalue()


def render_pdf(content: dict) -> bytes:
    # Keep the first release dependency-free; the content remains searchable ASCII/UTF-8 bytes.
    text = "\\n".join(_lines(content)).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 10 Tf 50 780 Td ({text[:3500]}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = BytesIO(b"%PDF-1.4\n")
    output.seek(0, 2)
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(output.tell())
        output.write(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    startxref = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n".encode())
    output.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode())
    output.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{startxref}\n%%EOF\n".encode())
    return output.getvalue()
