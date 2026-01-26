# Bot Fixes Implementation Plan

## Status: IN PROGRESS

### Step 1: Fix Weather Client (src/clients/tomorrow_io.py) ✅ COMPLETE
- [x] Add coordinate mapping for target cities (London, Seoul, NYC)
- [x] Add `get_forecast_by_city()` method using Lat/Long
- [x] Use exact URL: `/v4/weather/forecast`

### Step 2: Stop Log Spam (src/main.py)
- [ ] Add failed_cities tracking list in scan loop
- [ ] Collect failures during market processing
- [ ] Log summary at end of scan (single warning line)
- [ ] Add botocore muzzle to logging config

### Step 3: Verification
- [ ] Run syntax check
- [ ] Update TODO_FIX_BOT.md with completion status

