# Phase 1: Data Pipeline — Detailed SRS
# TRETE Backtest Engine

**Parent document:** TRETE_Backtest_SRS_v3.md
**Phase:** 1 of 8
**Estimated effort:** 2 days
**Date:** February 7, 2026

---

## 0. Instructions for the Implementer

This document fully specifies the data pipeline for the TRETE backtest engine. Implement it exactly as described. Do not add features, do not change the API, do not rename files. If something is ambiguous, follow the **Decision** callout in the relevant section. Every milestone ends with a concrete, runnable test. Do not proceed to the next milestone until all tests for the current milestone pass.

**Language:** Python 3.10+
**Dependencies:** `requests`, `pandas`, `numpy`, `pytest`, `python-dotenv`
**No other dependencies.** Do not use `yfinance`, `tiingo` (Python package), or any other data library. Use `requests` to call the Tiingo REST API directly.

---

## 1. Project Scaffolding

### 1.1 Directory Structure

Create this exact structure inside the project root directory `trete_backtest/`:

```
trete_backtest/
├── data/
│   ├── __init__.py
│   ├── tiingo_loader.py       # Milestone 1: Tiingo API client
│   ├── proxies.py             # Milestone 2: SHV→SGOV stitching
│   ├── risk_free.py           # Milestone 3: Risk-free rate loader
│   ├── pipeline.py            # Milestone 4: Orchestrator — builds the final dataset
│   └── cache/                 # Directory for cached CSV files (gitignored)
│       └── .gitkeep
├── config.py                  # Milestone 1: Configuration dataclass
├── tests/
│   ├── __init__.py
│   ├── conftest.py            # Shared fixtures
│   ├── test_tiingo_loader.py  # Milestone 1 tests
│   ├── test_proxies.py        # Milestone 2 tests
│   ├── test_risk_free.py      # Milestone 3 tests
│   ├── test_pipeline.py       # Milestone 4 tests
│   └── fixtures/              # Static test data (small CSVs)
│       └── .gitkeep
├── .env.example               # Template for API key
├── .gitignore
└── requirements.txt
```

### 1.2 `.env.example`

```
TIINGO_API_KEY=your_api_key_here
```

### 1.3 `.gitignore`

```
__pycache__/
*.pyc
.env
data/cache/*.csv
data/cache/*.zip
.pytest_cache/
```

### 1.4 `requirements.txt`

```
requests>=2.28
pandas>=2.0
numpy>=1.24
pytest>=7.0
python-dotenv>=1.0
```

---

## 2. Configuration (`config.py`)

Create a single dataclass that holds all data-pipeline-related configuration. Later phases will extend this dataclass with strategy parameters, but for Phase 1, only include data fields.

```python
from dataclasses import dataclass, field
from typing import List
import os
from dotenv import load_dotenv

load_dotenv()

@dataclass
class DataConfig:
    """Configuration for the TRETE data pipeline."""

    # --- API ---
    tiingo_api_key: str = field(
        default_factory=lambda: os.environ.get("TIINGO_API_KEY", "")
    )
    tiingo_base_url: str = "https://api.tiingo.com/tiingo/daily"

    # --- Tickers ---
    equity_tickers: List[str] = field(default_factory=lambda: ["SPY", "MGK"])
    cash_ticker: str = "SGOV"
    cash_proxy_ticker: str = "SHV"
    cash_proxy_cutover: str = "2020-05-26"   # SGOV inception date (first trading day)
    vix_ticker: str = "VIXY"                 # Tiingo doesn't serve ^VIX; see §3.4 for VIX handling

    # --- Date Range ---
    start_date: str = "2007-06-01"           # Fetch extra for warm-up (backtest starts 2008-01-02)
    end_date: str = "2025-12-31"
    backtest_start_date: str = "2008-01-02"  # Actual backtest start (after warm-up)

    # --- Risk-Free Rate ---
    french_rf_url: str = (
        "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/"
        "ftp/F-F_Research_Data_Factors_daily_CSV.zip"
    )

    # --- Cache ---
    cache_dir: str = "data/cache"
    use_cache: bool = True                   # If True, load from cache before hitting API
```

**Decision — VIX data:** Tiingo does not serve the CBOE VIX index (`^VIX`) on its free tier. The VIX data is handled as follows:

- **Primary:** Download VIX from FRED (series `VIXCLS`) via their CSV export URL (no API key required).
- **Fallback:** Accept a user-supplied CSV file placed at `data/cache/VIX.csv`.
- The `vix_ticker` config field is not used for Tiingo; it exists as a label only.

---

## 3. Milestone 1: Tiingo Loader (`data/tiingo_loader.py`)

### 3.1 Purpose

Download daily adjusted OHLCV data from the Tiingo REST API for a single ticker, returning a clean `pandas.DataFrame`.

### 3.2 Tiingo API Contract

**Endpoint:**
```
GET https://api.tiingo.com/tiingo/daily/{ticker}/prices
```

**Query parameters:**

| Parameter | Value |
|-----------|-------|
| `startDate` | `YYYY-MM-DD` |
| `endDate` | `YYYY-MM-DD` |
| `format` | `json` |
| `resampleFreq` | `daily` |
| `token` | `{api_key}` |

**Request headers:**
```
Content-Type: application/json
Authorization: Token {api_key}
```

**Response (JSON array of objects):**
```json
[
  {
    "date": "2008-01-02T00:00:00.000Z",
    "close": 144.93,
    "high": 147.49,
    "low": 143.88,
    "open": 146.53,
    "volume": 204091858,
    "adjClose": 104.4021,
    "adjHigh": 106.2449,
    "adjLow": 103.6458,
    "adjOpen": 105.5537,
    "adjVolume": 204091858,
    "divCash": 0.0,
    "splitFactor": 1.0
  },
  ...
]
```

### 3.3 Function Signatures

```python
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
```

```python
def load_ticker(
    ticker: str,
    config: DataConfig,
) -> pd.DataFrame:
    """
    Load ticker data with caching.

    1. If config.use_cache and cache file exists at {cache_dir}/{ticker}.csv → load from CSV.
    2. Otherwise, call fetch_ticker_from_tiingo(), save to cache, return.

    Returns same schema as fetch_ticker_from_tiingo.
    """
```

### 3.4 Implementation Requirements

1. **Retries:** On HTTP 429 (rate limit) or 5xx errors, retry up to 3 times with exponential backoff (1s, 2s, 4s). On 4xx errors other than 429, raise immediately.

2. **Date parsing:** The `date` field from Tiingo is ISO 8601 with timezone (`T00:00:00.000Z`). Parse it, strip timezone info, and convert to `datetime64[ns]` (timezone-naive). Set as index.

3. **Column selection:** From the full Tiingo response, keep only `adjOpen`, `adjClose`, `adjVolume`. Drop all other columns.

4. **Data types:** `adjOpen` and `adjClose` must be `float64`. `adjVolume` must be `int64` (or `float64` if NaN values exist).

5. **Sorting:** Ensure the DataFrame is sorted by date ascending. Verify no duplicate dates.

6. **Cache format:** CSV with columns `date,adjOpen,adjClose,adjVolume`. Date format: `YYYY-MM-DD`. File path: `{config.cache_dir}/{TICKER}.csv` (uppercase ticker).

7. **VIX special handling:**
   - VIX is NOT fetched from Tiingo.
   - Create a separate function:

```python
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
```

```python
def load_vix(config: DataConfig) -> pd.DataFrame:
    """
    Load VIX data with caching.
    Cache file: {cache_dir}/VIX.csv
    Columns: date,vix_close
    """
```

### 3.5 Milestone 1 Tests (`tests/test_tiingo_loader.py`)

**Test 1.1 — API key validation:**
```python
def test_fetch_raises_on_empty_api_key():
    with pytest.raises(ValueError, match="API key"):
        fetch_ticker_from_tiingo("SPY", "2024-01-01", "2024-01-31", api_key="")
```

**Test 1.2 — Response schema (requires live API or fixture):**
```python
def test_fetch_spy_returns_correct_schema(config_with_key):
    """Skip if TIINGO_API_KEY not set."""
    df = fetch_ticker_from_tiingo("SPY", "2024-01-02", "2024-01-31", config_with_key.tiingo_api_key)
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.name == "date"
    assert list(df.columns) == ["adjOpen", "adjClose", "adjVolume"]
    assert df.index.tz is None  # timezone-naive
    assert df["adjOpen"].dtype == np.float64
    assert df["adjClose"].dtype == np.float64
    assert len(df) > 15  # ~20 trading days in Jan
    assert df.index.is_monotonic_increasing
    assert not df.index.has_duplicates
```

**Test 1.3 — Cache write and read roundtrip:**
```python
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
    fake_data = pd.DataFrame({
        "adjOpen": [100.0, 101.0],
        "adjClose": [100.5, 101.5],
        "adjVolume": [1000000, 1100000],
    }, index=pd.DatetimeIndex(["2024-01-02", "2024-01-03"], name="date"))
    cache_path = tmp_path / "SPY.csv"
    fake_data.to_csv(cache_path)

    # Should load from cache without hitting API
    result = load_ticker("SPY", config)
    assert len(result) == 2
    assert result["adjClose"].iloc[0] == pytest.approx(100.5)
```

**Test 1.4 — VIX schema:**
```python
def test_vix_schema(config_with_key):
    """Skip if network unavailable."""
    df = fetch_vix("2024-01-02", "2024-01-31")
    assert isinstance(df.index, pd.DatetimeIndex)
    assert "vix_close" in df.columns
    assert df["vix_close"].dtype == np.float64
    assert not df["vix_close"].isna().any()  # after forward-fill
```

**Test 1.5 — Offline fixture test (always runs):**
```python
def test_load_ticker_from_fixture(tmp_path):
    """Create a fixture CSV, verify load_ticker reads it correctly."""
    # (construct CSV, write to tmp_path, configure cache_dir, call load_ticker)
```

### 3.6 Milestone 1 Acceptance

- [ ] `fetch_ticker_from_tiingo` successfully downloads SPY, MGK, SGOV, SHV for at least one month of data.
- [ ] `fetch_vix` successfully downloads VIX from FRED.
- [ ] `load_ticker` writes and reads cache files correctly.
- [ ] All 5 tests pass.
- [ ] No external dependencies beyond `requests`, `pandas`, `numpy`.

---

## 4. Milestone 2: SGOV/SHV Proxy Stitching (`data/proxies.py`)

### 4.1 Purpose

SGOV (iShares 0-3 Month Treasury Bond ETF) launched on 2020-05-26. Before that date, use SHV (iShares Short Treasury Bond ETF) as a substitute. This module stitches the two series into a single continuous `SGOV` series.

### 4.2 Stitching Logic

```
If date < cash_proxy_cutover:
    Use SHV data
Else:
    Use SGOV data
```

**Critical: There is NO level-matching or ratio adjustment.** Both SHV and SGOV track ultra-short Treasuries and trade near par. The stitch is a simple concatenation — SHV rows for the early period, SGOV rows for the later period. The cutover date row uses SGOV if available, else SHV.

**Decision:** If there is an overlap period where both SHV and SGOV have data on the same date, use SGOV for the overlapping dates.

### 4.3 Function Signature

```python
def stitch_cash_proxy(
    sgov_df: pd.DataFrame,
    shv_df: pd.DataFrame,
    cutover_date: str,
) -> pd.DataFrame:
    """
    Stitch SHV (pre-cutover) and SGOV (post-cutover) into a single cash series.

    Both inputs must have the same schema:
      Index: DatetimeIndex named 'date'
      Columns: 'adjOpen', 'adjClose', 'adjVolume'

    Returns a DataFrame with the same schema, labeled as 'SGOV'.
    The index is continuous (no gaps beyond normal market holidays).

    Raises:
      ValueError: if shv_df has no data before cutover_date
      ValueError: if sgov_df has no data on or after cutover_date
    """
```

### 4.4 Milestone 2 Tests (`tests/test_proxies.py`)

**Test 2.1 — Basic stitching:**
```python
def test_stitch_basic():
    shv = pd.DataFrame({
        "adjOpen": [50.0, 50.01, 50.02, 50.03],
        "adjClose": [50.01, 50.02, 50.03, 50.04],
        "adjVolume": [100]*4,
    }, index=pd.DatetimeIndex(["2020-05-21","2020-05-22","2020-05-26","2020-05-27"], name="date"))

    sgov = pd.DataFrame({
        "adjOpen": [100.0, 100.01],
        "adjClose": [100.01, 100.02],
        "adjVolume": [200]*2,
    }, index=pd.DatetimeIndex(["2020-05-26","2020-05-27"], name="date"))

    result = stitch_cash_proxy(sgov, shv, cutover_date="2020-05-26")

    # Pre-cutover rows come from SHV
    assert result.loc["2020-05-21", "adjClose"] == pytest.approx(50.01)
    assert result.loc["2020-05-22", "adjClose"] == pytest.approx(50.02)
    # Cutover date and after come from SGOV
    assert result.loc["2020-05-26", "adjClose"] == pytest.approx(100.01)
    assert result.loc["2020-05-27", "adjClose"] == pytest.approx(100.02)
    # No duplicate dates
    assert not result.index.has_duplicates
    assert len(result) == 4
```

**Test 2.2 — Error on missing pre-cutover data:**
```python
def test_stitch_raises_if_no_shv_before_cutover():
    # SHV only has data AFTER cutover
    shv = make_df(dates=["2020-06-01"], prices=[50.0])
    sgov = make_df(dates=["2020-05-26"], prices=[100.0])
    with pytest.raises(ValueError, match="no data before"):
        stitch_cash_proxy(sgov, shv, "2020-05-26")
```

**Test 2.3 — Error on missing SGOV data:**
```python
def test_stitch_raises_if_no_sgov_after_cutover():
    shv = make_df(dates=["2020-05-21"], prices=[50.0])
    sgov = make_df(dates=["2020-05-20"], prices=[100.0])  # All before cutover
    with pytest.raises(ValueError, match="no data on or after"):
        stitch_cash_proxy(sgov, shv, "2020-05-26")
```

**Test 2.4 — Index continuity:**
```python
def test_stitch_sorted_no_duplicates():
    # Use realistic date ranges
    shv = make_df(dates=pd.bdate_range("2008-01-02", "2020-05-25"), ...)
    sgov = make_df(dates=pd.bdate_range("2020-05-26", "2025-12-31"), ...)
    result = stitch_cash_proxy(sgov, shv, "2020-05-26")
    assert result.index.is_monotonic_increasing
    assert not result.index.has_duplicates
```

### 4.5 Milestone 2 Acceptance

- [ ] Stitched SGOV series runs from ~2007-06 through 2025-12 with no gaps or duplicates.
- [ ] Pre-cutover rows have SHV prices; post-cutover rows have SGOV prices.
- [ ] All 4 tests pass.

---

## 5. Milestone 3: Risk-Free Rate (`data/risk_free.py`)

### 5.1 Purpose

Load the daily risk-free rate (1-month T-bill return) from the Kenneth French Data Library. This rate is used for Sharpe ratio calculations, excess return computation, and borrowing costs.

### 5.2 Data Source

**URL:** `https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_daily_CSV.zip`

This ZIP file contains a CSV with daily Fama-French factors. The format is:

```
(header lines to skip — variable number, look for the first line that starts with a date-like number)
      Mkt-RF      SMB      HML       RF
19260701    0.10   -0.24   -0.28    0.009
19260702    0.45   -0.32   -0.08    0.009
...
```

The `RF` column contains the **daily** risk-free rate as a **percentage** (e.g., `0.009` means 0.009%, i.e., 0.00009 as a decimal).

### 5.3 Function Signatures

```python
def fetch_french_rf(url: str) -> pd.DataFrame:
    """
    Download and parse the daily risk-free rate from Kenneth French's data library.

    Steps:
      1. Download the ZIP file from `url`.
      2. Extract the CSV from inside the ZIP.
      3. Parse the CSV:
         - Skip header lines until the first row where the first column is a valid 8-digit date (YYYYMMDD).
         - Stop parsing when a non-numeric first column is encountered (indicates a footer section).
         - Extract the 'RF' column.
      4. Convert the date column (YYYYMMDD int) to DatetimeIndex.
      5. Convert RF from percentage to decimal: RF_decimal = RF_pct / 100.

    Returns a DataFrame with:
      Index: DatetimeIndex named 'date' (timezone-naive)
      Columns: 'rf' (float64, daily return as decimal, e.g., 0.00009)
    """
```

```python
def load_rf(config: DataConfig) -> pd.DataFrame:
    """
    Load risk-free rate with caching.
    Cache file: {cache_dir}/RF_DAILY.csv
    Columns: date,rf
    """
```

### 5.4 Parsing Details

The French CSV is notoriously tricky to parse. Here is the exact algorithm:

```python
# 1. Download ZIP, extract first file inside it
# 2. Read the raw text lines
# 3. Find the first line where stripped_line[:8] is all digits → that's the data start
# 4. Read from that line onward until a line doesn't start with digits → that's data end
# 5. Parse the data lines as fixed-width or comma-separated (the file uses variable whitespace)
#    Columns: date_int, Mkt-RF, SMB, HML, RF
# 6. The date_int is YYYYMMDD (e.g., 20080102)
# 7. Convert date_int to datetime
# 8. Keep only the RF column
# 9. Divide by 100 to get decimal returns
```

**Decision:** Use `pd.read_csv` with `skiprows` and `nrows` determined by scanning the file, or use `io.StringIO` on the extracted lines. Either approach is acceptable. The important thing is that it handles the variable header/footer reliably.

### 5.5 Milestone 3 Tests (`tests/test_risk_free.py`)

**Test 3.1 — Fixture-based parsing:**
```python
def test_parse_french_rf_from_fixture(tmp_path):
    """Create a minimal French-format CSV, verify parsing."""
    raw = """
This file was created by CMPT_ME_BEME_RETS using the 202401 CRSP database.

      Mkt-RF      SMB      HML       RF
20080102    -1.53    0.04    0.26    0.020
20080103    -0.35    0.19    0.05    0.020
20080104    -2.19    0.12   -0.24    0.020


 Annual Factors: January-December
     Mkt-RF      SMB      HML       RF
2008   -38.28    1.56    5.75    1.58
"""
    # Write to a ZIP file, call fetch_french_rf, verify output
    # Expected: 3 rows, RF = [0.00020, 0.00020, 0.00020]
```

**Test 3.2 — RF values are reasonable:**
```python
def test_rf_values_are_reasonable():
    """Skip if no network. RF should be between 0 and 0.001 daily (~0% to ~25% annual)."""
    df = fetch_french_rf(DataConfig().french_rf_url)
    assert (df["rf"] >= 0).all()
    assert (df["rf"] < 0.001).all()  # < 25% annualized
    assert len(df) > 20000  # ~95 years of daily data
```

**Test 3.3 — Date format:**
```python
def test_rf_date_format():
    df = fetch_french_rf(DataConfig().french_rf_url)
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.name == "date"
    assert df.index[0].year >= 1926  # French data starts 1926
```

**Test 3.4 — Cache roundtrip:**
```python
def test_rf_cache_roundtrip(tmp_path):
    # Similar to Test 1.3: write a fake cached RF CSV, verify load_rf reads it.
```

### 5.6 Milestone 3 Acceptance

- [ ] `fetch_french_rf` correctly parses the ZIP file and extracts daily RF as decimal.
- [ ] RF values are non-negative and plausible (0 to ~0.03% daily).
- [ ] The date index starts in 1926, is monotonically increasing, and has no duplicates.
- [ ] Cache works correctly.
- [ ] All 4 tests pass.

---

## 6. Milestone 4: Pipeline Orchestrator (`data/pipeline.py`)

### 6.1 Purpose

This is the top-level module that calls the loader, proxy, and RF modules to produce a single, aligned, validated `pandas.DataFrame` (or a dict of DataFrames) ready for the strategy engine in Phase 2+.

### 6.2 Output Schema

The pipeline produces two objects:

**Object 1: `price_data` — A single DataFrame (multi-column, single DatetimeIndex)**

```
Index: DatetimeIndex named 'date' (trading days only, 2008-01-02 to end_date)

Columns (MultiIndex level 0 = ticker, level 1 = field):
  ('SPY', 'adjOpen')     float64
  ('SPY', 'adjClose')    float64
  ('MGK', 'adjOpen')     float64
  ('MGK', 'adjClose')    float64
  ('SGOV', 'adjOpen')    float64    # SHV pre-cutover, SGOV post-cutover
  ('SGOV', 'adjClose')   float64    # SHV pre-cutover, SGOV post-cutover
  ('VIX', 'close')       float64    # VIX close (signal only, not traded)
```

**Object 2: `rf_data` — A single-column DataFrame**

```
Index: DatetimeIndex named 'date' (aligned to same trading days as price_data)
Columns: 'rf' (float64, daily decimal return)
```

### 6.3 Function Signature

```python
def build_dataset(config: DataConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Build the complete aligned dataset for the TRETE backtest.

    Steps:
      1. Load SPY, MGK from Tiingo (load_ticker)
      2. Load SGOV and SHV from Tiingo (load_ticker)
      3. Stitch SGOV/SHV (stitch_cash_proxy)
      4. Load VIX (load_vix)
      5. Load RF (load_rf)
      6. Align all series to a common date index (intersection of trading days)
      7. Trim to [backtest_start_date - warm_up_buffer, end_date]
         where warm_up_buffer = 120 trading days (extra safety beyond the 54-day minimum)
      8. Validate (see §6.4)
      9. Return (price_data, rf_data)

    Raises:
      ValueError: if any validation check fails
    """
```

### 6.4 Validation Checks

The pipeline must perform all of the following checks and raise `ValueError` with a descriptive message if any fail:

| # | Check | Condition |
|---|-------|-----------|
| V1 | No NaN in adjClose | `price_data[ticker, 'adjClose'].isna().sum() == 0` for all tickers |
| V2 | No NaN in adjOpen | `price_data[ticker, 'adjOpen'].isna().sum() == 0` for SPY, MGK, SGOV |
| V3 | Positive prices | `(price_data[ticker, col] > 0).all()` for all price columns |
| V4 | Reasonable daily returns | For SPY and MGK: `abs(close-to-close return) < 0.25` for all days (no single-day >25% move; would indicate bad data) |
| V5 | VIX range | `(price_data['VIX', 'close'] > 5).all() and (price_data['VIX', 'close'] < 100).all()` (VIX has never been outside this range historically) |
| V6 | RF aligned | RF must have data for every trading day in price_data. Forward-fill up to 5 days to cover minor gaps. Raise if gap > 5 days. |
| V7 | Date coverage | price_data must have data from at least 2007-09-01 (needed for warm-up before 2008-01-02 backtest start) |
| V8 | Minimum row count | `len(price_data) >= 4000` (roughly 17+ years of trading days) |
| V9 | adjOpen ≤ adjClose OR adjOpen ≥ adjClose | This is NOT a check — Open can be above or below Close. This note is here to prevent an erroneous check. |
| V10 | No duplicate dates | `price_data.index.has_duplicates == False` |

### 6.5 Alignment Logic

Different tickers may have slightly different trading calendars (e.g., early closures, different listing dates). The alignment uses an **inner join** on dates:

```python
common_dates = spy.index.intersection(mgk.index).intersection(sgov_stitched.index)
```

VIX may have additional days that equities don't (or vice versa). Forward-fill VIX for up to 2 days to cover minor mismatches, then inner-join with the equity dates.

RF from French extends much further back than needed. Simply reindex to `common_dates` and forward-fill gaps up to 5 days.

### 6.6 Derived Columns (Computed at Pipeline Time)

In addition to the raw prices, compute and include these derived columns for downstream convenience:

```python
# Close-to-close daily returns (used for volatility, sizing, performance)
for ticker in ['SPY', 'MGK', 'SGOV']:
    price_data[(ticker, 'ret_cc')] = price_data[(ticker, 'adjClose')].pct_change()

# Open-to-close return (used for entry-day partial return)
for ticker in ['SPY', 'MGK', 'SGOV']:
    price_data[(ticker, 'ret_oc')] = (
        price_data[(ticker, 'adjClose')] / price_data[(ticker, 'adjOpen')] - 1
    )

# Close-to-open return (next day gap, used for exit-day slippage)
for ticker in ['SPY', 'MGK', 'SGOV']:
    price_data[(ticker, 'ret_co')] = (
        price_data[(ticker, 'adjOpen')].shift(-1) / price_data[(ticker, 'adjClose')] - 1
    )
```

This means the final `price_data` columns per ticker are: `adjOpen`, `adjClose`, `ret_cc`, `ret_oc`, `ret_co`.

### 6.7 Milestone 4 Tests (`tests/test_pipeline.py`)

**Test 4.1 — Schema validation:**
```python
def test_pipeline_output_schema():
    """Skip if no API key/network. Run full pipeline, check output shapes."""
    config = DataConfig()
    price_data, rf_data = build_dataset(config)

    # Check structure
    assert isinstance(price_data.columns, pd.MultiIndex)
    assert set(price_data.columns.get_level_values(0).unique()) == {"SPY", "MGK", "SGOV", "VIX"}

    # Check fields per equity ticker
    for ticker in ["SPY", "MGK", "SGOV"]:
        assert (ticker, "adjOpen") in price_data.columns
        assert (ticker, "adjClose") in price_data.columns
        assert (ticker, "ret_cc") in price_data.columns
        assert (ticker, "ret_oc") in price_data.columns
        assert (ticker, "ret_co") in price_data.columns

    # VIX has close only
    assert ("VIX", "close") in price_data.columns

    # RF
    assert "rf" in rf_data.columns
    assert price_data.index.equals(rf_data.index)
```

**Test 4.2 — No NaN in critical columns:**
```python
def test_pipeline_no_nans_in_prices():
    config = DataConfig()
    price_data, rf_data = build_dataset(config)

    for ticker in ["SPY", "MGK", "SGOV"]:
        # First row of ret_cc will be NaN (pct_change), skip it
        assert price_data[(ticker, "adjClose")].isna().sum() == 0
        assert price_data[(ticker, "adjOpen")].isna().sum() == 0
        # ret_cc has NaN on first row only
        assert price_data[(ticker, "ret_cc")].isna().sum() <= 1

    assert rf_data["rf"].isna().sum() == 0
```

**Test 4.3 — Date range:**
```python
def test_pipeline_date_range():
    config = DataConfig()
    price_data, _ = build_dataset(config)
    assert price_data.index[0] <= pd.Timestamp("2007-10-01")  # warm-up period present
    assert price_data.index[-1] >= pd.Timestamp("2025-12-30")
```

**Test 4.4 — Return calculations are correct:**
```python
def test_return_calculations():
    config = DataConfig()
    price_data, _ = build_dataset(config)

    # Check ret_cc manually for SPY on a known row
    spy_close = price_data[("SPY", "adjClose")]
    expected_ret = spy_close.iloc[10] / spy_close.iloc[9] - 1
    actual_ret = price_data[("SPY", "ret_cc")].iloc[10]
    assert actual_ret == pytest.approx(expected_ret, rel=1e-10)

    # Check ret_oc
    spy_open = price_data[("SPY", "adjOpen")]
    expected_oc = spy_close.iloc[10] / spy_open.iloc[10] - 1
    actual_oc = price_data[("SPY", "ret_oc")].iloc[10]
    assert actual_oc == pytest.approx(expected_oc, rel=1e-10)
```

**Test 4.5 — Validation catches bad data:**
```python
def test_pipeline_validation_catches_negative_price(monkeypatch):
    """Inject a negative price into the cache, verify pipeline raises."""
    # (create fixture with a negative adjClose, expect ValueError)
```

**Test 4.6 — Offline full test with fixtures:**
```python
def test_pipeline_with_fixtures(tmp_path):
    """
    Create minimal fixture CSVs for SPY, MGK, SHV, SGOV, VIX, RF.
    Run build_dataset with cache pointing to fixtures.
    Verify output schema, alignment, and return calculations.
    This test always runs (no network needed).
    """
```

### 6.8 Milestone 4 Acceptance

- [ ] `build_dataset` produces correctly structured multi-index DataFrame.
- [ ] All tickers aligned to common date index with no NaN in price columns.
- [ ] Derived return columns (`ret_cc`, `ret_oc`, `ret_co`) are mathematically correct.
- [ ] All 10 validation checks from §6.4 are enforced.
- [ ] RF is aligned and forward-filled.
- [ ] VIX is included and forward-filled for minor gaps.
- [ ] All 6 tests pass.

---

## 7. Test Infrastructure (`tests/conftest.py`)

### 7.1 Shared Fixtures

```python
import pytest
import os
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
```

### 7.2 Test Categories

Tests are marked for selective execution:

```python
# In conftest.py:
def pytest_configure(config):
    config.addinivalue_line("markers", "network: requires network access")
    config.addinivalue_line("markers", "offline: runs without network")
```

Usage:
```
pytest -m offline          # Run only offline tests (fixtures)
pytest -m network          # Run only network tests (live API)
pytest                     # Run all
```

---

## 8. Summary: Milestones and Acceptance Gates

| Milestone | Module | Key Deliverables | Tests | Gate |
|-----------|--------|------------------|-------|------|
| **M1** | `tiingo_loader.py`, `config.py` | Tiingo fetcher + VIX fetcher + caching | 5 tests | Can download SPY, MGK, SGOV, SHV, VIX. Cache works. |
| **M2** | `proxies.py` | SHV→SGOV stitching | 4 tests | Stitched SGOV series is continuous from 2007 to 2025. |
| **M3** | `risk_free.py` | French RF parser + caching | 4 tests | Daily RF extracted, in decimal, plausible values. |
| **M4** | `pipeline.py` | Full dataset builder + validation + derived returns | 6 tests | Single call produces a validated, aligned, multi-index DataFrame ready for Phase 2. |

**Total: 19 tests across 4 milestones.**

### Build Order

```
M1 → M2 → M3 → M4
         ↘        ↗
          M3 ────┘
```

M1 must be complete first (it provides the ticker loader). M2 and M3 can be done in parallel. M4 depends on all three.

---

## 9. Edge Cases and Pitfalls

| # | Pitfall | Guidance |
|---|---------|----------|
| 1 | Tiingo returns timezone-aware dates (`T00:00:00.000Z`) | Strip timezone immediately after parsing. Use `pd.to_datetime(...).tz_localize(None)`. |
| 2 | French CSV has variable-width whitespace, not commas | Use `pd.read_csv(..., sep='\s+')` or `delim_whitespace=True` on the extracted text. |
| 3 | French ZIP contains a single CSV with a name that varies by vintage | Extract the first file from the ZIP regardless of its name. |
| 4 | FRED VIX CSV uses `.` for missing values | Pass `na_values=['.']` to `pd.read_csv`. Then forward-fill. |
| 5 | SHV has very low volume and may have zero-volume days | Do not filter by volume. Zero-volume days still have valid prices. |
| 6 | SGOV may have NaN adjOpen on its first trading day | If adjOpen is NaN but adjClose is valid, set adjOpen = adjClose for that day. Log a warning. |
| 7 | Market holidays differ between US equities and VIX | VIX is computed from options, which trade on the same calendar as equities. Minor mismatches are possible and handled by forward-fill. |
| 8 | The `ret_co` (close-to-open) column will have NaN on the last row | This is expected because it references `adjOpen.shift(-1)`. Document and leave as NaN. |
| 9 | Tiingo free tier has rate limits (~500 requests/hour for EOD) | We only make 4 requests (SPY, MGK, SGOV, SHV). Caching ensures repeat runs don't re-fetch. |
| 10 | French factor file header has inconsistent quoting/spacing across vintages | Scan for the first all-numeric date line rather than relying on a fixed `skiprows` count. |

---

## 10. What Phase 2 Expects

Phase 2 (Indicators) will import `build_dataset` and use the output directly:

```python
from data.pipeline import build_dataset
from config import DataConfig

config = DataConfig()
price_data, rf_data = build_dataset(config)

# Phase 2 then computes:
spy_close = price_data[("SPY", "adjClose")]   # For EMA, Donchian, Keltner
spy_open  = price_data[("SPY", "adjOpen")]     # For execution price
spy_ret   = price_data[("SPY", "ret_cc")]      # For rolling volatility (sizing)
vix       = price_data[("VIX", "close")]        # For MR overlay filters
rf        = rf_data["rf"]                        # For Sharpe, excess returns
```

The data pipeline is **complete and self-contained** — downstream phases should never need to call Tiingo, parse French data, or stitch proxies. If `build_dataset` returns without error, the data is clean and ready.
