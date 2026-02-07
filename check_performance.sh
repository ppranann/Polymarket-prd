#!/bin/bash
# Quick Performance Check Script
# ==============================
# Shows recent performance metrics from bot logs

echo "=== RECENT P&L SUMMARIES ==="
echo ""
docker-compose logs --tail 200 | grep "💰 P&L:" | tail -10

echo ""
echo "=== FULL PERFORMANCE SUMMARIES ==="
echo ""
docker-compose logs --tail 500 | grep -A 20 "PERFORMANCE SUMMARY"

echo ""
echo "=== WIN RATE & STATS ==="
echo ""
docker-compose logs --tail 500 | grep -E "(Win Rate|Winning Trades|Losing Trades)"

echo ""
echo "=== RECENT TRADES ==="
echo ""
docker-compose logs --tail 100 | grep "AUDIT: TRADE"

echo ""
echo "=== RECENT EXITS ==="
echo ""
docker-compose logs --tail 100 | grep "AUDIT: EXIT"
