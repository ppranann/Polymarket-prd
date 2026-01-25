# Deployment TODO

## Changes Made
- **scanner.py**: Removed `query='temperature'` parameter from `get_markets()` call
- **polymarket.py**: Cleaned up duplicate docstring, fixed Hybrid Discovery logic

## Deployment Steps
- [ ] 1. Commit and push changes to git
- [ ] 2. SSH into EC2 and pull changes
- [ ] 3. Rebuild Docker image
- [ ] 4. Restart the bot container
- [ ] 5. Check logs for "Found X temperature buckets"

