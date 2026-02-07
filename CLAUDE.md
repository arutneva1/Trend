# CLAUDE.md

## Project
TRETE Backtest Engine — a Python backtesting system for trend-following ETF timing.

## Key Documents
- `TRETE_Phase1_Data_Pipeline_SRS.md` — **Read this fully before writing any code.** It contains exact function signatures, schemas, test specifications, and acceptance criteria for the data pipeline.
- `TRETE_Backtest_SRS_v3.md` — Parent SRS for full system context. Reference as needed but Phase 1 SRS is self-contained.

## Conventions
- Python 3.10+, type hints on all function signatures
- `pytest` for testing, tests in `tests/` directory
- No dependencies beyond: requests, pandas, numpy, pytest, python-dotenv
- Do NOT use yfinance, the tiingo Python package, or any other data library
- Tiingo API via direct `requests` calls only
- All prices use Tiingo's `adjOpen` and `adjClose` fields
- API key loaded from `.env` file via python-dotenv

## Working Style
- Implement one milestone at a time (M1 → M2 → M3 → M4)
- Run tests after each milestone before proceeding
- Do not skip tests or mark them as TODO
```

---

**2. Work milestone-by-milestone, not all at once**

The single biggest mistake is asking Claude Code to implement the entire pipeline in one prompt. Instead, do four separate prompts, one per milestone. Each prompt should follow this pattern: instruct → implement → test → verify → proceed.

---

**3. Prompt templates for each milestone**

**Milestone 1 prompt:**
```
Read TRETE_Phase1_Data_Pipeline_SRS.md in full.

Implement Milestone 1 only:
1. Create the project scaffolding (directory structure from §1.1, .env.example, .gitignore, requirements.txt)
2. Create config.py with the DataConfig dataclass (§2)
3. Create data/tiingo_loader.py with fetch_ticker_from_tiingo, load_ticker, fetch_vix, and load_vix (§3.3, §3.4)
4. Create tests/conftest.py with shared fixtures (§7)
5. Create tests/test_tiingo_loader.py with all 5 tests from §3.5

Follow the function signatures exactly as specified. Implement retry logic with exponential backoff for Tiingo API calls. Handle VIX via FRED as described in §3.4.

After creating all files, run: pytest tests/test_tiingo_loader.py -v

Fix any failures before finishing.
```

**Milestone 2 prompt:**
```
Read §4 of TRETE_Phase1_Data_Pipeline_SRS.md.

Implement Milestone 2: create data/proxies.py with stitch_cash_proxy (§4.3) and tests/test_proxies.py with all 4 tests from §4.4.

The stitching is a simple date-based concatenation — SHV before cutover, SGOV on/after cutover. No level adjustment. SGOV takes priority on overlap dates.

Run: pytest tests/test_proxies.py -v

Fix any failures before finishing.
```

**Milestone 3 prompt:**
```
Read §5 of TRETE_Phase1_Data_Pipeline_SRS.md.

Implement Milestone 3: create data/risk_free.py with fetch_french_rf and load_rf (§5.3) and tests/test_risk_free.py with all 4 tests from §5.5.

The French CSV parsing is tricky — follow the algorithm in §5.4 exactly. Scan for the first line starting with an 8-digit date, read until non-numeric, use whitespace-delimited parsing. RF values in the file are percentages — divide by 100.

Run: pytest tests/test_risk_free.py -v

Fix any failures before finishing.
```

**Milestone 4 prompt:**
```
Read §6 of TRETE_Phase1_Data_Pipeline_SRS.md.

Implement Milestone 4: create data/pipeline.py with build_dataset (§6.3) and tests/test_pipeline.py with all 6 tests from §6.7.

This module orchestrates everything from M1-M3. Key requirements:
- Output is a MultiIndex DataFrame (ticker, field) aligned to common trading days
- Compute derived return columns: ret_cc, ret_oc, ret_co per §6.6
- Run all 10 validation checks from §6.4
- Inner-join alignment, forward-fill VIX (2 days) and RF (5 days)

Run: pytest tests/ -v

ALL 19 tests across all milestones must pass. Fix any failures.
```

---

**4. When things go wrong — debugging prompts**

If a milestone's tests fail and Claude Code's first fix attempt doesn't resolve it, don't let it spiral. Be specific:
```
The test test_fetch_spy_returns_correct_schema is failing with:
[paste the exact error traceback]

The issue is [your diagnosis if you have one]. Fix only this specific issue.
Do not refactor other code.
```

If Claude Code gets stuck in a loop fixing one thing and breaking another:
```
Stop. Show me the current state of data/tiingo_loader.py without making changes.
Let's reason through the problem before editing.
```

---

**5. Things to watch for and correct early**

**Tiingo API format.** Claude Code may hallucinate the response format or add unnecessary request parameters. The SRS specifies the exact JSON schema from my search results. If you see it constructing the URL wrong, correct it:
```
The Tiingo EOD endpoint is:
GET https://api.tiingo.com/tiingo/daily/{ticker}/prices?startDate=YYYY-MM-DD&endDate=YYYY-MM-DD&token={key}

Headers: Authorization: Token {key}, Content-Type: application/json

Response fields include: date, adjOpen, adjClose, adjVolume (among others).
Do not use query parameter 'format' — JSON is the default.
```

**French CSV parsing.** This is historically the most failure-prone piece. If Claude Code's parser breaks, provide a concrete fixture:
```
Here is the exact format of the first 10 lines of the French daily factors CSV after unzipping:

This file was created by CMPT_ME_BEME_RETS using the 202401 CRSP database.
The 1-month TBill return is from Ibbotson and Associates Inc.


      Mkt-RF      SMB      HML       RF
19260701    0.10   -0.24   -0.28    0.009
19260702    0.45   -0.32   -0.08    0.009

The data lines use variable whitespace. The date column is YYYYMMDD as an integer.
Parse accordingly.
```

**MultiIndex DataFrame.** Claude Code sometimes creates flat column names like `SPY_adjClose` instead of a proper `pd.MultiIndex`. If that happens:
```
The price_data DataFrame MUST use pd.MultiIndex columns, not flat strings.
Correct: price_data[("SPY", "adjClose")]
Incorrect: price_data["SPY_adjClose"]

Use pd.concat with keys parameter to build the MultiIndex naturally.
```

---

**6. Environment setup prompt (run first)**

Before any milestone, start your Claude Code session with:
```
Set up the Python environment for this project:
1. Create a virtual environment in .venv
2. Install: requests pandas numpy pytest python-dotenv
3. Create a .env file with TIINGO_API_KEY={your actual key}
4. Verify the key works by running:
   curl -H "Authorization: Token {key}" "https://api.tiingo.com/tiingo/daily/SPY/prices?startDate=2024-01-02&endDate=2024-01-03"
```

This catches API key issues before any Python code is written.

---

**7. Final integration verification prompt**

After all four milestones pass individually:
```
Run the full test suite: pytest tests/ -v --tb=short

Then run this integration smoke test as a standalone script:

python -c "
from data.pipeline import build_dataset
from config import DataConfig
config = DataConfig()
price_data, rf_data = build_dataset(config)
print(f'Shape: {price_data.shape}')
print(f'Date range: {price_data.index[0]} to {price_data.index[-1]}')
print(f'Columns: {price_data.columns.tolist()[:6]}...')
print(f'SPY last close: {price_data[(\"SPY\",\"adjClose\")].iloc[-1]:.2f}')
print(f'RF last: {rf_data[\"rf\"].iloc[-1]:.6f}')
print(f'VIX last: {price_data[(\"VIX\",\"close\")].iloc[-1]:.2f}')
print('Pipeline OK')
"
```

---

**8. Session management**

Claude Code has a context window that fills up during long implementation sessions. If you're doing all four milestones in one session, you may hit limits around M3 or M4. Two strategies:

- **Option A (recommended):** One session per milestone. Start each session with "Read TRETE_Phase1_Data_Pipeline_SRS.md" so it reloads context.
- **Option B:** If doing it in one session, after M2 completes, say: "Summarize what we've built so far and what remains. Then proceed to M3." This helps Claude Code compress its internal context.

If the context window runs low during debugging, start a fresh session with:
```
Read TRETE_Phase1_Data_Pipeline_SRS.md. Then review the current codebase in data/ and tests/. 
Milestone [N] is partially complete. The failing test is [X]. Fix it.