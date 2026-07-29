import pytest

from coal_platform.load_test import Sample, percentile, summarize


def test_percentile_interpolates_ordered_samples() -> None:
    assert percentile([40, 10, 30, 20], 0.5) == pytest.approx(25)
    assert percentile([], 0.95) == 0


def test_summary_reports_failures_latency_throughput_and_queue_peak() -> None:
    result = summarize(
        [
            Sample("list_tasks", 200, 10),
            Sample("create_task", 201, 20),
            Sample("upload_file", 500, 40, "server error"),
        ],
        elapsed=2,
        queue_peak=7,
    )
    assert result["requests"] == 3
    assert result["error_rate"] == pytest.approx(0.3333)
    assert result["throughput_rps"] == 1.5
    assert result["latency_ms"]["p95"] == pytest.approx(38)
    assert result["queue_waiting_peak"] == 7
    assert len(result["errors"]) == 1
