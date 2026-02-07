import logging
import os
import time
from pathlib import Path

import pandas as pd
import requests

from config import DataConfig

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_SECONDS = [1, 2, 4]


def fetch_ticker_from_tiingo(
    ticker: str,
    start_date: str,
    end_date: str,
    api_key: str,
    base_url: str = "https://api.tiingo.com/tiingo/daily",
) -> pd.DataFrame:
    """
    Fetch daily adjusted price data from Tiingo for a single ticker.

    Returns a DataFrame with:
      Index: DatetimeIndex named 'date' (timezone-naive, daily)
      Columns: 'adjOpen', 'adjClose', 'adjVolume'

    Raises:
      ValueError: if api_key is empty
      ConnectionError: if HTTP request fails (after retries)
      ValueError: if response is empty or ticker not found
    """
    if not api_key:
        raise ValueError("API key must not be empty")

    url = f"{base_url}/{ticker}/prices"
    params = {
        "startDate": start_date,
        "endDate": end_date,
        "format": "json",
        "resampleFreq": "daily",
        "token": api_key,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Token {api_key}",
    }

    last_exception: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=30)

            if response.status_code == 429 or response.status_code >= 500:
                if attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_SECONDS[attempt])
                    continue
                raise ConnectionError(
                    f"HTTP {response.status_code} after {_MAX_RETRIES + 1} attempts for {ticker}"
                )

            if response.status_code == 404:
                raise ValueError(f"Ticker '{ticker}' not found on Tiingo (404)")

            response.raise_for_status()
            break
        except requests.exceptions.ConnectionError as exc:
            last_exception = exc
            if attempt < _MAX_RETRIES:
                time.sleep(_BACKOFF_SECONDS[attempt])
                continue
            raise ConnectionError(
                f"Connection failed after {_MAX_RETRIES + 1} attempts for {ticker}"
            ) from exc
    else:
        raise ConnectionError(
            f"Request failed after {_MAX_RETRIES + 1} attempts for {ticker}"
        ) from last_exception

    data = response.json()
    if not data:
        raise ValueError(f"Empty response for ticker '{ticker}'")

    df = pd.DataFrame(data)

    # Parse date, strip timezone, set as index
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df = df.set_index("date")

    # Keep only required columns
    df = df[["adjOpen", "adjClose", "adjVolume"]]

    # Enforce data types
    df["adjOpen"] = df["adjOpen"].astype("float64")
    df["adjClose"] = df["adjClose"].astype("float64")
    # adjVolume: use int64 if no NaN, else float64
    if df["adjVolume"].isna().any():
        df["adjVolume"] = df["adjVolume"].astype("float64")
    else:
        df["adjVolume"] = df["adjVolume"].astype("int64")

    # Sort and validate
    df = df.sort_index()
    if df.index.has_duplicates:
        df = df[~df.index.duplicated(keep="first")]
        logger.warning("Duplicate dates removed for %s", ticker)

    return df


def load_ticker(
    ticker: str,
    config: DataConfig,
) -> pd.DataFrame:
    """
    Load ticker data with caching.

    1. If config.use_cache and cache file exists at {cache_dir}/{ticker}.csv -> load from CSV.
    2. Otherwise, call fetch_ticker_from_tiingo(), save to cache, return.

    Returns same schema as fetch_ticker_from_tiingo.
    """
    cache_path = Path(config.cache_dir) / f"{ticker.upper()}.csv"

    if config.use_cache and cache_path.exists():
        df = pd.read_csv(cache_path, parse_dates=["date"], index_col="date")
        df["adjOpen"] = df["adjOpen"].astype("float64")
        df["adjClose"] = df["adjClose"].astype("float64")
        if df["adjVolume"].isna().any():
            df["adjVolume"] = df["adjVolume"].astype("float64")
        else:
            df["adjVolume"] = df["adjVolume"].astype("int64")
        return df

    df = fetch_ticker_from_tiingo(
        ticker=ticker,
        start_date=config.start_date,
        end_date=config.end_date,
        api_key=config.tiingo_api_key,
        base_url=config.tiingo_base_url,
    )

    # Save to cache
    os.makedirs(config.cache_dir, exist_ok=True)
    df.to_csv(cache_path, date_format="%Y-%m-%d")

    return df


def fetch_vix(
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """
    Fetch VIX (CBOE Volatility Index) daily close from FRED.

    URL: https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS&cosd={start_date}&coed={end_date}

    Returns a DataFrame with:
      Index: DatetimeIndex named 'date' (timezone-naive, daily)
      Columns: 'vix_close' (float64)

    Missing values (FRED uses '.') are forward-filled.
    """
    url = (
        f"https://fred.stlouisfed.org/graph/fredgraph.csv"
        f"?id=VIXCLS&cosd={start_date}&coed={end_date}"
    )

    response = requests.get(url, timeout=30)
    response.raise_for_status()

    from io import StringIO

    df = pd.read_csv(
        StringIO(response.text),
        na_values=["."],
        parse_dates=["DATE"],
    )
    df = df.rename(columns={"DATE": "date", "VIXCLS": "vix_close"})
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)

    # Forward-fill missing values
    df["vix_close"] = df["vix_close"].ffill()

    # Drop any remaining NaN at the start (before first valid observation)
    df = df.dropna(subset=["vix_close"])

    df["vix_close"] = df["vix_close"].astype("float64")

    df = df.sort_index()

    return df


def load_vix(config: DataConfig) -> pd.DataFrame:
    """
    Load VIX data with caching.
    Cache file: {cache_dir}/VIX.csv
    Columns: date,vix_close
    """
    cache_path = Path(config.cache_dir) / "VIX.csv"

    if config.use_cache and cache_path.exists():
        df = pd.read_csv(cache_path, parse_dates=["date"], index_col="date")
        df["vix_close"] = df["vix_close"].astype("float64")
        return df

    df = fetch_vix(start_date=config.start_date, end_date=config.end_date)

    # Save to cache
    os.makedirs(config.cache_dir, exist_ok=True)
    df.to_csv(cache_path, date_format="%Y-%m-%d")

    return df
