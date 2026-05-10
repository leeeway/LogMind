from datetime import datetime, timezone

from logmind.domain.alert.channels.webhook import (
    _build_error_log_alert,
    _format_time_range,
    _localize_inline_timestamps,
)


def test_format_time_range_uses_beijing_time():
    time_from = datetime(2026, 5, 10, 14, 1, 39, tzinfo=timezone.utc)
    time_to = datetime(2026, 5, 10, 14, 31, 39, tzinfo=timezone.utc)

    rendered = _format_time_range(time_from, time_to)

    assert rendered == "2026-05-10 22:01:39 ~ 2026-05-10 22:31:39 (北京时间)"


def test_localize_inline_timestamps_converts_iso_zulu():
    summary = "[2026-05-10T14:23:46.401Z] [WARNING] domain:stage"

    rendered = _localize_inline_timestamps(summary)

    assert "[2026-05-10 22:23:46]" in rendered
    assert "WARNING" in rendered


def test_build_error_log_alert_renders_localized_summary_and_range():
    alert = _build_error_log_alert(
        business_line="光宇通v5核心",
        domain="stage-tong-kernel.gyyx.cn",
        branch="master",
        host_name="",
        language="java",
        log_count=5000,
        error_summary="[2026-05-10T14:23:46.401Z] [WARNING] sample",
        time_from=datetime(2026, 5, 10, 14, 1, 39, tzinfo=timezone.utc),
        time_to=datetime(2026, 5, 10, 14, 31, 39, tzinfo=timezone.utc),
    )

    assert "**时间范围**: 2026-05-10 22:01:39 ~ 2026-05-10 22:31:39 (北京时间)" in alert
    assert "[2026-05-10 22:23:46]" in alert
