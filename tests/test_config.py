from logmind.core.config import Settings


def test_analysis_window_defaults_are_split():
    settings = Settings(_env_file=None)

    assert settings.effective_patrol_interval_minutes == 5
    assert settings.effective_anomaly_window_minutes == 5
    assert settings.effective_lookback_minutes == 10


def test_split_windows_do_not_inherit_legacy_cooldown():
    settings = Settings(_env_file=None, analysis_cooldown_minutes=30)

    assert settings.effective_patrol_interval_minutes == 5
    assert settings.effective_anomaly_window_minutes == 5
    assert settings.effective_lookback_minutes == 10


def test_explicit_split_windows_override_legacy_cooldown():
    settings = Settings(
        _env_file=None,
        analysis_cooldown_minutes=30,
        analysis_patrol_interval_minutes=5,
        analysis_anomaly_window_minutes=5,
        analysis_lookback_minutes=10,
    )

    assert settings.effective_patrol_interval_minutes == 5
    assert settings.effective_anomaly_window_minutes == 5
    assert settings.effective_lookback_minutes == 10
