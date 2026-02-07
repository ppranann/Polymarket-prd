#!/bin/bash
# Performance Analysis Script with AWS Secrets Manager Integration
# =================================================================
# Fetches Supabase credentials from AWS Secrets Manager and runs performance analysis

set -e

# Configuration
SECRET_NAME="polymarket-bot/production"
REGION="us-east-1"  # Change if your secret is in a different region

echo "🔐 Fetching credentials from AWS Secrets Manager..."

# Fetch secret from AWS Secrets Manager
SECRET_JSON=$(aws secretsmanager get-secret-value \
    --secret-id "$SECRET_NAME" \
    --region "$REGION" \
    --query SecretString \
    --output text)

if [ -z "$SECRET_JSON" ]; then
    echo "❌ Failed to fetch secret from AWS Secrets Manager"
    echo "   Secret name: $SECRET_NAME"
    echo "   Region: $REGION"
    exit 1
fi

# Extract Supabase credentials
export SUPABASE_URL=$(echo "$SECRET_JSON" | jq -r '.supabase_url')
export SUPABASE_KEY=$(echo "$SECRET_JSON" | jq -r '.supabase_service_key')

# Validate credentials
if [ "$SUPABASE_URL" = "null" ] || [ "$SUPABASE_KEY" = "null" ]; then
    echo "❌ Supabase credentials not found in secret"
    echo ""
    echo "Expected keys in secret:"
    echo "  - supabase_url"
    echo "  - supabase_service_key"
    echo ""
    echo "Current secret keys:"
    echo "$SECRET_JSON" | jq 'keys'
    exit 1
fi

echo "✅ Credentials loaded successfully"
echo ""

# Check for --test-only flag
if [[ "$1" == "--test-only" ]]; then
    python3 test_supabase.py
    exit $?
fi

# Test connection first (optional: pass --skip-test to skip)
if [[ "$1" != "--skip-test" ]]; then
    echo "🧪 Testing Supabase connection..."
    if ! python3 test_supabase.py; then
        echo ""
        echo "❌ Connection test failed. Fix the issues above before continuing."
        exit 1
    fi
fi

# Determine number of days (default: 7)
# Shift args if first arg was --skip-test
if [[ "$1" == "--skip-test" ]]; then
    shift
fi

DAYS=${1:-7}

# Run the performance analysis
echo "📊 Running performance analysis for last $DAYS days..."
echo ""
python3 analyze_performance.py "$DAYS"
