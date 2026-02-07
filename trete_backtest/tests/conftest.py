import pytest
import os
import sys

# Ensure the trete_backtest package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import DataConfig


@pytest.fixture
def config_with_key():
    """DataConfig with API key from environment. Skip test if not set."""
    key = os.environ.get("TIINGO_API_KEY", "")
    if not key:
        pytest.skip("TIINGO_API_KEY not set")
    return DataConfig(tiingo_api_key=key)


@pytest.fixture
def offline_config(tmp_path):
    """DataConfig pointing to tmp_path for cache, with use_cache=True."""
    return DataConfig(
        tiingo_api_key="",
        cache_dir=str(tmp_path),
        use_cache=True,
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "network: requires network access")
    config.addinivalue_line("markers", "offline: runs without network")
