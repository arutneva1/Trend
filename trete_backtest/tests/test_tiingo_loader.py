import pytest
import numpy as np
import pandas as pd

from config import DataConfig
from data.tiingo_loader import fetch_ticker_from_tiingo, load_ticker, fetch_vix, load_vix


# ---------- Test 1.1 — API key validation ----------
def test_fetch_raises_on_empty_api_key():
    with pytest.raises(ValueError, match="API key"):
        fetch_ticker_from_tiingo("SPY", "2024-01-01", "2024-01-31", api_key="")


# ---------- Test 1.2 — Response schema (requires live API) ----------
@pytest.mark.network
def test_fetch_spy_returns_correct_schema(config_with_key):
    """Skip if TIINGO_API_KEY not set."""
    df = fetch_ticker_from_tiingo(
        "SPY", "2024-01-02", "2024-01-31", config_with_key.tiingo_api_key
    )
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.name == "date"
    assert list(df.columns) == ["adjOpen", "adjClose", "adjVolume"]
    assert df.index.tz is None  # timezone-naive
    assert df["adjOpen"].dtype == np.float64
    assert df["adjClose"].dtype == np.float64
    assert len(df) > 15  # ~20 trading days in Jan
    assert df.index.is_monotonic_increasing
    assert not df.index.has_duplicates


# ---------- Test 1.3 — Cache write and read roundtrip ----------
@pytest.mark.offline
def test_cache_roundtrip(tmp_path, monkeypatch):
    """load_ticker should write cache on first call, read from it on second."""
    config = DataConfig(
        tiingo_api_key="TEST_KEY",
        cache_dir=str(tmp_path),
        start_date="2024-01-02",
        end_date="2024-01-05",
        use_cache=True,
    )
    # Create a fake cached file
    fake_data = pd.DataFrame(
        {
            "adjOpen": [100.0, 101.0],
            "adjClose": [100.5, 101.5],
            "adjVolume": [1000000, 1100000],
        },
        index=pd.DatetimeIndex(["2024-01-02", "2024-01-03"], name="date"),
    )
    cache_path = tmp_path / "SPY.csv"
    fake_data.to_csv(cache_path)

    # Should load from cache without hitting API
    result = load_ticker("SPY", config)
    assert len(result) == 2
    assert result["adjClose"].iloc[0] == pytest.approx(100.5)


# ---------- Test 1.4 — VIX schema ----------
@pytest.mark.network
def test_vix_schema(config_with_key):
    """Skip if network unavailable."""
    df = fetch_vix("2024-01-02", "2024-01-31")
    assert isinstance(df.index, pd.DatetimeIndex)
    assert "vix_close" in df.columns
    assert df["vix_close"].dtype == np.float64
    assert not df["vix_close"].isna().any()  # after forward-fill


# ---------- Test 1.5 — Offline fixture test (always runs) ----------
@pytest.mark.offline
def test_load_ticker_from_fixture(tmp_path):
    """Create a fixture CSV, verify load_ticker reads it correctly."""
    # Build fixture data
    dates = pd.bdate_range("2024-01-02", periods=5)
    fixture_df = pd.DataFrame(
        {
            "adjOpen": [470.0, 471.0, 472.0, 473.0, 474.0],
            "adjClose": [471.0, 472.0, 473.0, 474.0, 475.0],
            "adjVolume": [50000000, 51000000, 52000000, 53000000, 54000000],
        },
        index=pd.DatetimeIndex(dates, name="date"),
    )
    # Write fixture CSV
    cache_path = tmp_path / "MGK.csv"
    fixture_df.to_csv(cache_path, date_format="%Y-%m-%d")

    # Configure to use the tmp_path as cache
    config = DataConfig(
        tiingo_api_key="",
        cache_dir=str(tmp_path),
        use_cache=True,
    )

    result = load_ticker("MGK", config)

    assert isinstance(result.index, pd.DatetimeIndex)
    assert result.index.name == "date"
    assert list(result.columns) == ["adjOpen", "adjClose", "adjVolume"]
    assert len(result) == 5
    assert result["adjOpen"].dtype == np.float64
    assert result["adjClose"].dtype == np.float64
    assert result["adjClose"].iloc[0] == pytest.approx(471.0)
    assert result["adjClose"].iloc[-1] == pytest.approx(475.0)
    assert result["adjVolume"].iloc[0] == 50000000
