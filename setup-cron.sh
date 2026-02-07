#!/bin/bash
# Setup automatic weekly cleanup cron job

set -e

GREEN='\033[0;32m'
NC='\033[0m'

echo -e "${GREEN}Setting up weekly cleanup cron job...${NC}"

# Get the full path to cleanup.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLEANUP_SCRIPT="$SCRIPT_DIR/cleanup.sh"

# Create cron job that runs every Sunday at 2 AM
CRON_CMD="0 2 * * 0 $CLEANUP_SCRIPT >> /tmp/docker-cleanup.log 2>&1"

# Check if cron job already exists
if crontab -l 2>/dev/null | grep -q "$CLEANUP_SCRIPT"; then
    echo "Cron job already exists"
else
    # Add to crontab
    (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -
    echo -e "${GREEN}✓ Cron job added: Weekly cleanup every Sunday at 2 AM${NC}"
fi

echo ""
echo "Current crontab:"
crontab -l

echo ""
echo -e "${GREEN}To manually run cleanup: ./cleanup.sh${NC}"
echo -e "${GREEN}To view cleanup logs: tail -f /tmp/docker-cleanup.log${NC}"
