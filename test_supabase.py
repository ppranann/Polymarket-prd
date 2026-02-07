#!/usr/bin/env python3
"""
Test Supabase Connection
========================
Verifies Supabase credentials and checks if audit tables exist.
"""

import os
import sys
from datetime import datetime, timedelta
from supabase import create_client


def test_connection():
    """Test Supabase connection and table access"""

    print("=" * 80)
    print("SUPABASE CONNECTION TEST")
    print("=" * 80)
    print("")

    # Check environment variables
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")

    if not url:
        print("❌ SUPABASE_URL not set")
        return False

    if not key:
        print("❌ SUPABASE_KEY not set")
        return False

    print(f"✅ SUPABASE_URL: {url[:30]}...")
    print(f"✅ SUPABASE_KEY: {key[:20]}...")
    print("")

    # Try to connect
    try:
        print("🔌 Connecting to Supabase...")
        client = create_client(url, key)
        print("✅ Connection successful")
        print("")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False

    # Check if audit_log table exists
    try:
        print("📊 Checking 'polymarket_audit_log' table...")
        response = (
            client
            .table("polymarket_audit_log")
            .select("id", count="exact")
            .limit(1)
            .execute()
        )

        print(f"✅ Table exists with {response.count} total records")
        print("")
    except Exception as e:
        print(f"❌ Table access failed: {e}")
        print("")
        print("Possible issues:")
        print("  - Table 'polymarket_audit_log' doesn't exist")
        print("  - Service key doesn't have read permissions")
        print("")
        return False

    # Check for recent data
    try:
        print("📅 Checking for recent data (last 7 days)...")
        start_date = datetime.now() - timedelta(days=7)

        response = (
            client
            .table("polymarket_audit_log")
            .select("event_type", count="exact")
            .gte("created_at", start_date.isoformat())
            .execute()
        )

        if response.count > 0:
            print(f"✅ Found {response.count} records in the last 7 days")

            # Count by event type
            event_counts = {}
            for record in response.data:
                event_type = record.get("event_type", "Unknown")
                event_counts[event_type] = event_counts.get(event_type, 0) + 1

            print("")
            print("Event breakdown:")
            for event_type, count in sorted(event_counts.items()):
                print(f"  {event_type}: {count}")
        else:
            print("⚠️  No data found in the last 7 days")
            print("")
            print("This could mean:")
            print("  - Bot hasn't run yet")
            print("  - Bot is running in a different mode")
            print("  - Data is older than 7 days")

        print("")
    except Exception as e:
        print(f"❌ Data query failed: {e}")
        return False

    # Check rewards table
    try:
        print("🎁 Checking 'polymarket_rewards' table...")
        response = (
            client
            .table("polymarket_rewards")
            .select("id", count="exact")
            .limit(1)
            .execute()
        )

        print(f"✅ Table exists with {response.count} total records")
        print("")
    except Exception as e:
        print(f"⚠️  Rewards table not accessible: {e}")
        print("   (This is optional - won't affect main analysis)")
        print("")

    print("=" * 80)
    print("✅ ALL TESTS PASSED - Ready to run performance analysis!")
    print("=" * 80)
    print("")
    print("Next steps:")
    print("  ./analyze.sh           # Last 7 days")
    print("  ./analyze.sh 1         # Today only")
    print("  ./analyze.sh 30        # Last 30 days")
    print("")

    return True


if __name__ == "__main__":
    success = test_connection()
    sys.exit(0 if success else 1)
