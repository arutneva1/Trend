# Software Requirements Specification (SRS)
# Trend & Reversion ETF Timing Engine (TRETE)

**Based on:**
- Core methodology: "A Century of Profitable Trends" by Zarattini & Antonacci (2025 Dow Award)
- Instrument universe & regime logic: SMURF model by Gary Antonacci (Optimal Momentum)

**Version:** 3.0
**Date:** February 7, 2026

---

## 1. Executive Summary

This document specifies a Python backtesting engine that applies the Keltner/Donchian channel breakout methodology from the Timing Industry paper to a focused pair of U.S. equity ETFs — SPY and MGK. The system implements a **regime-switching** framework: when equity trends are positive, capital is allocated to stock ETFs using trend-following entries with mean-reversion timing refinements; when equity trends are negative, capital parks entirely in SGOV (short-term Treasury bills). All signals are computed from **Close prices**; all trades execute at the **next-day Open price**. The engine tracks all positions, computes performance analytics, and benchmarks against buy-and-hold SPY and a static 60/40 portfolio.

---

## 2. Design Philosophy

The original Timing Industry paper applies a single trend-following system uniformly across 48 industries. This adaptation narrows the universe to two equity ETFs but deepens the signal toolkit by layering mean-reversion entry/exit refinements onto the same core channel-breakout engine:

1. **Trend-following** (primary driver): Keltner/Donchian breakouts on SPY and MGK, exactly as specified in the Timing Industry paper.
2. **Mean-reversion overlay** (entry/exit refinement): VIX-based and price-based overbought/oversold filters that time entries and exits within uptrends — selling strength and buying weakness.
3. **Binary regime switch**: When neither SPY nor MGK is in an uptrend, 100% of capital defaults to SGOV.

This is a pure equity-timing system: you are either in stocks or in T-bills. There are no bond trades, no calendar anomaly trades, and no satellite positions.

---

## 3. Execution Model

All signal computation and trade execution follows a strict **signal-on-close / execute-on-open** convention to eliminate look-ahead bias:

```
Day t (Close):
  - All indicators (EMA, Donchian, Keltner, VIX) are computed using Close prices through day t
  - Entry, exit, and sizing decisions are determined
  - Target weights for day t+1 are locked

Day t+1 (Open):
  - All trades are executed at the Open price of day t+1
  - New positions are entered at Open[t+1]
  - Exited positions are closed at Open[t+1]
  - Weight adjustments (rebalances) execute at Open[t+1]
```

**Key implications:**

1. **Signals always use Close prices.** Every indicator — EMA, Donchian highs/lows, Keltner bands, AvgAbsChange, rolling volatility, VIX level — is computed from Close price data only. Open, High, and Low prices are never used for signal generation.

2. **Execution always at next-day Open.** When a signal fires at Close of day t, the trade executes at Open of day t+1. This means the position is exposed to overnight gap risk between the signal (Close[t]) and execution (Open[t+1]).

3. **Return accounting.** Once a position is entered at Open[t+1], the first return captured is from Open[t+1] to Open[t+2] (or equivalently, the position is marked at Close[t+1] for end-of-day P&L). See §9 for detailed return calculation.

4. **Data requirement.** Both Close and Open prices are required for all traded instruments (SPY, MGK, SGOV). VIX requires Close only (not traded).

5. **Trailing stop evaluation.** The trailing stop is evaluated against Close[t]. If `Close[t] < TrailingStop[t]`, the exit executes at Open[t+1]. The slippage between Close[t] and Open[t+1] (gap risk) is naturally captured in the backtest.

6. **Sizing uses Close-of-day data.** Volatility (σ) and target weights are computed from close-to-close returns at end of day t. The resulting dollar allocation is then converted to share counts using Open[t+1] prices.

---

## 4. Instrument Universe

### 4.1 Equity Instruments

| Ticker | Name | Role | Rationale |
|--------|------|------|-----------|
| SPY | SPDR S&P 500 ETF Trust | Core equity, broad market | Large, liquid benchmark exposure |
| MGK | Vanguard Mega Cap Growth ETF | Core equity, growth tilt | Mega-cap growth has delivered superior risk-adjusted returns with trend-following |

### 4.2 Cash Instrument

| Ticker | Name | Role | Rationale |
|--------|------|------|-----------|
| SGOV | iShares 0-3 Month Treasury Bond ETF | Default position when no equity trend | Near-zero volatility; holds T-bills; actual tradable instrument |

### 4.3 Volatility Signal

| Ticker | Name | Role |
|--------|------|------|
| ^VIX (CBOE VIX) | CBOE Volatility Index | Implied volatility input for mean-reversion entry/exit filters |

### 4.4 Data Requirements

| Item | Source | Fields | Period |
|------|--------|--------|--------|
| SPY, MGK prices | Tiingo REST API | Date, **adjOpen**, **adjClose**, Volume | Jan 2008 – present |
| SGOV prices | Tiingo REST API | Date, **adjOpen**, **adjClose** | May 2020 – present (use SHV as proxy before that) |
| VIX | Tiingo REST API (or CBOE) | Date, Close | Jan 2008 – present |
| Risk-free rate | Kenneth French daily RF or FRED (DGS1MO) | Daily rate | Jan 2008 – present |

**Data source notes:**
- **Tiingo** (https://api.tiingo.com) is the primary data provider. Requires an API key (free tier available). Use the `/tiingo/daily/<ticker>/prices` endpoint for adjusted OHLCV data.
- **Open prices are required** for all traded instruments (SPY, MGK, SGOV/SHV) because all trade executions occur at the next-day Open (see §3). VIX requires Close only (signal input, not traded).
- **Adjustment:** Use Tiingo's split- and dividend-adjusted fields (`adjOpen`, `adjClose`) for both Open and Close to ensure consistency. Verify that the adjustment factor is applied uniformly to both prices on the same day.

**Proxy rules:**
- SGOV launched May 2020. Before that date, use SHV (iShares Short Treasury Bond ETF) as a proxy, as noted in the SALT documentation.
- MGK launched Dec 2007. Backtest starts Jan 2008 to ensure all core instruments are available.

---

## 5. Model Architecture

The system operates a single signal engine with a binary regime switch:

```
┌─────────────────────────────────────────────┐
│            PORTFOLIO ALLOCATOR               │
│  Converts trend signals into daily weights   │
└─────────────────────┬───────────────────────┘
                      │
             ┌────────▼────────┐
             │  TREND ENGINE   │
             │  SPY & MGK      │
             │                 │
             │  Signals: Close │
             │  Execution: Open│
             │                 │
             │  Entry: Channel │
             │    breakout     │
             │  Exit: Trailing │
             │    stop         │
             │  + MR overlays  │
             │    (sell        │
             │    strength,    │
             │    buy weakness)│
             └────────┬────────┘
                      │
              ┌───────▼───────┐
              │ REGIME SWITCH │
              │               │
              │ Trend ON  →   │
              │   SPY/MGK     │
              │   (vol-target │
              │    sized)     │
              │               │
              │ Trend OFF →   │
              │   100% SGOV   │
              └───────────────┘
```

---

## 6. Trend-Following Engine (SPY & MGK)

This engine applies the Timing Industry paper's methodology, adapted to two assets instead of 48. All signal computations use **Close prices**; all resulting trades execute at the **next-day Open**.

### 6.1 Entry Signal

A long entry signal is generated for asset `j ∈ {SPY, MGK}` at Close of day `t` when:

```
Close[t, j] >= UpperBand[t-1, j]
```

The trade executes at **Open[t+1, j]**.

Where:

```
UpperBand[t, j] = min(DonchianUp[t,j](20), KeltnerUp[t,j](20, 2))
```

#### 6.1.1 Donchian Upper Band

```
DonchianUp[t,j](n) = max(Close[t,j], Close[t-1,j], ..., Close[t-n+1,j])
```
- Lookback: n = 20 days
- Computed from Close prices only

#### 6.1.2 Adapted Keltner Upper Band

```
KeltnerUp[t,j](n, k) = EMA(Close, n)[t,j] + 1.4 × k × AvgAbsChange(n)[t,j]
```

Where:
- `EMA(Close, n)` = n-day exponential moving average of **Close** prices
- `AvgAbsChange(n)[t,j] = (1/n) × Σ_{i=0}^{n-1} |Close[t-i,j] - Close[t-i-1,j]|`
- Factor 1.4 approximates ATR / average absolute change ratio
- n = 20, k = 2

### 6.2 Exit Signal (Trailing Stop)

An exit signal is generated at Close of day `t` when:

```
Close[t, j] < LongTrailingStop[t, j]
```

The exit executes at **Open[t+1, j]**.

The trailing stop never moves down:

```
LongTrailingStop[t+1, j] = max(LongTrailingStop[t, j], LowerBand[t, j])
```

Where:

```
LowerBand[t, j] = max(DonchianDown[t,j](40), KeltnerDown[t,j](40, 2))
```

#### 6.2.1 Donchian Lower Band
```
DonchianDown[t,j](n) = min(Close[t,j], Close[t-1,j], ..., Close[t-n+1,j])
```
- Lookback: n = 40 days
- Computed from Close prices only

#### 6.2.2 Adapted Keltner Lower Band
```
KeltnerDown[t,j](n, k) = EMA(Close, n)[t,j] - 1.4 × k × AvgAbsChange(n)[t,j]
```
- n = 40, k = 2

### 6.3 Mean-Reversion Exit Override (Sell Strength)

SMURF exits when prices "rise sharply, indicating possible mean reversion." We implement this as an overbought filter that temporarily exits a trend position:

```
Overbought_Exit[t, j] =
    (Close[t,j] - EMA(Close, 20)[t,j]) / EMA(Close, 20)[t,j] > Overbought_Threshold
    AND VIX_Close[t] < VIX_Low_Threshold
```

The exit executes at **Open[t+1, j]**. Capital moves to SGOV.

**Parameters (configurable):**
- `Overbought_Threshold`: default = 0.06 (price 6% above 20-day EMA)
- `VIX_Low_Threshold`: default = 14 (low VIX confirms complacency)

When the overbought exit triggers, the position is temporarily closed. Re-entry occurs when the overbought condition clears (Close retreats below the threshold) AND the standard trend signal is still valid or Close remains above the LowerBand. If Close falls below the trailing stop during the temporary exit, the position is fully closed and requires a fresh UpperBand breakout to re-enter.

### 6.4 Mean-Reversion Entry Enhancement (Buy Weakness)

SMURF "buys weakness within uptrends." When the trend is positive but price has pulled back sharply, a re-entry is triggered earlier than a fresh Donchian/Keltner breakout would require:

```
MR_Reentry[t, j] =
    Trend_Was_Active_Recently[j]             # Position was closed by overbought exit or minor stop
    AND Close[t,j] > LowerBand[t,j]          # Not a full trend breakdown
    AND (EMA(Close,20)[t,j] - Close[t,j]) / EMA(Close,20)[t,j] > Oversold_Threshold
    AND VIX_Close[t] > VIX_Elevated_Threshold # Fear spike = reversion opportunity
```

The re-entry executes at **Open[t+1, j]**.

**Parameters (configurable):**
- `Oversold_Threshold`: default = 0.04 (price 4% below 20-day EMA)
- `VIX_Elevated_Threshold`: default = 25
- `Trend_Recent_Window`: default = 10 days (trend was active within last 10 days)

This gives a mechanism to re-enter positions during pullbacks within uptrends without waiting for a full channel breakout, consistent with SMURF's "buying weakness and selling strength within uptrends."

### 6.5 Position State Machine

Each equity asset independently tracks one of four states:

```
                    UpperBand breakout (Close[t])
                    → execute at Open[t+1]
        ┌──────┐  ──────────────────────────►  ┌──────────┐
        │ FLAT │                                │  IN TREND │
        └──┬───┘  ◄────────────────────────── └──┬───┬───┘
           │        Trailing stop hit (Close[t])   │   │
           │        → exit at Open[t+1]            │   │
           │                                       │   │
           │    MR re-entry (Close[t])             │   │ Overbought exit
           │    → enter at Open[t+1]               │   │ (Close[t])
           │  ◄────────────────────────────────────┘   │ → exit at Open[t+1]
           │                                           │
           │                                    ┌──────▼──────┐
           │                                    │  TEMP EXIT   │
           │                                    │ (in SGOV)    │
           │  ◄─────────────────────────────── └──────────────┘
           │    Trailing stop hit during temp exit
           │    → remains FLAT (already out)
```

**FLAT**: No position. Requires UpperBand breakout (evaluated at Close) to enter (at next Open).
**IN TREND**: Active long position. Subject to trailing stop and overbought exit (both evaluated at Close, executed at next Open).
**TEMP EXIT**: Trend is still structurally intact but position temporarily closed due to overbought condition. Can re-enter via MR re-entry or standard breakout. Falls back to FLAT if trailing stop is breached during temp exit.

---

## 7. Regime Switching & Portfolio Allocation

### 7.1 Regime Definition

```
EQUITY_REGIME:  active when SPY OR MGK has a live trend-following position (state = IN TREND)
CASH_REGIME:    active when NEITHER SPY nor MGK has a live position (both FLAT or TEMP EXIT)
```

### 7.2 Capital Allocation

| Regime | SPY Weight | MGK Weight | SGOV Weight |
|--------|-----------|-----------|-------------|
| Both trends active | vol-target sized | vol-target sized | remainder to 100% |
| SPY trend only | vol-target sized | 0% | remainder to 100% |
| MGK trend only | 0% | vol-target sized | remainder to 100% |
| No trend (cash regime) | 0% | 0% | 100% |

There are no satellite positions, no bond trades, and no calendar trades. The portfolio is always fully invested — either in equities (vol-target sized) with the residual in SGOV, or 100% in SGOV.

### 7.3 Expected Regime Distribution

Per SMURF: ~63% of time in equities, ~37% in cash/SGOV.

---

## 8. Position Sizing

### 8.1 Volatility Target

Identical methodology to the Timing Industry paper, adapted for N = 2 equity assets:

```
σ_target_per_position = Portfolio_Vol_Target / N_equity
w_v[j,t] = σ_target_per_position / σ[j,t]
```

Where:
- `Portfolio_Vol_Target`: default = 0.015 (1.5% daily, same as the paper)
- `N_equity` = 2 (SPY and MGK)
- `σ[j,t]` = 14-day rolling standard deviation of **close-to-close daily returns** for asset j, computed at Close of day t

If only one equity position is active (e.g., SPY trend on, MGK trend off), that single position gets the full vol budget: `w = Portfolio_Vol_Target / σ[j,t]`.

### 8.2 Leverage Cap

```
Total_Equity_Weight = Σ w_v[j,t]  for active positions
If Total_Equity_Weight > Max_Leverage:
    w*[j,t] = w_v[j,t] / Total_Equity_Weight × Max_Leverage
Else:
    w*[j,t] = w_v[j,t]
```

Default `Max_Leverage` = 1.0 (100%, no leverage). Configurable up to 2.0 to match the original paper.

### 8.3 SGOV Residual

```
w_SGOV[t] = max(0, 1.0 - Σ w*[j,t])
```

If leverage is enabled and equity weights exceed 100%, SGOV weight is 0 and the excess is financed at the risk-free borrowing rate.

### 8.4 Weight-to-Shares Conversion

Target weights are computed at Close of day t using Close prices. On day t+1, share counts are determined at Open[t+1]:

```
Target_Dollar_Value[j] = w*[j,t] × AUM[t]
Target_Shares[j] = floor(Target_Dollar_Value[j] / Open[t+1, j])
```

This means the actual realized weight may differ slightly from the target due to rounding and the gap between Close[t] and Open[t+1].

---

## 9. Portfolio Return Calculation

### 9.1 Daily Return

Because trades execute at Open and signals are evaluated at Close, the portfolio return on day t reflects holding from Open[t] to Open[t+1] (or equivalently, capturing the close-to-close return on the shares held, minus any execution slippage at the Open):

**Simplified model (weight-based, assuming small Close-to-Open gaps):**

```
R_portfolio[t] = Σ_j (w[j,t-1] × R_close[j,t])  +  w_SGOV[t-1] × R_close_SGOV[t]
```

Where `R_close[j,t] = Close[t,j] / Close[t-1,j] - 1` (standard close-to-close return).

**Precise model (Open-execution aware):**

On days when positions change (entry, exit, or rebalance), the return is split:

```
# For a NEW entry on day t (signal at Close[t-1], execute at Open[t]):
R_entry_day[j,t] = Close[t,j] / Open[t,j] - 1    # Only captures Open-to-Close

# For an EXIT on day t (signal at Close[t-1], execute at Open[t]):
R_exit_day[j,t] = Open[t,j] / Close[t-1,j] - 1   # Captures Close-to-Open gap (slippage)

# For HELD positions (no change):
R_held[j,t] = Close[t,j] / Close[t-1,j] - 1       # Full close-to-close
```

The precise model should be used as the default. The simplified model is available as a configuration option for comparison.

### 9.2 Borrowing Cost (If Leveraged)

If `Max_Leverage > 1.0` and total equity weight exceeds 100%:

```
Borrowing_Cost[t] = (Σ w[j,t-1] - 1) × RF[t]
R_portfolio[t] -= Borrowing_Cost[t]
```

### 9.3 Transaction Costs

Applied when any position changes (at Open of execution day):

```
Cost[t] = Σ_j |ΔShares[j,t]| × Open[t,j] × Cost_Rate
```

Or equivalently in weight terms:
```
Cost[t] = Σ_j |Δw[j,t]| × AUM[t] × Cost_Rate
```

**Parameters:**
- `Cost_Rate`: default = 0.0005 (5 basis points per transaction, representing commission + half-spread for liquid ETFs)
- Alternative: per-share model (see §9.4)

### 9.4 Per-Share Cost Model (Optional)

For more realistic modeling with a fixed dollar portfolio:

| Parameter | Default |
|-----------|---------|
| `cost_per_share` | $0.0035 |
| `min_cost_per_trade` | $0.35 |
| `starting_capital` | $100,000 |

---

## 10. System Architecture

### 10.1 Module Decomposition

```
trete_backtest/
│
├── data/
│   ├── tiingo_loader.py        # Download/load SPY, MGK, SGOV, VIX from Tiingo API
│   ├── proxies.py              # SHV→SGOV proxy stitching, data alignment
│   └── risk_free.py            # RF rate loader (French or FRED)
│
├── indicators/
│   ├── ema.py                  # Exponential Moving Average (computed on Close)
│   ├── donchian.py             # Donchian Channel upper/lower (computed on Close)
│   ├── keltner.py              # Adapted Keltner Channel upper/lower (computed on Close)
│   ├── bands.py                # UpperBand, LowerBand combiners
│   └── trailing_stop.py        # Trailing stop logic (evaluated on Close, never moves down)
│
├── signals/
│   ├── trend.py                # Keltner/Donchian entry/exit for SPY, MGK (Close-based)
│   ├── mr_overlay.py           # Overbought exit + oversold re-entry logic (Close + VIX)
│   ├── vix_filter.py           # VIX-based overbought/oversold filters (Close)
│   ├── state_machine.py        # FLAT / IN_TREND / TEMP_EXIT per asset
│   └── regime.py               # Regime switch logic (equity vs cash)
│
├── strategy/
│   ├── sizing.py               # Volatility-target sizing (Close-based vol, Open-based shares)
│   ├── allocator.py            # Combine signals → daily weight vector (SPY, MGK, SGOV)
│   ├── portfolio.py            # Daily P&L with Open-execution returns, cost deduction
│   └── rebalance.py            # Rebalance threshold filter
│
├── analytics/
│   ├── performance.py          # CAGR, vol, Sharpe, Sortino, UPI, MDD, hit ratios
│   ├── regression.py           # Alpha/beta, upside/downside beta
│   ├── trade_stats.py          # Per-trade: count, duration, avg return, win rate, P/L ratio
│   ├── regime_stats.py         # Time in equities vs cash, regime-conditional performance
│   └── reporting.py            # Formatted summary tables, monthly return matrix
│
├── visualization/
│   ├── equity_curve.py         # Log-scale equity curves vs benchmarks
│   ├── drawdown.py             # Rolling max drawdown (strategy vs SPY)
│   ├── regime_chart.py         # Timeline showing equity/cash regime bands
│   ├── signal_example.py       # Single-asset chart with bands + entry/exit markers
│   ├── exposure_chart.py       # Stacked area: SPY / MGK / SGOV allocation over time
│   └── monthly_heatmap.py      # Monthly return heatmap
│
├── config.py                   # All configurable parameters (see §10.2)
├── run_backtest.py             # Main entry point
├── run_sensitivity.py          # Parameter sweep / sensitivity analysis
└── tests/
    ├── test_indicators.py
    ├── test_trend_signals.py
    ├── test_mr_overlay.py
    ├── test_state_machine.py
    ├── test_sizing.py
    ├── test_allocator.py
    ├── test_portfolio.py
    └── test_regime.py
```

### 10.2 Master Configuration

```python
@dataclass
class BacktestConfig:
    # === Data ===
    data_source: str = "tiingo"             # Primary data provider
    tiingo_api_key: str = ""                # Tiingo API key (loaded from env or config file)
    start_date: str = "2008-01-02"
    end_date: str = "2025-12-31"
    starting_capital: float = 100_000.0
    sgov_proxy_ticker: str = "SHV"          # Pre-SGOV proxy
    sgov_proxy_cutover: str = "2020-05-01"  # Switch from SHV to SGOV

    # === Execution Model ===
    signal_price: str = "close"             # Always "close" — signals computed on Close
    execution_price: str = "open"           # Always "open" — trades execute at next-day Open
    return_model: str = "precise"           # "precise" (Open-aware) or "simplified" (Close-to-Close)

    # === Trend Engine (SPY, MGK) ===
    keltner_upper_lookback: int = 20
    keltner_lower_lookback: int = 40
    keltner_multiplier: float = 2.0
    keltner_atr_ratio: float = 1.4
    donchian_upper_lookback: int = 20
    donchian_lower_lookback: int = 40

    # === Mean-Reversion Overlay ===
    enable_mr_overlay: bool = True          # Toggle MR overlay on/off
    # Overbought exit (sell strength)
    overbought_threshold: float = 0.06      # 6% above EMA(20)
    vix_low_threshold: float = 14.0
    # Oversold re-entry (buy weakness)
    oversold_threshold: float = 0.04        # 4% below EMA(20)
    vix_elevated_threshold: float = 25.0
    trend_recent_window: int = 10           # Days to consider trend "recently active"

    # === Sizing ===
    portfolio_vol_target: float = 0.015     # 1.5% daily
    vol_lookback: int = 14
    max_leverage: float = 1.0              # 100% default (no leverage)

    # === Transaction Costs ===
    cost_model: str = "proportional"        # "proportional" or "per_share"
    proportional_cost_rate: float = 0.0005  # 5 bps per transaction
    cost_per_share: float = 0.0035
    min_cost_per_trade: float = 0.35
    rebalance_threshold: float = 0.05       # 5% minimum adjustment to trigger rebalance
```

---

## 11. Execution Flow (Daily Loop)

```
For each trading day t (after warm-up period):

  1. OBSERVE PRICES:
     - Read Close[t] for SPY, MGK, SGOV
     - Read Open[t] for SPY, MGK, SGOV  (needed for today's execution if signals fired yesterday)
     - Read VIX Close[t]

  2. EXECUTE PENDING TRADES FROM YESTERDAY'S SIGNALS:
     - Any entry/exit/rebalance signals generated at Close[t-1] execute now at Open[t]
     - Update share counts and AUM based on Open[t] prices
     - Deduct transaction costs

  3. MARK-TO-MARKET:
     - Compute portfolio value at Close[t] using current holdings × Close[t] prices
     - Record daily return (Open[t] → Close[t] for new positions, Close[t-1] → Close[t] for held)

  4. COMPUTE INDICATORS using Close[t]:
     - EMA(20), EMA(40) for SPY, MGK
     - AvgAbsChange(20), AvgAbsChange(40)
     - DonchianUp(20), DonchianDown(40)
     - KeltnerUp(20,2), KeltnerDown(40,2)
     - UpperBand, LowerBand

  5. GENERATE SIGNALS using Close[t]:
     For each asset j ∈ {SPY, MGK}:

     a. If state = FLAT:
        - Check entry: Close[t,j] >= UpperBand[t-1,j]            → signal ENTER (exec at Open[t+1])
        - Check MR re-entry: oversold + recently active           → signal ENTER (exec at Open[t+1])

     b. If state = IN_TREND:
        - Update trailing stop: max(prev stop, current LowerBand)
        - Check stop: Close[t,j] < TrailingStop[t,j]             → signal EXIT (exec at Open[t+1])
        - Check overbought: sharp rise + low VIX                  → signal TEMP_EXIT (exec at Open[t+1])

     c. If state = TEMP_EXIT:
        - Continue updating trailing stop
        - Check stop breach: Close[t,j] < TrailingStop[t,j]      → signal FULL_EXIT (→ FLAT)
        - Check overbought cleared + Close above LowerBand        → signal RE-ENTER (exec at Open[t+1])
        - Check MR re-entry: oversold bounce                      → signal RE-ENTER (exec at Open[t+1])

  6. DETERMINE TARGET WEIGHTS for tomorrow:
     a. Determine which assets will be IN_TREND after signals apply
     b. Compute N_active
     c. For each active asset: w[j] = portfolio_vol_target / N_active / σ[j,t]
     d. Apply leverage cap
     e. w_SGOV = max(0, 1.0 - Σ w[j])

  7. APPLY REBALANCE THRESHOLD:
     Suppress small weight changes below threshold

  8. QUEUE TRADES for execution at Open[t+1]

  9. LOG: equity, weights, signals, state transitions, trades
```

### 11.1 Warm-Up Period

The longest lookback is 40 days (Donchian/Keltner lower bands) plus 14 days for volatility. No signals should be generated until at least **54 trading days** of data are available. During warm-up, the portfolio is 100% SGOV.

### 11.2 N_active for Vol-Target Denominator

When computing the vol-target weight, `N_active` is the number of assets that will be in IN_TREND state after today's signals apply:

- Both SPY and MGK entering/staying in trend: `N_active = 2`, each gets `vol_target / 2 / σ[j]`
- Only one in trend: `N_active = 1`, that asset gets `vol_target / σ[j]` (full vol budget)
- Neither in trend: `N_active = 0`, no equity weights, 100% SGOV

**Important nuance:** When an asset transitions from FLAT to IN_TREND on day t, the other asset's weight may need to adjust (from full budget to half budget) on the same day. The allocator must compute all state transitions first, then size.

---

## 12. Output & Reporting Requirements

### 12.1 Primary Performance Table

| Metric | TRETE | TRETE (no MR) | SPY | MGK | 60/40 SPY/SGOV |
|--------|-------|---------------|-----|-----|----------------|
| CAGR | | | | | |
| Std Dev | | | | | |
| Sharpe | | | | | |
| Sortino | | | | | |
| UPI | | | | | |
| Max DD | | | | | |
| Avg DD | | | | | |
| Win % (months) | | | | | |

Where:
- **TRETE**: Full strategy (trend + MR overlay)
- **TRETE (no MR)**: Trend-only, no overbought/oversold overlay (`enable_mr_overlay = False`)
- **SPY / MGK**: Buy-and-hold benchmarks
- **60/40 SPY/SGOV**: Static balanced portfolio, rebalanced monthly

### 12.2 Regime Analysis Table

| Metric | Equity Regime | Cash Regime | Overall |
|--------|---------------|-------------|---------|
| % Time | | | 100% |
| Annualized Return | | | |
| Annualized Vol | | | |
| Sharpe | | | |
| Max DD | | | |
| Avg Holding Period | | | |

### 12.3 Trade Statistics

| Asset | Total Trades | Trades/Year | Avg Duration (days) | Win Rate | Avg Win | Avg Loss | Profit Factor |
|-------|-------------|-------------|---------------------|----------|---------|----------|---------------|
| SPY (trend) | | | | | | | |
| MGK (trend) | | | | | | | |
| SPY (MR re-entry) | | | | | | | |
| MGK (MR re-entry) | | | | | | | |

Track standard trend trades and MR-triggered re-entries separately to assess the overlay's contribution. Trade returns are measured from Open (entry) to Open (exit) to reflect actual execution prices.

### 12.4 Monthly Return Table

Year × Month matrix with yearly totals (same format as Table 2 in the original paper).

### 12.5 Alpha/Beta Regression

```
R_excess_TRETE[t] = α + β × R_excess_SPY[t] + ε[t]
```

Report annualized α, β, t-stats (Newey-West), R².

Upside/downside decomposition:

```
R_TRETE_yearly = α + β_up × R_SPY_yearly+ + β_down × R_SPY_yearly- + ε
```

### 12.6 MR Overlay Impact Report

Compare full strategy vs. trend-only (no MR overlay) to isolate the contribution of selling strength / buying weakness:

| Metric | With MR Overlay | Without MR Overlay | Difference |
|--------|----------------|-------------------|------------|
| CAGR | | | |
| Sharpe | | | |
| Max DD | | | |
| # Trades/Year | | | |
| % Time Invested | | | |

### 12.7 Visualizations

| Chart | Description |
|-------|-------------|
| Equity curve (log) | TRETE vs SPY vs 60/40 |
| Rolling max drawdown | TRETE vs SPY (per SMURF fact sheet style) |
| Regime timeline | Colored bands: green = equity, gray = SGOV |
| Asset allocation stacked area | Daily % allocation: SPY / MGK / SGOV |
| Signal example (SPY) | Close price + UpperBand + TrailingStop + Open-price entry/exit markers |
| Signal example (MGK) | Same as SPY |
| Monthly return heatmap | Color-coded monthly returns |

---

## 13. Sensitivity Analysis

### 13.1 Parameter Sweeps

| Parameter | Sweep Values |
|-----------|-------------|
| `keltner_upper_lookback` | {10, 15, 20, 25, 30} |
| `donchian_upper_lookback` | {10, 15, 20, 25, 30} |
| `keltner_lower_lookback` | {20, 30, 40, 50, 60} |
| `donchian_lower_lookback` | {20, 30, 40, 50, 60} |
| `portfolio_vol_target` | {0.005, 0.010, 0.015, 0.020, 0.025} |
| `max_leverage` | {1.0, 1.5, 2.0} |
| `overbought_threshold` | {0.04, 0.06, 0.08, 0.10} |
| `oversold_threshold` | {0.02, 0.04, 0.06} |
| `vix_low_threshold` | {12, 14, 16, 18} |
| `vix_elevated_threshold` | {20, 25, 30} |
| `enable_mr_overlay` | {True, False} |

Output: CSV table with all parameter combinations and their Sharpe, CAGR, MDD, and trade count.

### 13.2 Walk-Forward Validation

- **In-sample window**: 3 years rolling
- **Out-of-sample window**: 1 year
- Step forward 1 year at a time
- At each step, optimize parameters on in-sample, evaluate on out-of-sample
- Report aggregated out-of-sample metrics

---

## 14. Validation & Testing

### 14.1 Unit Tests

| Component | Test |
|-----------|------|
| EMA | Compare vs `pandas.Series.ewm(span=n, adjust=False)` on Close prices |
| Donchian | Compare vs `Close.rolling(n).max()` / `.min()` |
| Keltner | Verify on synthetic Close data with known absolute changes |
| Trailing Stop | Verify it never decreases; verify exit triggers on Close |
| State Machine | Verify all transitions: FLAT→IN_TREND, IN_TREND→FLAT, IN_TREND→TEMP_EXIT, TEMP_EXIT→IN_TREND, TEMP_EXIT→FLAT |
| VIX Filter | Verify overbought/oversold conditions on known VIX Close series |
| Open Execution | Verify trades fill at Open[t+1], not Close[t] |

### 14.2 Integration Tests

- **Signal-to-execution lag**: Generate an entry signal at Close[t]; verify the position is not active until Open[t+1]; verify no return is captured between Close[t] and Open[t+1] on the signal day.
- **Gap risk**: Inject a scenario where Close[t] triggers entry and Open[t+1] gaps significantly; verify the entry price is Open[t+1] and the slippage is captured.
- **Regime switch**: Construct a synthetic scenario where SPY trend turns off at Close; verify portfolio moves to 100% SGOV starting at Open[t+1].
- **MR overlay**: Inject a scenario where SPY is in uptrend and surges 8% above EMA with VIX at 12; verify temp exit triggers at next Open. Then inject a pullback; verify re-entry at next Open.
- **Dual-asset sizing**: Both SPY and MGK in trend; verify each gets half the vol budget. One exits; verify the other scales up to the full budget at the next Open.

### 14.3 Acceptance Criteria

Since the SMURF model is proprietary and we are approximating its equity-timing logic, exact replication is not expected. Qualitative targets:

| Metric | Target Range | Rationale |
|--------|-------------|-----------|
| CAGR | 10–18% | SMURF reports 20.8% with all components; we expect lower without TOM/SALT alpha |
| Sharpe | > 0.8 | Should meaningfully beat SPY's ~0.6 Sharpe |
| Max DD | < -20% | SMURF reports -6.7%; without bond trades, drawdowns will be deeper |
| % Time in Equities | 55–70% | SMURF reports 63% |
| Trend Trades/Year (per asset) | 3–8 | SMURF reports < 5 combined for MGK+SPY |
| Avg Trade Duration | 30–90 days | Consistent with the Timing Industry paper's ~52 days |

### 14.4 Sanity Checks

- Portfolio weights always sum to exactly 1.0 (or ≤ Max_Leverage if leverage enabled, with residual in SGOV).
- SGOV weight is never negative when Max_Leverage = 1.0.
- No look-ahead bias: signals use Close[t], trades execute at Open[t+1]. Never the reverse.
- During warm-up period, portfolio is 100% SGOV.
- Trailing stop never decreases for any active position.
- State transitions are logged and auditable.
- Trade entry/exit prices in the trade log match Open prices, not Close prices.

---

## 15. Development Plan

### Phase 1: Data Pipeline (Days 1–2)
- Tiingo API client for SPY, MGK, SGOV/SHV, VIX (adjOpen + adjClose)
- SHV→SGOV proxy stitching
- Risk-free rate loader
- Data validation (missing dates, Open/Close consistency, adjustment sanity)

### Phase 2: Indicators (Days 3–4)
- EMA, Donchian, adapted Keltner (all on Close)
- UpperBand / LowerBand combiners
- Trailing stop engine (evaluated on Close)
- Full unit test suite for all indicators

### Phase 3: Signal Engine (Days 5–7)
- Trend entry/exit signals for SPY, MGK (Close-based)
- Mean-reversion overlay (overbought exit, oversold re-entry)
- VIX filter module
- Position state machine (FLAT / IN_TREND / TEMP_EXIT)
- Regime detection
- Integration tests including signal-to-execution lag verification

### Phase 4: Portfolio Engine (Days 8–10)
- Volatility-target sizing with dynamic N_active (Close-based vol)
- Leverage cap
- Weight allocator (SPY, MGK, SGOV)
- Daily P&L calculator with Open-execution-aware return model
- Weight-to-shares conversion at Open prices
- Rebalance threshold logic
- Transaction cost models (proportional and per-share)
- End-to-end portfolio backtest
- Trade logger (recording Open execution prices)

### Phase 5: Analytics & Reporting (Days 11–13)
- Performance metrics (CAGR, Sharpe, Sortino, UPI, MDD, Avg DD, Win %)
- Regression analysis (alpha/beta, upside/downside)
- Trade-level statistics per asset (with Open-to-Open returns)
- Regime analysis
- MR overlay impact comparison
- Monthly return matrix
- Benchmark comparisons (SPY, MGK, 60/40)

### Phase 6: Visualization (Days 14–15)
- All 7 chart types from §12.7
- Publication-quality matplotlib styling
- Signal charts showing Close-based bands with Open-price execution markers

### Phase 7: Sensitivity & Validation (Days 16–18)
- Parameter sweep engine
- Walk-forward framework
- Full acceptance test suite
- Document results vs SMURF benchmarks

### Phase 8: Documentation & Polish (Day 19)
- README with usage instructions
- Tiingo API key configuration guide
- Configuration guide
- Docstrings and type hints throughout

---

## 16. Technical Requirements

### 16.1 Python Version
Python 3.10+

### 16.2 Dependencies

| Package | Purpose |
|---------|---------|
| numpy | Numerical computation |
| pandas | Data manipulation, rolling statistics |
| scipy | Statistical functions |
| statsmodels | OLS with Newey-West HAC errors |
| matplotlib | Visualization |
| seaborn | Heatmaps |
| requests | Tiingo REST API calls |
| pytest | Testing |

**Note:** No `yfinance` dependency. Tiingo is accessed via direct REST calls using `requests` (or optionally via the `tiingo` Python package).

### 16.3 Performance

The dataset is very small (~4,500 trading days × 4 instruments). Full backtest should complete in under 2 seconds. Sensitivity sweep with 10,000+ parameter combinations should complete in under 10 minutes.

---

## 17. Key Differences from the Original Timing Industry Paper

| Aspect | Timing Industry Paper | This Adaptation |
|--------|----------------------|-----------------|
| Universe | 48 Kenneth French industries | 3 ETFs (SPY, MGK, SGOV) + VIX signal |
| Data source | Kenneth French website | Tiingo REST API |
| Period | 1926–2024 | 2008–2025 |
| Signal price | Close | Close (same) |
| Execution price | Close (implied, same-day) | **Open (next day)** |
| Signal types | Trend-following only | Trend + mean-reversion overlay (overbought/oversold) |
| Exit types | Trailing stop only | Trailing stop + overbought temporary exit |
| Entry types | Channel breakout only | Channel breakout + oversold re-entry within trends |
| VIX usage | None | Overbought/oversold confirmation |
| Regime | Always invested in industries or cash (T-bills) | Binary equity/SGOV regime switch |
| Cash position | 1-month T-bills (theoretical) | SGOV ETF (actual tradable instrument) |
| Default leverage | 200% | 100% (configurable) |
| N for vol target | 48 | 1 or 2 (dynamic, based on active trend count) |
| Rebalancing | Daily | Daily with rebalance threshold |
| Position states | Binary (long or flat) | Three-state (FLAT, IN_TREND, TEMP_EXIT) |
| Price fields needed | Close only | **Close + Open** |

---

## 18. Risks & Assumptions

| # | Risk / Assumption | Mitigation |
|---|-------------------|------------|
| 1 | SMURF exact rules are proprietary; our MR overlay approximates them | Make MR overlay toggleable; compare with/without |
| 2 | Only 2 equity instruments limits diversification vs 48-industry paper | This is by design; concentration is offset by instrument quality (SPY, MGK) |
| 3 | SHV is an imperfect proxy for SGOV pre-2020 | Both hold ultra-short Treasuries; difference is negligible |
| 4 | VIX data may have gaps or misaligned timestamps | Forward-fill VIX; validate against known VIX spikes |
| 5 | Short backtest period (17 years) limits statistical significance | Report confidence intervals; compare behavior across sub-periods |
| 6 | Overbought/oversold thresholds are subjective | Provide parameter sweeps; let users calibrate |
| 7 | Without SALT/TOM, risk-off periods earn only T-bill returns | This is the intended simplification; can be extended later |
| 8 | Dynamic N_active changes vol budget when one asset enters/exits trend | Test edge cases; log all sizing transitions |
| 9 | Open-price execution introduces gap risk not present in Close-to-Close models | This is more realistic; gap risk is captured and reported. Compare precise vs simplified return models. |
| 10 | Tiingo API rate limits or data availability | Cache data locally after first download; implement retry logic; fall back to CSV import |

---

## 19. Glossary

| Term | Definition |
|------|------------|
| adjClose | Split- and dividend-adjusted closing price (from Tiingo) |
| adjOpen | Split- and dividend-adjusted opening price (from Tiingo) |
| CAGR | Compound Annual Growth Rate |
| EMA | Exponential Moving Average |
| MDD | Maximum Drawdown |
| MR | Mean Reversion (here: overbought/oversold overlay on trend signals) |
| SGOV | iShares 0-3 Month Treasury Bond ETF (cash equivalent) |
| SMURF | Stock Market Upside Reversal Factor (Antonacci's equity model) |
| TRETE | Trend & Reversion ETF Timing Engine (this system) |
| UPI | Ulcer Performance Index (return / Ulcer Index) |
| VIX | CBOE Volatility Index (implied volatility of S&P 500 options) |
