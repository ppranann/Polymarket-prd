# Quick Start Guide

## 3-Step Deployment

### Step 1: One-Time Setup (5 minutes)

```bash
# Verify prerequisites
docker --version   # Should show Docker version
aws --version      # Should show AWS CLI version

# Configure AWS
aws configure
# Enter your Access Key, Secret Key, region (us-east-1)

# Create ECR repository
./setup-ecr.sh
```

### Step 2: Build & Push (3-5 minutes)

```bash
# Build Docker image locally and push to ECR
./build.sh

# Optional: Build with specific version
./build.sh v1.0.0
```

This builds the Docker image on your local machine (avoiding EC2 memory issues) and pushes it to AWS ECR.

### Step 3: Deploy to EC2 (2 minutes)

```bash
# SSH to EC2
ssh -i keys/Polymarket-ec2-test.pem ec2-user@<YOUR-EC2-IP>

# Deploy
./deploy.sh latest
```

Done! Your bot is now running.

---

## Verify It's Working

```bash
# Check container status
docker-compose ps

# View live logs
docker-compose logs -f

# Should see:
[INFO] Starting Polymarket Weather Trading Bot...
[INFO] Scanning markets...
[INFO] Found X weather markets
```

---

## Common Commands

```bash
# View logs
docker-compose logs -f

# Stop bot
docker-compose down

# Restart bot
docker-compose restart

# Update bot (after code changes)
./build.sh v1.1.0          # On local machine
./deploy.sh v1.1.0         # On EC2
```

---

## Troubleshooting

**Can't build?**
- Check Docker is running: `docker info`
- Check AWS credentials: `aws sts get-caller-identity`

**Can't deploy?**
- Verify ECR repository exists: `aws ecr describe-repositories`
- Check EC2 can reach ECR: `aws ecr get-login-password`

**Bot not working?**
- Check logs: `docker-compose logs`
- Verify secrets: Check AWS Secrets Manager
- Check API keys are valid

---

## Full Documentation

- [SETUP.md](SETUP.md) - Account setup and initial configuration
- [DEPLOYMENT.md](DEPLOYMENT.md) - Detailed deployment guide
- [README.md](README.md) - Bot features and architecture

---

## The Problem We Solved

**Old way (BROKEN):**
```
SSH to EC2 → git pull → docker build → Memory exhaustion → Instance hangs
```

**New way (WORKS):**
```
Build locally → Push to ECR → EC2 pulls pre-built image → Works perfectly
```

---

## Architecture

```
┌──────────────┐
│ Local Build  │  1. Build Docker image
│   Machine    │  2. Push to ECR
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   AWS ECR    │  Image Registry
└──────┬───────┘
       │
       ▼
┌──────────────┐
│     EC2      │  3. Pull image
│  t3.micro    │  4. Run bot
└──────────────┘
```

---

**Questions?** See [DEPLOYMENT.md](DEPLOYMENT.md) for detailed troubleshooting.
