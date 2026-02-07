# Deployment Guide

Complete guide for deploying the Polymarket Weather Trading Bot to AWS EC2.

## Table of Contents
- [Quick Start](#quick-start)
- [Prerequisites](#prerequisites)
- [Initial Setup](#initial-setup)
- [Deployment Process](#deployment-process)
- [Monitoring](#monitoring)
- [Troubleshooting](#troubleshooting)

---

## Quick Start

```bash
# 1. One-time setup (local machine)
./setup-ecr.sh

# 2. Build and push (local machine)
./build.sh

# 3. Deploy (on EC2)
ssh -i keys/Polymarket-ec2-test.pem ec2-user@<EC2-IP>
./deploy.sh latest
```

---

## Prerequisites

### Required Tools
- Docker Desktop (for local builds)
- AWS CLI configured with credentials
- SSH access to EC2 instance
- Git (for version tagging)

### Required Accounts
- AWS Account (for ECR and EC2)
- Polymarket Account with API keys
- Tomorrow.io API key
- Supabase Account

---

## Initial Setup

### 1. Configure AWS Credentials

```bash
aws configure
# Enter your:
#   AWS Access Key ID
#   AWS Secret Access Key
#   Default region: us-east-1
```

### 2. Create ECR Repository (One-time)

```bash
./setup-ecr.sh
```

This creates the ECR repository to store Docker images.

### 3. Setup AWS Secrets Manager

Create a secret named `polymarket-bot/production`:

```bash
aws secretsmanager create-secret \
  --name polymarket-bot/production \
  --secret-string '{
    "polygon_private_key": "0x...",
    "polymarket_api_key": "...",
    "polymarket_api_secret": "...",
    "tomorrow_io_api_key": "...",
    "supabase_url": "https://....supabase.co",
    "supabase_service_key": "..."
  }'
```

### 4. Setup Supabase Tables

Run this SQL in your Supabase dashboard:

```sql
CREATE TABLE polymarket_audit_log (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    event_type VARCHAR(50) NOT NULL,
    market_id VARCHAR(100),
    action VARCHAR(50),
    fair_price DECIMAL(10, 4),
    market_price DECIMAL(10, 4),
    alpha DECIMAL(10, 4),
    trade_size DECIMAL(10, 4),
    weather_data JSONB,
    decision_reason TEXT,
    profit_loss DECIMAL(10, 4),
    reward_earned DECIMAL(10, 4),
    status VARCHAR(20),
    error_message TEXT
);

CREATE TABLE polymarket_rewards (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at DATE DEFAULT CURRENT_DATE,
    market_id VARCHAR(100),
    usdc_earned DECIMAL(10, 4),
    order_ids TEXT[],
    order_type VARCHAR(20)
);
```

### 5. Copy Scripts to EC2

```bash
# Copy deploy script to EC2
scp -i keys/Polymarket-ec2-test.pem deploy.sh ec2-user@<EC2-IP>:~/

# Copy docker-compose.yml
scp -i keys/Polymarket-ec2-test.pem docker-compose.yml ec2-user@<EC2-IP>:~/

# Create config and logs directories on EC2
ssh -i keys/Polymarket-ec2-test.pem ec2-user@<EC2-IP>
mkdir -p config logs
exit
```

---

## Deployment Process

### Why Build Locally?

Building Docker images on EC2 t3.micro instances causes **memory exhaustion** during `pip install` of native packages (numpy, scikit-learn, cryptography). This hangs the instance and makes it unreachable.

**Solution**: Build locally (or on a powerful CI server) and push pre-built images to ECR.

### Deployment Flow

```
┌─────────────┐
│   Local     │  1. Build Docker image
│   Machine   │  2. Push to ECR
└──────┬──────┘
       │
       │ AWS ECR (Image Registry)
       │
       ▼
┌─────────────┐
│     EC2     │  3. Pull image
│  Instance   │  4. Start container
└─────────────┘
```

### Step-by-Step

#### Step 1: Build and Push (Local Machine)

```bash
# Build with auto-versioning (uses git commit hash)
./build.sh

# Or build with specific version
./build.sh v1.0.0

# Or build with custom version
./build.sh my-feature
```

**What happens:**
- Builds Docker image locally with all dependencies
- Tags with version, latest, and timestamp
- Pushes all tags to AWS ECR

**Build time**: 3-5 minutes (first build), 1-2 minutes (subsequent)

#### Step 2: Deploy to EC2

SSH to your EC2 instance:

```bash
ssh -i keys/Polymarket-ec2-test.pem ec2-user@<EC2-IP>
```

Deploy:

```bash
# Deploy latest version
./deploy.sh latest

# Or deploy specific version
./deploy.sh v1.0.0
```

**What happens:**
- Authenticates with ECR
- Pulls the specified image
- Stops old container
- Starts new container
- Shows logs

**Deploy time**: 1-2 minutes

---

## Monitoring

### View Live Logs

```bash
# On EC2
docker-compose logs -f

# View last 100 lines
docker-compose logs --tail=100

# View logs for specific time
docker-compose logs --since 10m
```

### Check Container Status

```bash
docker-compose ps
docker stats polymarket-weather-bot
```

### View Supabase Logs

Access your Supabase dashboard to view:
- `polymarket_audit_log` - All trading decisions
- `polymarket_rewards` - Liquidity rewards earned

### Common Log Patterns

**Normal operation:**
```
[INFO] Scanning markets...
[INFO] Found 5 weather markets with Liquidity Rewards
[INFO] Evaluating market: Will NYC temp be above 75°F?
[INFO] Fair price: 0.65 | Market price: 0.45 | Alpha: 44.4%
[INFO] BUY signal: Placing order for 2.50 USDC
```

**Errors to watch for:**
```
[ERROR] Weather API rate limit exceeded
[ERROR] Polymarket API authentication failed
[ERROR] Daily loss limit reached
```

---

## Troubleshooting

### Build Issues

**Error: Docker not running**
```bash
# Start Docker Desktop
open -a Docker  # macOS
# or start Docker Desktop manually
```

**Error: AWS credentials not found**
```bash
aws configure
aws sts get-caller-identity  # Test credentials
```

**Error: ECR repository not found**
```bash
./setup-ecr.sh
```

**Error: M1/M2 Mac compatibility**
```bash
# Already handled in build.sh with --platform linux/amd64
# If issues persist, check Docker settings:
# Docker Desktop > Settings > Features in development
# Enable "Use Rosetta for x86/amd64 emulation"
```

### Deployment Issues

**Error: Cannot pull image**
```bash
# Check if image exists
aws ecr list-images --repository-name polymarket-weather-bot

# Re-authenticate
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin \
  <account-id>.dkr.ecr.us-east-1.amazonaws.com
```

**Error: Container won't start**
```bash
# Check logs
docker-compose logs

# Check if previous container is running
docker ps -a
docker-compose down
docker-compose up -d
```

**Error: Out of disk space on EC2**
```bash
# Clean up old images
docker system prune -a

# Check disk usage
df -h
```

### Runtime Issues

**Bot not finding markets**
- Check category filters in config.yaml
- Verify Polymarket API connectivity
- Check if markets have Liquidity Rewards active

**Orders not filling**
- Check if price is competitive
- Verify Post-Only flag is set
- Check order expiration time

**Weather API errors**
- Check API key in AWS Secrets Manager
- Verify rate limits not exceeded
- Check location coordinates are valid

---

## Configuration

### config.yaml

Key settings to adjust:

```yaml
risk:
  initial_capital: 50.0      # Starting bankroll
  max_trade_size: 2.50       # Max per position
  max_daily_loss: 5.0        # Daily loss limit

scanner:
  scan_interval: 30          # Seconds between scans
  categories:
    - "Weather"

farming:
  price_threshold: 0.10      # Farm if price < $0.10
```

### Paper Trading vs Live Trading

The bot defaults to **paper trading** mode for safety.

**Paper trading (recommended first):**
```yaml
# In docker-compose.yml
command: ["python", "-m", "src.main", "--paper", "--config", "/app/config/config.yaml"]
```

**Live trading:**
```yaml
# In docker-compose.yml
command: ["python", "-m", "src.main", "--config", "/app/config/config.yaml"]
```

⚠️ **Always test in paper mode for 24-48 hours first!**

---

## Rollback

To rollback to a previous version:

```bash
# List available versions
aws ecr list-images --repository-name polymarket-weather-bot

# Deploy specific version
./deploy.sh <timestamp>
# Example: ./deploy.sh 20260207-120000
```

---

## Maintenance

### Update Bot Code

```bash
# On local machine:
git pull
./build.sh v1.1.0

# On EC2:
./deploy.sh v1.1.0
```

### View All Deployed Versions

```bash
aws ecr describe-images \
  --repository-name polymarket-weather-bot \
  --query 'sort_by(imageDetails,& imagePushedAt)[*].[imageTags[0],imagePushedAt]' \
  --output table
```

### Clean Up Old Images

```bash
# Keep last 10 versions, delete older
aws ecr list-images \
  --repository-name polymarket-weather-bot \
  --query 'imageIds[10:].[imageDigest]' \
  --output text | \
  while read digest; do
    aws ecr batch-delete-image \
      --repository-name polymarket-weather-bot \
      --image-ids imageDigest=$digest
  done
```

---

## Security Best Practices

1. **Never commit secrets** - Use AWS Secrets Manager
2. **Use IAM roles** - Give EC2 minimal required permissions
3. **Enable ECR scanning** - Already enabled in setup-ecr.sh
4. **Regularly rotate API keys** - Update in Secrets Manager
5. **Monitor logs** - Watch for suspicious activity
6. **Start with paper trading** - Test thoroughly before live

---

## Cost Estimates

| Service | Cost |
|---------|------|
| EC2 t3.micro | ~$7/month |
| ECR storage (5 images) | ~$0.50/month |
| AWS Secrets Manager | $0.40/month |
| Data transfer | ~$1/month |
| **Total** | **~$9/month** |

---

## Support

- Issues: Check logs first, then review this guide
- Questions: See README.md for bot features
- AWS Issues: Check AWS Console for service health

---

**Last Updated**: 2026-02-07
