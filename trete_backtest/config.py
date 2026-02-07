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
