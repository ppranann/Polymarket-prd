#!/bin/bash
# deploy.sh - Deploy by pulling the latest image from ECR
# Usage: ./deploy.sh [version]
# Example: ./deploy.sh v1.0.0 or ./deploy.sh latest

set -e  # Exit on error
set -u  # Exit on undefined variable
set -o pipefail  # Exit on pipe failure

# Configuration
ECR_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
REPOSITORY_NAME="polymarket-weather-bot"
VERSION="${1:-latest}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Helper functions
info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

info "Starting deployment process..."

# Verify AWS CLI is installed
if ! command -v aws &> /dev/null; then
    error "AWS CLI not found. Please install it first."
fi

# Verify Docker is installed and running
if ! command -v docker &> /dev/null; then
    error "Docker not found. Please install it first."
fi

if ! docker info &> /dev/null; then
    error "Docker is not running. Please start Docker first."
fi

# Get AWS account ID
info "Getting AWS account ID..."
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) || \
    error "Failed to get AWS account ID. Check AWS credentials."

# Construct ECR repository URL
ECR_URL="${AWS_ACCOUNT_ID}.dkr.ecr.${ECR_REGION}.amazonaws.com/${REPOSITORY_NAME}"
FULL_IMAGE="${ECR_URL}:${VERSION}"

info "Deployment Configuration:"
echo "  Region:  ${ECR_REGION}"
echo "  Account: ${AWS_ACCOUNT_ID}"
echo "  Image:   ${FULL_IMAGE}"
echo ""

# Authenticate with ECR
info "Authenticating with ECR..."
aws ecr get-login-password --region ${ECR_REGION} 2>/dev/null | \
    docker login --username AWS --password-stdin ${ECR_URL} &>/dev/null || \
    error "ECR authentication failed"

# Pull the image
info "Pulling image from ECR..."
docker pull ${FULL_IMAGE} || error "Failed to pull image. Check if version exists."

# Stop existing container
info "Stopping existing container..."
docker-compose down 2>/dev/null || warn "No existing container to stop"

# Export image for docker-compose
export ECR_IMAGE="${FULL_IMAGE}"
export AWS_DEFAULT_REGION="${ECR_REGION}"

# Start new container
info "Starting new container..."
docker-compose up -d || error "Failed to start container"

# Wait for container to be healthy
info "Waiting for container to start..."
sleep 5

# Check container status
echo ""
info "Container Status:"
docker-compose ps || warn "Could not get container status"

# Show recent logs
echo ""
info "Recent Logs:"
docker-compose logs --tail=20 || warn "Could not get logs"

echo ""
info "Deployment Complete!"
echo "  Image:     ${FULL_IMAGE}"
echo "  Container: polymarket-weather-bot"
echo ""
echo "Monitor logs: docker-compose logs -f"
echo "Stop bot:     docker-compose down"

