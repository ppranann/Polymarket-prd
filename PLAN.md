# Weather Bot: From Toy to Validated Compounding System

## Core Thesis
Weather is predictable. You have a real-time API (Tomorrow.io) that updates faster than Polymarket participants react. The edge is **speed + probability model**. But the edge must be **measured before it's leveraged**.

---

## Phase 1: Fix the Plumbing (Compounding Bugs)
> *Unblocks everything. These are bugs, not features.*

### 1A. Add `max_trade_size_pct` to RiskConfig
**File:** `src/config.py`
- Add `max_trade_size_pct: float = 0.10` to `RiskConfig` dataclass (line ~37)
- Add `max_trade_size_pct=risk_cfg.get('max_trade_size_pct', 0.10)` in `parse()` (line ~275)
- **Why:** Currently `getattr(self.risk, 'max_trade_size_pct', None)` returns `None` → percentage sizing never activates → stuck at fixed $3.00

### 1B. Feed Realized P&L Back Into Capital
**File:** `src/risk.py`
- In `close_position()` (line ~446), add `self._capital += realized_pnl` before deleting the position
- **Why:** Currently `_capital` stays at `initial_capital` forever. Wins never increase your betting power. This is the single biggest blocker to compounding.

### 1C. Scale the Hard Cap With Capital
**File:** `src/risk.py`
- In `calculate_position_size()` (line ~316), change the hard cap from fixed to proportional:
  - Old: `return min(size, self.risk.max_trade_size)`
  - New: `return min(size, max(self.risk.max_trade_size, self.capital * 0.15))`
  - This means: hard cap is either $3.00 or 15% of capital, whichever is larger
  - At $30 capital → cap is $4.50. At $100 → cap is $15. Scales naturally.
- **Why:** Fixed $3 cap means even at $500 capital, max bet is $3. Defeats compounding.

### 1D. Config Update
**File:** `config.yaml`
- Ensure `max_trade_size_pct: 0.10` is present in `risk:` section (already there)
- No other config changes needed for Phase 1

---

## Phase 2: Enhanced Logging for Calibration
> *You can't improve what you don't measure.*

### 2A. Create Prediction Log Table
**File:** `src/logger.py`
- Add new `PredictionLog` dataclass:
  ```
  market_id, condition_token_id, question_text,
  city, target_date, condition_type (above/below/range),
  threshold_f, forecast_temp_f, sigma_used,
  days_to_resolution, fair_price, market_price,
  alpha, side (BUY/SELL), size,
  z_score, created_at
  ```
- Add `log_prediction()` method to `SupabaseLogger` that writes to `polymarket_predictions` table
- **Why:** Current AUDIT logs are flat strings. Need structured data for backtesting.

### 2B. Log Every Calculation with Full Context
**File:** `src/calculator.py`
- In `calculate_fair_price_from_weather()`, build and return a `PredictionLog` alongside the fair_price
- Include: `z_score`, `sigma_used`, `forecast_temp`, `threshold`, `condition_type`, `city`, `target_date`, `days_to_resolution`
- **Why:** Need to reconstruct "why did it buy this?" for every single bet.

### 2C. Extract Horizon (Days to Resolution)
**File:** `src/calculator.py`
- Parse the market question for the target date (e.g., "February 12" from "Highest temperature in London on February 12?")
- Calculate `days_to_resolution = (target_date - today).days`
- Store in PredictionLog
- **Why:** Same-day bets are fundamentally different from 3-day-out bets. Need to analyze separately.

---

## Phase 3: Market Resolution Tracker
> *Close the feedback loop: did your prediction actually come true?*

### 3A. Resolution Checker
**File:** `src/resolution.py` (new file)
- `ResolutionTracker` class with method `check_resolved_markets()`
- Periodically (every 30 min) query Polymarket API for markets the bot has bet on
- Check if market has resolved (settled) and what the outcome was (YES/NO)
- Uses `py_clob_client` or gamma API to check resolution status

### 3B. Log Outcomes
**File:** `src/logger.py`
- Add `log_resolution()` method
- Updates the prediction log with: `resolved_at`, `actual_outcome` (0 or 1), `pnl_realized`, `was_correct` (bool)
- **Why:** Without outcomes, you can never compute calibration or Brier score.

### 3C. Wire Into Main Loop
**File:** `src/main.py`
- Add resolution check to the main scan loop (every N scans, check for resolved markets)
- When a market resolves, update the prediction log and close the position properly

---

## Phase 4: Dynamic Sigma
> *The single most impactful model improvement.*

### 4A. Horizon-Based Sigma
**File:** `src/calculator.py`
- Replace `DEFAULT_SIGMA_FAHRENHEIT = 3.5` with a function:
  ```python
  def get_sigma(days_to_resolution: int) -> float:
      # Same-day: forecast is tight, sigma small
      # 1 day: moderate
      # 2-3 days: wider
      base_sigma = 2.0  # °F, same-day
      return base_sigma + (days_to_resolution * 1.5)
      # 0 days → 2.0°F, 1 day → 3.5°F, 2 days → 5.0°F
  ```
- **Why:** A forecast for "today's high" is much more certain than "3 days from now." Fixed sigma overprices near-term tails and underprices far-out tails.

### 4B. Calibrate Sigma from Data (After Phase 3)
- Once you have ~100+ resolved predictions, compute actual forecast error distribution
- Group by `days_to_resolution` and city
- Measure actual standard deviation of (forecast - actual)
- Update the sigma function with empirical values
- **Why:** This turns sigma from a guess into a measured parameter.

---

## Phase 5: Kelly Criterion Position Sizing
> *Size based on measured edge, not arbitrary multipliers.*

### 5A. Kelly Fraction Calculator
**File:** `src/alpha.py`
- Add `calculate_kelly_fraction()`:
  ```python
  def calculate_kelly_fraction(fair_price: float, market_price: float) -> float:
      # Kelly: f* = (bp - q) / b
      # b = net odds = (1 / market_price) - 1
      # p = fair_price (our estimated probability)
      # q = 1 - p
      if market_price <= 0.01 or market_price >= 0.99:
          return 0.0
      b = (1.0 / market_price) - 1.0
      p = fair_price
      q = 1.0 - p
      kelly = (b * p - q) / b
      return max(0.0, kelly)
  ```

### 5B. Fractional Kelly in Position Sizing
**File:** `src/risk.py`
- Replace the linear alpha multiplier with fractional Kelly:
  ```python
  kelly_fraction = calculate_kelly_fraction(fair_price, market_price)
  kelly_bet = self.capital * kelly_fraction * KELLY_SCALE  # 0.25x Kelly
  size = min(kelly_bet, self.capital * max_trade_size_pct)
  ```
- Config: `kelly_fraction_scale: 0.25` (quarter Kelly - conservative, robust to estimation error)
- **Why:** Kelly is mathematically optimal for long-term growth. Quarter-Kelly is industry standard for robustness.

### 5C. Total Exposure Cap
**File:** `src/risk.py`
- Add check in `can_open_position()`:
  ```python
  if self.total_exposure >= self.capital * max_total_exposure_pct:
      return False, "Total exposure cap reached"
  ```
- Config: `max_total_exposure_pct: 0.30` (max 30% of capital deployed at once)
- **Why:** Even with Kelly, you don't want 100% of capital in weather bets.

---

## Phase 6: Calibration Report
> *Weekly post-mortem capability.*

### 6A. Report Generator Script
**File:** `src/calibration.py` (new file)
- Query Supabase for all resolved predictions
- Group by:
  - Probability bucket (0-10%, 10-20%, ..., 90-100%)
  - Horizon (same-day, 1-day, 2-day)
  - City (London, Seoul, NYC)
  - Price bucket ($0.01-0.05, $0.05-0.15, $0.15+)
- For each group, compute:
  - Hit rate (% of bets that were correct)
  - Average EV per bet
  - Brier score
  - Total P&L
- Output as formatted table to logs

### 6B. Run as CLI Command
- `python -m src.calibration --days 30`
- Prints calibration report for the last N days
- **Why:** Weekly ritual - review, adjust, improve. Like watching demos in CS2.

---

## Implementation Order & Dependencies

```
Phase 1 (Day 1) ──→ Phase 2 (Day 2-3) ──→ Phase 3 (Day 3-4)
   Fix bugs           Enhanced logging       Resolution tracking
   Unblock scaling     Structured data        Close feedback loop
                                                    │
                                                    ▼
                       Phase 6 (Day 7) ←── Phase 4 (Day 5-6)
                       Calibration report    Dynamic sigma
                                                    │
                                                    ▼
                                             Phase 5 (After ~200 resolved bets)
                                             Kelly sizing
                                             (needs calibration data)
```

## What You Do vs What I Do

| Task | Who |
|------|-----|
| Phase 1: Fix compounding bugs | I code it |
| Phase 2: Enhanced logging | I code it |
| Phase 3: Resolution tracker | I code it |
| Create Supabase `polymarket_predictions` table | You (on Supabase dashboard) |
| Phase 4: Dynamic sigma | I code it |
| Deploy each phase | You (git pull + deploy on EC2) |
| Phase 5: Kelly sizing | I code it (after calibration data exists) |
| Phase 6: Calibration report | I code it |
| Run calibration, review results weekly | You |
| Decide to increase capital based on data | You (only after calibration proves edge) |

## Config Changes Summary

```yaml
risk:
  max_trade_size: 3.00              # Base max (scales with capital)
  max_trade_size_pct: 0.10          # 10% of capital per trade
  max_total_exposure_pct: 0.30      # Max 30% capital deployed
  kelly_fraction_scale: 0.25        # Quarter-Kelly (conservative)

alpha:
  size_multiplier_max: 3.0          # Kept as fallback, Kelly overrides when ready
```

## Success Criteria

Before increasing capital beyond $50:
- [ ] 200+ resolved predictions logged
- [ ] Calibration shows your "15%" bucket hits 10-20% in reality
- [ ] Positive EV per bet across at least 2 of 3 cities
- [ ] Brier score < 0.25 (better than "always guess 50%")
- [ ] Dynamic sigma calibrated against actual forecast errors

## The Compounding Endgame

Once calibration proves positive EV:
1. Capital grows via reinvested wins (Phase 1 fix)
2. Position sizes grow proportionally (max_trade_size_pct)
3. Kelly sizing ensures optimal growth rate without ruin
4. Total exposure cap prevents catastrophic drawdowns
5. Weekly calibration catches model drift before it costs you

This isn't "bet more and hope." It's "prove the edge, then scale it."
