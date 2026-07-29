from coal_platform.document_parser import DocumentParseError
from coal_platform.worker import is_retryable_job_error


def test_worker_does_not_retry_permanent_document_or_state_errors() -> None:
    assert is_retryable_job_error(DocumentParseError("block limit")) is False
    assert is_retryable_job_error(ValueError("job is not runnable")) is False
    assert is_retryable_job_error(ConnectionError("redis unavailable")) is True
