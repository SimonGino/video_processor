from douyu2bilibili import config


def test_logging_config_constants_exist():
    assert hasattr(config, "LOG_LEVEL")
    assert config.LOG_LEVEL in ("DEBUG", "INFO", "WARNING", "ERROR")
    assert hasattr(config, "LOG_DIR")
    assert config.LOG_DIR.endswith("logs")
    assert hasattr(config, "LOG_RETENTION_DAYS")
    assert config.LOG_RETENTION_DAYS == 3
