# Bot Maintenance Guide

## Quick Answers

### 1. Can I just leave this running?
**YES**, but with maintenance:
- Run `./cleanup.sh` weekly to free disk space
- Or run `./setup-cron.sh` once to automate it

### 2. Is this still paper/mock trading?
**YES** - See in logs: `Mode: PAPER`

These are **simulated orders** - no real money.

To switch to LIVE trading:
```yaml
# config.yaml
development:
  paper_trading: false  # Change true → false
```

⚠️ **IMPORTANT**: Only switch to live after thoroughly testing!

### 3. Storage getting full?
This is the main issue on t3.micro. **Two solutions:**

#### Option A: Manual Cleanup (When disk > 80% full)
```bash
./cleanup.sh
./deploy.sh latest
```

#### Option B: Automatic Cleanup (Recommended)
```bash
./setup-cron.sh  # Run once, cleans every Sunday at 2 AM
```

### 4. Can I stay on t3.micro?
**YES** for paper trading with these scripts.

For LIVE trading with real money, monitor disk and consider:
- t3.small (2GB RAM, $0.0208/hr = ~$15/mo) if logs grow
- But start with t3.micro + cleanup scripts

### 5. Supabase warnings?
**Not a problem** - These are now hidden at INFO level.

Supabase logs are optional - the bot works fine without them. The warnings meant:
- EC2 can't reach Supabase (DNS/network issue)
- But trades still work (paper or live)
- Logs still go to Docker logs

To see Supabase warnings for debugging:
```bash
LOG_LEVEL=DEBUG docker-compose up
```

---

## Maintenance Schedule

### Weekly (Automatic with setup-cron.sh)
- Docker cleanup (removes old images/containers)
- Happens every Sunday at 2 AM
- View logs: `tail -f /tmp/docker-cleanup.log`

### Monthly
- Check disk usage: `df -h /`
- Review bot performance in logs
- Update config if needed

### When Deploying New Version
```bash
# On local machine
./build.sh

# On EC2
./deploy.sh latest
```

---

## Disk Space Breakdown

**t3.micro has 8GB disk total**

Typical usage:
- OS + packages: ~2GB
- Docker images: ~1-2GB (per version)
- Docker logs: 50MB max (with rotation)
- Build cache: 0-500MB

**After cleanup.sh:**
- Removes ALL old images/containers
- Keeps only: Current running image + OS
- Frees: 1-3GB typically

---

## Common Commands

### Check Status
```bash
docker-compose ps                    # Container status
docker-compose logs -f              # Live logs
df -h /                             # Disk usage
docker system df                    # Docker disk usage
```

### Maintenance
```bash
./cleanup.sh                        # Manual cleanup
./deploy.sh latest                  # Restart with latest image
docker-compose restart              # Quick restart
```

### Troubleshooting
```bash
# Bot not starting?
docker-compose logs

# Disk full?
./cleanup.sh

# Check what's using space
docker system df -v
du -sh /var/lib/docker/*
```

---

## When to Upgrade from t3.micro

Stay on t3.micro if:
- Paper trading only
- Using cleanup scripts
- Disk usage < 80%

Consider t3.small if:
- Live trading with real money
- Need more than 1GB RAM
- Deploying multiple bots

---

## Current Status

Your bot is:
- ✅ Running in PAPER mode (mock trades)
- ✅ Finding 37 markets
- ✅ Fetching weather successfully
- ✅ Detecting alpha signals (100%!)
- ✅ Placing mock orders successfully
- ✅ Logs are clean (no more spam)

**Next Steps:**
1. Let it run for 1-2 days to test stability
2. Run `./setup-cron.sh` on EC2 for automatic cleanup
3. Monitor disk usage: `df -h /`
4. When ready for live trading, switch `paper_trading: false`

---

## Monitoring

### Daily Check (30 seconds)
```bash
ssh -i keys/Polymarket-ec2-test.pem ubuntu@<IP>
df -h /                             # Disk usage
docker-compose logs --tail 50       # Recent logs
```

### Weekly Check (2 minutes)
```bash
docker system df                    # Docker disk usage
tail -f /tmp/docker-cleanup.log    # Cleanup logs
docker-compose logs | grep ERROR    # Any errors?
```

---

**Questions?** See [README.md](README.md) for bot features or [DEPLOYMENT.md](DEPLOYMENT.md) for deployment issues.
