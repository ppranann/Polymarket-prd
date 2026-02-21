"""
Shared Utilities Module
=======================
Common helpers used across scanner, main, and alpha modules.
"""

import re
from datetime import datetime
from typing import Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # Python < 3.9 fallback

# City → IANA timezone
CITY_TIMEZONES = {
    "london":   "Europe/London",
    "nyc":      "America/New_York",
    "seoul":    "Asia/Seoul",
    "incheon":  "Asia/Seoul",
}


# Month name → number mapping
MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12
}

# City patterns for extraction from market questions
CITY_PATTERNS = {
    "seoul": ["seoul", "incheon"],
    "london": ["london", "heathrow"],
    "nyc": ["new york", "nyc", "new-york"],
}


def extract_city(question: str) -> str:
    """
    Extract city name from a market question.

    Args:
        question: Market question text

    Returns:
        City slug in lowercase (e.g. "seoul", "london", "nyc", "unknown")
    """
    q_lower = question.lower()
    for city, patterns in CITY_PATTERNS.items():
        for pattern in patterns:
            if pattern in q_lower:
                return city
    return "unknown"


def extract_target_date(question: str) -> Tuple[str, int]:
    """
    Extract target date and days-to-resolution from market question.

    E.g. "Highest temperature in London on February 12?" → ("2026-02-12", 2)

    Args:
        question: Market question text

    Returns:
        Tuple of (ISO date string, days_to_resolution)
    """
    match = re.search(r'on\s+(\w+)\s+(\d{1,2})', question, re.IGNORECASE)
    if match:
        month_name = match.group(1).lower()
        day = int(match.group(2))
        month = MONTH_MAP.get(month_name)
        if month:
            year = datetime.now().year
            try:
                target = datetime(year, month, day).date()
                days_out = (target - datetime.now().date()).days
                return target.isoformat(), max(0, days_out)
            except ValueError:
                pass
    return datetime.now().date().isoformat(), 0


def classify_market_type(question: str) -> str:
    """
    Classify a market as 'threshold' or 'max_temp' based on its question text.

    - threshold: directional markets with "higher"/"lower"/"above"/"below"
    - max_temp: range/bucket markets ("11-15°C", "? - 4°C", "≤10°C", "≥21°C")

    Args:
        question: Market question text

    Returns:
        "threshold" or "max_temp"
    """
    q_lower = question.lower()

    # Check for directional keywords → threshold market
    directional_keywords = [
        "higher", "above", "or more", "at least", "over",
        "lower", "below", "or less", "at most", "under",
    ]
    if any(kw in q_lower for kw in directional_keywords):
        return "threshold"

    # Everything else (ranges, bare numbers, buckets) → max_temp
    return "max_temp"


def hours_remaining_in_local_day(city: str) -> float:
    """
    Return hours remaining until midnight in the city's local timezone.

    Used to decide whether a same-day market still has enough uncertainty
    to trade. Once < 6h remain, the day's max temp is basically determined.

    Args:
        city: City slug (e.g. "london", "nyc", "seoul")

    Returns:
        Float hours remaining (0.0–24.0)
    """
    tz_name = CITY_TIMEZONES.get(city.lower(), "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return 0.0  # conservative: skip if lookup fails
    now_local = datetime.now(tz)
    seconds_elapsed = now_local.hour * 3600 + now_local.minute * 60 + now_local.second
    return (86400 - seconds_elapsed) / 3600


def make_event_key(city: str, date: str) -> str:
    """
    Create a unique key for a city+date event.

    Args:
        city: City slug (e.g. "seoul")
        date: ISO date string (e.g. "2026-02-18")

    Returns:
        Event key like "seoul_2026-02-18"
    """
    return f"{city}_{date}"


def parse_bucket_range(question: str) -> Tuple[float, float]:
    """
    Parse bucket boundaries from a market question.

    Handles Polymarket formats:
    - "11-15°C" → (11, 15) in original unit
    - "≤ 10°C" or "10°C or lower" → (-inf, 10)
    - "≥ 21°C" or "21°C or higher" → (21, inf)
    - "? - 4°C" (bare bucket) → (4, 5) single-degree
    - "26-27°F" → (26, 27)

    Returns bounds in the ORIGINAL unit (caller must convert C→F if needed).

    Args:
        question: Market question or bucket label

    Returns:
        Tuple of (low_bound, high_bound). Uses float('inf') / float('-inf') for unbounded.
    """
    q = question.strip()

    is_celsius = '°C' in q or '°c' in q

    # "≤ 10" or "10 or lower/less/below/under"
    le_match = re.search(r'[≤<=]\s*(-?\d+\.?\d*)', q)
    if le_match:
        high = float(le_match.group(1))
        return (float('-inf'), high)

    lower_match = re.search(r'(-?\d+\.?\d*)\s*[°]?[CFcf]?\s*(?:or\s+)?(?:lower|below|under|less|at most)', q, re.IGNORECASE)
    if lower_match:
        high = float(lower_match.group(1))
        return (float('-inf'), high)

    # "≥ 21" or "21 or higher/more/above/over"
    ge_match = re.search(r'[≥>=]\s*(-?\d+\.?\d*)', q)
    if ge_match:
        low = float(ge_match.group(1))
        return (low, float('inf'))

    higher_match = re.search(r'(-?\d+\.?\d*)\s*[°]?[CFcf]?\s*(?:or\s+)?(?:higher|above|over|more|at least)', q, re.IGNORECASE)
    if higher_match:
        low = float(higher_match.group(1))
        return (low, float('inf'))

    # Explicit range: "11-15", "26-27", "-2-0"
    range_match = re.search(r'(-?\d+\.?\d*)\s*[-–]\s*(-?\d+\.?\d*)\s*[°]?[CFcf]?', q)
    if range_match:
        low = float(range_match.group(1))
        high = float(range_match.group(2))
        return (low, high)

    # Bare number from "? - 4°C" pattern → single-degree bucket
    bare_match = re.search(r'\?\s*-\s*(-?\d+\.?\d*)\s*[°]?[CFcf]?', q)
    if bare_match:
        temp = float(bare_match.group(1))
        return (temp, temp + 1)

    # Last resort: any standalone number
    num_match = re.search(r'(-?\d+\.?\d*)\s*[°]?[CFcf]?', q)
    if num_match:
        temp = float(num_match.group(1))
        return (temp, temp + 1)

    return (float('-inf'), float('inf'))


def is_celsius_market(question: str) -> bool:
    """Check if a market question uses Celsius."""
    return '°C' in question or '°c' in question


def c_to_f(celsius: float) -> float:
    """Convert Celsius to Fahrenheit."""
    return (celsius * 9.0 / 5.0) + 32.0
