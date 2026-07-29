from coal_platform.parse_quality import evaluate_parse_quality


def test_parse_quality_requires_review_for_low_confidence_and_unresolved_pages() -> None:
    result = evaluate_parse_quality(
        [{"page_no": 1, "confidence": 0.5}, {"page_no": 2, "confidence": 0.9}],
        {"page_count": 3, "needs_ocr": True, "unresolved_ocr_pages": [3]},
    )

    assert result["block_count"] == 2
    assert result["low_confidence_block_count"] == 1
    assert result["unresolved_page_count"] == 1
    assert result["review_required"] is True
    assert {"low_confidence_blocks", "unresolved_pages", "low_page_coverage"} <= set(result["review_reasons"])


def test_parse_quality_accepts_complete_high_confidence_content() -> None:
    result = evaluate_parse_quality(
        [{"page_no": 1, "confidence": 1.0}, {"page_no": 2, "confidence": 0.95}],
        {"page_count": 2},
    )

    assert result["quality_score"] > 0.9
    assert result["page_coverage_ratio"] == 1.0
    assert result["review_required"] is False
