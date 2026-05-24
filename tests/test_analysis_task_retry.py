from elastic_transport import ConnectionError as ESConnectionError

from logmind.core.exceptions import PipelineError
from logmind.domain.analysis.tasks import _is_retryable_analysis_error


def test_retryable_analysis_error_detects_direct_connection_reset():
    exc = ConnectionResetError(104, "Connection reset by peer")

    assert _is_retryable_analysis_error(exc) is True


def test_retryable_analysis_error_detects_nested_pipeline_connection_error():
    captured = None
    try:
        try:
            raise ESConnectionError("Connection error caused by: ConnectionError(Connection reset by peer)")
        except ESConnectionError as original:
            raise PipelineError("prompt_build", original)
    except PipelineError as exc:
        captured = exc

    assert captured is not None
    assert _is_retryable_analysis_error(captured) is True


def test_retryable_analysis_error_rejects_non_transient_pipeline_errors():
    exc = PipelineError("result_parse", ValueError("Invalid JSON payload"))

    assert _is_retryable_analysis_error(exc) is False
