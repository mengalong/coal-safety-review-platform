from __future__ import annotations

from typing import Any


def evaluate_parse_quality(blocks: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    confidences = [max(0.0, min(1.0, float(item.get("confidence", 1.0)))) for item in blocks]
    mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    low_confidence_count = sum(value < 0.6 for value in confidences)
    page_count = int(summary.get("page_count") or summary.get("sheet_count") or 0)
    covered_pages = len({int(item.get("page_no", 1)) for item in blocks})
    coverage_ratio = min(1.0, covered_pages / page_count) if page_count else (1.0 if blocks else 0.0)
    unresolved_pages = list(summary.get("unresolved_ocr_pages") or [])
    quality_score = round(mean_confidence * 0.7 + coverage_ratio * 0.3, 4)
    reasons = []
    if not blocks:
        reasons.append("no_blocks")
    if low_confidence_count:
        reasons.append("low_confidence_blocks")
    if unresolved_pages or summary.get("needs_ocr"):
        reasons.append("unresolved_pages")
    if coverage_ratio < 0.8:
        reasons.append("low_page_coverage")
    return {
        "quality_score": quality_score,
        "mean_confidence": round(mean_confidence, 4),
        "page_coverage_ratio": round(coverage_ratio, 4),
        "block_count": len(blocks),
        "low_confidence_block_count": low_confidence_count,
        "unresolved_page_count": len(unresolved_pages),
        "review_required": bool(reasons) or quality_score < 0.75,
        "review_reasons": reasons,
    }
