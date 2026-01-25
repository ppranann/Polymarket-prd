#!/usr/bin/env python3
"""
Debug script to check what markets Polymarket API is returning.
Run this on EC2: python debug_markets.py
"""

import asyncio
import os
import sys

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.clients.polymarket import create_polymarket_client


async def debug_markets():
    print("=" * 60)
    print("DEBUG: Checking Polymarket API for weather markets")
    print("=" * 60)
    
    # Get API key from environment
    api_key = os.environ.get('POLYMARKET_API_KEY')
    print(f"API Key present: {bool(api_key)}")
    
    if not api_key:
        print("ERROR: POLYMARKET_API_KEY not set!")
        return
    
    # Create client
    client = create_polymarket_client(api_key)
    
    try:
        # Fetch ALL markets
        print("\n[1] Fetching ALL markets from Gamma API...")
        url = f"{client.GAMMA_URL}/markets?active=true&closed=false&limit=500"
        async with client._session.get(url) as resp:
            data = await resp.json()
            raw_markets = data if isinstance(data, list) else data.get('data', [])
            print(f"    Total markets returned: {len(raw_markets)}")
        
        # Show all market titles
        print("\n[2] ALL market titles (first 30):")
        for i, m in enumerate(raw_markets[:30]):
            title = m.get('question', m.get('title', 'Unknown'))
            active = m.get('active', True)
            category = m.get('category', '')
            print(f"    {i+1}. [{'ACTIVE' if active else 'INACTIVE'}] [{category}] {title[:80]}")
        
        # Check for London/weather markets with different patterns
        print("\n[3] Looking for London/weather markets...")
        
        # Pattern 1: Contains 'london'
        london_markets = [m for m in raw_markets if 'london' in m.get('question', '').lower()]
        print(f"    Markets containing 'london': {len(london_markets)}")
        for m in london_markets:
            print(f"      - {m.get('question', '')[:80]}")
        
        # Pattern 2: Contains 'weather'
        weather_markets = [m for m in raw_markets if 'weather' in m.get('question', '').lower()]
        print(f"    Markets containing 'weather': {len(weather_markets)}")
        for m in weather_markets[:5]:
            print(f"      - {m.get('question', '')[:80]}")
        
        # Pattern 3: Contains '°' (degree symbol)
        degree_markets = [m for m in raw_markets if '°' in m.get('question', '')]
        print(f"    Markets containing '°': {len(degree_markets)}")
        for m in degree_markets[:5]:
            print(f"      - {m.get('question', '')[:80]}")
        
        # Pattern 4: Contains 'temperature'
        temp_markets = [m for m in raw_markets if 'temperature' in m.get('question', '').lower()]
        print(f"    Markets containing 'temperature': {len(temp_markets)}")
        for m in temp_markets[:5]:
            print(f"      - {m.get('question', '')[:80]}")
        
        # What the scanner ACTUALLY looks for
        print("\n[4] What the scanner will find (requires city + '°'):")
        target_cities = ['seoul', 'london', 'incheon', 'city airport']
        qualifying = []
        for m in raw_markets:
            title = m.get('question', '').lower()
            if any(city in title for city in target_cities) and '°' in title:
                qualifying.append(m)
        
        print(f"    Markets matching scanner criteria: {len(qualifying)}")
        for m in qualifying:
            print(f"      - {m.get('question', '')[:80]}")
        
        print("\n" + "=" * 60)
        print("RECOMMENDATION: If you see London weather markets above but")
        print("they're not in the scanner results, the issue is the '°' symbol")
        print("requirement. The code needs to be updated to be less strict.")
        print("=" * 60)
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(debug_markets())

