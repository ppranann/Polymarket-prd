# TODO: Weather Client Updates

## Objective
Update the WeatherClient to use Lat/Long coordinates (hardcoded) and implement error batching to stop log spam.

## Changes Required

### 1. Update COORD_MAP in src/clients/tomorrow_io.py ✅ DONE
- [x] London: `51.5028,-0.0551` (London City Airport - EGLC)
- [x] Incheon: `37.4602,126.4407` (Incheon Intl Airport - RKSI)
- [x] Seoul: `37.4602,126.4407` (Force Seoul to use Incheon station)
- [x] NYC: `40.7128,-74.0060` (Central Park Station)

### 2. Ensure Consistent Coordinate Format ✅ DONE
- [x] Use string format "lat,lon" for URL params in API calls
- [x] Add convenience mapping from city aliases
- [x] Updated `get_forecast_by_city()` to use `COORD_MAP`

### 3. Verify Error Batching in src/main.py ✅ ALREADY IMPLEMENTED
- [x] Failed cities tracked in list during scan
- [x] Batch log ONCE at end of scan
- [x] Log shows unique failures only

## Status: COMPLETE ✅

All changes have been applied successfully.

