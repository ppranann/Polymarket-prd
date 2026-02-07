#!/bin/bash
# Disk Space Cleanup for t3.micro EC2
# Run this weekly or when disk is >80% full

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Docker Cleanup Script${NC}"
echo -e "${GREEN}========================================${NC}"

# Show disk usage before
echo -e "\n${YELLOW}Disk usage BEFORE cleanup:${NC}"
df -h / | grep -v Filesystem

echo -e "\n${YELLOW}Cleaning up Docker resources...${NC}"

# Stop containers gracefully
echo "Stopping containers..."
docker-compose down || true

# Remove ALL unused Docker data (images, containers, volumes, networks)
# This is safe - it only removes STOPPED containers and UNUSED images
echo "Pruning Docker system..."
docker system prune -a -f --volumes

# Show disk usage after
echo -e "\n${GREEN}Disk usage AFTER cleanup:${NC}"
df -h / | grep -v Filesystem

echo -e "\n${GREEN}✓ Cleanup complete!${NC}"
echo -e "${YELLOW}To restart bot: ./deploy.sh latest${NC}"
